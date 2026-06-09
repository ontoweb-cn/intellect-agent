# Multi-Database, Cache & Message Queue Architecture Design

**Date:** 2026-06-02 (updated 2026-06-06)
**Status:** **P1–P4 implemented** (`v0.4.2`) — joint PR train **intellect-agent + intellect-webui**; **P5 (read replicas) next**. Decisions locked: M1–M3 + T1–T12 + M4 ([briefs](2026-06-05-p2-gates-m1-m3-decision-brief.md), [remaining](2026-06-05-joint-pr-remaining-decisions.md))
**Author:** Claude Opus 4.8 + simongu
**Out of joint PR scope:** Graphiti, RAG/GraphRAG, Helm/K8s charts (see §17)

---

## 1. Problem Statement

intellect-agent currently uses SQLite exclusively via raw `sqlite3` with no ORM, no connection pooling, and no async database support. All caching is in-process (LRU `OrderedDict`, `asyncio.Queue`), and there is no message queue or event bus.

To serve both **high-performance deployments** (multi-user gateway, high-concurrency API server, distributed agents) and **small single-user deployments** (local CLI, Raspberry Pi, Termux), the storage architecture must be modularized to support:

1. **Multiple relational databases**: SQLite (default), PostgreSQL
2. **Distributed caching**: Redis (optional), with in-memory fallback
3. **Async message/event bus**: for cross-process agent coordination, run status updates, and platform eventing

The design must **preserve backward compatibility** — a fresh `pip install intellect-agent` with zero configuration must continue to work exactly as it does today with SQLite and in-memory caching.

**Joint scope (approved 2026-06-05):** The same storage/cache/event abstractions are implemented in **intellect-agent** and consumed by **intellect-webui**. Production WebUI high availability is **multi-process WebUI workers + Redis** (in-process `AIAgent` per worker remains the default execution model; converging WebUI chat onto gateway `/v1/runs` is not required for this milestone).

**Feature requirements (2026-06-05):**

1. **Dual backend capability** — One binary ships **SQLite and PostgreSQL + Redis** code paths; **single-user** deployments use **SQLite only** (zero extra deps). Enabling **multi-user** offers an explicit choice to stay on SQLite or adopt the **PG (+ Redis)** stack (§10.3, §16.6).
2. **Multi-user → PG migration** — When the operator selects PostgreSQL, **OAuth and other DB-resident runtime state** must be migrated from `state.db` into PG before cutover (not only `members` rows).

---

## 2. Current State Analysis

### 2.1 Database Layer

| Aspect | Current State |
|--------|--------------|
| **Library** | `sqlite3` (stdlib, no deps) |
| **ORM** | None — raw SQL with `sqlite3.Row` |
| **Connection** | One connection per `SessionDB` instance, `check_same_thread=False` |
| **Transactions** | `BEGIN IMMEDIATE` + explicit commit/rollback, `isolation_level=None` |
| **Concurrency** | `threading.Lock()` per instance, jittered retry (20-150ms, 15 retries) |
| **Pooling** | None — each of 30+ call sites creates its own `SessionDB()` |
| **Migrations** | Declarative reconciliation (`_reconcile_columns`) + version-gated data migrations |
| **Full-text search** | SQLite FTS5 + trigram tokenizer (CJK support) |
| **SQLite-specific features** | `AUTOINCREMENT`, `INSERT OR IGNORE`, `PRAGMA journal_mode`, `PRAGMA wal_checkpoint`, `REAL` type |

### 2.2 Database Files

| File | Purpose | Class |
|------|---------|-------|
| `~/.intellect/state.db` | Sessions, messages, members, teams, projects, OAuth | `SessionDB` |
| `~/.intellect/kanban.db` | Kanban board state | `KanbanDB` (separate module) |
| `~/.intellect/response_store.db` | Response API state cache | `ResponseStore` |
| `~/.intellect/members/<id>/` | Per-member skills, memories, workspace | Filesystem |

### 2.3 Class Hierarchy

```
SessionDB                         ← Raw sqlite3, 30+ call sites, ~3700 lines
  ├── MembershipDB(SessionDB)     ← Multi-user member CRUD, auth
  │     ├── TeamDB(MembershipDB)  ← Team management
  │     └── ProjectDB(MembershipDB) ← Project management
  └── KanbanDB                    ← Separate module, own connection management

ResponseStore                     ← Separate SQLite, LRU eviction
```

### 2.4 Instantiation Patterns

`SessionDB()` is instantiated at **30+ locations** across:
- `run_agent.py:469` — AIAgent init
- `cli.py:3200,4907,6628,10330` — CLI commands
- `gateway/run.py:1850` — GatewayRunner init
- `gateway/session.py:718` — SessionStore
- `gateway/platforms/api_server.py` — per-request (via helper)
- `mcp_serve.py:75` — MCP server
- `agent/membership.py:385` — MembershipDB wraps SessionDB
- `tools/memory_tool.py:97` — Memory tool
- `acp_adapter/session.py:417` — ACP adapter
- `tui_gateway/server.py:348` — TUI gateway
- Plus: `goals.py`, `main.py`, `oneshot.py`, `mirror.py`, `project_env.py` etc.

### 2.5 Caching & Messaging (All In-Process)

| Component | Implementation | Location |
|-----------|---------------|----------|
| Agent cache | `OrderedDict` LRU (max 128, 1h TTL) | `gateway/run.py` |
| Idempotency cache | In-memory dict with TTL (300s, 1000 items) | `api_server.py:_IdempotencyCache` |
| Tool defs cache | Module-level dict | `model_tools.py` |
| SSE event queues | Per-run `asyncio.Queue` | `api_server.py` |
| Run status | In-memory dict | `api_server.py:_run_statuses` |
| WAL fallback warnings | Module-level `set[str]` | `intellect_state.py` |

**Key gap**: All caches are per-process. In a multi-process or multi-node deployment, caches are not shared, leading to:
- Agent cache misses across gateway restarts
- Run status lost on gateway crash
- No cross-process event coordination

### 2.6 Async/Sync Pattern

