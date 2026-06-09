"""Credential pool persists auth.json outside the main mutex."""

from __future__ import annotations

import json
import threading
import time

import pytest


def _write_auth_store(tmp_path, payload: dict) -> None:
    intellect_home = tmp_path / "intellect"
    intellect_home.mkdir(parents=True, exist_ok=True)
    (intellect_home / "auth.json").write_text(json.dumps(payload, indent=2))


def test_acquire_lease_not_blocked_by_slow_persist(tmp_path, monkeypatch):
    """Disk writes must not run while ``_lock`` is held."""
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / "intellect"))
    _write_auth_store(
        tmp_path,
        {
            "version": 1,
            "credential_pool": {
                "anthropic": [
                    {
                        "id": "cred-1",
                        "label": "primary",
                        "auth_type": "api_key",
                        "priority": 0,
                        "source": "manual",
                        "access_token": "sk-test-1",
                    },
                    {
                        "id": "cred-2",
                        "label": "secondary",
                        "auth_type": "api_key",
                        "priority": 1,
                        "source": "manual",
                        "access_token": "sk-test-2",
                    },
                ]
            },
        },
    )

    from agent.credential_pool import load_pool

    pool = load_pool("anthropic")
    persist_started = threading.Event()
    persist_done = threading.Event()

    def slow_write(provider, entries):
        persist_started.set()
        time.sleep(0.4)
        from intellect_cli.auth import write_credential_pool as real_write

        real_write(provider, entries)
        persist_done.set()

    monkeypatch.setattr("agent.credential_pool.write_credential_pool", slow_write)

    results: list[float] = []

    def worker():
        start = time.monotonic()
        pool.acquire_lease()
        results.append(time.monotonic() - start)

    pool.mark_exhausted_and_rotate(status_code=402, api_key_hint="sk-test-1")
    persist_started.wait(timeout=2.0)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert all(duration < 0.15 for duration in results), results
    persist_done.wait(timeout=3.0)
