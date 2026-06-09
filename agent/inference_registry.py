"""Inference provider & model registry — unified DB-backed configuration.

Phase 1 (this module): schema DDL + read/write facade over ``inference_*``
tables.  Phase 2 (follow-up): migrate existing YAML/config.yaml providers
into the registry and switch runtime resolution to DB-first.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── DDL (idempotent CREATE TABLE IF NOT EXISTS) ───────────────────────────

INFERENCE_PROVIDERS_DDL = """
CREATE TABLE IF NOT EXISTS inference_providers (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    api_mode        TEXT NOT NULL DEFAULT 'chat_completions',
    auth_type       TEXT NOT NULL DEFAULT 'api_key',
    base_url        TEXT NOT NULL DEFAULT '',
    default_model   TEXT NOT NULL DEFAULT '',
    default_aux_model TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    priority        INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inference_models (
    id              TEXT PRIMARY KEY,
    provider_id     TEXT NOT NULL REFERENCES inference_providers(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL DEFAULT '',
    context_length  INTEGER NOT NULL DEFAULT 0,
    max_output_tokens INTEGER NOT NULL DEFAULT 0,
    supports_vision INTEGER NOT NULL DEFAULT 0,
    supports_thinking INTEGER NOT NULL DEFAULT 0,
    supports_fast_mode INTEGER NOT NULL DEFAULT 0,
    pricing_input   REAL NOT NULL DEFAULT 0.0,
    pricing_output  REAL NOT NULL DEFAULT 0.0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inference_provider_aliases (
    alias           TEXT PRIMARY KEY,
    provider_id     TEXT NOT NULL REFERENCES inference_providers(id) ON DELETE CASCADE,
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inference_models_provider
    ON inference_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_inference_aliases_provider
    ON inference_provider_aliases(provider_id);
"""


def ensure_inference_schema(db: Any) -> None:
    """Idempotently create inference registry tables via *db* (SessionDB-like)."""
    db._execute_write(lambda c: c.executescript(INFERENCE_PROVIDERS_DDL))
    logger.debug("Inference registry schema ensured")


# ── Facade ────────────────────────────────────────────────────────────────


class InferenceRegistry:
    """Read/write access to the inference provider & model registry."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # ── Providers ──────────────────────────────────────────────────────

    def list_providers(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM inference_providers"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority DESC, display_name ASC"
        return [dict(r) for r in self._db._conn.execute(sql).fetchall()]

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        row = self._db._conn.execute(
            "SELECT * FROM inference_providers WHERE id = ?", (provider_id,)
        ).fetchone()
        return dict(row) if row else None

    def resolve_provider(self, name: str) -> str | None:
        """Resolve an alias or direct id to a provider_id."""
        row = self._db._conn.execute(
            "SELECT provider_id FROM inference_provider_aliases WHERE alias = ?",
            (name.lower(),),
        ).fetchone()
        if row:
            return row["provider_id"]
        row2 = self._db._conn.execute(
            "SELECT id FROM inference_providers WHERE id = ?", (name,)
        ).fetchone()
        if row2:
            return name
        return None

    def upsert_provider(
        self,
        provider_id: str,
        *,
        display_name: str = "",
        api_mode: str = "chat_completions",
        auth_type: str = "api_key",
        base_url: str = "",
        default_model: str = "",
        default_aux_model: str = "",
        enabled: bool = True,
        priority: int = 0,
        aliases: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        import json, time
        now = time.time()

        def _upsert(conn):
            conn.execute(
                "INSERT OR REPLACE INTO inference_providers "
                "(id, display_name, api_mode, auth_type, base_url, "
                "default_model, default_aux_model, enabled, priority, "
                "metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE("
                "(SELECT created_at FROM inference_providers WHERE id = ?), ?), ?)",
                (
                    provider_id, display_name, api_mode, auth_type, base_url,
                    default_model, default_aux_model, int(enabled), priority,
                    json.dumps(kwargs), provider_id, now, now,
                ),
            )
            if aliases:
                conn.execute(
                    "DELETE FROM inference_provider_aliases WHERE provider_id = ?",
                    (provider_id,),
                )
                for alias in aliases:
                    conn.execute(
                        "INSERT OR REPLACE INTO inference_provider_aliases "
                        "(alias, provider_id, created_at) VALUES (?, ?, ?)",
                        (alias.lower(), provider_id, now),
                    )

        self._db._execute_write(_upsert)

    def delete_provider(self, provider_id: str) -> bool:
        deleted = False

        def _del(conn):
            nonlocal deleted
            cur = conn.execute(
                "DELETE FROM inference_providers WHERE id = ?", (provider_id,)
            )
            deleted = cur.rowcount > 0

        self._db._execute_write(_del)
        return deleted

    # ── Models ─────────────────────────────────────────────────────────

    def list_models(
        self, provider_id: str | None = None, *, enabled_only: bool = True
    ) -> list[dict[str, Any]]:
        if provider_id:
            sql = "SELECT * FROM inference_models WHERE provider_id = ?"
            params = (provider_id,)
        else:
            sql = "SELECT * FROM inference_models"
            params = ()
        if enabled_only:
            sql += " AND enabled = 1" if "WHERE" in sql else " WHERE enabled = 1"
        sql += " ORDER BY display_name ASC"
        return [dict(r) for r in self._db._conn.execute(sql, params).fetchall()]

    def upsert_model(
        self,
        model_id: str,
        provider_id: str,
        *,
        display_name: str = "",
        context_length: int = 0,
        max_output_tokens: int = 0,
        supports_vision: bool = False,
        supports_thinking: bool = False,
        supports_fast_mode: bool = False,
        pricing_input: float = 0.0,
        pricing_output: float = 0.0,
        enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        import json, time
        now = time.time()

        def _upsert(conn):
            conn.execute(
                "INSERT OR REPLACE INTO inference_models "
                "(id, provider_id, display_name, context_length, "
                "max_output_tokens, supports_vision, supports_thinking, "
                "supports_fast_mode, pricing_input, pricing_output, "
                "enabled, metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "COALESCE((SELECT created_at FROM inference_models WHERE id = ?), ?), ?)",
                (
                    model_id, provider_id, display_name, context_length,
                    max_output_tokens, int(supports_vision), int(supports_thinking),
                    int(supports_fast_mode), pricing_input, pricing_output,
                    int(enabled), json.dumps(kwargs), model_id, now, now,
                ),
            )

        self._db._execute_write(_upsert)

    def delete_model(self, model_id: str) -> bool:
        deleted = False

        def _del(conn):
            nonlocal deleted
            cur = conn.execute(
                "DELETE FROM inference_models WHERE id = ?", (model_id,)
            )
            deleted = cur.rowcount > 0

        self._db._execute_write(_del)
        return deleted

    # ── Aliases ────────────────────────────────────────────────────────

    def add_alias(self, alias: str, provider_id: str) -> None:
        import time
        now = time.time()

        def _ins(conn):
            conn.execute(
                "INSERT OR REPLACE INTO inference_provider_aliases "
                "(alias, provider_id, created_at) VALUES (?, ?, ?)",
                (alias.lower(), provider_id, now),
            )

        self._db._execute_write(_ins)

    def remove_alias(self, alias: str) -> bool:
        deleted = False

        def _del(conn):
            nonlocal deleted
            cur = conn.execute(
                "DELETE FROM inference_provider_aliases WHERE alias = ?",
                (alias.lower(),),
            )
            deleted = cur.rowcount > 0

        self._db._execute_write(_del)
        return deleted

    # ── Bootstrap ──────────────────────────────────────────────────────

    def seed_builtin_providers(self, providers: list[dict[str, Any]]) -> int:
        """Insert or update builtin providers from a static catalog. Returns count."""
        count = 0
        for p in providers:
            pid = p.get("id", "")
            if not pid:
                continue
            self.upsert_provider(
                pid,
                display_name=p.get("display_name", pid),
                api_mode=p.get("api_mode", "chat_completions"),
                auth_type=p.get("auth_type", "api_key"),
                base_url=p.get("base_url", ""),
                default_model=p.get("default_model", ""),
                default_aux_model=p.get("default_aux_model", ""),
                aliases=p.get("aliases", []),
                priority=p.get("priority", 0),
                **{k: v for k, v in p.items() if k not in (
                    "id", "display_name", "api_mode", "auth_type", "base_url",
                    "default_model", "default_aux_model", "aliases", "priority",
                )},
            )
            count += 1
        return count
