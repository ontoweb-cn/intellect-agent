"""Phase 5b tests — bi-temporal timeline rendering + dump CLI.

Covers:
  5.2  Timeline ASCII + JSON renderers, active/historical grouping,
       ISO date tolerance.
  5.6  dump → JSON-lines per-graph, group_id filter in dump cypher,
       per-graph error isolation, default output path.
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 5.2 — Timeline rendering
# ---------------------------------------------------------------------------

def _rec(fact, valid_at=None, invalid_at=None, observed_at=None, episode_id=None):
    return {
        "fact": fact,
        "valid_at": valid_at,
        "invalid_at": invalid_at,
        "observed_at": observed_at,
        "episode_id": episode_id,
    }


def test_timeline_empty_renders_placeholder():
    from plugins.memory.graphiti.timeline import render_timeline_text

    out = render_timeline_text([])
    assert "no facts found" in out
    assert "Graphiti timeline" in out


def test_timeline_groups_historical_and_active():
    from plugins.memory.graphiti.timeline import render_timeline_text

    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    records = [
        _rec(
            "Alice prefers tea",
            valid_at="2025-01-01T10:00:00+00:00",
            invalid_at=None,                              # still valid
            observed_at="2025-01-01T10:00:00+00:00",
        ),
        _rec(
            "Alice works at Acme",
            valid_at="2024-03-01T08:00:00+00:00",
            invalid_at="2025-09-15T17:00:00+00:00",       # closed
            observed_at="2024-03-01T08:00:00+00:00",
        ),
    ]
    out = render_timeline_text(records, node_id="alice", now=now)
    assert "alice" in out
    assert "Historical:" in out
    assert "Currently valid:" in out
    # Historical section comes BEFORE active section (read top-to-bottom).
    assert out.index("Historical:") < out.index("Currently valid:")
    # The closed fact must be in Historical.
    hist_block = out[out.index("Historical:") : out.index("Currently valid:")]
    assert "Acme" in hist_block
    # The open fact must be in Currently valid.
    active_block = out[out.index("Currently valid:") :]
    assert "tea" in active_block
    # Active fact must show "still valid", not a closing timestamp.
    assert "still valid" in active_block


def test_timeline_iso_z_suffix_parses():
    """Z-suffix ISO strings (Graphiti's default) parse cleanly."""
    from plugins.memory.graphiti.timeline import render_timeline_json

    rec = _rec(
        "x",
        valid_at="2025-01-01T00:00:00Z",
        invalid_at="2025-06-01T00:00:00Z",
        observed_at="2025-01-01T00:00:00Z",
    )
    out = json.loads(render_timeline_json([rec]))
    record = out["records"][0]
    # All three timestamps round-trip to ISO-8601 with offset.
    assert record["valid_at"].startswith("2025-01-01T00:00:00")
    assert record["invalid_at"].startswith("2025-06-01T00:00:00")
    assert record["observed_at"].startswith("2025-01-01T00:00:00")


def test_timeline_json_marks_active_correctly():
    from plugins.memory.graphiti.timeline import render_timeline_json

    now = datetime(2026, 6, 6, tzinfo=timezone.utc)
    records = [
        _rec("future closing", valid_at="2025-01-01T00:00:00Z",
             invalid_at="2030-01-01T00:00:00Z"),     # invalid_at in future = still active
        _rec("past closed",    valid_at="2024-01-01T00:00:00Z",
             invalid_at="2025-01-01T00:00:00Z"),     # invalid_at past = not active
        _rec("open ended",     valid_at="2025-01-01T00:00:00Z",
             invalid_at=None),                        # None = always active
    ]
    out = json.loads(render_timeline_json(records, now=now))
    active_flags = [r["active"] for r in out["records"]]
    assert active_flags == [True, False, True]


def test_timeline_text_long_fact_is_truncated():
    from plugins.memory.graphiti.timeline import render_timeline_text

    long_fact = "x" * 500
    out = render_timeline_text(
        [_rec(long_fact, valid_at="2025-01-01T00:00:00Z")],
        max_fact_len=50,
    )
    # The 500-char fact should not appear in full.
    assert "x" * 500 not in out
    # The truncation marker should be present.
    assert "…" in out


def test_timeline_text_falls_back_to_created_at_for_observed():
    """Records emitted by older client versions use 'created_at' not 'observed_at'."""
    from plugins.memory.graphiti.timeline import render_timeline_text

    rec = {
        "fact": "legacy",
        "valid_at": "2025-01-01T00:00:00Z",
        "invalid_at": None,
        "created_at": "2025-01-01T00:00:00Z",        # not observed_at
    }
    out = render_timeline_text([rec])
    assert "observed 2025-01-01" in out


def test_timeline_text_handles_missing_validity():
    """Records with no valid_at still render (in active or historical, gracefully)."""
    from plugins.memory.graphiti.timeline import render_timeline_text

    out = render_timeline_text([_rec("free-floating")])
    assert "free-floating" in out


# ---------------------------------------------------------------------------
# 5.6 — Dump CLI
# ---------------------------------------------------------------------------

def test_manager_dump_dispatches_to_each_scope_graph():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "x", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id="t1", project_id="p1")

    called = []
    class _FakeClient:
        def __init__(self, name): self.name = name
        def dump(self):
            called.append(self.name)
            return {
                "graph": self.name,
                "nodes": [{"uuid": f"{self.name}-n1"}],
                "edges": [],
            }

    with patch.object(mgr, "_client", lambda g: _FakeClient(g)):
        out = mgr.dump(scope="all")

    assert set(called) == {"member_m1", "team_t1", "project_p1"}
    for g in called:
        assert out[g]["nodes"][0]["uuid"] == f"{g}-n1"


