"""Tests for MCP result governance (G-18 / A3-6): 50K tier, identical-
result reference stub, mcp doctor sweep."""

import pytest
from types import SimpleNamespace

from tools import mcp_result_guard as mrg
from tools.budget_config import BudgetConfig


@pytest.fixture(autouse=True)
def _clean_store():
    mrg.reset_tracked()
    yield
    mrg.reset_tracked()


# ── budget tier ─────────────────────────────────────────────────────────

def test_mcp_tools_get_tight_50k_tier():
    cfg = BudgetConfig()
    assert cfg.resolve_threshold("mcp_github_search") == 50_000
    assert cfg.resolve_threshold("read_file") == float("inf")  # pinned wins
    assert cfg.resolve_threshold("some_other_tool") == 100_000  # default


def test_mcp_tier_yields_to_tool_overrides():
    cfg = BudgetConfig(tool_overrides={"mcp_github_search": 10_000})
    assert cfg.resolve_threshold("mcp_github_search") == 10_000


# ── identical-result reference stub ─────────────────────────────────────

BIG = "x" * 50_000


def test_below_threshold_passes_through():
    small = "small result"
    assert mrg.maybe_stub_identical_result("mcp_s_t", {"a": 1}, small) is small
    assert mrg.maybe_stub_identical_result("mcp_s_t", {"a": 1}, small) is small


def test_first_oversized_result_passes_then_identical_repeats_stub():
    args = {"q": "release notes"}
    first = mrg.maybe_stub_identical_result(
        "mcp_s_fetch", args, BIG, call_id="call_1")
    assert first == BIG  # first delivery is the real payload

    stub = mrg.maybe_stub_identical_result(
        "mcp_s_fetch", args, BIG, call_id="call_2")
    assert "intellect note:" in stub
    assert "byte-identical" in stub
    assert "call_1" in stub  # points at the FIRST delivery
    assert len(stub) < 1000  # the point is token savings


def test_changed_result_passes_through_again():
    mrg.maybe_stub_identical_result("mcp_s_t", {"a": 1}, BIG, call_id="call_1")
    changed = "y" * 50_001
    assert mrg.maybe_stub_identical_result("mcp_s_t", {"a": 1}, changed) == changed


def test_different_args_are_not_duplicates():
    mrg.maybe_stub_identical_result("mcp_s_t", {"a": 1}, BIG, call_id="call_1")
    assert mrg.maybe_stub_identical_result(
        "mcp_s_t", {"a": 2}, BIG) == BIG  # different args → real delivery


def test_dedup_store_is_bounded():
    for i in range(200):
        mrg.maybe_stub_identical_result(
            "mcp_s_t", {"i": i}, f"{BIG[:50_000]}-{i}")
    with mrg._LOCK:
        assert len(mrg._TRACKED) <= mrg._MAX_TRACKED


# ── mcp doctor sweep ────────────────────────────────────────────────────

def test_mcp_doctor_reports_per_server(capsys, monkeypatch):
    from intellect_cli import mcp_config

    monkeypatch.setattr(
        mcp_config, "_get_mcp_servers", lambda: {"good": {}, "bad": {}}
    )

    def _probe(name, config, connect_timeout=30):
        if name == "good":
            return [("tool_a", "does a"), ("tool_b", "does b")]
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mcp_config, "_probe_single_server", _probe)
    mcp_config.cmd_mcp_doctor(SimpleNamespace(timeout=5))
    out = capsys.readouterr().out
    assert "✓ good" in out and "2 tool(s)" in out
    assert "✗ bad" in out and "connection refused" in out
    assert "1/2 server(s) healthy" in out


def test_mcp_doctor_empty_config(capsys, monkeypatch):
    from intellect_cli import mcp_config

    monkeypatch.setattr(mcp_config, "_get_mcp_servers", lambda: {})
    mcp_config.cmd_mcp_doctor(SimpleNamespace(timeout=5))
    assert "No MCP servers configured" in capsys.readouterr().out
