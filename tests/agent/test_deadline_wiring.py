"""Timeout-equivalence acceptance for the G-02 resolver wiring (门-1 clause).

The resolver migration must not silently change any effective timeout when
the operator has NOT configured `timeouts.*`. Each wired site asserts its
effective value equals the documented pre-migration default.
"""


import tools.mcp_tool as mcp
from agent.tool_executor import _resolve_concurrent_batch_timeout


def test_mcp_tool_call_default_unchanged(monkeypatch):
    monkeypatch.delenv("INTELLECT_MCP_TOOL_TIMEOUT", raising=False)
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly", lambda: {}, raising=False
    )
    assert mcp._resolve_mcp_default_tool_timeout() == 120.0


def test_mcp_tool_call_config_override(monkeypatch):
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly",
        lambda: {"timeouts": {"mcp": {"tool_call": 300}}},
        raising=False,
    )
    assert mcp._resolve_mcp_default_tool_timeout() == 300.0


def test_mcp_tool_call_env_override(monkeypatch):
    monkeypatch.setenv("INTELLECT_MCP_TOOL_TIMEOUT", "60")
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly", lambda: {}, raising=False
    )
    assert mcp._resolve_mcp_default_tool_timeout() == 60.0


def test_mcp_default_is_none_safe_for_run_on_mcp_loop(monkeypatch):
    """0-config `0` must resolve to None and _run_on_mcp_loop tolerates None."""
    monkeypatch.setenv("INTELLECT_MCP_TOOL_TIMEOUT", "0")
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly", lambda: {}, raising=False
    )
    assert mcp._resolve_mcp_default_tool_timeout() is None
    # The None-tolerant deadline line in _run_on_mcp_loop (historical code):
    import inspect

    src = inspect.getsource(mcp._run_on_mcp_loop)
    assert "deadline = None if timeout is None else start_time + timeout" in src


def test_concurrent_batch_unbounded_by_default(monkeypatch):
    """HISTORICAL PARITY: no config + no env -> unbounded (None), exactly as
    before the resolver existed (the batch previously had no timeout)."""
    monkeypatch.delenv("INTELLECT_CONCURRENT_TOOL_TIMEOUT_S", raising=False)
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly", lambda: {}, raising=False
    )
    assert _resolve_concurrent_batch_timeout() is None


def test_concurrent_batch_explicit_config_bounds(monkeypatch):
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly",
        lambda: {"timeouts": {"tools": {"concurrent_batch": 90}}},
        raising=False,
    )
    assert _resolve_concurrent_batch_timeout() == 90.0


def test_sequential_resolver_key_documented_not_wired(monkeypatch):
    """sequential_call is DELIBERATELY not wired into the sequential executor:
    human approval windows dynamically extend sequential execution, so a
    fixed deadline primitive does not apply there (Hermes Phase 2a made the
    same call). This test pins the resolver-side contract only — unconfigured
    sequential inherits the batch default (unbounded) — for the day an
    approval-aware deadline lands."""
    from agent.deadline import resolve_timeout

    monkeypatch.delenv("INTELLECT_CONCURRENT_TOOL_TIMEOUT_S", raising=False)
    monkeypatch.delenv("INTELLECT_SEQUENTIAL_TOOL_TIMEOUT_S", raising=False)
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly", lambda: {}, raising=False
    )
    sequential = resolve_timeout(
        "tools.sequential_call",
        default=resolve_timeout("tools.concurrent_batch", default=None),
    )
    assert sequential is None

    # And the executor itself must NOT have a sequential resolver wired yet.
    import agent.tool_executor as te

    assert not hasattr(te, "_resolve_sequential_timeout")


def test_mcp_timeout_picks_up_config_reload(monkeypatch):
    """Freshness (P2-5): server construction re-resolves, so a config change
    takes effect on the next server — no process restart."""
    from tools.mcp_tool import MCPServerTask

    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly",
        lambda: {"timeouts": {"mcp": {"tool_call": 77}}},
        raising=False,
    )
    srv = MCPServerTask("freshness-probe")
    assert srv.tool_timeout == 77.0