def test_manager_dump_records_per_graph_errors():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "x", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id="t1", project_id=None)

    class _FakeClient:
        def __init__(self, name): self.name = name
        def dump(self):
            if self.name == "team_t1":
                raise RuntimeError("FalkorDB busy")
            return {"graph": self.name, "nodes": [], "edges": []}

    with patch.object(mgr, "_client", lambda g: _FakeClient(g)):
        out = mgr.dump(scope="all")

    assert "error" not in out["member_m1"]
    assert "FalkorDB busy" in out["team_t1"]["error"]
    # Failed graph still has empty nodes/edges keys for shape consistency.
    assert out["team_t1"]["nodes"] == []
    assert out["team_t1"]["edges"] == []


def test_manager_dump_default_scope_is_all():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "x", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id="t1", project_id=None)

    seen = []
    class _FakeClient:
        def __init__(self, name): self.name = name
        def dump(self):
            seen.append(self.name)
            return {"graph": self.name, "nodes": [], "edges": []}

    with patch.object(mgr, "_client", lambda g: _FakeClient(g)):
        mgr.dump()       # no scope kwarg

    assert set(seen) == {"member_m1", "team_t1"}


def test_dump_cypher_filters_by_group_id():
    """The dump query MUST filter by group_id; otherwise a Community-mode
    Neo4j deployment would leak cross-tenant data.
    """
    import inspect
    from plugins.memory.graphiti.client import GraphitiClient

    src = inspect.getsource(GraphitiClient._dump)
    assert "group_id" in src
    # Both the node and edge queries must apply the filter.
    assert src.count("WHERE") >= 2


