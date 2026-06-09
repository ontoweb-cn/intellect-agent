"""Phase 5 tests for the graphiti memory plugin.

Covers:
  5.1  ontology.yaml parsing → Pydantic models → Graphiti.add_episode kwargs
  5.4  Neo4j backend selection (driver build + manager plumbing)
  5.5  community rebuild scope routing + CLI

Live graphiti-core round-trips stay in tests/integration/.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 5.1 — Ontology
# ---------------------------------------------------------------------------

def _write_ontology(home: Path, body: str) -> Path:
    (home / "graphiti").mkdir(parents=True, exist_ok=True)
    p = home / "graphiti" / "ontology.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_ontology_returns_empty_when_file_missing(tmp_path):
    from plugins.memory.graphiti.ontology import load_ontology

    ontology = load_ontology(str(tmp_path))
    assert ontology.is_empty()
    assert ontology.as_add_episode_kwargs() == {}


def test_load_ontology_parses_entities_and_edges(tmp_path):
    from plugins.memory.graphiti.ontology import load_ontology

    _write_ontology(
        tmp_path,
        """
        entities:
          Person:
            description: "A human."
            properties:
              name: {type: str, required: true, description: Full name}
              email: {type: str}
          Project:
            properties:
              name: {type: str, required: true}
              status: {type: str}
        edges:
          WORKS_ON:
            description: "Person contributes to a Project."
            properties:
              role: {type: str}
        edge_map:
          "[Person, Project]": [WORKS_ON]
        """,
    )

    ont = load_ontology(str(tmp_path))
    assert not ont.is_empty()
    assert set(ont.entities) == {"Person", "Project"}
    assert set(ont.edges) == {"WORKS_ON"}
    assert ont.edge_type_map == {("Person", "Project"): ["WORKS_ON"]}

    # Pydantic class checks
    Person = ont.entities["Person"]
    inst = Person(name="Alice", email=None)
    assert inst.name == "Alice"
    # email default = None (optional)
    assert inst.email is None
    # required field enforcement
    with pytest.raises(Exception):
        Person()  # missing required 'name'


def test_ontology_kwargs_shape_matches_graphiti():
    """Output keys must match graphiti-core's add_episode kwargs."""
    from plugins.memory.graphiti.ontology import Ontology

    # Build a minimal ontology by hand to avoid file I/O.
    from pydantic import BaseModel
    class Foo(BaseModel):
        pass
    class Bar(BaseModel):
        pass
    o = Ontology(
        entities={"Foo": Foo},
        edges={"Bar": Bar},
        edge_type_map={("Foo", "Foo"): ["Bar"]},
    )
    kwargs = o.as_add_episode_kwargs()
    assert set(kwargs) == {"entity_types", "edge_types", "edge_type_map"}
    assert kwargs["entity_types"] == {"Foo": Foo}
    assert kwargs["edge_types"] == {"Bar": Bar}
    assert kwargs["edge_type_map"] == {("Foo", "Foo"): ["Bar"]}


def test_ontology_rejects_unknown_property_type(tmp_path, caplog):
    from plugins.memory.graphiti.ontology import load_ontology

    _write_ontology(
        tmp_path,
        """
        entities:
          Person:
            properties:
              age: {type: bigint}        # not in _TYPE_MAP
        """,
    )
    import logging
    with caplog.at_level(logging.WARNING):
        ont = load_ontology(str(tmp_path))
    assert ont.is_empty()
    assert any("bigint" in r.message or "unknown type" in r.message
               for r in caplog.records)


def test_ontology_rejects_edge_map_with_unknown_entity(tmp_path, caplog):
    from plugins.memory.graphiti.ontology import load_ontology

    _write_ontology(
        tmp_path,
        """
        entities:
          Person:
            properties:
              name: {type: str, required: true}
        edges:
          KNOWS:
            properties: {}
        edge_map:
          "[Person, Ghost]": [KNOWS]
        """,
    )
    import logging
    with caplog.at_level(logging.WARNING):
        ont = load_ontology(str(tmp_path))
    assert ont.is_empty()
    assert any("Ghost" in r.message for r in caplog.records)


