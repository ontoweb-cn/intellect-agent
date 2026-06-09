"""OAuth Fernet key creation — concurrent-safe."""

from __future__ import annotations

import threading

import pytest


def test_concurrent_key_creation_produces_one_key(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    from agent.oauth.storage import _get_or_create_key

    keys: list[bytes] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait(timeout=5)
        keys.append(_get_or_create_key())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(keys) == 8
    assert len(set(keys)) == 1
    key_path = tmp_path / ".oauth-key"
    assert key_path.is_file()
    assert key_path.read_bytes() == keys[0]
    assert oct(key_path.stat().st_mode & 0o777) == oct(0o600)
