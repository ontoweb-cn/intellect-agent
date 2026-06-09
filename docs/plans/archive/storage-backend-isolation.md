# Storage backend isolation (SQLite ↔ PostgreSQL)

> **Status:** Phase 1–5 + **P4 complete** (2026-06-06); **next: P5** read-replica routing (W5)  
> **Date:** 2026-06-06  
> **WebUI stub:** [`intellect-webui/docs/plans/storage-backend-isolation.md`](../../intellect-webui/docs/plans/storage-backend-isolation.md)

---

## 1. Policy

| Mode | `members.enabled` | Allowed `storage.backend` |
|------|-------------------|---------------------------|
| **Single-user** | `false` (default) | **`sqlite` only** |
| **Multi-user** | `true` | `sqlite` **or** `postgresql` |

**Isolation rules (runtime):**

1. **SQLite mode** — no PostgreSQL connections; optional `[db-postgresql]` extra not required.
2. **PostgreSQL mode** — no reads/writes to the **active** `state.db` file; migration tools may read SQLite as a one-time source.
3. **One primary store** — sessions, members, OAuth (`oauth_*` tables), and kanban (when `kanban.storage: unified`) must share the same backend.

**HA exception:** `INTELLECT_WEBUI_WORKERS > 1` requires PostgreSQL + Redis (`agent/webui_ha.py`) — multi-user only.

---

## 2. API contract

### `SessionDB`

| Constructor | Behavior |
|-------------|----------|
| `SessionDB()` | Uses `create_storage_backend(load_config())` — **production default** |
| `SessionDB(db_path=…)` | Forces `SQLiteBackend` at that path — **tests and explicit SQLite overrides only**; logs a warning when `storage.backend=postgresql` (unless `INTELLECT_ALLOW_SQLITE_DB_PATH=1` or pytest) |

### `MembershipDB` / `MembershipStore`

After Phase 1:

- `db_path is None` → `SessionDB()` (factory; respects `storage.backend`)
- `db_path` set → `SessionDB(db_path)` (tests only)

Callers must not assume `INTELLECT_HOME/state.db` is the active store when `storage.backend=postgresql`.

### WebUI bridge

- **Canonical:** `api/storage_bridge.get_session_db(profile)`  
- **Avoid:** `sqlite3.connect(…/state.db)`, `resolve_state_db_path().exists()` on PG deployments

---

## 3. Known split-brain (pre–Phase 1)

| Symptom | Root cause |
|---------|------------|
| Sessions in PG, members/OAuth in SQLite | `MembershipDB` always passed `SessionDB(db_path=state.db)` |
| WebUI bootstrap after PG migrate still writes SQLite | `POST /api/members/bootstrap` → `MembershipStore` |
| Health/recovery reads stale `state.db` | Raw `sqlite3` in `session_recovery.py`, deep health |

Phase 2–3 (WebUI + agent misc) address remaining SQLite assumptions.

---

## 4. Config validation (`validate_config_structure`)

| Rule | Severity |
|------|----------|
| `members.enabled: false` + `storage.backend: postgresql` | **error** |
| `storage.backend: postgresql` without DSN (`storage.postgresql.dsn` or `INTELLECT_PG_DSN`) | **warning** |
| `storage.backend: postgresql` + `kanban.storage: legacy` | **warning** (kanban stays in `kanban.db`) |

Enforced at startup (`print_config_warnings`) and `intellect doctor`.

---

## 5. Implementation phases

| Phase | Scope | Status |
|-------|-------|--------|
| **0** | This document + test skeleton | ✅ (`test_storage_backend_isolation.py`) |
| **1** | `MembershipDB` factory routing; config gates | ✅ |
| **2** | WebUI: recovery, health, onboarding, bootstrap UI; migrate retire `state.db` | ✅ |
| **3** | Agent: ACP, doctor, mcp, kanban unified+PG | ✅ |
| **4** | Doctor dual-write detection; bootstrap UX; docs | ✅ |
| **5** | Deprecate legacy `sqlite3` helpers; `SessionDB(db_path)` guard in prod | ✅ |

---

## 6. Acceptance tests

- `tests/agent/test_storage_backend_isolation.py` — `MembershipDB` opens via factory (`db_path=None`)
- `tests/agent/test_dual_write_probe.py` — split-brain row-count + mtime probes
- `tests/intellect_cli/test_config_validation.py` — single-user + PG rejected
- `tests/integration/test_storage_postgresql_smoke.py` — `MembershipDB` on live PG (CI)

**Manual (PG deploy):**

```bash
# After Phase 1 + migrate — state.db must not grow
touch -r ~/.intellect/state.db /tmp/marker
# … exercise members OAuth login …
test ~/.intellect/state.db -nt /tmp/marker && echo "SPLIT-BRAIN" || echo "OK"
```

### 6.1 Post–Phase 5 sweep (2026-06-06)

- **Compression sidebar:** `read_importable_agent_session_rows_for_profile` uses `include_children=True` so Python projection matches legacy sqlite3 behaviour; HTTP subprocess tests pass without monkeypatch.
- **Title unique index:** `idx_sessions_title_unique` scoped to `parent_session_id IS NULL` so compression continuations can reuse the parent title (e.g. default `Cli Session`).
- **P4a:** `RedisCache` + `create_response_store` / `create_idempotency_cache` / `create_run_status_store` wired in `gateway/platforms/api_server.py`; graceful fallback to memory/SQLite.
- **P4b:** WebUI Redis pub/sub for `webui.sessions`, `webui.approval.*`, `webui.clarify.*`, `webui.kanban.{board}`; gateway `runs.{run_id}` event publish; M4 DB-first member auth when `INTELLECT_WEBUI_WORKERS>1`.
- **Multi-worker deploy:** set `INTELLECT_WEBUI_WORKERS>1` with `storage.backend=postgresql`, `cache.backend=redis`, `events.backend=redis` (validated at WebUI startup via `agent/webui_ha.py`).

### 6.2 Post–P4 next steps

| Priority | Item | Doc |
|----------|------|-----|
| P5 | PG read-replica routing (W5) | §16.5 / [remaining-decisions](2026-06-05-joint-pr-remaining-decisions.md) |
| P1 | OAuth §9 E2E + `oauth-db-only` setup seeds | [oauth-follow-up-tasks.md](oauth-follow-up-tasks.md) |
| Follow-up | Sidebar FTS Option B (§17.1) | [multi-database design §17.1](2026-06-02-multi-database-cache-mq-design.md) |

---

## 7. Related docs

- [`2026-06-02-multi-database-cache-mq-design.md`](2026-06-02-multi-database-cache-mq-design.md) §16.6 migrate runbook  
- [`session-isolation-rollout.md`](../session-isolation-rollout.md)  
- [`oauth-db-only-migration-pr-plan.md`](oauth-db-only-migration-pr-plan.md)