def test_ontology_rejects_edge_map_with_unknown_edge(tmp_path, caplog):
    from plugins.memory.graphiti.ontology import load_ontology

    _write_ontology(
        tmp_path,
        """
        entities:
          A: {properties: {}}
          B: {properties: {}}
        edges:
          KNOWS: {properties: {}}
        edge_map:
          "[A, B]": [BOGUS]
        """,
    )
    import logging
    with caplog.at_level(logging.WARNING):
        ont = load_ontology(str(tmp_path))
    assert ont.is_empty()
    assert any("BOGUS" in r.message for r in caplog.records)


def test_ontology_rejects_invalid_yaml(tmp_path, caplog):
    from plugins.memory.graphiti.ontology import load_ontology

    p = tmp_path / "graphiti"
    p.mkdir()
    (p / "ontology.yaml").write_text(":::: this is not yaml ::::", encoding="utf-8")
    import logging
    with caplog.at_level(logging.WARNING):
        ont = load_ontology(str(tmp_path))
    assert ont.is_empty()


def test_provider_loads_ontology_on_initialize(tmp_path):
    """GraphitiMemoryProvider.initialize wires ontology → manager."""
    from plugins.memory.graphiti import GraphitiMemoryProvider
    from plugins.memory.graphiti import client as client_mod

    _write_ontology(
        tmp_path,
        """
        entities:
          Person:
            properties:
              name: {type: str, required: true}
        """,
    )

    p = GraphitiMemoryProvider()
    with patch.object(
        GraphitiMemoryProvider, "is_available", return_value=True
    ), patch.object(client_mod, "GraphitiClientManager") as MgrCls:
        p.initialize(
            session_id="s",
            intellect_home=str(tmp_path),
            member_id="m1",
            config={"memory": {"provider": "graphiti"}},
        )
    # The manager must have been built with the parsed ontology kwargs.
    assert MgrCls.call_count == 1
    _, kwargs = MgrCls.call_args
    assert "ontology_kwargs" in kwargs
    assert "entity_types" in kwargs["ontology_kwargs"]
    assert "Person" in kwargs["ontology_kwargs"]["entity_types"]


# ---------------------------------------------------------------------------
# 5.4 — Neo4j backend
# ---------------------------------------------------------------------------

def test_build_driver_default_is_falkordb():
    from plugins.memory.graphiti.client import _build_driver

    with patch("graphiti_core.driver.falkordb_driver.FalkorDriver") as FalkorDriver:
        _build_driver(
            backend="falkordb",
            graph_name="member_x",
            host="localhost",
            port=6380,
            password="",
        )
        assert FalkorDriver.called
        call = FalkorDriver.call_args
        assert call.kwargs["database"] == "member_x"
        assert call.kwargs["host"] == "localhost"
        assert call.kwargs["port"] == 6380


def test_build_driver_neo4j_multi_db_uses_graph_name_as_database():
    """Neo4j Enterprise: one database per tenant graph."""
    from plugins.memory.graphiti.client import _build_driver

    with patch("graphiti_core.driver.neo4j_driver.Neo4jDriver") as Neo4jDriver:
        _build_driver(
            backend="neo4j",
            graph_name="member_alice",
            host="ignored-when-uri-set",
            port=0,
            password="secret",
            uri="bolt://neo.example.com:7687",
            user="neo4j",
            multi_db=True,
        )
        assert Neo4jDriver.called
        call = Neo4jDriver.call_args
        assert call.kwargs["uri"] == "bolt://neo.example.com:7687"
        assert call.kwargs["user"] == "neo4j"
        assert call.kwargs["password"] == "secret"
        assert call.kwargs["database"] == "member_alice"


def test_build_driver_neo4j_single_db_falls_back_to_neo4j_database():
    """Neo4j Community: only the 'neo4j' database exists.

    Tenant isolation in this mode relies on the group_id we already
    write to every episode (defense-in-depth from Phase 1).
    """
    from plugins.memory.graphiti.client import _build_driver

    with patch("graphiti_core.driver.neo4j_driver.Neo4jDriver") as Neo4jDriver:
        _build_driver(
            backend="neo4j",
            graph_name="member_bob",
            host="ignored",
            port=0,
            password="",
            uri="bolt://neo.example.com:7687",
            user="neo4j",
            multi_db=False,
        )
        call = Neo4jDriver.call_args
        assert call.kwargs["database"] == "neo4j"   # NOT member_bob


