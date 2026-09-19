"""Shared test helpers for assertion on credential files.

``auth.json`` is Fernet-encrypted at rest (``agent.secret_store``), so a test
that does ``json.loads(path.read_text())`` gets ciphertext and raises
``JSONDecodeError``.  Use these helpers instead — they go through the same
decrypting path production uses, so the assertions keep testing the real
payload rather than a raw-bytes proxy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ENCRYPTION_HEADER = b"INTELLECT_FERNET_V1\n"


def read_auth_json(path: Path) -> Any:
    """Read ``auth.json``, decrypting it if it is encrypted.

    Accepts the file path itself (not the parent directory).

    A file carrying the encryption header is *always* decrypted — if that
    fails, the error propagates, because the alternative (falling back to a
    plaintext parse) would surface a meaningless ``JSONDecodeError`` against
    ciphertext and hide the real cause.  Only header-less files are treated as
    legacy plaintext, so tests that deliberately seed unencrypted content
    still round-trip.
    """
    path = Path(path)

    from agent.secret_store import SecretStore

    store = SecretStore(home=str(path.parent))
    if not path.exists():
        return store.read_json(path.name)

    if path.read_bytes().startswith(_ENCRYPTION_HEADER):
        return store.read_json(path.name)

    return json.loads(path.read_text(encoding="utf-8"))


def read_auth_json_str(path: Path) -> str:
    """Return the decrypted ``auth.json`` payload serialized as a string.

    Useful for the "this secret must NOT appear" assertions, which are vacuous
    against the ciphertext at rest.
    """
    return json.dumps(read_auth_json(path))
