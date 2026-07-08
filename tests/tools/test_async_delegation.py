"""Tests for background delegation (HP-202)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.async_delegation import (
    cancel_delegation,
    drain_gateway_completions,
    format_completion_synthesis,
    list_delegations,
    requeue_gateway_completions,
    should_inject_delegation_completion,
)


@pytest.fixture
def fresh_registry(monkeypatch):
    class _FakeReg:
        _entries = {}
        _queue = {}
        _counter = 0

        def register(self, parent_session_key, goal):
            self._counter += 1
            hid = f"d-{self._counter}"
            self._entries[hid] = {
                "handle_id": hid,
                "parent_session_key": parent_session_key,
                "goal": goal,
                "status": "running",
                "summary": "",
                "error": "",
            }
            return hid

        def complete(self, handle_id, status, summary, error):
            e = self._entries.get(handle_id)
            if not e or e["status"] != "running":
                return False
            e["status"] = status
            e["summary"] = summary
            e["error"] = error
            self._queue.setdefault(e["parent_session_key"], []).append(handle_id)
            return True

        def cancel(self, handle_id):
            e = self._entries.get(handle_id)
            if e and e["status"] == "running":
                e["cancel_requested"] = True
                return True
            return False

        def is_cancel_requested(self, handle_id):
            e = self._entries.get(handle_id)
            return bool(e and e.get("cancel_requested"))

        def count_running(self):
            return sum(1 for e in self._entries.values() if e["status"] == "running")

        def get(self, handle_id):
            return self._entries.get(handle_id)

        def list(self, parent_session_key=None):
            if parent_session_key:
                return [
                    self._entries[h]
                    for h in sorted(self._entries)
                    if self._entries[h]["parent_session_key"] == parent_session_key
                ]
            return [self._entries[h] for h in sorted(self._entries)]

        def drain_completions(self, parent_session_key):
            ids = self._queue.pop(parent_session_key, [])
            return ids

        def drain_completions_up_to(self, parent_session_key, limit):
            queue = self._queue.setdefault(parent_session_key, [])
            take = len(queue) if not limit else min(limit, len(queue))
            out = queue[:take]
            del queue[:take]
            if not queue:
                self._queue.pop(parent_session_key, None)
            return out

        def requeue_completions(self, parent_session_key, handle_ids):
            queue = self._queue.setdefault(parent_session_key, [])
            for hid in reversed(list(handle_ids)):
                if hid in self._entries:
                    queue.insert(0, hid)

    reg = _FakeReg()
    monkeypatch.setattr("tools.async_delegation.get_registry", lambda: reg)
    return reg


class TestDelegationRegistry:
    def test_register_and_complete(self, fresh_registry):
        hid = fresh_registry.register("agent:main:cli:abc", "fix bug")
        assert hid.startswith("d-")
        assert fresh_registry.count_running() == 1
        assert fresh_registry.complete(hid, "completed", "done", "")
        assert fresh_registry.count_running() == 0
        drained = fresh_registry.drain_completions("agent:main:cli:abc")
        assert drained == [hid]

    def test_cancel_sets_flag(self, fresh_registry):
        hid = fresh_registry.register("sk", "task")
        assert fresh_registry.cancel(hid)
        assert fresh_registry.is_cancel_requested(hid)


class TestAsyncDelegationHelpers:
    def test_format_single_completion(self):
        text = format_completion_synthesis([{
            "handle_id": "d-1",
            "status": "completed",
            "goal": "test",
            "summary": "ok",
        }])
        assert "d-1" in text
        assert "Untrusted subagent output" in text
        assert "IMPORTANT" not in text

    def test_list_delegations_empty(self, fresh_registry):
        assert list_delegations("agent:main:cli:x") == []

    def test_list_delegations_without_filter_returns_empty(self, fresh_registry):
        fresh_registry.register("sk", "task")
        assert list_delegations(None) == []

    def test_drain_gateway_respects_merge_limit(self, fresh_registry, monkeypatch):
        monkeypatch.setattr(
            "tools.async_delegation._get_max_merged_completions", lambda: 2
        )
        parent = "agent:main:cli:merge"
        ids = []
        for i in range(4):
            hid = fresh_registry.register(parent, f"task-{i}")
            fresh_registry.complete(hid, "completed", f"done-{i}", "")
            ids.append(hid)
        first, first_ids = drain_gateway_completions(parent)
        assert first is not None
        assert len(first_ids) == 2
        second, second_ids = drain_gateway_completions(parent)
        assert second is not None
        assert len(second_ids) == 2
        third, third_ids = drain_gateway_completions(parent)
        assert third is None
        assert third_ids == []

    def test_requeue_gateway_completions(self, fresh_registry):
        parent = "agent:main:cli:rq"
        hid = fresh_registry.register(parent, "task")
        fresh_registry.complete(hid, "completed", "ok", "")
        synth, ids = drain_gateway_completions(parent)
        assert synth is not None
        assert ids == [hid]
        requeue_gateway_completions(parent, ids)
        again, again_ids = drain_gateway_completions(parent)
        assert again is not None
        assert again_ids == [hid]

    def test_should_inject_notify_modes(self):
        ok = [{"status": "completed"}]
        fail = [{"status": "failed"}]
        assert should_inject_delegation_completion(ok, "all") is True
        assert should_inject_delegation_completion(ok, "result") is True
        assert should_inject_delegation_completion(ok, "error") is False
        assert should_inject_delegation_completion(fail, "error") is True
        assert should_inject_delegation_completion(ok, "off") is False


class TestDelegateToolBackground:
    def test_background_returns_handle(self, fresh_registry, monkeypatch):
        from tools.delegate_tool import delegate_task

        parent = MagicMock()
        parent.session_id = "sess1"
        parent.platform = "cli"
        parent._gateway_session_key = None
        parent._delegate_depth = 0
        parent._interrupt_requested = False

        child_result = {
            "task_index": 0,
            "status": "completed",
            "summary": "child done",
        }

        with patch("tools.delegate_tool._load_config", return_value={"max_iterations": 5}), \
             patch("tools.delegate_tool._resolve_delegation_credentials", return_value={
                 "model": "m", "provider": "p", "base_url": "", "api_key": "k",
                 "api_mode": "", "command": None, "args": None,
             }), \
             patch("tools.delegate_tool._build_child_agent", return_value=MagicMock()), \
             patch("tools.delegate_tool._run_single_child", return_value=child_result), \
             patch("tools.delegate_tool.is_spawn_paused", return_value=False), \
             patch("tools.delegate_tool._get_max_spawn_depth", return_value=2), \
             patch("tools.delegate_tool._get_max_concurrent_children", return_value=3), \
             patch("tools.delegate_tool._parent_session_id_for_delegate", return_value="sess1"), \
             patch("tools.delegate_tool._restore_parent_session_context"), \
             patch("model_tools._last_resolved_tool_names", []):
            raw = delegate_task(
                goal="background task",
                background=True,
                parent_agent=parent,
            )

        data = json.loads(raw)
        assert data["success"] is True
        assert data["background"] is True
        assert len(data["handles"]) == 1
