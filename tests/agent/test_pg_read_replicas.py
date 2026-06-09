"""PostgreSQL read-replica pool tests (P5 / W5)."""

from __future__ import annotations

from agent.storage.pg_replica_pool import PGReplicaPool, _build_replica_dsn


def test_build_replica_dsn_from_host_dict():
    pg = {"user": "intellect", "password": "pw", "port": 5432, "database": "intellect"}
    dsn = _build_replica_dsn(
        "postgresql://intellect:pw@primary:5432/intellect",
        {"host": "replica1.internal"},
        pg,
    )
    assert "replica1.internal" in dsn
    assert "intellect" in dsn


def test_replica_pool_round_robin():
    pool = PGReplicaPool(
        primary_dsn="postgresql://u:p@primary:5432/db",
        pg_config={"pool_size": 2},
        replicas=[{"host": "r1"}, {"host": "r2"}],
        strategy="round_robin",
    )
    assert pool.configured
    assert pool.pick_dsn() != pool.pick_dsn()
