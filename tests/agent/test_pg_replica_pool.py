"""PGReplicaPool — lag exclusion and primary fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.storage.pg_replica_pool import PGReplicaPool


def test_get_read_engine_falls_back_to_primary_when_all_replicas_laggy():
    primary = MagicMock(name="primary_engine")
    pool = PGReplicaPool(
        primary_dsn="postgresql://user:pass@primary/db",
        pg_config={},
        replicas=["postgresql://user:pass@replica1/db"],
        strategy="random",
        max_lag_seconds=5.0,
    )
    replica_engine = MagicMock(name="replica_engine")

    with patch.object(pool, "_get_or_create_engine", return_value=replica_engine), patch.object(
        pool, "_replica_is_usable", return_value=False
    ):
        assert pool.get_read_engine(primary) is primary


def test_get_read_engine_returns_first_healthy_replica():
    primary = MagicMock(name="primary_engine")
    pool = PGReplicaPool(
        primary_dsn="postgresql://user:pass@primary/db",
        pg_config={},
        replicas=[
            "postgresql://user:pass@replica-a/db",
            "postgresql://user:pass@replica-b/db",
        ],
        strategy="round_robin",
        max_lag_seconds=5.0,
    )
    good = MagicMock(name="good_replica")
    bad = MagicMock(name="bad_replica")

    def fake_create(dsn):
        if "replica-a" in dsn:
            return bad
        return good

    def usable(engine):
        return engine is good

    with patch.object(pool, "_get_or_create_engine", side_effect=fake_create), patch.object(
        pool, "_replica_is_usable", side_effect=usable
    ):
        assert pool.get_read_engine(primary) is good


def test_replica_is_usable_rejects_high_lag():
    pool = PGReplicaPool(
        primary_dsn="postgresql://primary/db",
        pg_config={},
        replicas=["postgresql://replica/db"],
        max_lag_seconds=2.0,
    )
    engine = MagicMock()
    raw = MagicMock()
    engine.raw_connection.return_value = raw

    with patch(
        "agent.storage.pg_replica_pool._replica_is_healthy", return_value=True
    ), patch("agent.storage.pg_replica_pool._replica_lag_seconds", return_value=10.0):
        assert pool._replica_is_usable(engine) is False
    raw.close.assert_called_once()
