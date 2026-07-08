"""Gateway E2E tests for background delegate_task completion injection (HP-205a).

Verifies ``_run_delegation_watcher`` drains the Rust completion queue and
injects a synthetic ``MessageEvent(internal=True)`` into the platform
adapter — the same path used for terminal ``notify_on_complete``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner, _bootstrap_gateway_mixins


# ---------------------------------------------------------------------------
# Fake DelegationRegistry (mirrors tests/tools/test_async_delegation.py)
# ---------------------------------------------------------------------------

class FakeDelegationRegistry:
    def __init__(self):
        self._entries: dict = {}
        self._queue: dict = {}
        self._counter = 0

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
        entry = self._entries.get(handle_id)
        if not entry or entry["status"] != "running":
            return False
        entry["status"] = status
        entry["summary"] = summary
        entry["error"] = error
        self._queue.setdefault(entry["parent_session_key"], []).append(handle_id)
        return True

    def cancel(self, handle_id):
        entry = self._entries.get(handle_id)
        if entry and entry["status"] == "running":
            entry["cancel_requested"] = True
            return True
        return False

    def is_cancel_requested(self, handle_id):
        entry = self._entries.get(handle_id)
        return bool(entry and entry.get("cancel_requested"))

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
        return self._queue.pop(parent_session_key, [])

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

    def queue_len(self, parent_session_key) -> int:
        return len(self._queue.get(parent_session_key, []))


@pytest.fixture
def fake_delegation_registry(monkeypatch):
    reg = FakeDelegationRegistry()
    import tools.async_delegation as ad

    monkeypatch.setattr(ad, "_registry_singleton", reg, raising=False)
    monkeypatch.setattr(ad, "get_registry", lambda: reg)
    return reg


def _build_runner(monkeypatch, tmp_path, mode: str = "all") -> GatewayRunner:
    _bootstrap_gateway_mixins()
    (tmp_path / "config.yaml").write_text(
        f"display:\n  background_process_notifications: {mode}\n",
        encoding="utf-8",
    )
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_intellect_home", tmp_path)
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock(), handle_message=AsyncMock())
    runner.adapters[Platform.TELEGRAM] = adapter
    return runner


def _watcher_dict(**overrides):
    base = {
        "parent_session_key": "agent:main:telegram:dm:123",
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "thread_id": "42",
        "user_id": "u1",
        "user_name": "Alice",
        "message_id": "999",
    }
    base.update(overrides)
    return base


async def _instant_sleep(*_a, **_kw):
    pass


def _seed_completed(
    reg: FakeDelegationRegistry,
    *,
    parent: str,
    goal: str = "fix tests",
    status: str = "completed",
    summary: str = "all green",
) -> str:
    hid = reg.register(parent, goal)
    reg.complete(hid, status, summary, "")
    return hid


# ---------------------------------------------------------------------------
# HP-205a — core E2E
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delegation_watcher_injects_internal_synthesis(
    monkeypatch, tmp_path, fake_delegation_registry
):
    """Completed background delegation → internal MessageEvent → adapter.handle_message."""
    parent = "agent:main:telegram:dm:123"
    _seed_completed(
        fake_delegation_registry,
        parent=parent,
        summary="tests passed",
    )

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_delegation_watcher(_watcher_dict(parent_session_key=parent))

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert isinstance(event, MessageEvent)
    assert event.internal is True
    assert event.message_id == "999"
    assert "tests passed" in event.text
    assert "Untrusted subagent output" in event.text
    assert event.source.platform == Platform.TELEGRAM
    assert event.source.chat_id == "123"
    assert event.source.thread_id == "42"
    assert fake_delegation_registry.queue_len(parent) == 0


@pytest.mark.asyncio
async def test_delegation_watcher_internal_event_bypasses_user_send_path(
    monkeypatch, tmp_path, fake_delegation_registry
):
    """Injection uses handle_message (agent turn), not adapter.send (user chat)."""
    parent = "agent:main:telegram:dm:123"
    _seed_completed(fake_delegation_registry, parent=parent)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    runner = _build_runner(monkeypatch, tmp_path)
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_delegation_watcher(_watcher_dict(parent_session_key=parent))

    adapter.handle_message.assert_awaited_once()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "status", "should_inject"),
    [
        ("all", "completed", True),
        ("result", "completed", True),
        ("error", "completed", False),
        ("error", "failed", True),
        ("off", "completed", False),
    ],
)
async def test_delegation_watcher_respects_notification_mode(
    monkeypatch,
    tmp_path,
    fake_delegation_registry,
    mode,
    status,
    should_inject,
):
    parent = "agent:main:telegram:dm:123"
    if status == "failed":
        hid = fake_delegation_registry.register(parent, "task")
        fake_delegation_registry.complete(hid, "failed", "", "boom")
    else:
        _seed_completed(
            fake_delegation_registry,
            parent=parent,
            status=status,
            summary="done",
        )

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    runner = _build_runner(monkeypatch, tmp_path, mode)
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_delegation_watcher(_watcher_dict(parent_session_key=parent))

    if should_inject:
        adapter.handle_message.assert_awaited_once()
    else:
        adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegation_watcher_requeues_on_routing_failure(
    monkeypatch, tmp_path, fake_delegation_registry
):
    """When routing metadata is missing, drained ids are requeued for retry."""
    parent = "agent:main:telegram:dm:123"
    hid = _seed_completed(fake_delegation_registry, parent=parent)

    sleep_calls = 0

    async def _sleep_then_cancel(*_a, **_kw):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep_then_cancel)
    runner = _build_runner(monkeypatch, tmp_path)
    watcher = _watcher_dict(
        parent_session_key=parent,
        session_key="",
        platform="not_a_real_platform",
        chat_id="",
    )

    with pytest.raises(asyncio.CancelledError):
        await runner._run_delegation_watcher(watcher)

    assert fake_delegation_registry.queue_len(parent) == 1
    assert fake_delegation_registry.get(hid)["status"] == "completed"


@pytest.mark.asyncio
async def test_delegation_watcher_requeues_on_inject_exception(
    monkeypatch, tmp_path, fake_delegation_registry
):
    parent = "agent:main:telegram:dm:123"
    _seed_completed(fake_delegation_registry, parent=parent)

    sleep_calls = 0

    async def _sleep_then_cancel(*_a, **_kw):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep_then_cancel)
    runner = _build_runner(monkeypatch, tmp_path)
    adapter = runner.adapters[Platform.TELEGRAM]
    adapter.handle_message = AsyncMock(side_effect=RuntimeError("inject failed"))

    with pytest.raises(asyncio.CancelledError):
        await runner._run_delegation_watcher(_watcher_dict(parent_session_key=parent))

    assert fake_delegation_registry.queue_len(parent) == 1


@pytest.mark.asyncio
async def test_delegation_watcher_idle_exits_without_completions(
    monkeypatch, tmp_path, fake_delegation_registry
):
    """Watcher stops after idle cycles when nothing is running or queued."""
    parent = "agent:main:telegram:dm:123"
    sleep_calls = 0

    async def _count_sleep(*_a, **_kw):
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(asyncio, "sleep", _count_sleep)
    runner = _build_runner(monkeypatch, tmp_path)
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_delegation_watcher(_watcher_dict(parent_session_key=parent))

    assert sleep_calls == 12
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegation_watcher_drains_merge_batch_then_remainder(
    monkeypatch, tmp_path, fake_delegation_registry
):
    """Two watcher inject cycles drain a 3-handle queue when merge limit is 2."""
    parent = "agent:main:telegram:dm:123"
    for i in range(3):
        hid = fake_delegation_registry.register(parent, f"task-{i}")
        fake_delegation_registry.complete(hid, "completed", f"ok-{i}", "")

    monkeypatch.setattr(
        "tools.async_delegation._get_max_merged_completions", lambda: 2
    )

    inject_calls = 0

    async def _sleep_then_stop(*_a, **_kw):
        nonlocal inject_calls
        # After second inject, queue empty and idle → allow idle exit
        inject_calls += 1

    monkeypatch.setattr(asyncio, "sleep", _sleep_then_stop)
    runner = _build_runner(monkeypatch, tmp_path)
    adapter = runner.adapters[Platform.TELEGRAM]

    await runner._run_delegation_watcher(_watcher_dict(parent_session_key=parent))

    assert adapter.handle_message.await_count == 2
    assert fake_delegation_registry.queue_len(parent) == 0


@pytest.mark.asyncio
async def test_delegation_watcher_spawn_dedup_single_inject(
    monkeypatch, tmp_path, fake_delegation_registry
):
    """Duplicate post-turn watcher spawns must not double-inject (HP-205f)."""
    from tools.async_delegation import try_start_delegation_watcher

    parent = "agent:main:telegram:dm:123"
    _seed_completed(fake_delegation_registry, parent=parent)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    runner = _build_runner(monkeypatch, tmp_path)
    adapter = runner.adapters[Platform.TELEGRAM]
    watcher = _watcher_dict(parent_session_key=parent)

    tasks = []
    for _ in range(2):
        if try_start_delegation_watcher(parent):
            tasks.append(asyncio.create_task(runner._run_delegation_watcher(watcher)))

    assert len(tasks) == 1
    await tasks[0]

    assert adapter.handle_message.await_count == 1
