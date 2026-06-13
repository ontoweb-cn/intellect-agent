"""Encrypted file-based credential storage.

Provides a simple Fernet encryption layer for JSON and plaintext credential
files under ``{INTELLECT_HOME}/``.  Reuses the existing ``.oauth-key``
encryption key (auto-generated on first use by ``agent.oauth.storage``).

All write operations produce files with mode ``0o600`` and use atomic
write-via-tempfile patterns to avoid corrupting the store on crash.

Usage::

    from agent.secret_store import SecretStore
    store = SecretStore()

    # Encrypted JSON
    store.write_json("auth.json", {"providers": {...}})
    data = store.read_json("auth.json")

    # Encrypted plaintext (.env style)
    store.write_text(".env", "API_KEY=sk-...\\n")
    text = store.read_text(".env")

    # One-shot migration of an existing plaintext file
    store.migrate_plaintext_to_encrypted(".env")
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from intellect_constants import get_intellect_home

# ── Stage 5b: Rust Fernet ──────────────────────────────────────────────────
try:
    from intellect_community_core import fernet_encrypt as _rust_fernet_encrypt  # type: ignore[import-not-found]
    from intellect_community_core import fernet_decrypt as _rust_fernet_decrypt
    _HAS_RUST_FERNET = True
except (ImportError, AttributeError):
    _HAS_RUST_FERNET = False

logger = logging.getLogger(__name__)

# Sentinel prefix written at the head of every encrypted file so we can
# distinguish plaintext from ciphertext without a separate metadata file.
_ENCRYPTION_HEADER = b"INTELLECT_FERNET_V1\n"


class SecretStore:
    """Read/write credential files with Fernet encryption at rest.

    The encryption key is ``{INTELLECT_HOME}/.oauth-key`` — shared with
    ``agent.oauth.storage`` so that DB tokens and file tokens use the same
    master secret.
    """

    def __init__(self, home: str | Path | None = None) -> None:
        if home is None:
            home = get_intellect_home()
        self._home = Path(home)
        self._home.mkdir(parents=True, exist_ok=True)
        self._key: bytes | None = None

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    @property
    def _fernet_key(self) -> bytes:
        if self._key is None:
            self._key = _get_or_create_key(self._home)
        return self._key

    def rotate_key(self) -> None:
        """Generate a new encryption key and re-encrypt all known files.

        WARNING: This invalidates the old key immediately.  Only call this
        after verifying that all encrypted files have been successfully
        re-encrypted with the new key.
        """
        # Re-read existing files with current key
        current_key = self._fernet_key
        migrated: list[tuple[Path, bytes]] = []
        for path in self._home.rglob("*"):
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
                if raw.startswith(_ENCRYPTION_HEADER):
                    migrated.append((path, raw))
            except OSError:
                continue

        # Write new key to a temp file first so a crash mid-rotation
        # leaves the old key intact.  Only atomically rename into place
        # after all files have been successfully re-encrypted.
        new_key = _generate_key()
        key_path = self._home / ".oauth-key"
        tmp_key_path = self._home / ".oauth-key.next"
        fd = os.open(str(tmp_key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(new_key)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                tmp_key_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        # Re-encrypt all files with new key BEFORE activation
        for path, raw in migrated:
            try:
                plaintext = _decrypt_bytes(raw, current_key)
                ciphertext = _encrypt_bytes(plaintext.encode("utf-8"), new_key)
                _atomic_write(path, _ENCRYPTION_HEADER + ciphertext)
                logger.info("Rotated encryption key for %s", path.name)
            except Exception as exc:
                logger.error(
                    "Failed to re-encrypt %s during key rotation: %s — "
                    "new key NOT activated, old key still in use", path.name, exc
                )
                # Abort: don't activate the new key if any file fails
                try:
                    tmp_key_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        # All files re-encrypted successfully — activate new key atomically
        try:
            os.replace(tmp_key_path, key_path)
        except OSError:
            # On some filesystems, rename into place may fail; unlink old
            # and retry as a fallback.
            try:
                key_path.unlink(missing_ok=True)
                os.replace(tmp_key_path, key_path)
            except OSError as exc2:
                logger.error(
                    "Failed to activate new encryption key: %s. "
                    "Files have been re-encrypted; old key still in place. "
                    "Run rotate-key again to complete.", exc2
                )
                raise

        # Update in-process key
        self._key = new_key

    # ------------------------------------------------------------------
    # JSON interface
    # ------------------------------------------------------------------

    def read_json(self, filename: str) -> dict[str, Any]:
        """Read and decrypt a JSON credential file.

        Returns an empty dict if the file does not exist.
        """
        path = self._resolve(filename)
        try:
            plaintext = self._read_encrypted(path)
        except FileNotFoundError:
            return {}
        return json.loads(plaintext)

    def write_json(self, filename: str, data: dict[str, Any]) -> None:
        """Encrypt and atomically write *data* as JSON."""
        path = self._resolve(filename)
        plaintext = json.dumps(data, indent=2, ensure_ascii=False)
        self._write_encrypted(path, plaintext)

    # ------------------------------------------------------------------
    # Plaintext interface (.env, etc.)
    # ------------------------------------------------------------------

    def read_text(self, filename: str) -> str:
        """Read and decrypt a text credential file.

        Returns an empty string if the file does not exist.
        """
        path = self._resolve(filename)
        try:
            return self._read_encrypted(path)
        except FileNotFoundError:
            return ""

    def write_text(self, filename: str, plaintext: str) -> None:
        """Encrypt and atomically write *plaintext*."""
        path = self._resolve(filename)
        self._write_encrypted(path, plaintext)

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migrate_plaintext_to_encrypted(self, filename: str) -> bool:
        """One-shot migration: read plaintext, write encrypted.

        Returns True if migration occurred, False if the file was already
        encrypted or did not exist.
        """
        path = self._resolve(filename)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return False

        if raw.startswith(_ENCRYPTION_HEADER):
            # Already encrypted — nothing to do
            return False

        if not raw.strip():
            # Empty file — delete instead of encrypting
            path.unlink(missing_ok=True)
            return False

        try:
            self._write_encrypted(path, raw.decode("utf-8"))
            logger.info("Migrated %s from plaintext to encrypted storage", filename)
            return True
        except Exception as exc:
            logger.error("Failed to migrate %s: %s", filename, exc)
            return False

    def is_encrypted(self, filename: str) -> bool:
        """Return True if *filename* exists and is encrypted."""
        path = self._resolve(filename)
        try:
            return path.read_bytes()[:len(_ENCRYPTION_HEADER)] == _ENCRYPTION_HEADER
        except FileNotFoundError:
            return False

    # ------------------------------------------------------------------
    # Individual key-value store (for API keys, tokens, etc.)
    # ------------------------------------------------------------------

    _KV_FILENAME = "secret-store.json"

    def get_secret(self, key: str) -> str | None:
        """Read a single encrypted secret by key. Returns None if not found."""
        data = self.read_json(self._KV_FILENAME)
        return data.get(key)

    def set_secret(self, key: str, value: str) -> None:
        """Encrypt and store a single secret by key."""
        data = self.read_json(self._KV_FILENAME)
        data[key] = value
        self.write_json(self._KV_FILENAME, data)

    def delete_secret(self, key: str) -> bool:
        """Delete a secret. Returns True if it existed."""
        data = self.read_json(self._KV_FILENAME)
        if key in data:
            del data[key]
            self.write_json(self._KV_FILENAME, data)
            return True
        return False

    def list_secrets(self) -> dict[str, str]:
        """Return all stored secret keys (values redacted)."""
        data = self.read_json(self._KV_FILENAME)
        return {k: "***" for k in data}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, filename: str) -> Path:
        path = Path(filename)
        if path.is_absolute():
            raise ValueError(
                f"secret_store filename must be relative, got {filename!r}"
            )
        return self._home / filename

    def _read_encrypted(self, path: Path) -> str:
        """Read and decrypt a file.  Raises FileNotFoundError if missing."""
        raw = path.read_bytes()
        return _decrypt_bytes(raw, self._fernet_key)

    def _write_encrypted(self, path: Path, plaintext: str) -> None:
        """Encrypt and atomically write *plaintext* to *path*."""
        ciphertext = _encrypt_bytes(plaintext.encode("utf-8"), self._fernet_key)
        _atomic_write(path, _ENCRYPTION_HEADER + ciphertext)


# ---------------------------------------------------------------------------
# Module-level helpers (shared with agent.oauth.storage)
# ---------------------------------------------------------------------------


def _get_or_create_key(home: Path) -> bytes:
    """Return the Fernet key, creating it if it doesn't exist.

    Mirrors ``agent.oauth.storage._get_or_create_key`` but accepts an
    explicit *home* parameter to avoid circular imports.
    """
    import time

    key_path = home / ".oauth-key"
    if key_path.exists():
        data = key_path.read_bytes()
        if data:
            return data

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = _generate_key()
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


def _generate_key() -> bytes:
    """Generate a new Fernet-format key (32 random bytes, base64)."""
    import base64
    return base64.urlsafe_b64encode(os.urandom(32))


def _encrypt_bytes(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt *plaintext* with the given Fernet *key*."""
    if _HAS_RUST_FERNET:
        token = _rust_fernet_encrypt(key.decode(), plaintext.decode())
        return (token + "=").encode()  # Fernet tokens may need padding
    from cryptography.fernet import Fernet
    return Fernet(key).encrypt(plaintext)


def _decrypt_bytes(raw: bytes, key: bytes | None = None) -> str:
    """Decrypt *raw* bytes.

    If *key* is None, auto-resolves from ``get_intellect_home()`` (used by
    module-level callers).  Instance callers should pass their resolved key.
    """
    if not raw.startswith(_ENCRYPTION_HEADER):
        # Legacy plaintext — return as-is for transparent reads during migration
        return raw.decode("utf-8")
    payload = raw[len(_ENCRYPTION_HEADER):]
    if key is None:
        from intellect_constants import get_intellect_home
        key = _get_or_create_key(get_intellect_home())
    if _HAS_RUST_FERNET:
        token_str = payload.decode()
        # Strip header padding if present
        return _rust_fernet_decrypt(key.decode(), token_str)
    from cryptography.fernet import Fernet
    return Fernet(key).decrypt(payload).decode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically via a tempfile rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.",
    )
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
