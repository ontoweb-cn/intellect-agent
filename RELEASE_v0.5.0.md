# Intellect Agent v0.5.0 — Single-User Refactoring & Hardening

**Release Date**: 2026-06-10

## Overview

v0.5.0 is a simplification release that removes multi-user/team/project features
and PostgreSQL support, keeping only the single-user SQLite path. It also
includes 27 security hardening and performance optimization fixes.

## Breaking Changes

### Architecture Simplification

- **Removed**: PostgreSQL storage backend (11 files deleted)
- **Removed**: Multi-user membership system (RBAC, OAuth login, sessions) → replaced with stubs
- **Removed**: Team and project features (CRUD, workspaces, env management) → replaced with stubs
- **Removed**: Teams Pipeline plugin
- **Simplified**: `agent/runtime_context.py` — single-user RuntimeContext (no member/team/project IDs)
- **Simplified**: `agent/storage/` — `SQLiteBackend` is now the sole backend, no factory/abstract class
- **Simplified**: `intellect_state.py` — SessionDB always uses SQLiteBackend directly
- **Simplified**: `intellect_cli/config.py` — removed `members` and `postgresql` config sections
- **Simplified**: `pyproject.toml` — removed `db-postgresql` optional deps

### Version Reset

- Version reset from `0.15.1` → `0.5.0` to mark the single-user simplification
- All version references unified across `pyproject.toml`, `intellect_cli/__init__.py`, `package.json`

## Security Hardening (10 fixes)

### HIGH
| # | Area | Fix |
|---|------|-----|
| H1 | Password hashing | Schema doc: SHA-256 → scrypt (stdlib, n=32768) |
| H2 | TUI Gateway | WebSocket `shell.exec` now requires `TUI_AUTH_TOKEN` env var |
| H3 | TUI Gateway | Quick commands reject empty command strings before `shell=True` |

### MEDIUM
| # | Area | Fix |
|---|------|-----|
| M1 | API Server | `/health/detailed` hides PID/platform list when unauthenticated |
| M2 | API Server | CORS `*` wildcard triggers warning on config load |
| M4 | Gateway | Agent cache eviction uses `ThreadPoolExecutor` (was unbounded threads) |
| M6 | TUI Gateway | Session key entropy: 24-bit → 128-bit (`secrets.token_hex(16)`) |
| M7 | Gateway | Agent cache default: 128 → 64 entries (env-var configurable) |

### LOW
| # | Area | Fix |
|---|------|-----|
| L1 | SQL safety | FTS identifier whitelist validation before f-string SQL |
| L2 | CLI output | ANSI escape sequence filtering (OSC/DCS) for LLM output |
| L3 | API Server | Session ID sanitization: blacklist → whitelist `[a-zA-Z0-9_-]+` |
| L4 | URL safety | DNS TOCTOU documentation; confirmed all user HTTP via `safe_http` |

## Performance Optimizations (8 fixes)

| # | File | Optimization | Expected Impact |
|---|------|-------------|-----------------|
| P3 | `intellect_state.py` | O(n²) dedup → O(n) hash-set per turn | Large session load speedup |
| P5 | `tools/skills_tool.py` | mtime-based skills scan cache | Eliminate 50+ file reads/turn |
| P6 | `intellect_state.py` | Compression tip: iterative walk → WITH RECURSIVE CTE | 100 queries → 1 |
| P8 | `intellect_state.py` | Schema reconciliation: process-level column cache | Skip 40+ PRAGMA per init |
| P9 | `gateway/run.py` | `account_usage` deferred import | -230ms startup |
| — | `intellect_state.py` | FTS5 support probe: process-level cache | Skip temp table create/drop |
| M5 | `gateway/run.py` | Gateway config: mtime-based expanded cache | Skip deepcopy per message |
| 5.2 | `run_agent.py` | `@timed` decorator for hot-path profiling | Debug-level perf visibility |

## Architecture Improvements (4 fixes)

| # | File | Fix |
|---|------|-----|
| 4.2 | `intellect_state.py` | `SessionDB.__init__` error path now calls `SQLiteBackend.close()` |
| 4.4 | `config.py` | `load_config_readonly()` → `MappingProxyType` enforced read-only |
| M3 | `secret_store.py` | `set_secret/get_secret/delete_secret/list_secrets` key-value API |
| M3 | `intellect_cli/main.py` | `intellect secrets store set/get/list/delete` CLI commands |

## Wiki / Vault Fixes

| # | Area | Fix |
|---|------|-----|
| — | `wiki_scaffold.py` | `wiki_write_mode`: single-user (actor_role=None) → `read_write` |
| — | `runtime_context.py` | `_resolve_wiki_path`: returns profile-level path via `resolve_wiki_target` |

## Bug Fixes from Code Review

| # | Area | Fix |
|---|------|-----|
| — | `members_oauth.py` | `OAuthMemberNotLinkedError`/`OAuthIdentityConflictError`: functions → classes |
| — | `membership.py` | `get_session_ttl`: hours → seconds (604800 = 1 week) |
| — | `membership.py` | `Resource.for_scope()` classmethod added |
| — | `membership.py` | Missing `members_mode`, `validate_*_id`, `get_registration_config` stubs |
| — | `auth.py` | Indentation fixes for `_NoopStore` in try blocks |
| — | `runtime_context.py` | Restored `resolve_member_id`, `resolve_project_id` as safe stubs |
| — | `runtime_context.py` | `RuntimeContext` accepts legacy kwargs for backward compat |

## Test Suite

- 36 multi-user/PG test files deleted
- 3 test files rewritten for single-user mode
- `test_runtime_context.py` — rewritten for simplified RuntimeContext
- `test_storage_p1.py` — updated for direct SQLiteBackend
- `test_intellect_state.py` — session visibility tests removed, cache reset fixture added
- **249 tests passing** (core suite)

## Upgrade Notes

1. **Config**: Remove `members` and `storage.postgresql` sections from `config.yaml` (ignored now)
2. **Dependencies**: `db-postgresql` extra removed; reinstall with `uv pip install -e .`
3. **Database**: Only SQLite `state.db` supported; PG data not migrated automatically
4. **Wiki path**: Default resolves to `~/wiki`; use `WIKI_PATH` env var to override
5. **Secrets**: New `intellect secrets store` CLI for encrypted API key storage