```
┌──────────────────────────────────────────────────────────┐
│  Async World (asyncio event loop)                        │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ aiohttp API │  │ Telegram/Discord│  │ MCP Server    │  │
│  │ Server      │  │ Adapters     │  │                │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘  │
│         │                │                   │           │
│         └────────────────┼───────────────────┘           │
│                          │                               │
│                 loop.run_in_executor()                   │
│                          │                               │
├──────────────────────────┼───────────────────────────────┤
│  Sync World (thread pool)│                               │
│  ┌───────────────────────▼──────────────────────────┐    │
│  │  AIAgent.run_conversation()  (blocking)          │    │
│  │  ├── Model API calls (httpx, openai SDK)         │    │
│  │  ├── Tool dispatch                               │    │
│  │  ├── SessionDB.append_message()  ← sync sqlite3  │    │
│  │  └── Memory read/write                           │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  Sync World (main thread in CLI)                         │
│  ┌──────────────────────────────────────────────────┐    │
│  │  CLI interactive loop (prompt_toolkit)            │    │
│  │  ├── SessionDB operations                         │    │
│  │  └── AIAgent.run_conversation()                   │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**Key implication**: The agent loop is synchronous. This means the PostgreSQL driver used in the agent thread needs to support sync access (psycopg2), while the gateway's async event loop uses asyncpg directly. For SQLite, everything stays as-is.

### 2.7 intellect-webui (Current State — Joint PR Baseline)

| Aspect | Current State |
|--------|---------------|
| **Chat transcript (source of truth)** | `{INTELLECT_HOME}/sessions/*.json` + in-process `SESSIONS` LRU (`api/models.py`) |
| **Agent metadata / insights** | Optional sync into `state.db` via `api/state_sync.py` (`sync_to_insights`, default off) |
| **Agent execution** | In-process `AIAgent.run_conversation()` in worker threads (`api/streaming.py`) — not gateway `/v1/runs` by default |
| **Run continuity** | Per-session JSONL run journal + SSE (`INTELLECT_WEBUI_RUNTIME_ADAPTER=legacy-journal`) |
| **Realtime UI** | Process-local SSE subscriber registries (`approval`, `clarify`, `sessions/events`, gateway stream, kanban) |
| **Members / teams** | WebUI routes + agent libraries; schema gaps documented in `intellect-webui/docs/plans/webui-agent-gap-analysis.md` |
| **Kanban API** | `api/kanban_bridge.py` → `intellect_cli.kanban_db` |
| **Sidebar search** | `/api/sessions/search` scans JSON messages (substring), not agent FTS5 |
| **Multi-worker** | Best-effort `flock` on journals; **not safe** for shared SQLite WAL across workers without PG + Redis |

**Implication for this design:** PostgreSQL + Redis unlock **multi-process WebUI**; SQLite + memory remains valid for single-process installs. Chat JSON files stay the transcript source of truth in the joint PR; `state.db`/PG holds shared metadata, members, and agent-side message mirrors.

---

## 3. Design Principles

1. **Backward compatibility as a hard constraint** — default install = SQLite + memory cache, zero config
2. **Gradual optionality** — each new backend (PG, Redis) is opt-in via config, with deps in optional extras
3. **Interface-first design** — ABCs for storage, cache, event bus; implementations are pluggable
4. **Minimal dependency footprint** — SQLite-only users should not install SQLAlchemy, asyncpg, redis-py, etc.
5. **Preserve the sync agent loop** — the agent remains synchronous; database backends provide sync interfaces; async backends are wrapped for sync use
6. **Connection pooling for PostgreSQL** — mandatory for performance; SQLite stays single-connection (or WAL multi-reader)
7. **Dialect-neutral SQL where possible** — use SQLAlchemy Core's expression language for cross-DB SQL generation; keep raw SQL for SQLite-only paths

---

## 4. Proposed Architecture

### 4.1 Layer Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     Application Layer                             │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────────┐   │
│  │ AIAgent  │  │ Gateway  │  │ CLI       │  │ API Server    │   │
│  │          │  │ Runner   │  │ Commands  │  │               │   │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └───────┬───────┘   │
│       │              │              │                 │           │
├───────┴──────────────┴──────────────┴─────────────────┴───────────┤
│                    Data Access Layer (new)                         │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  StorageManager (facade, created once per process)           │ │
│  │  ├── db: StorageBackend          ← polymorphic              │ │
│  │  ├── cache: CacheBackend         ← polymorphic              │ │
│  │  └── events: EventBus            ← polymorphic              │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │ SessionRepository│  │ MemberRepository │  │ ProjectRepo    │  │
│  │ (sessions, msgs) │  │ (members, teams) │  │ (projects)     │  │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬────────┘  │
│           │                     │                     │           │
├───────────┴─────────────────────┴─────────────────────┴───────────┤
│                    Backend Implementations                         │
│                                                                   │
│  Storage Backends:          Cache Backends:     Event Buses:      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │ SQLiteBackend    │  │ MemoryCache      │  │ MemoryEventBus │  │
│  │ (sqlite3, sync)  │  │ (in-process LRU) │  │ (asyncio.Queue)│  │
│  ├──────────────────┤  ├──────────────────┤  ├────────────────┤  │
│  │ PGBackend        │  │ RedisCache       │  │ RedisEventBus  │  │
│  │ (SQLAlchemy Core)│  │ (redis-py,async) │  │ (redis pub/sub)│  │
│  ├──────────────────┤  └──────────────────┘  └────────────────┘  │


│  └──────────────────┘                                              │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 StorageBackend ABC

```python
# agent/storage/backend.py

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar
from pathlib import Path

T = TypeVar("T")
Row = dict[str, Any]  # replaces sqlite3.Row

class StorageBackend(ABC):
    """Abstract storage backend for session/member/project persistence.

    Implementations: SQLiteBackend, PGStorageBackend.
    """

    # ── Lifecycle ──────────────────────────────────────────────────

    @abstractmethod
    def initialize(self) -> None:
        """Create schema, run migrations, verify connectivity."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close all connections and release resources."""
        ...

    @property
    @abstractmethod
    def dialect(self) -> str:
        """Return 'sqlite' or 'postgresql'."""
        ...

    # ── Read operations ────────────────────────────────────────────

    @abstractmethod
    def execute(self, sql: str, params: tuple = ()) -> "CursorProxy":
        """Execute a read query and return a cursor-like object."""
        ...

    def fetchone(self, sql: str, params: tuple = ()) -> Row | None:
        """Convenience: execute + fetchone."""
        ...

    def fetchall(self, sql: str, params: tuple = ()) -> list[Row]:
        """Convenience: execute + fetchall."""
        ...

    # ── Write operations ───────────────────────────────────────────

    @abstractmethod
    def execute_write(self, fn: Callable[["CursorProxy"], T]) -> T:
        """Execute *fn* inside a write transaction with retry logic.

        The backend handles BEGIN/COMMIT/ROLLBACK and retry on lock
        contention, matching the current SessionDB._execute_write pattern.
        """
        ...

    # ── Schema management ──────────────────────────────────────────

    @abstractmethod
    def ensure_schema(self, ddl: str) -> None:
        """Idempotently ensure tables/indexes from DDL exist."""
        ...

    # ── Full-text search (optional) ────────────────────────────────

    @abstractmethod
    def supports_fts(self) -> bool:
        """Return True if the backend supports full-text search."""
        ...

    @abstractmethod
    def search(self, query: str, limit: int = 50) -> list[Row]:
        """Full-text search across session messages."""
        ...

    # ── Connection info ────────────────────────────────────────────

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the backend has an active connection."""
        ...
```

### 4.3 CursorProxy — Unified Row Interface

Replace `sqlite3.Row` with a normalized dict-like cursor:

```python
class CursorProxy:
    """Unified cursor interface across backends.

    Wraps sqlite3.Cursor, SQLAlchemy ResultProxy, or raw DB-API cursor.
    """

    def execute(self, sql: str, params: tuple = ()) -> "CursorProxy":
        ...

    def fetchone(self) -> Row | None:
        """Return next row as dict, or None."""
        ...

    def fetchall(self) -> list[Row]:
        """Return all rows as list of dicts."""
        ...

    def __iter__(self):
        """Iterate over rows as dicts."""
        ...

    @property
    def lastrowid(self) -> int | None:
        """Return the last inserted rowid (if applicable)."""
        ...

    @property
    def rowcount(self) -> int:
        """Return the number of rows affected."""
        ...
```

### 4.4 CacheBackend ABC

```python
# agent/cache/backend.py

class CacheBackend(ABC):
    """Abstract cache backend. Implementations: MemoryCache, RedisCache."""

    @abstractmethod
    async def get(self, key: str) -> Any | None: ...
    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...
    @abstractmethod
    async def exists(self, key: str) -> bool: ...
    @abstractmethod
    async def clear(self) -> None: ...

    # Sync wrappers for use in agent thread (call via asyncio.run or thread-safe bridge)
    def get_sync(self, key: str) -> Any | None: ...
    def set_sync(self, key: str, value: Any, ttl: int | None = None) -> None: ...
```

### 4.5 EventBus ABC

```python
# agent/events/bus.py

class EventBus(ABC):
    """Abstract event bus for cross-component messaging."""

    @abstractmethod
    async def publish(self, channel: str, message: dict) -> None: ...
    @abstractmethod
    async def subscribe(self, channel: str, handler: Callable) -> None: ...
    @abstractmethod
    async def unsubscribe(self, channel: str) -> None: ...
```

---

## 5. SQL Dialect Strategy

### 5.1 Approach: Dialect-Neutral DDL + Backend-Specific SQL

**Option A (Recommended): SQLAlchemy Core as intermediate layer for PG**

For the PostgreSQL backend, use SQLAlchemy Core's `Table`, `Column`, `Index` objects to generate DDL. This avoids writing dialect-specific SQL manually. The existing `SCHEMA_SQL` string remains the source of truth for SQLite.

**Option B: Manual dialect SQL** — Write three versions of each SQL statement. More control but high maintenance burden.

**Recommendation**: **Hybrid A+B** — Use SQLAlchemy Core's schema reflection to auto-generate DDL for PG from a canonical schema definition, while keeping the SQLite path as raw SQL (preserving existing code). For query-level SQL, use a minimal dialect adapter that translates SQLite SQL → target dialect for common patterns.

### 5.2 Dialect Differences to Handle

Supported non-SQLite backend: **PostgreSQL only** (§15.11). MySQL is not a target.

| SQLite | PostgreSQL |
|--------|------------|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` / `GENERATED ALWAYS AS IDENTITY` |
| `TEXT` | `TEXT` |
| `REAL` | `DOUBLE PRECISION` |
| `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` |
| `INSERT OR REPLACE` | `INSERT ... ON CONFLICT ... DO UPDATE` |
| `PRAGMA journal_mode=WAL` | N/A (server WAL is automatic) |
| `PRAGMA foreign_keys=ON` | Always enforced |
| FTS5 virtual tables | `tsvector` + `pg_trgm` / `pg_bigm` (see §15.3) |
| `sqlite3.Row` | `dict` (e.g. `RealDictCursor`) |
| `BEGIN IMMEDIATE` | `BEGIN` (READ COMMITTED) |

### 5.3 Schema Management

```
Current:  SCHEMA_SQL string in intellect_state.py
          + _reconcile_columns() for declarative column adds
          + version-gated data migrations

Future:   schema/
          ├── canonical.py          # Canonical schema definition (dataclasses)
          ├── ddl_sqlite.py         # Generate SQLite DDL from canonical
          ├── ddl_postgresql.py     # Generate PostgreSQL DDL from canonical
          └── migrations/           # Alembic migrations (PG only)
              └── versions/
```

**Rationale**: Keep the existing declarative reconciliation for SQLite (proven, no new deps). Use Alembic for PostgreSQL (industry standard, handles complex migrations). The canonical schema definition ensures all three DDL generators stay in sync.

---

## 6. Connection & Pooling Strategy

### 6.1 SQLite (Default — No Pooling)

```
Current:  One connection per SessionDB instance
          check_same_thread=False, isolation_level=None
          WAL mode, threading.Lock, jittered retry

Keep:     Same pattern. The existing SQLite connection management is
          battle-tested and handles the single-writer constraint well.
          Multiple readers + single writer via WAL mode.
```

### 6.2 PostgreSQL (New — Connection Pool)

```
Pool:     SQLAlchemy's QueuePool (default)
          pool_size=5, max_overflow=10, pool_recycle=3600
          psycopg2 (sync) for agent thread access
          asyncpg (async) for gateway event loop access

Strategy: Two engine instances:
          1. Sync engine (psycopg2) — for agent thread
          2. Async engine (asyncpg) — for gateway async handlers
          Both share the same connection parameters.
```

### 6.3 Connection Lifecycle

```
Process Start
  │
  ├─ StorageManager.__init__(config)
  │   ├─ Parse config → determine backend
  │   ├─ Lazy-import backend module (SQLiteBackend / PGStorageBackend / ...)
  │   ├─ Create backend instance
  │   └─ backend.initialize() → connect, create schema, run migrations
  │
  ├─ Application uses storage_manager.db.execute(...)
  │
  └─ Process Shutdown
      └─ storage_manager.close() → close pool, checkpoint SQLite WAL
```

---

## 7. Cache Strategy

### 7.1 What to Cache

| Data | Current | Proposed | TTL | Backend |
|------|---------|----------|-----|---------|
| Agent instances | `OrderedDict` LRU (128) | CacheBackend | 1h | Redis or Memory |
| Session metadata | From DB every read | Cache-aside | 5min | Redis or Memory |
| Tool definitions | Module-level dict | CacheBackend | 10min | Redis or Memory |
| Idempotency keys | In-memory dict (1000) | CacheBackend | 5min | Redis (cluster-safe) |
| SSE event queues | `asyncio.Queue` | EventBus | — | Redis pub/sub |
| Run status | In-memory dict | CacheBackend + EventBus | 1h | Redis |
| OAuth state/nonce | In-memory (api_server) | CacheBackend | 10min | Redis (cluster-safe) |
| Config parse cache | Module-level | Not cached | — | — (config reads are rare) |
| FTS5 search | SQLite FTS5 | Backend-specific FTS | — | PG tsvector + pg_trgm |
| Response Store | Separate SQLite file (LRU 100) | CacheBackend → fallback to DB table | 1h | Redis or DB table |

### 7.2 Cache Invalidation

- **TTL-based**: Default strategy. Each cache entry has a TTL.
- **Write-through**: On SessionDB write, invalidate related cache keys.
- **Explicit invalidation**: Repository methods call `cache.delete(key)` after writes.

### 7.3 Redis Sentinel / Cluster (Future)

For high-availability deployments, support Redis Sentinel for failover. This is a future consideration, not in the initial implementation.

---

## 8. Event Bus / Message Queue Strategy

### 8.1 Events to Publish

| Event | Publisher | Subscribers | Channel |
|-------|-----------|-------------|---------|
| `run.started` | API Server | Gateway Runner, WebSocket clients | `runs.{run_id}` |
| `run.tool.started` | Agent Thread | SSE writer, WebSocket | `runs.{run_id}.tools` |
| `run.tool.completed` | Agent Thread | SSE writer, WebSocket | `runs.{run_id}.tools` |
| `run.completed` | Agent Thread | API Server, WebSocket | `runs.{run_id}` |
| `run.approval.needed` | Agent Thread | API Server | `runs.{run_id}.approval` |
| `session.updated` | SessionDB | Gateway cache invalidator | `sessions.{id}` |
| `member.login` | MembershipDB | Audit log, presence tracker | `members.{id}` |
| `member.logout` | MembershipDB | Presence tracker | `members.{id}` |
| `gateway.shutdown` | Gateway Runner | All platform adapters | `gateway.control` |
| `cron.triggered` | Cron Scheduler | Gateway Runner | `cron.{job_id}` |

### 8.2 Implementation Phases

**Phase 1 (this design)**: MemoryEventBus + RedisEventBus
- Memory: `asyncio.Queue`-based (keeps current behavior, no new deps)
- Redis: `redis-py` pub/sub (lightweight, already a dependency if Redis cache is used)

**Phase 2 (future)**: Dedicated message queue (RabbitMQ / NATS / Redis Streams)
- For high-throughput or persistent message delivery guarantees
- Only if Redis pub/sub proves insufficient

---

## 9. Repository Layer

### 9.1 Rationale

Currently, `SessionDB`, `MembershipDB`, `ProjectDB`, `KanbanDB`, and `ResponseStore` each manage their own SQLite connections and contain both data access logic and business logic. A repository layer separates concerns:

- **Repository**: Data access (SQL, serialization, caching)
- **Service**: Business logic (validation, RBAC, feature flags)
- **Backend**: Connection management, transaction handling, dialect translation

### 9.2 Repository Hierarchy

```
SessionRepository(StorageBackend, CacheBackend)
  ├── create_session(...)
  ├── get_session(session_id) → dict | None
  ├── append_message(session_id, message) → int
  ├── get_messages(session_id) → list[dict]
  ├── search_messages(query, limit) → list[dict]
  ├── delete_session(session_id)
  └── ...

MemberRepository(StorageBackend, CacheBackend)
  ├── create_member(...)
  ├── get_member(member_id) → dict | None
  ├── get_member_by_login(login_name) → dict | None
  ├── update_member(member_id, ...)
  ├── delete_member(member_id)
  └── ...

TeamRepository(StorageBackend, CacheBackend)
  ├── create_team(...)
  ├── get_team(team_id) → dict | None
  ├── add_member(team_id, member_id, role)
  └── ...

ProjectRepository(StorageBackend, CacheBackend)
  ├── create_project(...)
  ├── get_project(project_id) → dict | None
  ├── archive_project(project_id)
  └── ...

KanbanRepository(StorageBackend, CacheBackend)
  ├── create_board(...)
  ├── get_board(board_id) → dict | None
  ├── list_boards() → list[dict]
  ├── create_task(board_id, ...)
  ├── update_task(task_id, ...)
  ├── decompose_task(task_id, ...)
  └── ... (all kanban operations migrated from kanban_db.py, ~7440 lines)

ResponseRepository(StorageBackend, CacheBackend)
  ├── get(response_id) → dict | None
  ├── put(response_id, data)
  ├── delete(response_id)
  ├── get_conversation(name) → str | None
  ├── set_conversation(name, response_id)
  └── ...
  # Note: When Redis CacheBackend is configured, ResponseRepository
  # routes through cache for low-latency lookups. When memory-only,
  # uses a dedicated table within the main StorageBackend.
```

### 9.3 Transition Strategy

**Phase A (P1 — both repos):** Introduce thin `SessionRepository` / `MemberRepository` wrappers that delegate to existing `SessionDB` / `MembershipDB`. No behavior change. intellect-webui gains a profile-aware `StorageManager` factory instead of ad-hoc `SessionDB(db_path)` construction.

**Phase B (P6):** Move business logic from DB classes to Service classes. Repository becomes pure data access.

**Phase C (P2):** Swap the storage backend from SQLite to PostgreSQL via config. Repository interface unchanged.

This allows incremental migration without a big-bang rewrite. Repository wrappers land in **P1**, not after Redis/PG are production-ready.

---

## 10. Configuration Design

### 10.1 config.yaml (New Sections)

```yaml
# ── Storage Backend ─────────────────────────────────────────────
storage:
  # Backend: "sqlite" (default), "postgresql"
  backend: sqlite

  sqlite:
    # Path to SQLite database file
    path: ~/.intellect/state.db
    # WAL mode (strongly recommended for multi-process)
    wal: true
    # Checkpoint frequency (writes between PASSIVE checkpoints)
    checkpoint_every_n_writes: 50

  postgresql:
    # Connection DSN or individual params
    dsn: ""  # postgresql://user:pass@host:5432/intellect
    host: localhost
    port: 5432
    database: intellect
    user: intellect
    password: ""  # or use env INTELLECT_PG_PASSWORD
    # Connection pool
    pool_size: 5
    max_overflow: 10
    pool_recycle_seconds: 3600
    # SSL
    ssl_mode: prefer  # disable | allow | prefer | require | verify-ca | verify-full

# ── Cache Backend ───────────────────────────────────────────────
cache:
  # Backend: "memory" (default), "redis"
  backend: memory

  memory:
    # Max entries for LRU caches
    agent_cache_size: 128
    agent_cache_ttl_seconds: 3600
    idempotency_cache_size: 1000
    idempotency_cache_ttl_seconds: 300

  redis:
    url: ""  # redis://localhost:6379/0
    # or individual params
    host: localhost
    port: 6379
    db: 0
    password: ""  # or use env INTELLECT_REDIS_PASSWORD
    # Connection pool
    max_connections: 20
    # Sentinel (future)
    sentinel: false

# ── Event Bus ───────────────────────────────────────────────────
events:
  # Backend: "memory" (default), "redis"
  backend: memory

  redis:
    url: ""  # redis://localhost:6379/1  (separate DB from cache)
    host: localhost
    port: 6379
    db: 1
    password: ""

# ── Sessions (existing, extended) ───────────────────────────────
sessions:
  retention_days: 90
  auto_prune: false
  vacuum_after_prune: true
  min_interval_hours: 24
  # NEW: storage backend override for sessions specifically
  # (allows sessions on high-perf PG while members stay on SQLite)
  # storage_backend: postgresql  # future
```

### 10.2 Environment Variables

```
INTELLECT_STORAGE_BACKEND=postgresql
INTELLECT_PG_DSN=postgresql://user:pass@host:5432/intellect
INTELLECT_PG_PASSWORD=secret

INTELLECT_REDIS_URL=redis://localhost:6379/0
INTELLECT_REDIS_PASSWORD=secret
INTELLECT_CACHE_BACKEND=redis
INTELLECT_EVENTS_BACKEND=redis
```

### 10.3 Deployment Profiles (Single-User vs Multi-User)

The product exposes **one implementation** with **three supported configuration profiles**. Profile selection is config-driven; PG/Redis dependencies remain optional extras.

| Profile | `members.enabled` | `storage.backend` | `cache` / `events` | Typical use |
|---------|-------------------|-------------------|--------------------|-------------|
| **single_user** (default) | `false` | `sqlite` | `memory` / `memory` | CLI, solo WebUI, Raspberry Pi — **no PG/Redis required** |
| **multi_user_sqlite** | `true` | `sqlite` | `memory` / `memory` | Small team, **one** WebUI/gateway process; documented limits |
| **multi_user_ha** | `true` | `postgresql` | `redis` / `redis` | Multi-worker WebUI, gateway replicas, shared home |

**Rules:**

- **Single-user:** Doctor/setup must **not** require PostgreSQL or Redis. Default install unchanged.
- **Multi-user enablement** does **not** auto-switch storage. Operator chooses profile at enable time (§16.6). UI **recommends PostgreSQL** (pre-selected); SQLite remains an explicit secondary option.
- `INTELLECT_WEBUI_WORKERS>1` with `multi_user_sqlite` → **fail-fast** (T5 ✅); HA profile required.
- Code paths for PG and Redis are always present in the tree but **lazy-imported** until configured.

```yaml
# Example: multi-user on SQLite (explicit opt-in)
members:
  enabled: true
storage:
  backend: sqlite
cache:
  backend: memory
events:
  backend: memory

# Example: multi-user HA (after migration — §16.6)
members:
  enabled: true
storage:
  backend: postgresql
  postgresql:
    dsn: "postgresql://..."
cache:
  backend: redis
events:
  backend: redis
```

---

## 11. Dependency Management

### 11.1 New Optional Extras

```toml
[project.optional-dependencies]
# Storage backends
db-postgresql = [
    "sqlalchemy[asyncio]==2.0.43",
    "psycopg2-binary==2.9.11",
    "asyncpg==0.31.0",
    "alembic==1.16.4",
]

# Cache backend
cache-redis = [
    "redis[hiredis]==6.4.0",
]

# Event bus
events-redis = [
    "redis[hiredis]==6.4.0",  # same package, different usage
]

# Combined extras for convenience
high-performance = [
    "intellect-agent[db-postgresql]",
    "intellect-agent[cache-redis]",
    "intellect-agent[events-redis]",
]
```

**Install note (2026-06-05):** extras are consumed from a **source checkout** (`uv pip install -e ".[db-postgresql]"`). `intellect-agent` is not on PyPI yet; `high-performance` meta-extra resolves only after editable install of the repo.

### 11.2 Lazy Import Strategy

Follow the existing `tools/lazy_deps.py` pattern — backend modules are lazy-imported only when the configuration calls for them. SQLite-only users never import SQLAlchemy or redis-py.

```python
# agent/storage/__init__.py

def create_storage_backend(config: dict) -> StorageBackend:
    backend_name = _get_storage_backend_name(config)  # "sqlite" | "postgresql"

    if backend_name == "sqlite":
        from agent.storage.sqlite_backend import SQLiteBackend
        return SQLiteBackend(config)

    if backend_name == "postgresql":
        # Lazy import — only users who configure postgresql pay the dep cost
        from agent.storage.postgres_backend import PGStorageBackend
        return PGStorageBackend(config)



    raise ValueError(f"Unknown storage backend: {backend_name}")
```

---

## 12. Migration Path

### 12.1 Phase 1: Foundation (v0.16)

**Goal**: Introduce abstractions without changing behavior (agent + webui).

1. Create `agent/storage/` package with `StorageBackend` ABC
2. Create `agent/cache/` package with `CacheBackend` ABC
3. Extract `SQLiteBackend` from `SessionDB` — same code, new wrapper
4. Create `CursorProxy` wrapping `sqlite3.Cursor` → `dict`
5. `SessionDB` delegates to `SQLiteBackend`
6. **Phase A repositories** — thin wrappers; no SQL moves yet
7. **`get_storage_manager(profile=...)`** — shared factory for agent, gateway, webui (`api/state_sync.py`, `api/streaming.py`, `api/agent_sessions.py` call sites audited)
8. Add `storage`, `cache`, `events` sections to `DEFAULT_CONFIG` (all default to sqlite/memory/memory)
9. intellect-webui: read `storage.*` / `INTELLECT_*` env mirrors (same resolution order as agent gateway)

**Deliverables**: No user-visible change. All tests pass. ABCs stable. WebUI uses factory; behavior identical to today.

### 12.2 Phase 2: PostgreSQL Support (v0.17)

**Goal**: Run the full test suite against PostgreSQL; unblock multi-process WebUI.

**Prerequisites (same release train):** Resolve **M1–M3** in [2026-06-05-p2-gates-m1-m3-decision-brief.md](2026-06-05-p2-gates-m1-m3-decision-brief.md); session isolation write paths must stamp `member_id` before PG cutover.

1. Implement `PGStorageBackend` using SQLAlchemy Core
2. Canonical schema → SQLAlchemy `Table` objects → DDL generation
3. SQL dialect adapter for query-time SQL translation
4. Connection pool management (sync + async engines)
5. Alembic migrations for schema versioning
6. CI: agent + webui tests with `INTELLECT_STORAGE_BACKEND=postgresql`
7. intellect-webui: `state_sync`, sidebar CLI/agent session reads, `session_search` tool path on PG
8. Documentation: PostgreSQL deployment (single-process + multi-worker WebUI)
9. **`intellect db migrate-sqlite-to-pg`** (and WebUI wizard step): copy §16.6 tables from SQLite → PG; dry-run + checksum report
10. Post-migrate: set `storage.backend=postgresql`; optional Redis if HA profile; **verify OAuth login + model tokens**

**Deliverables**: PostgreSQL optional deps from a **source checkout** (`uv pip install -e ".[db-postgresql]"` or equivalent) + `storage.backend=postgresql` = shared state on PG. **`intellect-agent` is not published to PyPI** — `pip install 'intellect-agent[db-postgresql]'` will fail until a PyPI release ships.

**Multi-worker (W4b / Redis pub/sub):** ✅ **implemented** (2026-06-06, P4b). `INTELLECT_WEBUI_WORKERS>1` requires PostgreSQL + `cache.backend=redis` + `events.backend=redis` (fail-fast via `agent/webui_ha.py`). Single-worker + PG remains the recommended default for operators not running Redis. **Multi-user enablement can stay on SQLite** until operator opts into PG migration.

### 12.3 Phase 3: KanbanDB Integration + Backup/Restore (v0.18)

**Goal**: Eliminate the separate kanban_db.py module; provide database backup/restore. (**RAG deferred** — §17.)

1. Create `KanbanRepository` using the main `StorageBackend`
2. Port kanban schema to canonical definition (shared DDL generation)
3. Migrate ~30 agent call sites + **intellect-webui `api/kanban_bridge.py`** to `KanbanRepository`
4. Implement `backup()` / `restore()` / `list_backups()` on each backend
5. Backup manifest includes: `state.db` (or PG dump), `sessions/*.json`, run journals, `.member-sessions`, webui `settings.json` (profile-aware paths via `get_intellect_home()`)
6. CLI: `intellect db backup`, `intellect db restore`
7. Cross-backend migration: SQLite ↔ PG via JSON export/import
8. Remove `kanban_db.py` connection management (keep schema logic until fully ported)

**Deliverables**: Single storage backend for kanban + shared metadata; documented backup of WebUI transcript files alongside DB.

### 12.4 Phase 4: Redis Cache + Events (v0.19)

**Goal**: Shared cache and event bus across processes — **gateway + multi-process intellect-webui**. (**Graphiti deferred** — §17.)

**Agent / gateway (4a):**

1. Implement `RedisCache` and `RedisEventBus`
2. Migrate agent cache, idempotency cache, API-server run status / SSE fan-out
3. Migrate ResponseStore to CacheBackend (Redis when available, DB table fallback)
4. Graceful fallback: Redis unavailable → memory (warning once per process)

**intellect-webui (4b) — required for approved HA model:**

5. **Config gate:** `cache.backend=redis` and `events.backend=redis` required when `INTELLECT_WEBUI_WORKERS>1` (or equivalent); startup validation fails fast on SQLite-only + N workers
6. Publish/subscribe for cross-worker signals (see §16.3 channel map): `sessions_changed`, approval head, clarify head, optional stream liveness
7. **Keep in-process AIAgent** — Redis coordinates workers; does **not** require `RuntimeAdapter` → gateway `/v1/runs` for this milestone
8. Run journal remains local JSONL per worker; Redis carries **notifications**, not full transcript payloads
9. Sync `CacheBackend.get_sync` / `set_sync` in worker threads (no asyncio loop in chat workers)
10. Documentation: multi-process WebUI + Redis topology

**Deliverables**: N WebUI workers behind a load balancer with consistent session list / approval / clarify visibility; gateway multi-instance safe with same Redis.

### 12.5 Phase 5: Read Replicas (v0.20)

**Goal:** PostgreSQL read/write splitting for high-traffic agent + webui metadata reads. (**Helm/K8s deferred** — §17.)

1. Implement read replica routing (random, round_robin)
2. Lag monitoring: exclude replicas exceeding `max_replica_lag_seconds`
3. intellect-webui: read-heavy paths (`list_sessions`, agent session sidebar) use `get_read_connection()`
4. CloudNativePG / external RDS documentation (operator manifests optional, not bundled chart)
5. Agent gateway health: extend `/v1/health/detailed` with storage/cache connectivity

**Deliverables:** Read replica config documented and tested; no in-tree Helm chart in joint PR.

### 12.6 Phase 6: Repository Layer (v0.21+)

**Goal**: Clean separation of data access and business logic (Phase B/C completion).

1. Move business logic from DB classes to Service classes
2. Deprecate direct `SessionDB` / `MembershipDB` usage (agent 30+ sites + webui factory consumers)
3. Remove old DB classes (or keep as compatibility shims)

(WebUI sidebar: **Option A** in joint PR; **Option B** — §17.1 follow-up PR only.)

**Deliverables**: Repositories own all SQL; Services own RBAC and validation.

---

## 13. Key Design Decisions

> **T10–T12 resolved (2026-06-05):** Core only, sync agent loop, ResponseStore → CacheBackend — [remaining-decisions](2026-06-05-joint-pr-remaining-decisions.md).

### 13.1 SQLAlchemy: Core vs ORM? ✅ Resolved

**Decision**: SQLAlchemy **Core** only (no ORM).

- **Pros**: Dialect abstraction, connection pooling, battle-tested, no ORM overhead, works well with existing raw-SQL mindset
- **Cons**: Adds ~2MB dependency (only for PG users), learning curve for expression language
- **Alternative**: Write raw dialect-specific SQL — more control, no SQLAlchemy dep, but higher maintenance burden for cross-DB SQL

### 13.2 Sync Agent Loop: Keep or Go Async? ✅ Resolved

**Decision**: **Keep the sync agent loop** for now.

- **Pros**: No rewrite of `run_conversation()` (~3900 lines), all existing tool implementations stay sync, simpler debugging
- **Cons**: PG drivers need sync wrappers (~thread executor), can't use asyncpg's full performance from agent thread
- **Mitigation**: PostgreSQL backend provides both sync (psycopg2) and async (asyncpg) engines. Gateway async code uses asyncpg directly. Agent thread uses psycopg2.

### 13.3 Single DB or Multi-DB?

**Decision**: **Single database** for all state (sessions, messages, members, teams, projects, kanban, response store). ✅ Resolved.

- Currently, there are 3 separate SQLite files: `state.db`, `kanban.db`, `response_store.db`
- All consolidated into one database per deployment
- For PostgreSQL: schemas `public`, `kanban`, `responses` for logical separation
- **Pros**: Single connection pool, single backup target, simpler operations, unified migrations
- **Migrating kanban**: Tables move from separate `kanban.db` into the main database; existing data can be migrated during Phase 3

### 13.4 FTS5 → What for PG?

**Recommendation** (detail in §15.3):
- **PostgreSQL**: `tsvector` + GIN for European languages; `pg_trgm` / `pg_bigm` for CJK substrings
- **Snippets**: application-level `generate_snippet()` for all backends (not `ts_headline`)
- **Fallback**: `ILIKE` / trigram similarity (correct, slower)
- **WebUI sidebar search** (`/api/sessions/search`): **Option A** — JSON substring, documented limits (joint PR); **Option B** FTS — §17.1 separate PR

### 13.5 Schema Migration for Existing Users

When a user switches from SQLite to PostgreSQL:
1. Export from SQLite → JSON (using existing `SessionDB.export_all()`)
2. Import JSON → PostgreSQL (new import tool)
3. Or: Use a migration script that reads SQLite and writes PostgreSQL using both backends simultaneously

---

## 14. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Breaking existing SQLite users | HIGH | Phase 1 is pure refactoring; all tests must pass; zero config change |
| SQLAlchemy dependency creep | MEDIUM | Only imported when PG configured; in separate extras |
| Performance regression in SQLite path | HIGH | SQLiteBackend preserves exact same sqlite3 code; benchmark before merging |
| Dialect SQL bugs (PG) | MEDIUM | Run full test suite against each backend in CI; start with subset of tests |
| Redis unavailability breaks gateway | MEDIUM | Graceful fallback to memory backend on Redis connection failure |
| Transaction semantics differ across DBs | MEDIUM | Abstract transaction handling in StorageBackend; test concurrent writes per backend |
| Connection pool exhaustion | LOW | Configurable pool size; sensible defaults; monitoring hooks |
| Member data migration from SQLite → PG | LOW | Export/import tool; documented procedure; validate with checksums |

---

## 15. Resolved Design Decisions

### 15.1 Kanban DB → Merge into Main Storage Backend

**Decision**: The kanban module (~7440 lines, `intellect_cli/kanban_db.py`) will be folded into the main storage backend.

**Current state**:
- `kanban_db.connect()` creates a fresh connection per operation with cross-process init locking
- Own schema management (`init_db`, `_migrate_add_optional_columns`)
- `connect_closing()` context manager to prevent FD leaks
- Separate DB file: `~/.intellect/kanban.db` (or per-board files)

**Target state**:
- Kanban tables live in the same database as sessions/members/projects
- `KanbanRepository` uses the same `StorageBackend` instance — no separate connection management
- Schema managed by the same DDL reconciliation/migration system
- For SQLite: kanban tables live in **`state.db` only** (no long-term `kanban.db` + ATTACH) — migrate via `intellect db migrate-kanban` (T1 ✅)
- For PG: kanban tables go into a `kanban` schema within the same database

**Migration path**:
1. Create `KanbanRepository` wrapping the storage backend
2. Port schema from raw SQL to canonical definition (shared with other tables)
3. Migrate ~30 call sites from `kanban_db.connect()` to `KanbanRepository`
4. Remove `kanban_db.connect()` and `connect_closing()`

### 15.2 Response Store → Detailed Analysis

**Current state of ResponseStore** (`api_server.py:342`):

| Aspect | Detail |
|--------|--------|
| Purpose | Persist Responses API state across gateway restarts for `previous_response_id` chaining |
| Schema | `responses(response_id, data JSON, accessed_at)` + `conversations(name, response_id)` |
| Capacity | LRU eviction, max 100 entries |
| Data stored | Full `{response, conversation_history, instructions, session_id}` per response |
| Data sensitivity | HIGH — contains full conversation history with tool payloads, prompts, results |
| Security | `chmod 0o600` on DB and WAL/SHM sidecars |
| Fallback | `:memory:` SQLite if disk path unavailable |
| Lifetime | Process-scoped (one instance per `APIServerAdapter`) |

**Key observation**: The ResponseStore is fundamentally a **cache**, not a primary data store.
- All conversation history already lives in `state.db`'s `messages` table
- The value is fast lookup by `response_id` and `conversation_name`
- LRU eviction means data loss is expected and designed for
- The `previous_response_id` chaining is a convenience, not a durability guarantee

**Recommendation**: Route through the configured `CacheBackend`, falling back to a table in the main storage backend.

```
If Redis cache configured:
  → ResponseStore uses RedisCache
  → response_id → data + conversation_name → response_id mappings
  → TTL-based eviction instead of LRU counting
  → Data survives gateway restart (Redis is external)

If Memory cache only:
  → ResponseStore uses a table in the main StorageBackend
  → Same SQLite behavior as today (or PG table if configured)
  → LRU eviction via accessed_at timestamp
  → chmod 0o600 equivalent for PG (row-level security or schema permissions)
```

**Rationale**: This keeps ResponseStore simple, avoids a third storage engine, and naturally upgrades when Redis is available — without making Redis a hard dependency.

### 15.3 FTS Search API — Unified Interface Analysis

**Question**: Can PG provide the same search interface and ranking as SQLite FTS5? What are the obstacles?

**Current `search_messages()` interface**:

```python
def search_messages(
    self,
    query: str,                          # FTS5 MATCH syntax
    source_filter: List[str] = None,     # filter by session source
    exclude_sources: List[str] = None,   # exclude session sources
    role_filter: List[str] = None,       # filter by message role
    limit: int = 20,
    offset: int = 0,
    sort: str = None,                    # None=BM25 rank, "newest"/"oldest"=timestamp
) -> List[Dict[str, Any]]:              # Returns: id, session_id, role, snippet, content,
                                         #          timestamp, tool_name, source, model,
                                         #          session_started
```

**Detailed obstacle analysis across databases**:

#### 3.1 Query Syntax Translation

| Feature | SQLite FTS5 | PostgreSQL |
|---------|-------------|------------|-------|
| Keyword search | `MATCH 'docker'` | `@@ to_tsquery('docker')` | `MATCH(col) AGAINST('docker')` |
| Boolean OR | `MATCH 'docker OR kubernetes'` | `@@ to_tsquery('docker \| kubernetes')` | `AGAINST('docker kubernetes' IN BOOLEAN MODE)` |
| Boolean AND | Implicit (space) | `@@ to_tsquery('docker & kubernetes')` | `AGAINST('+docker +kubernetes' IN BOOLEAN MODE)` |
| Boolean NOT | `MATCH 'docker NOT swarm'` | `@@ to_tsquery('docker & !swarm')` | `AGAINST('+docker -swarm' IN BOOLEAN MODE)` |
| Prefix/wildcard | `MATCH 'deploy*'` | `@@ to_tsquery('deploy:*')` | `AGAINST('deploy*' IN BOOLEAN MODE)` |
| Phrase | `MATCH '"exact phrase"'` | `@@ phraseto_tsquery('exact phrase')` | `AGAINST('"exact phrase"' IN BOOLEAN MODE)` |
| Hyphenated terms | `MATCH '"chat-send"'` (wrapped by sanitizer) | `@@ to_tsquery('chat-send')` (handled by parser) | `AGAINST('"chat-send"' IN BOOLEAN MODE)` |

**Solution**: `_translate_query_to_dialect(query, dialect)` — a method on each backend that converts the FTS5-compatible query string into dialect-specific syntax. The public API accepts a single query string; the backend translates it internally.

#### 3.2 Snippet/Highlight Generation

| Database | Built-in function | Markers | Max length | CJK capable? |
|----------|-------------------|---------|------------|-------------|
| SQLite FTS5 | `snippet(fts_table, 0, '>>>', '<<<', '...', 40)` | ✅ Configurable | Max tokens | ✅ (via trigram FTS5 table) |
| PostgreSQL | `ts_headline('english', content, query, ...)` | ✅ Configurable | Max words | ❌ (see below) |

**PostgreSQL `ts_headline` has significant obstacles**:

##### Obstacle 1: Language Configuration is Hard-Coded to a Single Language

`ts_headline` requires a text search configuration parameter (e.g., `'english'`). PostgreSQL's built-in configurations are:
- `english`, `french`, `german`, `spanish`, `simple`, etc.
- `simple` is the most generic — it splits on whitespace and punctuation, no stemming
- **None of the built-in configs handle CJK** — they expect whitespace-delimited words

Using `ts_headline('english', content, tsquery)` on Chinese text produces garbage: the English stemmer/stripper can't segment CJK ideographs, so `to_tsvector('english', '你好世界')` produces no meaningful tokens, and `ts_headline` can't highlight matches.

##### Obstacle 2: `zhparser` Extension is Not a Viable Solution

PostgreSQL has a `zhparser` extension for Chinese text segmentation, but it is:
- **Not built-in** — must be compiled from C source with SCWS (Simple Chinese Word Segmentation) library
- **Not available on managed services** — RDS, Cloud SQL, Supabase, etc. don't offer it
- **Not maintained as a trusted extension** — pgxn installs it, but it's not in the core contrib
- **Doesn't handle Japanese** (requires `mecab` extension) or **Korean** cleanly

##### Obstacle 3: `ts_headline` is Incompatible with `pg_trgm` Search

The recommended PostgreSQL search strategy uses **two parallel paths**:
- **English/European**: `tsvector`/`tsquery` with GIN index → compatible with `ts_headline`
- **CJK**: `pg_trgm` with `similarity()` or `ILIKE` → **incompatible** with `ts_headline`

`ts_headline` operates on `tsvector` columns and `tsquery` expressions. It cannot highlight matches found via `pg_trgm` similarity or `ILIKE` substring matching. This makes it unusable for the CJK search path.

##### Obstacle 4: Mixed-Language Content

intellect-agent sessions commonly contain **mixed English + CJK** content in the same message and across messages. A single search query (e.g., `Kubernetes 部署`) can't use a single `ts_headline` config — `'english'` breaks on the CJK part, `'simple'` doesn't stem "deploying/deployment/deployments" to match "部署", and neither produces useful highlights for both scripts simultaneously.

##### Obstacle 5: Performance Profile

`ts_headline` re-parses the entire document text **per result row** to generate the snippet. For the `messages` table where `content` can be large (multi-kilobyte tool outputs, code blocks, conversation turns), this means:

```sql
-- This re-tokenizes content for every matching row
SELECT ts_headline('english', content, to_tsquery('docker'))
FROM messages WHERE content_tsvector @@ to_tsquery('docker');
```

With 20 result rows and average content length of 2KB, that's 40KB of re-tokenization per query — acceptable but not free. With 20 result rows and content averaging 50KB (common in long conversations), that's 1MB of re-tokenization. Compare to SQLite FTS5's `snippet()` which reads directly from the FTS index.

##### Corrected Recommendation

**Application-level snippet generation for ALL backends** (including SQLite, replacing its `snippet()` call to ensure consistent behavior):

```python
# agent/storage/snippet.py

def generate_snippet(
    content: str,
    query_terms: list[str],
    max_tokens: int = 40,
    start_marker: str = ">>>",
    end_marker: str = "<<<",
    ellipsis: str = "...",
) -> str:
    """Generate a highlighted snippet from *content* matching *query_terms*.

    Works identically across all storage backends.  No dialect-specific
    SQL functions needed.

    Algorithm:
    1. Find all match positions for each query term (case-insensitive,
       CJK substring-aware — no token boundary requirement)
    2. Score each position by term density within a sliding window
    3. Select the highest-scoring window
    4. Mark up matched terms with start_marker/end_marker
    5. Add ellipsis at truncated boundaries
    """
    ...
```

**Why this is the right call**:

| Aspect | Per-DB SQL Functions | Application-Level |
|--------|---------------------|-------------------|
| Consistency | ❌ Different per dialect | ✅ Identical across backends |
| CJK support | ❌ `ts_headline` broken for CJK | ✅ Same code handles CJK and ASCII |
| Mixed-language | ❌ Single config per `ts_headline` | ✅ Term-by-term matching |
| Maintenance | ❌ 3 implementations + dialect bugs | ✅ 1 implementation, tested once |
| Performance | ~0.1ms (indexed) | ~0.2ms (Python string ops) |
| SQLite migration | Keep `snippet()` (FTS5) | Replace with shared impl (consistent) |

The performance difference (~0.1ms per snippet) is negligible — we generate at most 20 snippets per search query. The consistency gain is substantial.

#### 3.3 Ranking

| Database | Default ranking | Scale | Deterministic? |
|----------|----------------|-------|----------------|
| SQLite FTS5 | BM25 (`rank` column) | Arbitrary (higher = better) | Yes for same data |
| PostgreSQL | `ts_rank()` | 0.0–1.0 normalized | Yes for same data |
| PostgreSQL | `ts_rank_cd()` (cover density) | 0.0–1.0 normalized | Yes for same data |


**Obstacle**: Absolute ranking values differ across databases. `rank = 12.5` on SQLite vs `rank = 0.87` on PostgreSQL. This is **not a problem** because:
1. Rankings are only used for ordering within a single query, not compared across queries
2. The `sort` parameter offers timestamp-based ordering as an alternative
3. Callers don't interpret rank values — they just display results in order

**Conclusion**: Ranking is compatible. The relative ordering semantics ("most relevant first") are preserved, even though absolute values differ.

#### 3.4 CJK (Chinese/Japanese/Korean) Search

**Current SQLite approach** (sophisticated, 3-path routing):
1. **Trigram FTS5** (≥3 CJK chars per token): `messages_fts_trigram` with `tokenize='trigram'` — overlapping 3-byte sequences
2. **Short CJK LIKE fallback** (1-2 CJK chars per token): `content LIKE '%keyword%'` — substring matching
3. **Unicode61 FTS5**: For non-CJK text, the default tokenizer

**PostgreSQL equivalent** (production default: `pg_trgm` + `pg_bigm`; optional `zhparser` where ops can install it):
- `pg_trgm`: similarity / `ILIKE` with GIN (`gin_trgm_ops`) on `content`
- `pg_bigm`: 2-gram CJK-friendly matching (preferred over unmaintained `zhparser` on managed PG)
- `tsvector` / `tsquery`: English and European token search
- **Backend-internal routing** — SQLite keeps 3-path CJK logic; PG selects path per query without exposing dialect to callers

**Impact of single-path vs. multi-path**: The current SQLite code's CJK detection logic (`_contains_cjk`, `_count_cjk`, per-token length check) is a workaround for SQLite FTS5's unicode61 tokenizer limitation. PostgreSQL doesn't need this workaround — its extension model provides multiple CJK-capable parsers. The search API stays the same; the CJK routing logic becomes SQLite-specific and is hidden behind the backend.

#### 3.5 Recommendation: Unified Interface is Superior

**Keep the exact same method signature and return type across all backends.**

| Aspect | Decision |
|--------|----------|
| Method signature | Identical — `search_messages(query, source_filter, exclude_sources, role_filter, limit, offset, sort)` |
| Return type | Identical — `List[Dict]` with keys `id, session_id, role, snippet, content, timestamp, tool_name, source, model, session_started` |
| Query string | Backend-internal translation; public API unchanged |
| Snippet format | Same markers (`>>>`, `<<<`, `...`); **application-level** `generate_snippet()` on all backends |
| Ranking | Relative ordering preserved; absolute values differ (callers don't depend on them) |
| CJK | Fully supported on all backends; SQLite keeps its 3-path routing; PG use single-path |

**Why this is superior**:
1. **Zero call-site changes** — `session_search_tool.py`, API server search endpoint, CLI search commands all work unchanged
2. **Same test suite** — `test_search_messages()` runs against SQLite and PostgreSQL with the same assertions
3. **Simpler mental model** — one API, two storage implementations (SQLite + PostgreSQL)
4. **Future-proof** — if a fourth backend is added, it conforms to the same interface

The cost (snippet in application code, query translation layer) is modest and contained within each backend implementation.

### 15.4 Multi-Tenancy → Row-Level Scoping Only

**Decision**: No database-per-tenant isolation. The existing row-level scoping via `member_id`, `team_id`, `project_id` columns is sufficient.

**Rationale**:
- intellect-agent is not a SaaS platform — each deployment serves one organization
- Row-level security via `WHERE member_id = ?` is already enforced at every query site
- Database-per-tenant adds operational complexity (schema migrations × N databases, connection pool × N)
- If a deployment truly needs physical isolation, they can run separate intellect instances with separate databases

### 15.5 Read Replicas → Supported with Configuration

**Decision**: The storage backend will support read/write splitting with a configuration option.

**Design**:

```yaml
storage:
  backend: postgresql
  postgresql:
    # Primary (write) connection
    host: primary.db.internal
    port: 5432

    # Read replicas (optional)
    read_replicas:
      - host: replica1.db.internal
        port: 5432
      - host: replica2.db.internal
        port: 5432

    # Replica selection strategy
    replica_strategy: random  # random | round_robin | nearest
    # Maximum replication lag before a replica is excluded (seconds)
    max_replica_lag_seconds: 5
```

**Implementation**:
- `StorageBackend` exposes two connection getters:
  - `get_write_connection()` → primary
  - `get_read_connection()` → replica (or primary if no replicas configured)
- `execute()` (read operations) uses `get_read_connection()`
- `execute_write()` (write operations) always uses `get_write_connection()`
- Replica health check: `SELECT pg_is_in_recovery()` / `SHOW SLAVE STATUS` on connection checkout
- Lag monitoring: `SELECT extract(epoch FROM (now() - pg_last_xact_replay_timestamp()))` — replica excluded if lag > `max_replica_lag_seconds`

**SQLite behavior**: `read_replicas` config is ignored. Single connection for reads and writes.

### 15.6 Backup/Restore → Standard Interface

**Decision**: The storage backend will provide a standard backup/restore interface.

**Interface**:

```python
class StorageBackend(ABC):
    @abstractmethod
    def backup(self, target_path: Path) -> BackupResult:
        """Create a consistent backup to *target_path*.
        
        Returns BackupResult with backup_path, size_bytes, timestamp, checksum.
        """
        ...

    @abstractmethod
    def restore(self, source_path: Path, *, dry_run: bool = False) -> RestoreResult:
        """Restore from a backup at *source_path*.
        
        When dry_run=True, validates the backup without applying it.
        """
        ...

    @abstractmethod
    def list_backups(self, backup_dir: Path) -> list[BackupResult]:
        """List available backups in *backup_dir*."""
        ...
```

**Per-backend implementation**:

| Backend | Backup method | Restore method | Consistency |
|---------|--------------|----------------|-------------|
| SQLite | `sqlite3.backup()` API or `VACUUM INTO 'path'` | Copy backup file over state.db | Single-writer lock ensures consistency |
| PostgreSQL | `pg_dump --format=custom` via subprocess | `pg_restore --clean --if-exists` | Snapshot-consistent via `pg_dump` |

**CLI integration**:
```bash
# Backup
intellect db backup --output ~/backups/intellect-2026-06-02.dump

# Restore
intellect db restore ~/backups/intellect-2026-06-02.dump --dry-run
intellect db restore ~/backups/intellect-2026-06-02.dump

# List backups
intellect db backups --dir ~/backups/
```

**Cross-backend migration** (SQLite → PG):
```bash
# Export from SQLite
intellect db backup --backend sqlite --output /tmp/intellect-backup.json

# Import to PostgreSQL
intellect db restore /tmp/intellect-backup.json --backend postgresql
```

The export/import path uses JSON as an intermediate format (leveraging the existing `SessionDB.export_all()`), allowing migration between any two backends.

### 15.7 Graphiti Integration as Direct Agent Memory

**Question**: Should intellect-agent adopt Graphiti (by GetZep) as a direct memory layer?

**What is Graphiti**: An Apache 2.0 open-source Python framework for building **temporally-aware knowledge graphs** designed specifically as an AI agent memory layer. It is the core technology behind Zep.

**Key properties**:

| Property | Graphiti | Current intellect Memory |
|----------|----------|--------------------------|
| Data model | Bi-temporal knowledge graph (entity + relation + timestamps) | Flat message history + plugin-based semantic memory |
| Updates | Incremental, real-time | Per-turn sync (plugin-dependent) |
| Query latency | ~300ms P95 (no LLM at query time) | Plugin-dependent (Honcho, Hindsight, etc.) |
| Temporal queries | Native ("what did I know about X last month?") | Manual via timestamp filters |
| Contradiction handling | Temporal invalidation (old edges expire, not overwritten) | Plugin-dependent |
| Custom entities | Pydantic models for domain ontologies | Not supported |
| Database backends | FalkorDB (primary, recommended) | SQLite |

#### FalkorDB as Graphiti's Backend

**Decision**: Use **FalkorDB** as the primary and recommended backend for Graphiti.

FalkorDB is a Redis-based graph database (originally RedisGraph) that stores graphs as **compressed sparse adjacency matrices** using GraphBLAS. Key advantages for intellect-agent:

| Property | FalkorDB | Neo4j (alternative) |
|----------|----------|---------------------|
| Performance (P50) | 55ms | 577ms (10.5× slower) |
| Cold start | 1.1ms | 90ms+ |
| Memory (same dataset) | 496MB | 2,668MB (5.4× less efficient) |
| QPS (8 threads) | 6,693 | ~1,000 (6.7× less) |
| Multi-tenancy | Native (10,000+ isolated graphs per instance) | Limited (Enterprise only) |
| License | SSPLv1 (open source) | GPLv3 Community / Commercial Enterprise |
| Embedded mode | `falkordblite` (pip install, no server needed) | None (always requires server) |
| Python client | `falkordb` + `falkordb.asyncio` (MIT license) | `neo4j` driver |
| Graphiti support | **Default and recommended backend** (since Graphiti v1.0.0) | Supported, legacy default |
| Kubernetes | Helm chart + KubeBlocks operator | Mature ecosystem but heavier |

**Why FalkorDB over Neo4j**:
1. **Graphiti's default backend** — Zep (Graphiti's creator) made FalkorDB the default in v1.0.0 after extensive performance comparisons. This validates the choice from Graphiti's own team.
2. **Memory efficiency** — 5-6× less RAM for the same knowledge graph. Critical for deployments that run PostgreSQL + FalkorDB + Redis on the same host.
3. **Embedded mode** — `pip install falkordblite` gives a zero-config embedded graph database for local development and single-user deployments. This is the graph equivalent of SQLite in the storage layer.
4. **Multi-tenancy** — Each intellect member/team/project can have an isolated graph within the same FalkorDB instance. Neo4j requires Enterprise edition for this.
5. **Cold start** — 1.1ms vs 90ms+. Graphiti initializes its graph schema on first use; FalkorDB's instant start eliminates a noticeable delay.

#### Integration Options

#### Option A: Plugin (MemoryProvider)

Follow the existing `plugins/memory/` pattern.

```
plugins/memory/graphiti/
├── plugin.yaml
├── __init__.py              # register(ctx) → MemoryProvider
├── provider.py               # GraphitiMemoryProvider(MemoryProvider)
└── cli.py                    # Optional CLI commands
```

**Pros**: Zero architectural change. Works with existing `MemoryManager.add_provider()` flow. User configures via `memory.provider: graphiti`.

**Cons**: Graphiti's continuous incremental update capability is underutilized in a per-turn sync interface.

#### Option B: Direct Memory Backend

Graphiti sits alongside `SessionDB` as a first-class memory store via a `MemoryBackend` ABC.

**Pros**: Deep integration. Temporal queries become first-class. Graph traversal replaces keyword search for entity lookups.

**Cons**: Large architectural change. FalkorDB becomes a hard dependency for graph features.

#### Option C: Hybrid — Plugin with Deep Hooks (Recommended)

Graphiti as a MemoryProvider plugin with extended hooks:

```python
class GraphitiMemoryProvider(MemoryProvider):
    def prefetch(self, query: str) -> str: ...
    def sync_turn(self, user_msg, assistant_msg) -> None: ...
    def on_message_append(self, session_id, role, content) -> None: ...
    def on_entity_query(self, entity_name: str, at_time: datetime = None) -> dict: ...
    def on_relation_query(self, entity_a: str, entity_b: str) -> list[str]: ...
```

**Recommendation**: **Option C**. Preserves the plugin architecture, enables deep integration via hooks, and keeps FalkorDB optional.

| Phase | Scope |
|-------|-------|
| G1 (v0.17) | Add `on_message_append` hook to `SessionDB` and `MemoryManager` |
| G2 (v0.19) | Implement `GraphitiMemoryProvider` as `plugins/memory/graphiti/` with FalkorDB backend |
| G3 (v0.20) | Add `on_entity_query`, `on_relation_query` extended hooks |
| G4 (v0.21) | Optional: Graphiti as direct `MemoryBackend` for FalkorDB-native deployments |

**Dependency**: `pip install graphiti-core[falkordb]`. For embedded/development use: `pip install falkordblite` (no external server needed — equivalent to SQLite's role in the storage layer).

**Deployment modes**:

| Mode | Graphiti Backend | Database Process | Use Case |
|------|-----------------|-----------------|----------|
| Embedded | `falkordblite` | In-process (pip install only) | Local dev, single-user CLI |
| Standalone | `falkordb` | Docker container or managed | Single-server production |
| High-availability | `falkordb` + Redis Sentinel | Clustered | Multi-node production |

### 15.8 RAG and GraphRAG Integration

**Question**: How should RAG, especially GraphRAG, be integrated? Is the Plugin pattern appropriate?

#### 15.8.1 Current State

intellect-agent has **no RAG or GraphRAG implementation**. The closest capabilities are:
- **FTS5 search** over session messages (`search_messages()`) — keyword/semantic search over conversation history
- **Memory provider plugins** — Honcho, Hindsight, Holographic, etc. — provide semantic retrieval but not RAG over external documents
- **Tool-based web search** — Brave, Exa, Firecrawl, Tavily, etc. — real-time search but no indexing/retrieval pipeline

#### 15.8.2 What RAG/GraphRAG Brings

| Capability | Traditional RAG | GraphRAG | Value for intellect-agent |
|-----------|----------------|----------|--------------------------|
| Document indexing | ✅ Chunk → embed → vector DB | ✅ Chunk → extract entities → graph + vectors | Index project docs, codebases, knowledge bases |
| Retrieval | Semantic similarity | Graph traversal + community summaries | Find related concepts across sessions |
| Query type | "What does doc X say about Y?" | "What are the themes across all documents?" | "How does this project relate to that team's work?" |
| Global understanding | ❌ | ✅ Community detection + summarization | Cross-session insight discovery |
| Incremental updates | ✅ (add chunks) | ❌ (full recomputation) | Real-time knowledge ingestion |

**Key insight**: GraphRAG and Graphiti solve complementary problems:
- **Graphiti** → **agent memory** (episodic, temporal, "what happened when")
- **GraphRAG** → **document understanding** (static corpora, "what do these documents say")

They can share the same FalkorDB instance but serve different use cases.

#### 15.8.3 Integration Approaches

**Approach 1: RAG as a Tool**

```
RAG exposed as a tool (like web_search, file_read, etc.)
├── rag_search(query, collection) → ToolDefinition
├── rag_index(path_or_url, collection) → ToolDefinition
└── rag_list_collections() → ToolDefinition
```

The LLM decides when to use RAG just like any other tool. Simple, familiar pattern.

**Pros**: Minimal architecture change, works with any RAG backend, agent-controlled retrieval.
**Cons**: LLM must decide to invoke RAG (can miss relevant context), no automatic pre-fetch.

**Approach 2: RAG as a Context Engine**

```
Context Engine pipeline (runs before every agent turn):
├── 1. Memory prefetch (MemoryProvider.prefetch)
├── 2. RAG prefetch (RAGProvider.prefetch)        ← NEW
├── 3. Skill context (active skills)
└── 4. System prompt assembly
```

RAG becomes part of the automatic context assembly, alongside memory. The agent doesn't "decide" to use RAG — relevant documents are always injected.

**Pros**: Always-on, no tool-call overhead, guaranteed context injection.
**Cons**: Every turn triggers a RAG query (latency cost), harder to tune relevance.

**Approach 3: RAG as a Plugin (Recommended)**

Follow the existing memory plugin pattern with a parallel `RAGProvider` abstraction:

```
plugins/rag/
├── __init__.py              # RAGProvider ABC + discovery
├── basic/                    # Simple chunk+embed RAG
│   ├── plugin.yaml
│   └── provider.py
├── graphrag/                 # Microsoft GraphRAG-style
│   ├── plugin.yaml
│   └── provider.py
├── langchain_graphrag/       # langchain-graphrag wrapper
│   ├── plugin.yaml
│   └── provider.py
└── lightrag/                 # LightRAG (lightweight GraphRAG)
    ├── plugin.yaml
    └── provider.py
```

```python
class RAGProvider(ABC):
    """Abstract RAG provider — parallel to MemoryProvider."""

    @abstractmethod
    def initialize(self, config: dict) -> None: ...
    @abstractmethod
    def index(self, source: str | Path, collection: str) -> IndexResult: ...
    @abstractmethod
    def search(self, query: str, collection: str, top_k: int = 5) -> list[RAGDocument]: ...
    @abstractmethod
    def delete_collection(self, collection: str) -> None: ...
    @abstractmethod
    def list_collections(self) -> list[str]: ...
    @abstractmethod
    def shutdown(self) -> None: ...

    # Optional hooks
    def prefetch(self, query: str, collections: list[str]) -> str | None:
        """Return context string for automatic injection, or None."""
        return None
```

**This is the recommended approach because**:
1. **Follows the established pattern** — `plugins/memory/` already proves this works
2. **Multiple backends** — Users pick `basic` (lightweight, no external deps), `graphrag` (Microsoft-style), or `lightrag` (lighter GraphRAG)
3. **Tool + Context hybrid** — Provider can expose both tool schemas and auto-prefetch
4. **Shared infrastructure** — GraphRAG's FalkorDB can be the same instance as Graphiti's (§15.7)
5. **Lazy deps** — `lightrag` pulls in its own deps; `basic` uses stdlib + numpy only

#### 15.8.4 LightRAG — A Promising Lightweight Option

[LightRAG](https://github.com/HKUDS/LightRAG) (HKU) is a lighter alternative to Microsoft GraphRAG:

| Aspect | Microsoft GraphRAG | LightRAG |
|--------|-------------------|----------|
| Indexing | LLM extracts entities → Leiden community detection → hierarchical summaries | LLM extracts entities → incremental graph update |
| Updates | Full recomputation | Incremental (like Graphiti) |
| Query | Global (map-reduce) + Local (entity traversal) | Hybrid: low-level (specific) + high-level (abstract) + graph traversal |
| Backend | FalkorDB, file-based | FalkorDB, NetworkX (in-memory), PostgreSQL, Milvus, Qdrant |
| Dependencies | Heavy (many LLM calls) | Lighter (fewer LLM calls, incremental) |
| Python API | CLI-first, no programmatic SDK | Python API first-class |

LightRAG's incremental update model is a better fit for intellect-agent's continuous operation than GraphRAG's batch recomputation.

#### 15.8.5 Shared Graph Infrastructure

When both Graphiti and GraphRAG/LightRAG are deployed, they share FalkorDB:

```
                    ┌──────────────────────────┐
                    │        FalkorDB         │
                    │                          │
                    │  ┌────────────────────┐  │
                    │  │ Graphiti namespace │  │  ← Agent episodic memory
                    │  │ (entity_*, edge_*, │  │    Temporal, incremental
                    │  │  episode_*, ...)    │  │
                    │  └────────────────────┘  │
                    │                          │
                    │  ┌────────────────────┐  │
                    │  │ RAG namespace      │  │  ← Document knowledge
                    │  │ (doc_*, chunk_*,   │  │    Batch or incremental
                    │  │  community_*, ...)  │  │
                    │  └────────────────────┘  │
                    └──────────────────────────┘
```

Both namespaces coexist in the same FalkorDB database. Cross-namespace queries (e.g., "what documents relate to this conversation entity?") become possible via FalkorDB's openCypher queries.

#### 15.8.6 Recommendation

| Phase | Scope |
|-------|-------|
| R1 (v0.18) | `RAGProvider` ABC + `plugins/rag/` discovery (parallel to `plugins/memory/`) |
| R2 (v0.18) | `basic` RAG provider (chunk + embed + FAISS/Chroma, no external deps beyond `numpy`) |
| R3 (v0.19) | `lightrag` provider (LightRAG integration, incremental graph indexing) |
| R4 (v0.20) | `graphrag` provider (Microsoft GraphRAG for static corpus analysis) |
| R5 (v0.21) | Shared FalkorDB infrastructure: Graphiti + RAG coexist, cross-namespace queries |

### 15.9 Containerization and Kubernetes Deployment

**Question**: How should intellect-agent support containerized and Kubernetes deployments, especially with the new multi-DB/cache/event infrastructure?

#### 15.9.1 Current State

> **Note:** Helm/K8s chart work is **deferred** from the joint PR (§17). This section records the target architecture only.

| Asset | Status |
|-------|--------|
| Dockerfile | ✅ Multi-stage, s6-overlay supervision, multi-arch (amd64/arm64) |
| docker-compose.yml | ✅ Single-service, host networking, volume mount |
| Kubernetes manifests | ❌ None (joint PR: document external PG/Redis only) |
| Helm chart | ❌ None (deferred — §17) |
| Service discovery | ❌ Hardcoded `localhost` for DB/cache/events |
| Health checks | Partial — `/v1/health` endpoint exists but no K8s probes |
| Graceful shutdown | Partial — gateway has shutdown hooks but no SIGTERM handling |
| Config from env vars | ✅ Extensive env var support for all settings |

#### 15.9.2 Target Kubernetes Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Namespace: intellect                                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  StatefulSet: intellect-gateway                           │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐         │   │
│  │  │ gateway-0  │  │ gateway-1  │  │ gateway-N  │         │   │
│  │  │ (primary)  │  │ (replica)  │  │ (replica)  │         │   │
│  │  │ s6: main + │  │ s6: main + │  │ s6: main + │         │   │
│  │  │  profiles  │  │  profiles  │  │  profiles  │         │   │
│  │  └────────────┘  └────────────┘  └────────────┘         │   │
│  │       │               │               │                  │   │
│  │       └───────────────┼───────────────┘                  │   │
│  │                       │                                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          │                                      │
│          ┌───────────────┼───────────────┐                      │
│          │               │               │                      │
│  ┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐              │
│  │ PostgreSQL   │ │ Redis       │ │ FalkorDB                  │              │
│  │ (StatefulSet)│ │ (Sentinel)  │ │ (Standalone │              │
│  │              │ │             │ │  or Cluster) │              │
│  │ primary +    │ │ primary +   │ │              │              │
│  │ replicas     │ │ replicas    │ │ (optional)   │              │
│  └──────────────┘ └─────────────┘ └──────────────┘              │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Optional: Horizontal Pod Autoscaler (HPA)               │   │
│  │  - API Server pods scale on CPU/memory/request rate      │   │
│  │  - Gateway pods scale on session count                   │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### 15.9.3 Helm Chart Structure

```
deploy/helm/intellect-agent/
├── Chart.yaml
├── values.yaml                    # Default values (SQLite + memory cache)
├── values-postgresql-redis.yaml   # High-performance profile
├── values-k8s-full.yaml           # PG + Redis + FalkorDB full stack
├── templates/
│   ├── _helpers.tpl
│   ├── configmap.yaml             # config.yaml generated from values
│   ├── secret.yaml                # API keys, DB passwords
│   ├── statefulset.yaml           # Gateway StatefulSet
│   ├── service.yaml               # ClusterIP / LoadBalancer
│   ├── service-api.yaml           # API server service
│   ├── ingress.yaml               # Optional ingress for API server
│   ├── hpa.yaml                   # Horizontal Pod Autoscaler
│   ├── pdb.yaml                   # Pod Disruption Budget
│   ├── serviceaccount.yaml        # RBAC
│   └── NOTES.txt                  # Post-install instructions
└── README.md
```

#### 15.9.4 Key Values

```yaml
# values.yaml (default — works out of the box like today)
replicaCount: 1

image:
  repository: ghcr.io/ontoweb/intellect-agent
  tag: latest
  pullPolicy: IfNotPresent

# Storage backend selection
storage:
  backend: sqlite  # sqlite | postgresql
  postgresql:
    host: ""       # If empty, deploy a PG StatefulSet
    port: 5432
    database: intellect
    poolSize: 5
    maxOverflow: 10

# Cache backend
cache:
  backend: memory  # memory | redis
  redis:
    host: ""
    port: 6379

# Event bus
events:
  backend: memory  # memory | redis

# Graph memory (optional)
graphiti:
  enabled: false
  backend: falkordb     # falkordb | falkordblite (embedded)
  falkordb_host:
    host: ""
    port: 7687

# GraphRAG (optional)
rag:
  enabled: false
  provider: lightrag  # basic | lightrag | graphrag

# Horizontal scaling
autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70

# API Server
apiServer:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  ingress:
    enabled: false
    className: nginx
    tls: []

# Persistence
persistence:
  enabled: true
  size: 10Gi
  storageClass: ""

# Probes
livenessProbe:
  httpGet:
    path: /v1/health
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 30
readinessProbe:
  httpGet:
    path: /v1/health/detailed
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 10
```

#### 15.9.5 Database Deployment Strategies

| Database | Strategy | Rationale |
|----------|----------|-----------|
| **SQLite** | EmptyDir volume or hostPath | Default, zero-dependency. Single replica only (WAL doesn't work over NFS). |
| **PostgreSQL** | External (RDS/Cloud SQL) or bundled StatefulSet | For production. External is recommended; bundled is for dev/demo. |
| **Redis** | External (ElastiCache/Memorystore) or bundled Deployment | Same as PG. External for production. |
| **FalkorDB** | External (FalkorDB Cloud) or bundled StatefulSet | Only when Graphiti or GraphRAG is enabled. External recommended. |

**Guidance**: The Helm chart should **NOT** bundle databases by default. It should:
1. Generate `config.yaml` pointing to user-provided external services
2. Offer optional sub-charts (Bitnami PostgreSQL, Bitnami Redis) for quick-start/demo deployments
3. Document the external service approach as the recommended production path

#### 15.9.6 Key Design Decisions

**1. Gateway as StatefulSet, not Deployment**

The gateway maintains in-process state (agent cache, run status, SSE event queues). When Redis is configured (§15.4), this state is externalized and the gateway becomes stateless — at which point it can be a Deployment. But for backward compatibility (SQLite + memory cache), it must be a StatefulSet.

```
StatefulSet (default) → stable network identity, persistent volume
Deployment (when Redis cache + Redis events) → stateless, freely scalable
```

**2. Config via ConfigMap + Secrets**

`config.yaml` is generated from Helm values and mounted as a ConfigMap. Secrets (API keys, DB passwords, OAuth client secrets) are stored in Kubernetes Secrets and referenced via environment variables.

**3. s6-overlay remains PID 1**

The existing s6-overlay supervision architecture (PID 1 = `/init`) maps cleanly to Kubernetes:
- `s6-svscan` handles subprocess supervision (main process + per-profile gateways)
- SIGTERM → s6 propagates to all services for graceful shutdown
- Readiness probe checks all supervised services are up

**4. Per-profile gateways run inside the same pod**

s6 manages per-profile gateway processes as supervised children. This avoids the complexity of a separate pod per profile. If a profile needs dedicated resources, it can be run as a separate StatefulSet with its own `config.yaml`.

#### 15.9.7 Container Image Extensions

The existing Dockerfile gains optional layers for new backends:

```dockerfile
# New build args for optional backends
ARG INCLUDE_PG=0
ARG INCLUDE_REDIS=0

# Conditionally install backend deps
RUN if [ "${INCLUDE_PG}" = "1" ]; then \
        uv pip install --no-cache-dir "sqlalchemy[asyncio]==2.0.43" "psycopg2-binary==2.9.11" "asyncpg==0.31.0"; \
    fi
RUN if [ "${INCLUDE_REDIS}" = "1" ]; then \
        uv pip install --no-cache-dir "redis[hiredis]==6.4.0"; \
    fi
```

Or, more simply, continue using `tools/lazy_deps.py` at first use — the container already supports runtime `uv pip install` via the writable `.venv`.

### 15.10 Sandbox Compatibility with New Storage Architecture

**Question**: Of the 6 sandbox types, which remain compatible with the multi-DB/cache/event architecture?

#### 15.10.1 Sandbox Inventory

| # | Sandbox | Execution Model | Network | Filesystem | State |
|---|---------|----------------|---------|------------|-------|
| 1 | **Local** | Host subprocess (`bash -c`) | Full host access | Host FS | Host filesystem |
| 2 | **Docker** | `docker exec` in container | Bridge/NAT (configurable) | Bind mounts + tmpfs | Container volume |
| 3 | **Singularity** | `apptainer exec instance://` | Host (containall) | Overlay + bind mounts | Instance overlay |
| 4 | **SSH** | SSH remote execution | Remote host's network | Remote host's FS | Remote filesystem |
| 5 | **Modal** | `modal.Sandbox.create()` + `exec()` | Cloud Sandbox network | Cloud Sandbox ephemeral + volume | Snapshot JSON |
| 6 | **Daytona** | Daytona SDK `Sandbox.process.exec()` | Cloud workspace network | Cloud workspace volume | SDK-managed |
| *(6a)* | *(Modal Managed)* | REST API via OntoWeb gateway | Gateway-proxied | Cloud Sandbox | Gateway-managed |

#### 15.10.2 Compatibility Analysis

The core question is: **can the sandbox reach the storage/cache/event backend?**

| Sandbox | SQLite | PostgreSQL | Redis | Notes |
|---------|--------|-----------|-------|-------|
| **Local** | ✅ Native | ✅ Via network | ✅ Via network | Full connectivity. WAL file locking works on local FS. |
| **Docker** | ⚠️ Conditional | ✅ Via network | ✅ Via network | SQLite needs bind mount to host `~/.intellect`. PG/Redis via `host.docker.internal` or host network. |
| **Singularity** | ❌ Not viable | ✅ Via network | ✅ Via network | `--containall --no-home` isolates FS completely. PG/Redis work via network. SQLite cannot access host `~/.intellect`. |
| **SSH** | ✅ Remote FS | ✅ Via network | ✅ Via network | SQLite is on the remote host's FS (separate instance). PG/Redis via remote host's network. |
| **Modal** | ❌ Not viable | ✅ Via network | ✅ Via network | Ephemeral cloud sandbox. Cannot access host SQLite. PG/Redis work (cloud → cloud or cloud → external). |
| **Daytona** | ❌ Not viable | ✅ Via network | ✅ Via network | Same as Modal — cloud workspace cannot reach host SQLite. |

#### 15.10.3 Detailed Analysis per Sandbox

**Sandbox 1: Local** — ✅ Fully Compatible

No change. Local execution has full access to the host filesystem and network. All backends work:
- SQLite: Direct file access to `~/.intellect/state.db`
- PostgreSQL: Network connection to `localhost:5432` or remote
- Redis: Network connection to `localhost:6379` or remote
- FalkorDB: Network connection (if configured)

**Sandbox 2: Docker** — ⚠️ Partially Compatible

Current `docker-compose.yml` uses `network_mode: host`, which gives the container full host network access. With the new architecture:

- **SQLite**: Requires bind mount of `~/.intellect:/opt/data` (already configured). WAL mode works — SQLite file locking is within the same host kernel. ✅
- **PostgreSQL / Redis / FalkorDB (via FalkorDB)**: With host networking, `localhost` resolves to the host. ✅
- **Bridge network mode**: `host.docker.internal` (Mac/Windows) or `--add-host host.docker.internal:host-gateway` (Linux Docker 20.10+) resolves to host. ✅

**Verdict**: Compatible. The existing bind mount + host network configuration covers all backends.

**Sandbox 3: Singularity** — ⚠️ Partially Compatible

Singularity uses `--containall --no-home` for isolation. The sandbox has **no access to the host filesystem** by default.

- **SQLite**: ❌ Cannot reach `~/.intellect/state.db`. Even with `--bind`, WAL locks on a host bind-mounted file from inside a Singularity container are unreliable (the container's SQLite may not see host-level locks).
- **PostgreSQL / Redis**: ✅ Network access is available (host network is shared by default in Singularity).
- **Workaround for SQLite**: Mount the state.db directory with `--bind ~/.intellect:/opt/data`. WAL locking may still fail depending on the Singularity version and configuration.

**Verdict**: Compatible **only** with network-based backends (PG + Redis). SQLite degraded.

**Recommendation**: Document that Singularity sandbox requires PostgreSQL or Redis backend for reliable state access. For SQLite-only deployments, add a `--bind` for the intellect home directory.

**Sandbox 4: SSH** — ✅ Fully Compatible

SSH execution runs commands on a remote host. The database connection is resolved on the **remote host**:

- **SQLite**: The remote host's `~/.intellect/state.db` (separate instance from the gateway). This is a **different database** — intentional for SSH sandbox isolation.
- **PostgreSQL / Redis**: The remote host connects to the configured DB/cache host. This could be the same PG/Redis as the gateway (if network-reachable) or a different instance.

**Verdict**: Compatible. The remote host resolves connections independently. No architectural change needed.

**Sandbox 5: Modal** — ⚠️ Partially Compatible

Modal sandboxes are ephemeral cloud VMs. They cannot access the host filesystem.

- **SQLite**: ❌ Modal sandbox cannot reach host SQLite. Modal's own filesystem is ephemeral (unless `snapshot_filesystem()` is used, which persists within Modal but not to the host).
- **PostgreSQL / Redis**: ✅ If PG/Redis are cloud-hosted (RDS, ElastiCache, etc.) and network-accessible from Modal's VPC, the sandbox can connect. Modal supports [VPC peering and private networking](https://modal.com/docs/guide/network).
- **Current Modal workflow**: The sandbox runs code, returns results via stdout/stderr. The gateway's agent thread writes to SessionDB. The sandbox itself doesn't need to access SessionDB directly.

**Verdict**: Compatible. The Modal sandbox doesn't access the database directly — it runs code and returns output. The agent thread on the gateway handles all database operations. No sandbox code path calls `SessionDB`.

**Sandbox 6: Daytona** — ✅ Compatible (same reasoning as Modal)

Daytona cloud workspaces cannot access the host filesystem, but like Modal, the sandbox doesn't directly access the database. The agent thread on the gateway handles all persistence.

**Verdict**: Compatible with all backends.

#### 15.10.4 Summary Matrix

| Sandbox | SQLite | PG | Redis | Recommendation |
|---------|--------|----------|-------|----------------|
| **Local** | ✅ | ✅ | ✅ | No changes needed |
| **Docker** | ✅ | ✅ | ✅ | Already configured with bind mount + host network |
| **Singularity** | ❌ | ✅ | ✅ | Require PG backend or add `--bind` for SQLite |
| **SSH** | ✅ (remote DB) | ✅ | ✅ | No changes needed |
| **Modal** | N/A (sandbox doesn't access DB) | N/A | N/A | No changes needed |
| **Daytona** | N/A (sandbox doesn't access DB) | N/A | N/A | No changes needed |

**Key insight**: Modal and Daytona sandboxes do not directly access SessionDB. They execute code and return output to the agent thread, which handles all database operations. This means the database backend change is transparent to cloud sandboxes.

#### 15.10.5 Actions Required

| Action | Priority | Scope |
|--------|----------|-------|
| Document Singularity SQLite limitation | P2 | Docs + error message if SQLite + Singularity detected |
| Add `--bind` workaround for Singularity SQLite | P3 | Singularity environment init |
| Docker bridge network: verify `host.docker.internal` resolution | P2 | Test + document |
| Cloud sandbox (Modal/Daytona): verify no code path accesses DB directly | P1 | Code audit |
| Add validation: warn on incompatible sandbox + backend combinations | P3 | Config validation at startup |

### 15.11 PostgreSQL vs MySQL — Adoption Decision

**Decision**: Support PostgreSQL as the **sole** non-SQLite relational database backend. Do not integrate MySQL.

This decision was reached after a detailed 8-dimension analysis comparing PostgreSQL and MySQL for intellect-agent's specific workload. The full analysis follows.

#### 15.11.1 Analysis Summary

| Dimension | Winner | Key Differentiator |
|-----------|--------|-------------------|
| Full-text search (CJK) | **PostgreSQL** | Extension ecosystem: `zhparser`, `pg_jieba`, `pg_bigm`, `PGroonga`. MySQL has only ngram parser. |
| JSON/unstructured data | **PostgreSQL** | `JSONB` with GIN indexing vs MySQL's text-based JSON + generated column workarounds |
| Python drivers | **PostgreSQL** | `asyncpg` is unmatched — native asyncio, speaks PG wire protocol. MySQL drivers go through C libs. |
| AI/ML ecosystem | **PostgreSQL** | `pgvector` is a decisive differentiator. No open-source vector extension exists for MySQL. |
| SQL migration from SQLite | **PostgreSQL** (slight) | `ON CONFLICT` pattern already used in codebase maps directly to PG. MySQL requires different syntax. |
| Connection pooling | Equal | PgBouncer ≈ ProxySQL. Both adequate. |
| Operational (backup/K8s) | **PostgreSQL** | Built-in WAL archiving vs MySQL's dual-log complexity. CloudNativePG is the gold standard K8s operator. |
| Licensing & vendor risk | **PostgreSQL** | PostgreSQL License (MIT-like). MySQL is Oracle-owned; community edition investment is declining. |

#### 15.11.2 pgvector — The Decisive Factor

pgvector is the single most important differentiator for an AI agent application:

- **Same database, same transaction**: `SELECT * FROM messages WHERE session_id = $1 ORDER BY embedding <=> $query LIMIT 10` — filter by metadata AND sort by semantic similarity in one query. No data synchronization between a separate vector store and the database.
- **No external vector database needed**: With MySQL, you must deploy and maintain Pinecone, Weaviate, Milvus, or Qdrant alongside the database. This adds operational complexity, cost, and a synchronization layer.
- **Ecosystem alignment**: The entire Python AI/LLM ecosystem (LangChain, LlamaIndex, pgai, pgvector-python) treats PostgreSQL as the default relational database for AI workloads. Every major framework has first-class `PGVector` support.

While intellect-agent does not currently use vector embeddings, the architecture should not preclude adding semantic search, RAG, or embedding-based memory in the future. Choosing PostgreSQL ensures this path is open. Choosing MySQL would require a separate vector store to be introduced at that point.

#### 15.11.3 Oracle's Stewardship of MySQL

Between September 2025 and January 2026, the MySQL GitHub repository had **zero commits** — the longest development pause in its 25-year history. Oracle laid off ~70 MySQL core engineers in September 2025. Oracle's investment is shifting to proprietary offerings (MySQL Enterprise, MySQL HeatWave).

Building a long-term product on a database whose open-source version is visibly declining is an unnecessary risk. PostgreSQL has no single corporate owner — it is governed by the PostgreSQL Global Development Group under a MIT-like license.

#### 15.11.4 Full-Text Search for CJK

PostgreSQL's extension model provides multiple CJK search paths:
- **zhparser** (SCWS-based Chinese word segmentation) — high precision for simplified/traditional Chinese
- **pg_jieba** (Jieba-based) — supports multiple search modes
- **pg_bigm** (2-gram) — CJK-agnostic, simple install, works for Chinese + Japanese + Korean with one extension
- **PGroonga** (Groonga engine) — fastest, real-time updates

MySQL has only the built-in **ngram parser**. It works for all CJK languages but is a blunt instrument with no linguistic awareness. There is no extension model to add zhparser or equivalent. For high-quality CJK search, MySQL users would need an external search engine (Elasticsearch, Meilisearch) — adding operational complexity.

#### 15.11.5 Conclusion

PostgreSQL is the right choice for intellect-agent's non-SQLite relational database. The decision is driven by pgvector (future-proofing for AI workloads), superior CJK full-text search, better Python driver ecosystem, lower migration complexity, and zero vendor/license risk.

MySQL users who need intellect-agent can:
1. Use SQLite (the default, works everywhere)
2. Deploy PostgreSQL alongside their existing MySQL infrastructure
3. Use the JSON-based export/import path for data migration (see §15.6)

The cost of supporting both PostgreSQL and MySQL (two SQLAlchemy dialect adapters, two sets of dialect-specific SQL, two CI pipelines, two documentation sets, ongoing divergence as features are added) is not justified by the marginal benefit. Focus resources on making the PostgreSQL integration excellent rather than splitting effort across two databases.

---

## 16. intellect-webui Joint Architecture (Approved 2026-06-05)

### 16.1 Scope

| In joint PR | Out of joint PR (§17) |
|-------------|------------------------|
| `StorageManager` / `StorageBackend` consumption | Graphiti / FalkorDB memory |
| PostgreSQL for shared `state.db` semantics | RAG / GraphRAG / LightRAG plugins |
| Redis cache + event bus for **N WebUI workers** | Helm chart / in-tree K8s manifests |
| Backup manifest incl. WebUI `sessions/*.json` | WebUI → gateway `/v1/runs` convergence |
| Members schema alignment + session isolation | Full transcript migration JSON → `messages` table |

### 16.2 High Availability Model

**Decision:** WebUI HA = **multi-process WebUI + PostgreSQL + Redis**, not gateway offload.

```
                    ┌─────────────────────────────────────┐
                    │  Load balancer / reverse proxy       │
                    └───────────┬─────────────────────────┘
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   ┌─────────────┐       ┌─────────────┐       ┌─────────────┐
   │ webui-w0    │       │ webui-w1    │       │ webui-wN    │
   │ AIAgent     │       │ AIAgent     │       │ AIAgent     │
   │ in-process  │       │ in-process  │       │ in-process  │
   │ SSE (local) │       │ SSE (local) │       │ SSE (local) │
   └──────┬──────┘       └──────┬──────┘       └──────┬──────┘
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
              ┌─────────────────────────────────────┐
              │ PostgreSQL (shared metadata/msgs)    │
              │ Redis (cache + pub/sub fan-out)      │
              └─────────────────────────────────────┘
```

- Each worker still runs `AIAgent.run_conversation()` locally (`api/streaming.py`).
- `INTELLECT_WEBUI_RUNTIME_ADAPTER` stays **`legacy-direct`** or **`legacy-journal`** by default; **`runner-local`** is optional future work, not HA-critical.
- **Forbidden topology:** `N>1` WebUI workers sharing one `state.db` on SQLite/NFS without Redis — startup must error with a clear message.

### 16.3 Event Bus Channel Map (WebUI)

| WebUI surface today | Local mechanism | Redis channel (P4b) | Payload |
|--------------------|-----------------|---------------------|---------|
| `/api/sessions/events` | in-process broadcast | `webui.sessions` | `{action: "sessions_changed", profile, member_id?}` |
| `/api/approval/stream` | `_approval_sse_subscribers` | `webui.approval.{session_id}` | `{head, total}` |
| `/api/clarify/stream` | clarify subscriber dict | `webui.clarify.{session_id}` | `{head, total}` |
| `/api/chat/stream` | per-stream thread queue | *stay local* | Full tokens stay on worker SSE; Redis only for **liveness** optional |
| `/api/sessions/gateway/stream` | EventSource | `gateway.sessions` (agent) | Unchanged agent contract |
| Kanban panel SSE | `panels.js` EventSource | `webui.kanban.{board_id}` | `{revision}` invalidate |

Workers **subscribe** on Redis and **fan in** to their local SSE subscribers so browser tabs attached to any worker receive updates.

### 16.4 Data Retention (Joint PR)

| Layer | Role in joint PR |
|-------|------------------|
| `sessions/*.json` | **Transcript source of truth** for WebUI chat |
| PostgreSQL `messages` | Agent tool `session_search`, gateway/api_server, insights sync, CLI sidebar import |
| Run journal JSONL | Per-worker replay after disconnect; not stored in Redis |
| `sync_to_insights` | Continues to mirror usage/title into PG when enabled |

Moving WebUI writes fully into `messages` is a **follow-up** (post P6), not a joint PR gate.

### 16.5 intellect-webui Work Packages

| ID | Phase | Deliverable |
|----|-------|-------------|
| W1 | P1 | `get_storage_manager(profile)`; replace direct `SessionDB()` in `state_sync`, `streaming`, handoff markers |
| W2 | P2 | ✅ PG CI job for webui; members schema fixes; `state_sync` + gateway watcher on PG |
| W2+ | P2 | ✅ CLI sidebar + `get_state_db_session_messages` / `delete_cli_session` via `storage_bridge` |
| W3 | P3 | ✅ `kanban_bridge` → `KanbanRepository`（T1）；**T6 ✅** `intellect db backup` 含 `webui/sessions/` |
| W4 | P4b | ✅ Redis pub/sub §16.3（`webui.sessions` / `webui.approval.*` / `webui.clarify.*` / `webui.kanban.{board}` / `runs.{run_id}`）；T5 multi-worker startup gate |
| W5 | P5 | **Next** — Read replica routing for session list / agent sidebar reads |
| W6 | P6 | Deprecate raw `sqlite3` in `agent_sessions.py` |

**Related plans (must stay aligned):** `intellect-webui/docs/plans/webui-agent-gap-analysis.md`, `session-member-isolation-plan.md`.

### 16.6 Multi-User Enablement & SQLite → PostgreSQL Migration

When an operator turns on multi-user (`members.enabled: true`) — via **WebUI bootstrap**, **`intellect config set`**, or setup wizard — the UI MUST offer a **storage upgrade choice** (not silent migration).

**Product default (approved 2026-06-05):** **Recommend PostgreSQL** — the primary / pre-selected option in WebUI bootstrap and setup. SQLite is labeled as a **limited** path (single process, no multi-worker HA). Operators can still choose SQLite without installing PG.

#### Enablement flow (WebUI + CLI parity)

```
Enable multi-user (members.enabled = true)
        │
        ├─► [Recommended] PostgreSQL (+ Redis when HA)  → profile: multi_user_ha
        │     • Default selected in bootstrap UI
        │     • Short copy: multi-user + OAuth + scale; needs PG (and Redis if N workers)
        │     Steps:
        │       1. Collect PG DSN (+ Redis URL if workers > 1 or “enable HA” checked)
        │       2. Dry-run: intellect db migrate-sqlite-to-pg --dry-run
        │       3. Apply migration → PG (OAuth/providers/tokens included — see below)
        │       4. Atomically update config.yaml (storage/cache/events)
        │       5. Smoke test: OAuth provider row read, token refresh, member login
        │       6. Retain state.db as state.db.pre-pg-migrate.<timestamp> backup
        │
        └─► [Advanced] Keep SQLite only  → profile: multi_user_sqlite
              • Secondary choice; confirm dialog explains limits
              • state.db unchanged; OAuth stays in SQLite
              • Inline warning: cannot run INTELLECT_WEBUI_WORKERS>1 or multi-gateway HA
```

#### WebUI bootstrap UX (W7)

| Element | Requirement |
|---------|-------------|
| **Default selection** | **PostgreSQL** radio/card selected on load |
| **Primary label** | e.g. “PostgreSQL (recommended)” — multi-user, OAuth, and scaling |
| **Secondary label** | e.g. “SQLite only (single process)” — link to doc limits |
| **Redis block** | Shown when PG selected; auto-check “Enable Redis” if user sets workers > 1 |
| **PG fields** | DSN or host/port/db/user; test connection before migrate |
| **Migrate CTA** | “Test & migrate” runs dry-run; success enables bootstrap completion |
| **Skip PG** | Explicit “Continue with SQLite” — no dark pattern; one confirmation step |

**Copy principles:** Do not imply PG is mandatory for `members.enabled`, but make clear it is the **supported path** for teams, OAuth at scale, and multiple WebUI workers.

**CLI:** `intellect members bootstrap` and `intellect setup` — default `--storage postgresql` when stdin is a TTY; `--storage sqlite` opt-out. Non-interactive: require explicit flag (no silent PG).

#### Data copied SQLite → PG (required)

All rows in the active profile’s `state.db` that back **runtime** behavior, including:

| Domain | Tables / data (non-exhaustive) |
|--------|--------------------------------|
| **OAuth / providers** | `oauth_providers`, `oauth_tokens`, `oauth_pool_entries`; provider `extra_metadata` (model endpoint cache, etc.) |
| **Members / RBAC** | `members`, `member_invites`, `member_sessions`, `member_admin_audit_log`, … |
| **Teams / projects** | `teams`, `team_memberships`, `projects`, `project_memberships`, `project_tokens`, … |
| **Sessions (agent)** | `sessions`, `messages`, FTS side tables / PG `tsvector` rebuild |
| **Kanban** | Kanban tables (in `state.db` after T1 ✅) |
| **Other state** | `identities`, `secret_access_log`, cron/gateway-persisted rows in `state.db` |

**Not migrated to PG by this tool** (unchanged truth sources):

| Data | Reason |
|------|--------|
| `sessions/*.json` (WebUI chat) | §16.4 — JSON remains transcript source |
| `config.yaml`, `.env` | Files stay on disk; migration **updates** `storage`/`cache`/`events` keys only |
| `~/.intellect/skills/`, member dirs | Filesystem layout unchanged |
| Legacy `auth.json` | Migration to DB should already have run (`auth_json_migration`); tool re-exports if rows missing |

#### OAuth & “basic runtime parameters” invariant

After cutover to PG:

- **Login OAuth** (WebUI `/api/members/oauth/*`, gateway) reads `oauth_providers` / `oauth_tokens` from **PG** via `StorageManager` — same content as pre-migrate SQLite.
- **Model / Codex-style tokens** stored per `oauth_providers.extra_metadata` and `oauth_tokens` must round-trip; post-migrate automated check: `intellect doctor --storage` includes OAuth row counts + one provider smoke read.
- **Encrypted fields** (`client_secret_encrypted`, `access_token_encrypted`, …) are **byte-copied**; encryption keys remain profile-local (no re-encrypt unless key rotation is a separate operation).

#### Config updates on PG path (atomic)

Written in one transaction to `config.yaml` (and env mirrors if used):

```yaml
members:
  enabled: true
storage:
  backend: postgresql
  postgresql: { dsn: "..." }
cache:
  backend: redis   # when HA or WEBUI_WORKERS>1
events:
  backend: redis
```

Rollback: restore `state.db` backup + revert config to `storage.backend: sqlite`.

#### Work packages (add to joint PR)

| ID | Phase | Deliverable |
|----|-------|-------------|
| **W7** | P2 | ✅ WebUI bootstrap: **PG recommended (default)** + SQLite advanced path; migrate dry-run |
| **W8** | P2 | ✅ `intellect db migrate-sqlite-to-pg` + `intellect doctor --storage` |
| **A-MIG** | P2 | ✅ Migration module: table-ordered copy, checksum report; PG DDL strips `--` comments before `;` split (fixes `invite; no secrets` fragment bug) |

#### Operator runbook (verified 2026-06-05)

Prerequisites: PostgreSQL reachable; DSN in `storage.postgresql.dsn` or `INTELLECT_PG_DSN` in `~/.intellect/.env`.

1. **Install PG deps** (from intellect-agent repo root, same venv as `intellect` CLI):
   ```bash
   uv pip install -e ".[db-postgresql]"
   # or, without editable install:
   uv pip install \
     'sqlalchemy[asyncio]>=2.0.43,<3' \
     'psycopg2-binary>=2.9.11,<3' \
     'asyncpg>=0.31.0,<0.32' \
     'alembic>=1.16.4,<2'
   ```
2. **Dry-run:** `intellect db migrate-sqlite-to-pg --dry-run` — review per-table checksums.
3. **Apply:** `intellect db migrate-sqlite-to-pg --apply-config` — copies §16.6 tables, sets `storage.backend=postgresql`, leaves `state.db.pre-pg-migrate.<timestamp>` beside the old SQLite file.
4. **Verify:** `intellect doctor --storage` — PG connectivity, OAuth row counts, provider smoke read.
5. **WebUI:** restart the server; confirm agent/CLI sessions appear in the sidebar and messages load from PG.

Rollback: restore `config.yaml` to `storage.backend: sqlite` and swap `state.db` from the `.pre-pg-migrate.*` backup.

---

## 17. Deferred Track (Not in Joint PR)

Graphiti, RAG/GraphRAG, and Helm/K8s remain **approved directions** (§15.7–§15.9) but ship in **separate PRs** after the storage/cache/event joint milestone stabilizes.

| Track | Former phase | New home | Notes |
|-------|--------------|----------|-------|
| `RAGProvider` + `plugins/rag/` | P3 | Post P4 | No blocker for WebUI HA |
| Graphiti + `on_message_append` hook | P4 | Post P4 | Optional `falkordblite` for dev |
| LightRAG / MS GraphRAG providers | P4–P5 | Post P5 | |
| `deploy/helm/intellect-agent/` | P5 | Ops repo or later PR | Document external PG/Redis instead |
| Shared FalkorDB (Graphiti + RAG) | P6 | Graph track | |
| WebUI sidebar v2 FTS (Option B) | — | **Follow-up PR** (§17.1) | **Option A (substring) = joint PR** |

Rationale: joint PR already spans two repositories and two persistence styles; adding graph DBs and chart maintenance would dilute review and delay HA validation.

### 17.1 WebUI Sidebar Search

**Approved split (2026-06-05):**

| Option | Name | When | Status |
|--------|------|------|--------|
| **A** | **侧栏保持轻量子串** | **Joint PR (this train)** | **Deliver now** — no FTS wiring in sidebar |
| **B** | **侧栏 v2 接 FTS** | **Separate PR** after P4 stable | **Ready** — P4 ✅; open follow-up when prioritized (§17.1 gates) |

#### Option A — joint PR deliverable

`GET /api/sessions/search` (`api/routes.py` `_handle_sessions_search`) **stays** as today:

- Title: case-insensitive substring on `session.title`
- Body (when `content=1`): substring on the last *N* messages from **`sessions/*.json`** (`depth`, default 5)
- Member scope: existing `apply_member_scope_to_session_rows()` on the session list
- **Does not** call `search_messages()` / FTS5 / PostgreSQL FTS

**Joint PR docs/tests (light touch):**

1. User-facing note: sidebar search is for **quick session discovery**, not full-history semantic/FTS search (use in-chat `session_search` / agent tools for that).
2. Document known limits: no boolean/FTS query syntax, no BM25 rank, shallow `depth`, CJK = contiguous substring only.
3. Regression tests unchanged unless member-scope or routing changes in the same PR.

#### Option B — follow-up PR only (not in scope now)

After storage/cache joint PR (target: **post P4**), a **separate PR** may add sidebar v2, e.g. `?mode=fts` or a dedicated endpoint calling `search_messages()` via `StorageManager`, with FTS snippets/rank in the UI.

**Decision gates for Option B PR** (when opened):

1. Must sidebar results match `session_search` for the same query?
2. Full transcript history vs keep shallow list filter?
3. When `sync_to_insights` is off, how to handle content only in JSON?

§13.4 / §15.3 **agent FTS unification** (SQLite ↔ PostgreSQL) applies to agent call sites in the joint PR; it does **not** require sidebar changes until Option B.

---

## 18. Summary

This design evolves **intellect-agent + intellect-webui** storage, cache, and events in one coordinated release train. Graph/memory/K8s packaging follows in §17.

### Master Phase Plan (Joint PR)

| Phase | Version | Dimension | Key Deliverables |
|-------|---------|-----------|-----------------|
| **P1** | v0.16 | Storage Foundation | ABCs, SQLiteBackend, **Phase A repos**, WebUI `StorageManager` factory |
| **P2** | v0.17 | PostgreSQL | PG backend, members schema alignment, WebUI `state_sync` on PG |
| **P3** | v0.18 | Kanban + Backup | Kanban merge, backup incl. WebUI JSON, `kanban_bridge` port |
| **P4** | v0.19 | Redis | ✅ **4a** gateway/api_server + **4b** multi-process WebUI pub/sub (`v0.4.2`) |
| **P5** | v0.20 | Read Replicas | **Next** — PG replica routing (agent + webui reads) |
| **P6** | v0.21+ | Repository cleanup | Phase B services; deprecate direct SessionDB |
| *(ongoing)* | — | Sandbox compat | Modal/Daytona audit; Singularity SQLite doc |

### Resolved Design Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Kanban DB | **Merge** into main storage backend |
| 2 | Response Store | **CacheBackend → DB fallback** (§15.2) |
| 3 | FTS Search API | **Unified agent API** + app-level snippets (§15.3); WebUI sidebar **Option A (substring) in joint PR**; **Option B (FTS) §17.1** separate PR |
| 4 | Multi-tenancy | **Row-level scoping only** |
| 5 | Read replicas | **Supported** with lag monitoring |
| 6 | Backup/Restore | **DB + WebUI session files** in one manifest (§12.3) |
| 7 | Graphiti memory | **Deferred** — §17 (design unchanged in §15.7) |
| 8 | RAG/GraphRAG | **Deferred** — §17 (design unchanged in §15.8) |
| 9 | K8s / Helm | **Deferred** — §17; external PG/Redis documented |
| 10 | Sandbox compatibility | PG/Redis network sandboxes OK; Singularity SQLite limited (§15.10) |
| 11 | PostgreSQL vs MySQL | **PostgreSQL only** (§15.11) |
| 12 | Graphiti DB backend | **FalkorDB** when graph track ships (§17) |
| **13** | **intellect-webui in scope?** | **Yes** — joint PR (§16) |
| **14** | **WebUI HA model** | **Multi-process WebUI + PG + Redis**; in-process agent kept (§16.2) |
| **15** | **Graphiti/RAG/Helm in joint PR?** | **No** — §17 deferred track |
| **16** | **WebUI transcript store** | **JSON primary** in joint PR; PG mirrors for agent/shared features (§16.4) |
| **17** | **WebUI sidebar search** | **Option A (substring) in joint PR**; **Option B (FTS) separate PR** (§17.1) |
| **18** | **P2 gates M1–M3** | **M1=A** hex-only new members; **M2=B** `joined_at` pending; **M3=strict defaults** — [brief](2026-06-05-p2-gates-m1-m3-decision-brief.md) |
| **19** | **T1–T12 + M4** | ✅ [remaining-decisions](2026-06-05-joint-pr-remaining-decisions.md) — Kanban→`state.db`, Redis db0/1, no P4 chat liveness, tarball backup, `INTELLECT_WEBUI_WORKERS`, `member_sessions`, etc. |
| **20** | **Dual SQLite+PG in one product** | Single-user = SQLite only; multi-user = operator choice SQLite **or** PG+Redis (§10.3) |
| **21** | **Multi-user → PG migration** | OAuth/providers/tokens + agent session state copied; config switched atomically (§16.6) |
| **22** | **Multi-user storage UX** | **Recommend PostgreSQL** (pre-selected in bootstrap); SQLite optional with limits (§16.6) |

### Key Architectural Principles Preserved

- **Backward compatibility**: Default install = SQLite + memory, single WebUI process, zero config change
- **Lazy dependencies**: `sqlalchemy`, `redis` only via optional extras when configured
- **Sync agent loop unchanged** in both CLI and WebUI workers
- **Single primary database** for agent-shared tables (not full WebUI transcript in v1)
- **Plugin architecture** for future Graphiti/RAG — not part of joint PR delivery

### Joint PR Infrastructure Map

```
  intellect-webui (N workers)          intellect-agent (gateway/CLI)
         │                                      │
         ├──────── sessions/*.json (transcript) │
         │                                      │
         └──────────────┬───────────────────────┘
                        ▼
              ┌─────────────────────┐
              │ StorageBackend       │
              │ SQLite | PostgreSQL  │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ Cache + EventBus     │
              │ memory | Redis       │
              └─────────────────────┘
```

### Next Steps (post P4, 2026-06-06)

1. **P5 / W5:** PG read-replica routing for session list, sidebar import, and other read-heavy paths
2. **OAuth §9 QA:** `auth-json-deprecation` + `oauth-db-only` E2E checklist — [oauth-follow-up-tasks.md](oauth-follow-up-tasks.md)
3. **WebUI product gaps:** team/project admin roles, Teams/Projects panels — [intellect-webui `docs/plans/README.md`](../../intellect-webui/docs/plans/README.md)
4. **Sidebar search Option B (§17.1):** FTS sidebar v2 — separate PR after P4 stable
5. Track Graphiti/RAG/Helm in §17 backlog — do not expand joint PR scope
6. Phased checklist: [2026-06-05-joint-pr-remaining-decisions.md](2026-06-05-joint-pr-remaining-decisions.md) (P1–P4 ✅; P5 open)
