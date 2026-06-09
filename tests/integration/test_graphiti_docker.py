"""Live FalkorDB integration tests for the graphiti memory plugin.

Skipped unless ALL of:
  - graphiti-core + falkordb importable (intellect-agent[graphiti] installed)
  - INTELLECT_TEST_GRAPHITI_DOCKER=1 in the environment
  - FalkorDB reachable at GRAPHITI_FALKORDB_HOST:PORT
    (defaults to localhost:6380 to match docker-compose.three-container.yml)

Local run:
    docker run -d --rm --name falkordb-test -p 6380:6379 falkordb/falkordb:latest
    INTELLECT_TEST_GRAPHITI_DOCKER=1 \\
        pytest tests/integration/test_graphiti_docker.py -v -m integration

CI: spin a falkordb service container; this file picks it up automatically.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.integration


def _require_graphiti() -> None:
    if os.environ.get("INTELLECT_TEST_GRAPHITI_DOCKER") != "1":
        pytest.skip("INTELLECT_TEST_GRAPHITI_DOCKER != 1")
    try:
        import graphiti_core  # noqa: F401
        import falkordb  # noqa: F401
    except ImportError:
        pytest.skip("graphiti-core / falkordb not installed (graphiti extra)")


def _config() -> dict:
    return {
        "falkordb_host": os.environ.get("GRAPHITI_FALKORDB_HOST", "localhost"),
        "falkordb_port": int(os.environ.get("GRAPHITI_FALKORDB_PORT", "6380")),
        "falkordb_password": os.environ.get("GRAPHITI_FALKORDB_PASSWORD", ""),
        "embedding_provider": "local",
        "embedding_model": "bge-m3",
        "auto_ingest": True,
    }


_REAL_KEY_BEFORE_HERMETIC = os.environ.get("OPENAI_API_KEY", "")


@pytest.fixture
def manager(monkeypatch):
    """Build a real GraphitiClientManager bound to an ephemeral member.

    Uses a unique member id per test so parallel runs don't collide.
    Tears down the loop thread + clients at the end.

    Also re-installs OPENAI_API_KEY (cleared by tests/conftest.py's
    _hermetic_environment autouse fixture) so graphiti-core's default
    LLM/embedder clients can construct.  For ping() the placeholder is
    enough; real round-trips gate on GRAPHITI_TEST_REAL_LLM=1.
    """
    _require_graphiti()
    from plugins.memory.graphiti.client import GraphitiClientManager

    # Captured at module import time, before conftest scrubbed it.
    key = _REAL_KEY_BEFORE_HERMETIC or "sk-test-placeholder-not-real"
    monkeypatch.setenv("OPENAI_API_KEY", key)

    mgr = GraphitiClientManager(_config())
    member_id = f"test_{uuid.uuid4().hex[:8]}"
    mgr.bind_scope(member_id=member_id, team_id=None, project_id=None)
    yield mgr
    try:
        mgr.shutdown()
    except Exception:
        pass


def _require_real_llm() -> None:
    if os.environ.get("GRAPHITI_TEST_REAL_LLM") != "1":
        pytest.skip(
            "GRAPHITI_TEST_REAL_LLM != 1 (set + provide a real OPENAI_API_KEY "
            "to exercise add_episode / search round-trips)"
        )


def test_falkordb_reachable(manager):
    """ping() returns True for the member's write graph."""
    pings = manager.ping()
    assert pings, "no graphs to ping"
    assert all(pings.values()), f"unreachable graphs: {pings}"


def test_add_episode_round_trip(manager):
    """Write an episode, then read it back via search_facts.

    Requires GRAPHITI_TEST_REAL_LLM=1 + a real OPENAI_API_KEY because
    add_episode runs LLM extraction and search uses embeddings.
    """
    _require_real_llm()
    result = manager.add_episode(
        content="Alice prefers tea over coffee in the mornings.",
        source_description="integration-test",
    )
    assert result.get("episode_id"), result
    # Graphiti extraction is async-internal; give it a beat to settle.
    time.sleep(2.0)

    facts = manager.search_facts("tea preference", max_results=5)
    # We don't assert the exact wording — Graphiti extracts predicates —
    # but at least one fact must come back referencing the right entity.
    matches = [f for f in facts if "tea" in f.get("fact", "").lower()]
    assert matches, f"no tea-related facts found; got {facts!r}"


def test_two_members_are_isolated(monkeypatch):
    """Writes by one member are NOT visible in another member's auto scope.

    Requires GRAPHITI_TEST_REAL_LLM=1 (uses add_episode + search).
    """
    _require_graphiti()
    _require_real_llm()
    monkeypatch.setenv(
        "OPENAI_API_KEY",
        _REAL_KEY_BEFORE_HERMETIC or "sk-test-placeholder-not-real",
    )
    from plugins.memory.graphiti.client import GraphitiClientManager

    cfg = _config()
    alice_id = f"alice_{uuid.uuid4().hex[:8]}"
    bob_id = f"bob_{uuid.uuid4().hex[:8]}"

    mgr_a = GraphitiClientManager(cfg)
    mgr_a.bind_scope(member_id=alice_id, team_id=None, project_id=None)
    mgr_b = GraphitiClientManager(cfg)
    mgr_b.bind_scope(member_id=bob_id, team_id=None, project_id=None)

    try:
        secret = f"Alice's secret code is XYZZY-{uuid.uuid4().hex[:6]}."
        mgr_a.add_episode(content=secret, source_description="integration-test")
        time.sleep(2.0)

        # Alice can see her own fact.
        alice_facts = mgr_a.search_facts("secret code XYZZY", max_results=5)
        assert any("XYZZY" in f.get("fact", "") for f in alice_facts), (
            f"alice can't see her own write: {alice_facts!r}"
        )

        # Bob's auto scope must NOT see it.
        bob_facts = mgr_b.search_facts("secret code XYZZY", max_results=5)
        assert not any("XYZZY" in f.get("fact", "") for f in bob_facts), (
            f"cross-tenant leak: bob saw alice's data: {bob_facts!r}"
        )
    finally:
        for mgr in (mgr_a, mgr_b):
            try:
                mgr.shutdown()
            except Exception:
                pass


def test_circuit_breaker_opens_on_bad_host(monkeypatch):
    """A bad host trips the breaker; subsequent calls fail fast.

    Uses ping() so it doesn't need OPENAI_API_KEY at all.
    """
    _require_graphiti()
    monkeypatch.setenv(
        "OPENAI_API_KEY",
        _REAL_KEY_BEFORE_HERMETIC or "sk-test-placeholder-not-real",
    )
    from plugins.memory.graphiti.client import GraphitiClientManager

    bad_cfg = {
        "falkordb_host": "not-a-real-host-12345.invalid",
        "falkordb_port": 6380,
        "falkordb_password": "",
        "embedding_provider": "local",
        "embedding_model": "bge-m3",
    }
    mgr = GraphitiClientManager(bad_cfg)
    mgr.bind_scope(member_id="x", team_id=None, project_id=None)
    try:
        for _ in range(5):
            try:
                mgr.ping()
            except Exception:
                pass
        # After enough failures the breaker should be open.
        stats = mgr.stats()
        any_open = any(
            b.get("open")
            for b in stats.get("circuit_breakers", {}).values()
        )
        assert any_open, f"breaker did not open: {stats}"
    finally:
        try:
            mgr.shutdown()
        except Exception:
            pass
