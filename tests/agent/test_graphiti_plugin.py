"""Phase 1 tests for the graphiti memory plugin.

Scope: things testable WITHOUT graphiti-core / falkordb installed.
Live FalkorDB integration tests live in
``tests/integration/test_graphiti_docker.py`` (Phase 4).
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Provider discovery + schema
# ---------------------------------------------------------------------------

def test_plugin_discovered_in_picker():
    from plugins.memory import discover_memory_providers

    names = {n for (n, _desc, _avail) in discover_memory_providers()}
    assert "graphiti" in names


def test_plugin_loads_without_optional_deps():
    """Plugin module must import even when graphiti-core / falkordb absent."""
    from plugins.memory import load_memory_provider

    p = load_memory_provider("graphiti")
    assert p is not None
    assert p.name == "graphiti"
    # Without deps installed, is_available() must return False, not raise.
    _ = p.is_available()


def test_config_schema_fields_present():
    from plugins.memory.graphiti.config import get_config_schema

    keys = {f["key"] for f in get_config_schema()}
    assert {
        "falkordb_host",
        "falkordb_port",
        "falkordb_password",
        "embedding_provider",
    } <= keys


def test_tool_schemas_shape():
    from plugins.memory.graphiti.tools import ALL_SCHEMAS, tool_names

    names = tool_names()
    assert names == [
        "graphiti_add_episode",
        "graphiti_search_facts",
        "graphiti_search_nodes",
        "graphiti_get_node_timeline",
        "graphiti_delete_episode",
    ]
    for s in ALL_SCHEMAS:
        assert "name" in s and "description" in s and "parameters" in s
        assert s["parameters"]["type"] == "object"


def test_delete_tool_marked_sensitive():
    from plugins.memory.graphiti.tools import SENSITIVITY

    assert SENSITIVITY["graphiti_delete_episode"] == "sensitive"
    assert SENSITIVITY["graphiti_search_facts"] == "routine"


# ---------------------------------------------------------------------------
# Scope routing
# ---------------------------------------------------------------------------

def test_scope_resolution_member_only():
    from plugins.memory.graphiti.client import _Scope

    s = _Scope(member_id="mem_abc")
    assert s.write_graph() == "member_mem_abc"
    assert s.graphs_for("auto") == ["member_mem_abc"]
    assert s.graphs_for("team") == []
    assert s.graphs_for("all") == ["member_mem_abc"]


def test_scope_resolution_member_plus_team():
    from plugins.memory.graphiti.client import _Scope

    s = _Scope(member_id="m1", team_id="t1")
    assert s.write_graph() == "member_m1"          # writes always to member
    assert s.graphs_for("auto") == ["member_m1", "team_t1"]
    assert s.graphs_for("team") == ["team_t1"]
    assert s.graphs_for("all") == ["member_m1", "team_t1"]


def test_scope_resolution_full():
    from plugins.memory.graphiti.client import _Scope

    s = _Scope(member_id="m1", team_id="t1", project_id="p1")
    assert s.graphs_for("all") == ["member_m1", "team_t1", "project_p1"]
    assert s.graphs_for("project") == ["project_p1"]


def test_scope_resolution_anonymous():
    from plugins.memory.graphiti.client import _Scope

    s = _Scope()
    assert s.write_graph() == "global"
    assert s.graphs_for("member") == ["global"]


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_opens_after_threshold():
    from plugins.memory.graphiti.client import CircuitBreaker

    b = CircuitBreaker(threshold=3, cooldown=99.0)
    assert b.allow()
    b.record_failure()
    b.record_failure()
    assert b.allow()
    b.record_failure()
    assert not b.allow()
    snap = b.snapshot()
    assert snap["open"] is True
    assert snap["failures"] == 3


def test_circuit_breaker_resets_on_success():
    from plugins.memory.graphiti.client import CircuitBreaker

    b = CircuitBreaker(threshold=2, cooldown=99.0)
    b.record_failure()
    b.record_success()
    assert b.snapshot()["failures"] == 0
    b.record_failure()
    assert b.allow()


# ---------------------------------------------------------------------------
# Provider lifecycle (without deps — exercises fail-safe paths)
# ---------------------------------------------------------------------------

def test_provider_initialize_without_deps_is_inert():
    """When graphiti-core is not available, initialize stays inert.

    Forces is_available=False to simulate the missing-deps state, so the
    test result is the same whether the dev has the [graphiti] extra
    installed or not.
    """
    from unittest.mock import patch
    from plugins.memory.graphiti import GraphitiMemoryProvider

    p = GraphitiMemoryProvider()
    with patch.object(
        GraphitiMemoryProvider, "is_available", return_value=False
    ):
        p.initialize(
            session_id="s1",
            platform="cli",
            intellect_home="/tmp",
            member_id="m1",
            team_id="t1",
            config={"memory": {"provider": "graphiti"}},
        )
        assert p.system_prompt_block() == ""
        assert p.get_tool_schemas() == []
        assert p.prefetch("hello") == ""
        p.sync_turn(user_content="u", assistant_content="a")  # no-op
        p.shutdown()


def test_tool_dispatch_inert_when_uninitialized():
    from plugins.memory.graphiti import GraphitiMemoryProvider

    p = GraphitiMemoryProvider()
    out = json.loads(p.handle_tool_call("graphiti_search_facts", {"query": "x"}))
    assert out["ok"] is False
    assert "not initialized" in out["error"]


def test_message_serialization_handles_multimodal():
    from plugins.memory.graphiti import GraphitiMemoryProvider

    msgs = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "part1"},
                {"type": "image", "url": "..."},
                {"type": "text", "text": "part2"},
            ],
        },
        {"role": "tool", "content": ""},          # skipped
        {"role": "system", "content": "sys"},
    ]
    out = GraphitiMemoryProvider._serialize_messages(msgs)
    assert "user: hello" in out
    assert "assistant: part1\npart2" in out
    assert "tool" not in out                       # empty content skipped
    assert "system: sys" in out


def test_delete_requires_reason():
    """delete_episode without a `reason` is rejected before any I/O."""
    from plugins.memory.graphiti import GraphitiMemoryProvider

    p = GraphitiMemoryProvider()
    # Fake manager so handle_tool_call gets past the "not initialized" gate.
    class _FakeMgr:
        def delete_episode(self, *_a, **_kw):
            raise AssertionError("delete should be blocked before I/O")

    p._mgr = _FakeMgr()
    p._member_id = None
    out = json.loads(
        p.handle_tool_call(
            "graphiti_delete_episode", {"episode_id": "e1", "reason": ""}
        )
    )
    assert out["ok"] is False
    assert "reason" in out["error"]


# ---------------------------------------------------------------------------
# Plugin manifest
# ---------------------------------------------------------------------------

def test_plugin_yaml_declares_phase1_hooks():
    import yaml
    from pathlib import Path

    manifest = yaml.safe_load(
        Path(__file__).parents[2].joinpath(
            "plugins/memory/graphiti/plugin.yaml"
        ).read_text()
    )
    assert manifest["name"] == "graphiti"
    assert "on_session_end" in manifest["hooks"]
    assert "on_pre_compress" in manifest["hooks"]
    assert "on_session_switch" in manifest["hooks"]
