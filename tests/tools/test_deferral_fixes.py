"""Behavioral regression suite for the tool-search bridge activation path.

Each test class pins one user-visible behavior that used to break the moment
the bridge activated, asserted at the public seams (planner batch admission,
bridge peel, catalog rendering) rather than private implementation details.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _td(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
            },
        },
    }


def _mock_tool_call(name: str = "tool_call", arguments: str = '{"name":"x","arguments":{}}',
                    call_id: str = "c") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _register(name: str, toolset: str) -> str:
    from tools.registry import registry

    def _handler(args, task_id=None, **kw):
        return json.dumps({"ok": True})

    registry.register(name=name, handler=_handler, schema=_td(name), toolset=toolset)
    return name


def _deregister(names) -> None:
    from tools.registry import registry
    for n in names:
        registry.deregister(n)


def _setup_parallel_mcp(monkeypatch) -> None:
    """Mark the ``docs`` MCP server as parallel-safe; ``github`` stays opt-out."""
    import tools.mcp_tool as mcp_tool
    monkeypatch.setattr(mcp_tool, "_parallel_safe_servers", {"docs"})
    monkeypatch.setattr(mcp_tool, "_mcp_tool_server_names", {
        "mcp_docs_search": "docs",
        "mcp_docs_read_file": "docs",
        "mcp_github_list_repos": "github",
    })


class TestBridgePeelInPlanner:
    """The batch planner must admit bridged ``tool_call`` batches on the
    UNDERLYING tool name — exactly as if those tools had been listed
    directly — and treat read-only bridge lookups as parallel-safe."""

    def test_two_bridged_parallel_safe_mcp_calls_run_parallel(self, monkeypatch):
        from run_agent import _should_parallelize_tool_batch
        _setup_parallel_mcp(monkeypatch)
        names = [_register("mcp_docs_search", "mcp-docs"),
                 _register("mcp_docs_read_file", "mcp-docs")]
        try:
            batch = [
                _mock_tool_call(arguments=json.dumps(
                    {"name": "mcp_docs_search", "arguments": {"q": "x"}})),
                _mock_tool_call(arguments=json.dumps(
                    {"name": "mcp_docs_read_file", "arguments": {"q": "y"}})),
            ]
            assert _should_parallelize_tool_batch(batch)
        finally:
            _deregister(names)

    def test_bridged_call_to_non_opted_in_tool_stays_sequential(self, monkeypatch):
        from run_agent import _should_parallelize_tool_batch
        _setup_parallel_mcp(monkeypatch)
        name = _register("mcp_github_list_repos", "mcp-github")
        try:
            batch = [
                _mock_tool_call(arguments=json.dumps(
                    {"name": "mcp_github_list_repos", "arguments": {}})),
                _mock_tool_call(arguments=json.dumps(
                    {"name": "mcp_github_list_repos", "arguments": {}})),
            ]
            assert not _should_parallelize_tool_batch(batch)
        finally:
            _deregister([name])

    def test_bridge_lookups_are_parallel_safe(self, monkeypatch):
        from run_agent import _should_parallelize_tool_batch
        _setup_parallel_mcp(monkeypatch)
        batch = [
            _mock_tool_call(name="tool_search", arguments='{"queries": ["a"]}'),
            _mock_tool_call(name="tool_describe", arguments='{"names": ["b"]}'),
        ]
        assert _should_parallelize_tool_batch(batch)

    def test_malformed_bridge_call_stays_a_barrier(self, monkeypatch):
        from run_agent import _should_parallelize_tool_batch
        _setup_parallel_mcp(monkeypatch)
        name = _register("mcp_docs_search", "mcp-docs")
        try:
            # Missing ``name`` inside tool_call → resolve fails → stays the
            # literal tool_call name → not parallel-safe → whole batch serial.
            bad = _mock_tool_call(arguments='{"arguments": {}}')
            good = _mock_tool_call(arguments=json.dumps(
                {"name": "mcp_docs_search", "arguments": {}}))
            assert not _should_parallelize_tool_batch([bad, good])
        finally:
            _deregister([name])

    def test_emission_order_survives_the_peel(self, monkeypatch):
        """Peeling is pure: it resolves the underlying name/args and never
        mutates the original objects, so model emission order is preserved."""
        from agent.tool_dispatch_helpers import _peel_bridge_call
        _setup_parallel_mcp(monkeypatch)
        name = _register("mcp_docs_search", "mcp-docs")
        try:
            effective_name, effective_args = _peel_bridge_call(
                "tool_call", {"name": "mcp_docs_search", "arguments": {"q": "x"}},
            )
            assert effective_name == "mcp_docs_search"
            assert effective_args == {"q": "x"}
            # Non-bridge names pass through untouched.
            assert _peel_bridge_call("web_search", {"q": "x"}) == ("web_search", {"q": "x"})
        finally:
            _deregister([name])

    def test_bridged_mcp_admission_matches_direct_admission(self, monkeypatch):
        from run_agent import _should_parallelize_tool_batch
        _setup_parallel_mcp(monkeypatch)
        names = [_register("mcp_docs_search", "mcp-docs"),
                 _register("mcp_docs_read_file", "mcp-docs")]
        try:
            direct = [
                _mock_tool_call(name="mcp_docs_search", arguments='{"q":"x"}'),
                _mock_tool_call(name="mcp_docs_read_file", arguments='{"q":"y"}'),
            ]
            bridged = [
                _mock_tool_call(arguments=json.dumps(
                    {"name": "mcp_docs_search", "arguments": {"q": "x"}})),
                _mock_tool_call(arguments=json.dumps(
                    {"name": "mcp_docs_read_file", "arguments": {"q": "y"}})),
            ]
            assert _should_parallelize_tool_batch(direct)
            assert _should_parallelize_tool_batch(bridged) == _should_parallelize_tool_batch(direct)
        finally:
            _deregister(names)

    def test_core_file_tools_cannot_be_smuggled_through_the_bridge(self, monkeypatch):
        from run_agent import _should_parallelize_tool_batch
        _setup_parallel_mcp(monkeypatch)
        # A bridged attempt at a core file tool is not deferrable → resolve
        # fails → stays the literal tool_call name → serial barrier. A core
        # tool must never gain concurrency by being wrapped in the bridge.
        batch = [
            _mock_tool_call(arguments=json.dumps(
                {"name": "write_file", "arguments": {"path": "a"}})),
            _mock_tool_call(arguments=json.dumps(
                {"name": "write_file", "arguments": {"path": "b"}})),
        ]
        assert not _should_parallelize_tool_batch(batch)


class TestSequentialExecutorProbe:
    """A single (non-parallel) tool_call must still run the blind-call probe.

    ``_should_parallelize_tool_batch`` returns False for a batch of one, so a
    lone deferred tool_call routes through ``execute_tool_calls_sequential`` —
    the common case. Its unwrap must return the schema, not dispatch blind.
    """

    @staticmethod
    def _register_required():
        from tools.registry import registry

        def _handler(args, task_id=None, **kw):
            return json.dumps({"ok": True, "doc": args.get("document_id")})

        schema = _td("mcp_probe_seq")
        schema["function"]["description"] = "Read a doc by id."
        schema["function"]["parameters"] = {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        }
        registry.register(
            name="mcp_probe_seq", handler=_handler, schema=schema, toolset="mcp-probe-seq",
        )
        return "mcp_probe_seq"

    def test_single_bridged_call_probed_in_sequential_executor(self):
        from unittest.mock import MagicMock
        from agent.tool_executor import execute_tool_calls_sequential

        name = self._register_required()
        try:
            stub = _make_sequential_stub()
            tc = _mock_tool_call(arguments=json.dumps({"name": name, "arguments": {}}))
            msg = SimpleNamespace(tool_calls=[tc])
            messages = []
            execute_tool_calls_sequential(stub, msg, messages, "task")
            text = str(messages[-1])
            assert "NOT invoked" in text
            assert "document_id" in text
        finally:
            _deregister([name])


def _make_sequential_stub():
    """Minimal agent stub that survives execute_tool_calls_sequential's
    pre-dispatch bookkeeping up to the blind-call probe result."""
    from unittest.mock import MagicMock

    class _Stub:
        log_prefix = ""
        quiet_mode = True
        verbose_logging = False
        log_prefix_chars = 200
        enabled_toolsets = None
        disabled_toolsets = None
        session_id = "test-session"
        _checkpoint_mgr = MagicMock(enabled=False)
        _subdirectory_hints = MagicMock()
        _tool_guardrails = MagicMock()
        _todo_store = MagicMock()
        _memory_store = MagicMock()
        _memory_manager = None
        _rag_manager = None
        context_compressor = None
        tool_progress_callback = None
        tool_start_callback = None
        tool_complete_callback = None
        valid_tool_names = set()
        _turns_since_memory = 0
        _iters_since_skill = 0
        _current_tool = None
        _interrupt_requested = False
        tool_delay = 0

        def _touch_activity(self, desc):
            pass

        def _vprint(self, msg, force=False):
            pass

        def _safe_print(self, msg):
            pass

        def _should_emit_quiet_tool_messages(self):
            return False

        def _get_session_db_for_recall(self):
            return None

        def _tool_result_content_for_active_model(self, function_name, function_result):
            return function_result

        def _apply_pending_steer_to_tool_results(self, *a, **kw):
            return None

    stub = _Stub()
    stub._tool_guardrails.before_call.return_value = MagicMock(allows_execution=True)
    stub._subdirectory_hints.check_tool_call.return_value = ""
    return stub