def test_build_driver_neo4j_falls_back_uri_from_host_port():
    from plugins.memory.graphiti.client import _build_driver

    with patch("graphiti_core.driver.neo4j_driver.Neo4jDriver") as Neo4jDriver:
        _build_driver(
            backend="neo4j",
            graph_name="m",
            host="neo.local",
            port=7688,
            uri=None,
        )
        call = Neo4jDriver.call_args
        assert call.kwargs["uri"] == "bolt://neo.local:7688"


def test_build_driver_rejects_unknown_backend():
    from plugins.memory.graphiti.client import _build_driver

    with pytest.raises(ValueError, match="unknown graphiti backend"):
        _build_driver(
            backend="cassandra",
            graph_name="m",
            host="x",
            port=0,
        )


def test_manager_plumbs_backend_choice_into_client():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager(
        {
            "backend": "neo4j",
            "neo4j_uri": "bolt://n.example.com:7687",
            "neo4j_user": "neo4j",
            "falkordb_password": "secret",
            "neo4j_multi_db": True,
        }
    )
    mgr.bind_scope(member_id="alice", team_id=None, project_id=None)
    client = mgr._client("member_alice")
    assert client._backend == "neo4j"
    assert client._uri == "bolt://n.example.com:7687"
    assert client._user == "neo4j"
    assert client._multi_db is True


def test_config_schema_includes_backend_and_neo4j_fields():
    from plugins.memory.graphiti.config import get_config_schema

    fields = {f["key"]: f for f in get_config_schema()}
    assert "backend" in fields
    assert fields["backend"]["choices"] == ["falkordb", "neo4j"]
    assert "neo4j_uri" in fields
    assert "neo4j_user" in fields
    assert "neo4j_multi_db" in fields
    # 'when' gating keeps the wizard tidy
    assert fields["neo4j_uri"].get("when") == {"backend": "neo4j"}
    assert fields["falkordb_host"].get("when") == {"backend": "falkordb"}


# ---------------------------------------------------------------------------
# 5.5 — Community rebuild
# ---------------------------------------------------------------------------

def test_rebuild_communities_dispatches_to_each_scope_graph():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "x", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id="t1", project_id="p1")

    called = []
    class _FakeClient:
        def __init__(self, name):
            self.name = name
        def build_communities(self):
            called.append(self.name)
            return {"built": True, "graph": self.name, "community_count": 2, "community_edge_count": 1}

    with patch.object(mgr, "_client", lambda g: _FakeClient(g)):
        out = mgr.rebuild_communities(scope="all")

    assert set(called) == {"member_m1", "team_t1", "project_p1"}
    assert all(out[g]["built"] for g in called)
    assert all(out[g]["community_count"] == 2 for g in called)


def test_rebuild_communities_scope_team_skips_member_and_project():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "x", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id="t1", project_id="p1")

    called = []
    class _FakeClient:
        def __init__(self, name): self.name = name
        def build_communities(self):
            called.append(self.name)
            return {"built": True, "graph": self.name}

    with patch.object(mgr, "_client", lambda g: _FakeClient(g)):
        out = mgr.rebuild_communities(scope="team")

    assert called == ["team_t1"]
    assert "member_m1" not in out
    assert "project_p1" not in out


def test_rebuild_communities_records_per_graph_errors():
    """One graph failing must not prevent the others from running."""
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "x", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id="t1", project_id=None)

    class _FakeClient:
        def __init__(self, name): self.name = name
        def build_communities(self):
            if self.name == "team_t1":
                raise RuntimeError("FalkorDB busy")
            return {"built": True, "graph": self.name}

    with patch.object(mgr, "_client", lambda g: _FakeClient(g)):
        out = mgr.rebuild_communities(scope="all")

    assert out["member_m1"]["built"] is True
    assert out["team_t1"]["built"] is False
    assert "FalkorDB busy" in out["team_t1"]["error"]


def test_cli_registers_rebuild_communities_subcommand():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args(["rebuild-communities", "--scope", "team"])
    assert args.graphiti_command == "rebuild-communities"
    assert args.scope == "team"


def test_cli_rebuild_communities_default_scope_is_all():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args(["rebuild-communities"])
    assert args.scope == "all"
