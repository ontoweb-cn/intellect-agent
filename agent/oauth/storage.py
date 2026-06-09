"""Encrypted OAuth token storage.

Tokens are encrypted at rest using Fernet (symmetric AES-128-CBC + HMAC).
The encryption key is stored at ``{INTELLECT_HOME}/.oauth-key`` and
auto-generated on first use.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


def _get_or_create_key() -> bytes:
    """Return the Fernet key, creating it if it doesn't exist."""
    import time

    from intellect_constants import get_intellect_home

    key_path = get_intellect_home() / ".oauth-key"
    if key_path.exists():
        data = key_path.read_bytes()
        if data:
            return data

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = base64.urlsafe_b64encode(os.urandom(32))
    try:
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return key
    except FileExistsError:
        for _ in range(50):
            try:
                data = key_path.read_bytes()
            except OSError:
                data = b""
            if data:
                return data
            time.sleep(0.002)
        raise RuntimeError(f"OAuth key file exists but unreadable: {key_path}")


def encrypt_token(plaintext: str) -> str:
    """Encrypt an OAuth token. Returns base64 ciphertext."""
    from cryptography.fernet import Fernet

    f = Fernet(_get_or_create_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt an OAuth token. Returns plaintext."""
    from cryptography.fernet import Fernet

    f = Fernet(_get_or_create_key())
    return f.decrypt(ciphertext.encode()).decode()


def store_oauth_token(
    provider_id: str,
    access_token: str,
    db: Any,
    member_id: str | None = None,
    refresh_token: str | None = None,
    scope: str = "",
    expires_in: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Store an encrypted OAuth token in the database. Returns the token row id."""
    import time
    import uuid

    token_id = uuid.uuid4().hex[:12]
    now = time.time()
    meta_json = json.dumps(metadata if metadata is not None else {})

    def _insert(cursor):
        # SQLite UNIQUE treats each NULL member_id as distinct — delete singleton
        # model rows before insert so updates replace in place (PR-A8).
        if member_id is None:
            cursor.execute(
                "DELETE FROM oauth_tokens WHERE provider_id=? AND member_id IS NULL",
                (provider_id,),
            )
        else:
            cursor.execute(
                "DELETE FROM oauth_tokens WHERE provider_id=? AND member_id=?",
                (provider_id, member_id),
            )
        cursor.execute(
            "INSERT INTO oauth_tokens "
            "(id, provider_id, member_id, access_token_encrypted, refresh_token_encrypted, "
            "token_type, scope, expires_at, issued_at, last_used_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, 'bearer', ?, ?, ?, ?, ?)",
            (
                token_id,
                provider_id,
                member_id,
                encrypt_token(access_token),
                encrypt_token(refresh_token) if refresh_token else None,
                scope,
                now + expires_in if expires_in > 0 else None,
                now,
                now,
                meta_json,
            ),
        )

    db._execute_write(_insert)
    return token_id


def get_oauth_token(
    provider_id: str, db: Any, member_id: str | None = None
) -> dict | None:
    """Retrieve and decrypt an OAuth token. Returns dict with access_token, refresh_token, etc."""
    import time

    now = time.time()
    if member_id:
        row = db._conn.execute(
            "SELECT * FROM oauth_tokens WHERE provider_id=? AND member_id=? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (provider_id, member_id, now),
        ).fetchone()
    else:
        row = db._conn.execute(
            "SELECT * FROM oauth_tokens WHERE provider_id=? AND member_id IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY last_used_at DESC LIMIT 1",
            (provider_id, now),
        ).fetchone()
    if not row:
        return None

    result = dict(row)
    result["access_token"] = decrypt_token(result["access_token_encrypted"])
    if result.get("refresh_token_encrypted"):
        result["refresh_token"] = decrypt_token(result["refresh_token_encrypted"])
    try:
        result["metadata"] = json.loads(result.get("metadata") or "{}")
    except json.JSONDecodeError:
        result["metadata"] = {}
    return result