def test_row_to_dict_handles_dict_and_mapping_and_unknown():
    from plugins.memory.graphiti.client import _row_to_dict

    assert _row_to_dict({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    class _Row:
        def __init__(self, data): self._d = data
        def keys(self): return self._d.keys()
        def __getitem__(self, k): return self._d[k]
    r = _Row({"x": "y"})
    assert _row_to_dict(r) == {"x": "y"}

    out = _row_to_dict(object())
    assert "_raw" in out


def test_to_jsonable_handles_datetime_and_set():
    from datetime import datetime, timezone
    from plugins.memory.graphiti.client import _to_jsonable

    dt = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    out = _to_jsonable(dt)
    assert isinstance(out, str)
    assert out.startswith("2025-01-02T03:04:05")

    out = _to_jsonable({1, 2, 3})
    assert isinstance(out, list)
    assert sorted(out) == [1, 2, 3]


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def test_cli_registers_timeline_and_dump():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args(
        ["timeline", "node-uuid-123", "--since", "2024-01-01"]
    )
    assert args.graphiti_command == "timeline"
    assert args.node_id == "node-uuid-123"
    assert args.since == "2024-01-01"

    args = parser.parse_args(["dump", "--scope", "team"])
    assert args.graphiti_command == "dump"
    assert args.scope == "team"


def test_cli_dump_default_scope_is_all():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args(["dump"])
    assert args.scope == "all"


def test_cli_dump_writes_jsonl_per_graph(tmp_path, monkeypatch):
    """End-to-end CLI behaviour for the dump command, with the manager
    mocked so we exercise the file-writing path without a live FalkorDB.
    """
    import argparse
    from plugins.memory.graphiti.cli import register_cli, _cmd_dump

    parser = argparse.ArgumentParser()
    register_cli(parser)
    out_dir = tmp_path / "dumps"
    args = parser.parse_args(["dump", "--scope", "all", "--out", str(out_dir)])

    class _FakeMgr:
        def dump(self, *, scope):
            assert scope == "all"
            return {
                "member_alice": {
                    "graph": "member_alice",
                    "nodes": [{"uuid": "n1", "name": "Alice"}],
                    "edges": [{"uuid": "e1", "fact": "Alice likes tea"}],
                },
                "team_eng": {"nodes": [], "edges": [], "error": "boom"},
            }
        def shutdown(self): pass

    monkeypatch.setattr(
        "plugins.memory.graphiti.cli._build_manager_for_cli",
        lambda: _FakeMgr(),
    )
    _cmd_dump(args)

    alice_path = out_dir / "member_alice.jsonl"
    assert alice_path.exists()
    lines = alice_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3            # header + 1 node + 1 edge
    import json as _json
    header = _json.loads(lines[0])
    assert header == {
        "kind": "header",
        "graph": "member_alice",
        "node_count": 1,
        "edge_count": 1,
        "format_version": 1,
    }
    node = _json.loads(lines[1])
    assert node["kind"] == "node" and node["uuid"] == "n1"
    edge = _json.loads(lines[2])
    assert edge["kind"] == "edge" and edge["fact"] == "Alice likes tea"

    # team_eng failed; no file should be written for it.
    assert not (out_dir / "team_eng.jsonl").exists()


# ---------------------------------------------------------------------------
# Query-driver caching (avoids stray bg task per call)
# ---------------------------------------------------------------------------

def test_client_caches_query_driver_across_calls():
    """The same driver instance should be reused across ping/dump/timeline."""
    import asyncio
    from plugins.memory.graphiti.client import GraphitiClient

    c = GraphitiClient(
        graph_name="g", falkordb_host="x", falkordb_port=0,
    )
    captured = []

    def fake_build(**kwargs):
        captured.append(kwargs)
        class _D: pass
        return _D()

    import plugins.memory.graphiti.client as cmod
    orig = cmod._build_driver
    cmod._build_driver = fake_build
    try:
        loop = asyncio.new_event_loop()
        try:
            d1 = loop.run_until_complete(c._driver_for_queries())
            d2 = loop.run_until_complete(c._driver_for_queries())
            d3 = loop.run_until_complete(c._driver_for_queries())
        finally:
            loop.close()
    finally:
        cmod._build_driver = orig

    assert d1 is d2 is d3
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# get_node_timeline now includes observed_at
# ---------------------------------------------------------------------------

def test_get_node_timeline_cypher_emits_created_at():
    """The cypher query must select created_at so observed_at can be
    populated downstream.
    """
    import inspect
    from plugins.memory.graphiti.client import GraphitiClient

    src = inspect.getsource(GraphitiClient._get_node_timeline)
    assert "created_at" in src
    assert "observed_at" in src
