"""Tests for Fernet encryption at rest in ``agent.secret_store``.

Regression coverage for a padding bug: ``_encrypt_bytes`` used to append a
fixed ``"="`` to the (unpadded) base64url token returned by the Rust helper.
Base64 needs 0, 1 or 2 pad characters depending on ``len % 4``, so the
unconditional append produced a token of length 1 mod 4 — undecodable — for
every token whose length was already a multiple of 4.  That corrupted roughly
a quarter of all writes in a data-dependent way, and presented as
``ValueError: invalid token`` from ``read_json``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent.secret_store import (
    _ENCRYPTION_HEADER,
    SecretStore,
    _decrypt_bytes,
    _encrypt_bytes,
    _get_or_create_key,
    rust_fernet_encrypt,
)


@pytest.fixture()
def store(tmp_path) -> SecretStore:
    return SecretStore(tmp_path)


@pytest.mark.parametrize(
    "size",
    # Includes the sizes that the fixed-width "=" append corrupted: those where
    # the ciphertext token length happened to be a multiple of 4.
    [1, 20, 90, 200, 512, 1200, 1700, 3000, 5000],
)
def test_write_read_json_round_trip(store, size):
    """Every payload size must survive an encrypt/decrypt round trip."""
    payload = {"k": "x" * size, "nested": {"a": [1, 2, 3]}}
    store.write_json("auth.json", payload)
    assert store.read_json("auth.json") == payload


def test_round_trip_across_many_sizes(store, tmp_path):
    """Sweep lengths to catch any residual mod-4 padding dependence."""
    for size in range(1, 600, 3):
        s = SecretStore(tmp_path / f"h{size}")
        payload = {"k": "y" * size}
        s.write_json("auth.json", payload)
        assert s.read_json("auth.json") == payload


def test_padding_matches_token_length():
    """The stored padding must make the token length a multiple of 4."""
    with tempfile.TemporaryDirectory() as td:
        key = _get_or_create_key(Path(td))
        for size in range(1, 400, 7):
            plaintext = json.dumps({"k": "z" * size}).encode()
            # Fernet randomizes the IV, so one call is enough to check the
            # stored length; encrypting twice would compare unrelated tokens.
            stored = _encrypt_bytes(plaintext, key)
            assert stored.startswith(b"gAAAAA"), "expected a Fernet token"
            assert len(stored) % 4 == 0, f"size={size} produced {len(stored) % 4} mod 4"
            # And the padded form must actually decrypt back.
            assert _decrypt_bytes(_ENCRYPTION_HEADER + stored, key) == plaintext.decode()


def test_ciphertext_has_encryption_header(store):
    """Written files carry the version header so reads know to decrypt."""
    store.write_json("auth.json", {"a": 1})
    raw = (store._home / "auth.json").read_bytes()
    assert raw.startswith(_ENCRYPTION_HEADER)


def test_plaintext_is_not_recoverable_from_disk(store):
    """The whole point: secrets must not be readable in the raw file."""
    secret = "MANUAL_SECRET_STAYS_PERSISTABLE"
    store.write_json("auth.json", {"token": secret})
    assert secret not in (store._home / "auth.json").read_bytes().decode("utf-8")


def test_legacy_unpadded_plaintext_still_reads(store):
    """Files written before encryption existed stay readable (migration path)."""
    (store._home / "auth.json").write_text(json.dumps({"legacy": True}), encoding="utf-8")
    assert store.read_json("auth.json") == {"legacy": True}


def test_recovers_file_written_with_spurious_padding(tmp_path):
    """A file left by the buggy writer must still be readable.

    The old code appended "=" even when the token already had the right length,
    so the on-disk token was 1 mod 4.  ``_decrypt_bytes`` strips the stray
    character rather than forcing affected users to re-authenticate.
    """
    key = _get_or_create_key(tmp_path)
    plaintext = json.dumps({"recovered": True})
    token = rust_fernet_encrypt(key.decode(), plaintext)
    (tmp_path / "auth.json").write_bytes(_ENCRYPTION_HEADER + (token + "=").encode())

    store = SecretStore(tmp_path)
    assert store.read_json("auth.json") == {"recovered": True}
