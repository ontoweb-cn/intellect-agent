# Graphiti Memory Plugin — Gap Analysis

## 1. Existing Memory Plugin Mechanism (Reference)

### 1.1 Plugin Discovery

```
plugins/memory/__init__.py :: discover_memory_providers()
  → Scans plugins/memory/<name>/ (bundled) + $INTELLECT_HOME/plugins/<name>/ (user)
  → Each dir must have __init__.py containing "register_memory_provider" or "MemoryProvider"
  → Reads plugin.yaml for description
  → Calls provider.is_available() to determine availability
  → Returns [(name, description, is_available), ...]
```

**Entry point:** `__init__.py` with `register(ctx)` function that calls `ctx.register_memory_provider(instance)`.

### 1.2 Plugin Manifest (plugin.yaml)

Fields actually consumed by the system:

| Field | Consumed by | Purpose |
|---|---|---|
| `name` | — | Identifier (matches directory name) |
| `version` | — | Display only |
| `description` | `discover_memory_providers()`, `discover_plugin_cli_commands()` | Display in picker + CLI help |
| `pip_dependencies` | `memory_setup.py:_install_dependencies()` | `uv pip install` during setup |
| `external_dependencies` | `memory_setup.py:_install_dependencies()` | Check + print install hint |
| `hooks` | (informational) | Declares which optional hooks are implemented |

### 1.3 Memory Setup Wizard Flow

```
intellect memory setup
  → cmd_setup()
    → _get_available_providers()
      → discover_memory_providers() → [(name, desc, available), ...]
      → load_memory_provider(name) → provider instance
      → provider.get_config_schema() → inspect for secrets vs non-secrets
      → Returns [(name, setup_hint, provider_instance), ...]
    → curses picker: list of providers + "Built-in only"
    → User selects provider
    → _install_dependencies(name) → uv pip install from plugin.yaml
    → If provider.post_setup() exists:
        provider.post_setup(intellect_home, config)  ← custom wizard
      Else:
        Walk provider.get_config_schema() field-by-field
        Write secrets to .env, non-secrets to provider.save_config()
    → config["memory"]["provider"] = name
    → save_config(config)
```

### 1.4 Provider Lifecycle (MemoryManager)

```
agent_init.py
  → load_memory_provider(name)
  → MemoryManager.add_provider(provider)
  → MemoryManager.initialize_all(session_id, **kwargs)
      kwargs currently: session_id, platform, intellect_home, agent_context,
                        session_title, user_id, user_name, chat_id, chat_name,
                        chat_type, thread_id, gateway_session_key,
                        agent_identity, agent_workspace
      kwargs MISSING: member_id, team_id, config
```

### 1.5 CLI Registration (only for active plugin)

```
intellect_cli/main.py :: main()
  → _plugin_cli_discovery_needed() checks if first arg is a known built-in
  → discover_plugin_cli_commands()
      → _get_active_memory_provider() reads config.yaml memory.provider
      → Only loads CLI for the ACTIVE plugin
      → Looks for cli.py → register_cli(subparser)
      → Handler: {name}_command or "honcho_command" (hardcoded fallback)
  → Plugin gets its own argparse subparser: intellect <name>
```

### 1.6 Provider Configuration Persistence

Two locations:
1. **`config.yaml`** → `memory.provider: <name>` (activation) + `memory.<name>:` block (provider config)
2. **`$INTELLECT_HOME/<name>.json`** or similar → provider-native config via `save_config()`
3. **`$INTELLECT_HOME/.env`** → secrets via env vars

### 1.7 Existing Providers Reference

| Provider | pip deps | Has CLI? | Has post_setup? | Config storage | External service |
|---|---|---|---|---|---|
| holographic | None | No | No | config.yaml | No (local SQLite) |
| honcho | honcho-ai | Yes | No (uses generic schema walk) | honcho.json | Yes (Honcho Cloud) |
| hindsight | hindsight-client | No | **Yes** (custom wizard) | hindsight/config.json | Yes/No (cloud or local) |
| mem0 | mem0ai | No | No | mem0.json | Yes (Mem0 Platform) |
| openviking | None (httpx) | No | No | Env vars only | Yes (self-hosted server) |
| retaindb | requests | No | No | Env vars only | Yes (RetainDB Cloud) |
| byterover | None (brv CLI) | No | No | Env vars only | No (local CLI) |
| supermemory | supermemory | No | No | supermemory.json | Yes (Supermemory API) |

---

## 2. v3 Plan vs. Existing Mechanism: Gap Checklist

### 2.1 Matches existing pattern ✓

| Aspect | Plan status |
|---|---|
| `register(ctx)` in `__init__.py` | ✓ Correct |
| `plugin.yaml` with `pip_dependencies` | ✓ Correct |
| `is_available()` checks imports + config | ✓ Correct |
| `get_config_schema()` for setup wizard | ✓ Correct |
| `save_config()` for native config file | ✓ Correct |
| `system_prompt_block()` | ✓ Correct |
| `prefetch()` / `queue_prefetch()` / `sync_turn()` | ✓ Correct |
| `get_tool_schemas()` / `handle_tool_call()` | ✓ Correct |
| `shutdown()` | ✓ Correct |
| Lifecycle hooks (`on_session_end`, `on_pre_compress`, `on_memory_write`, etc.) | ✓ Correct |
| CLI via `cli.py` → `register_cli(subparser)` | ✓ Correct |
| CLI handler `graphiti_command(args)` | ✓ Correct |

### 2.2 Deviations from existing pattern ❌

| # | Gap | Plan says | Should be | Severity |
|---|---|---|---|---|
| 1 | **Setup entry point** | `intellect graphiti setup` as a separate CLI command | `intellect graphiti setup` should delegate to `cmd_setup_provider("graphiti")` → the unified `intellect memory setup` flow, same as honcho does | High |
| 2 | **CLI commands for inactive plugin** | Plans `intellect graphiti *` subcommands always available | Only available when graphiti is the ACTIVE provider (enforced by `discover_plugin_cli_commands()`) | Medium — already handled by existing mechanism, but plan text is misleading |
| 3 | **`mcp_server.py` as plugin component** | MCP server inside the plugin directory | MCP is not part of any existing plugin pattern. This is novel functionality. Either: (a) keep it as a plugin extension (consistent with plugin philosophy), or (b) make it a separate top-level concern. The plan should explicitly note this is a new pattern. | Medium |
| 4 | **`ontology.py` scope** | Plan includes Pydantic entity/edge type definitions | Graphiti handles ontology internally (learned mode by default). Prescribed ontology is advanced Graphiti config, not plugin integration code. Should be config-driven, not Python models. | Low |
| 5 | **Plan describes Graphiti internals** | Detailed sections on how Graphiti async works, how FalkorDB connection works | Both are external components. Plan should describe the *integration layer* only — how the plugin calls them, not how they work internally. | Low |
| 6 | **`agent_init.py` patch not clearly called out** | Buried in Section 3.2 | This is the ONLY modification to the existing agent codebase. It should be a top-level, hard-to-miss section. | High |
| 7 | **No `post_setup()` consideration** | Plan uses generic `get_config_schema()` flow | Graphiti has complex setup (backend choice → embedding choice → model install). A `post_setup()` custom wizard (like Hindsight) would provide a better UX than the generic field-by-field walk. Should discuss trade-off. | Medium |
| 8 | **Config file location** | `$INTELLECT_HOME/graphiti/config.json` | This is fine (matches mem0, supermemory pattern). But the plan also assumes setup wizard writes to this path. The existing `save_config()` is called by `memory_setup.py` L342 — the path is what the provider's `save_config()` implementation chooses. Consistent. | Low |

---

## 3. Required Modifications to Existing System

### 3.1 `pyproject.toml` — Python version

```diff
- requires-python = ">=3.11"
+ requires-python = ">=3.12"
```

**Rationale:** graphiti-core 和 FalkorDB Python 客户端要求 Python ≥3.12。这会影响整个项目。

**Risk:** Users on Python 3.11 can no longer install/update intellect-agent. Mitigation: document prominently in release notes.

### 3.2 `agent/agent_init.py` L1000-1048 — Pass membership context to memory providers

**Current state:** `_init_kwargs` passed to `MemoryManager.initialize_all()` does NOT include `member_id`, `team_id`, or `config`.

**Required change (~8 lines):**

```python
# After the existing _init_kwargs construction (circa L1005-L1047), add:
if agent._member_id:
    _init_kwargs["member_id"] = agent._member_id
if agent._team_id:
    _init_kwargs["team_id"] = agent._team_id
if _agent_cfg:
    _init_kwargs["config"] = _agent_cfg
```

**Why needed:** The graphiti plugin must know:
- `member_id` — to scope the member private graph
- `team_id` — to scope the team shared graph
- `config` — to call `is_members_enabled()` / `is_teams_enabled()` / `members_mode()` for scope resolution

**Impact on existing providers:** Zero. Existing providers receive these as additional `**kwargs` entries and ignore unknown kwargs. `MemoryProvider.initialize()` signature accepts `**kwargs` explicitly.

### 3.3 `plugins/memory/__init__.py` L393-394 — CLI handler discovery

**Current state:**
```python
handler_fn = getattr(cli_mod, f"{active_provider}_command", None) or \
             getattr(cli_mod, "honcho_command", None)
```

The `"honcho_command"` fallback is a hardcoded hack for honcho. For graphiti, `graphiti_command` matches the first pattern. No change needed for graphiti, but this should eventually be cleaned up.

**No change required for graphiti.**

### 3.4 No changes needed

The following existing components require **zero modifications**:

| Component | Reason |
|---|---|
| `agent/memory_provider.py` | ABC is sufficient — graphiti implements it |
| `agent/memory_manager.py` | Generic — delegates to providers |
| `plugins/memory/__init__.py` | Discovery works for any plugin following the pattern |
| `intellect_cli/memory_setup.py` | `cmd_setup()` uses `get_config_schema()` + `save_config()` generically |
| `intellect_cli/main.py` | `discover_plugin_cli_commands()` handles any active plugin |
| `tools/registry.py` | Memory tool schemas merged by MemoryManager |

---

## 4. Corrected Scope: What the Plugin Actually Contains

### 4.1 In scope (we write this code)

```
plugins/memory/graphiti/
├── __init__.py      # GraphitiMemoryProvider — MemoryProvider ABC implementation
├── plugin.yaml      # Manifest
├── config.py        # Config helpers: load/save/schema
├── cli.py           # register_cli() + graphiti_command()
└── README.md        # User docs
```

Optionally:
```
├── client.py        # Graphiti async wrapper (if __init__.py gets too large)
├── mcp_server.py    # MCP bridge (new pattern — see Section 5)
```

### 4.2 Out of scope (external, installed via pip)

- `graphiti-core` + `falkordb` — external dependencies declared in `plugin.yaml`
- `fastembed` — already part of intellect-agent's embedding system
- `mcp` — Python MCP SDK, declared in `plugin.yaml`
- Neo4j server, FalkorDB remote server — user's infrastructure
- Graphiti ontology design — Graphiti handles this (learned mode default)

### 4.3 The plugin's actual job

The plugin is ~600-800 lines of **integration glue**:

1. **Config management** (~100 lines): Load/save `graphiti/config.json`, env var mapping, schema for setup wizard
2. **Client wrapper** (~200 lines): `GraphitiClient` — async event loop thread, call `graphiti_core.Graphiti` methods, manage FalkorDB connection lifecycle
3. **Scope routing** (~100 lines): `GraphitiClientManager` — resolve member/team scope, multi-graph routing, merged search
4. **MemoryProvider ABC** (~300 lines): Lifecycle methods, 5 tool schemas + handlers, circuit breaker, hooks
5. **CLI** (~100 lines): `register_cli()`, status/stats commands

---

## 5. MCP Bridge: New Pattern Discussion

### 5.1 Current state

No existing memory plugin has an MCP server. The MCP concept does not exist in the current plugin mechanism.

### 5.2 Options

**Option A: Plugin-internal MCP server (plan's approach)**

- `mcp_server.py` lives inside the plugin directory
- `intellect graphiti mcp start` registered via the normal `cli.py` → `register_cli()` mechanism
- Server connects to the same FalkorDB instance (via Redis protocol)

Pros: Self-contained, no changes to plugin infrastructure
Cons: Only works for graphiti; other plugins can't offer MCP

**Option B: Generic MCP bridge at the memory infrastructure level**

- A new `agent/mcp_memory_bridge.py` that any MemoryProvider can opt into
- Provider declares `mcp_tools: true` in `plugin.yaml`
- Unified `intellect memory mcp start` command

Pros: Reusable across providers
Cons: Larger scope, changes to plugin infrastructure

**Option C: MCP as a separate plugin type**

- MCP servers become their own plugin category (`plugins/mcp/`)
- Graphiti provides both a memory plugin AND an MCP plugin

Pros: Clean separation of concerns
Cons: More infrastructure changes, duplicate config

**Recommendation:** Start with Option A (plugin-internal) for Phase 3. It's zero-risk to the existing infrastructure. If other memory providers later want MCP, refactor to Option B.

---

## 6. Simplified Plugin Design

### 6.1 Files (corrected)

```
plugins/memory/graphiti/
├── __init__.py      # GraphitiMemoryProvider (~500 lines)
│                    #   - Tool schemas (5)
│                    #   - MemoryProvider ABC implementation
│                    #   - GraphitiClientManager (scope routing)
│                    #   - GraphitiClient (async wrapper)
│                    #   - Circuit breaker
│                    #   - register(ctx)
├── plugin.yaml      # Manifest (~15 lines)
├── config.py        # Config (~100 lines)
│                    #   - load_config() / save_config()
│                    #   - get_config_schema()
├── cli.py           # CLI (~80 lines)
│                    #   - register_cli(subparser)
│                    #   - graphiti_command(args)
│                    #   - Subcommands: status, stats (setup delegates to memory setup)
└── README.md        # Docs
```

If MCP is included:
```
├── mcp_server.py    # MCP bridge (~200 lines)
```

### 6.2 plugin.yaml (corrected)

```yaml
name: graphiti
version: 0.1.0
description: >
  Graphiti — temporal knowledge graph memory. Tracks facts with
  bi-temporal validity windows, episode-level provenance, and
  hybrid search (semantic + BM25 + graph traversal).
pip_dependencies:
  - graphiti-core[falkordb]>=1.0.0
  - falkordb>=0.3.0
  - mcp
hooks:
  - on_session_end
  - on_pre_compress
```

Note: `fastembed` is NOT listed as a dependency because it's already part of intellect-agent's embedding system. The plugin uses `embedding_providers/` which handles its own dependency management.

### 6.3 CLI (corrected — follows honcho pattern)

```python
# cli.py
def graphiti_command(args) -> None:
    """Route graphiti subcommands."""
    sub = getattr(args, "graphiti_command", None)
    if sub == "setup":
        # Delegate to unified memory setup — same as honcho
        print("\n  Graphiti is configured via the memory provider system.")
        print("  Running 'intellect memory setup'...\n")
        from intellect_cli.memory_setup import cmd_setup_provider
        cmd_setup_provider("graphiti")
        return
    elif sub == "status":
        cmd_status(args)
    elif sub == "stats":
        cmd_stats(args)
    elif sub == "mcp":
        cmd_mcp(args)    # Only if MCP is implemented
    elif sub is None:
        cmd_status(args)
    else:
        print(f"  Unknown graphiti command: {sub}")


def register_cli(subparser) -> None:
    """Build the ``intellect graphiti`` argparse subcommand tree."""
    subs = subparser.add_subparsers(dest="graphiti_command")

    setup = subs.add_parser("setup", help="Configure Graphiti (opens memory setup)")
    setup.set_defaults(func=graphiti_command)

    status = subs.add_parser("status", help="Show graph status and health")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=graphiti_command)

    stats = subs.add_parser("stats", help="Detailed knowledge graph statistics")
    stats.add_argument("--scope", choices=["all", "member", "team"], default="all")
    stats.set_defaults(func=graphiti_command)

    # Optional: MCP subcommand
    mcp = subs.add_parser("mcp", help="MCP server management")
    mcp_subs = mcp.add_subparsers(dest="mcp_action")
    mcp_start = mcp_subs.add_parser("start", help="Start MCP server")
    mcp_start.add_argument("--scope", default="auto")
    mcp_start.set_defaults(func=graphiti_command)
    mcp_config = mcp_subs.add_parser("config", help="Print MCP client config")
    mcp_config.set_defaults(func=graphiti_command)

    subparser.set_defaults(func=graphiti_command)
```

---

## 7. Summary: What Changes in the Existing Codebase

| File | Change | Lines | Risk |
|---|---|---|---|
| `pyproject.toml` | `requires-python >=3.12` + graphiti optional deps | 3 | Medium — breaks 3.11 users |
| `agent/agent_init.py` | Add `member_id`, `team_id`, `config` to `_init_kwargs` | ~8 | Low — existing providers ignore unknown kwargs |
| `plugins/memory/graphiti/` | New directory — the plugin itself | ~700-900 | Low — self-contained |
| `plugins/memory/__init__.py` | None | 0 | — |
| `agent/memory_provider.py` | None | 0 | — |
| `agent/memory_manager.py` | None | 0 | — |
| `intellect_cli/memory_setup.py` | None | 0 | — |
| `intellect_cli/main.py` | None | 0 | — |

**Total: 2 files modified, 1 new directory created.**

---

## 8. FalkorDB 架构分析与三库关系

### 8.1 FalkorDB 本质：Redis 模块

FalkorDB 是一个 **Redis 模块**（`.so` 共享库），加载到 `redis-server` 进程中运行。它不是独立数据库，而是 Redis 生态的一部分。

| 模式 | 工作方式 | 需要 Redis？ |
|------|---------|:----------:|
| **Server 模式**（生产） | FalkorDB 模块加载到 `redis-server` 进程 | ✅ 是 |
| **Embedded 模式**（`falkordblite`） | Python 包，进程内，无服务器 | ❌ 否 |

**历史背景：** FalkorDB 由 RedisGraph（Redis Labs 开发）的原始贡献者 fork 而来。当 Redis Ltd. 将 RedisGraph 转为限制性许可证并停止维护后，原作者以 SSPL 许可证继续开发 FalkorDB。

### 8.2 决策：不使用 FalkorDBLite 作为缺省

**变更：** 原设计文档以 FalkorDBLite（嵌入式）为缺省，现改为 **FalkorDB（Server 模式）** 为缺省。

| 维度 | FalkorDBLite（嵌入式） | FalkorDB（Server） |
|------|:-------------------:|:---------------:|
| 配置复杂度 | 零配置 | 需要 Docker/进程 |
| 并发访问 | ❌ 单进程 | ✅ 多进程/多 worker |
| 多租户隔离 | 独立 .db 文件 | 独立 keyspace/graph |
| 与 WebUI 共享 | ❌ 不支持 | ✅ 通过 Redis 协议 |
| 内存管理 | 进程内 | 独立进程，可控 |
| 生产适用性 | 仅开发/测试 | ✅ 生产级 |

**理由：** Intellect Agent 的 WebUI 是多线程 HTTP 服务器，Gateway 支持多 worker。FalkorDBLite 的单进程限制无法满足这些场景。

### 8.3 三库架构：PostgreSQL + Redis + FalkorDB

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  PostgreSQL      │  │  Redis           │  │  FalkorDB        │
│  (主数据库)       │  │  (缓存/事件/队列) │  │  (知识图谱)       │
│                  │  │                  │  │                  │
│  sessions        │  │  session cache   │  │  Graphiti        │
│  members         │  │  rate limiting   │  │  entities        │
│  teams           │  │  SSE pub/sub     │  │  relations       │
│  oauth_tokens    │  │  idempotency     │  │  episodes        │
│  projects        │  │  member sessions │  │  communities     │
│                  │  │                  │  │                  │
│  port 5432       │  │  port 6379       │  │  port 6380       │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### 8.4 FalkorDB 能否与 Redis 共用？

**结论：不能共用，应独立部署。**

FalkorDB 作为 Redis 模块加载后，它**就是**那个 Redis 实例——不存在"共享"的概念。如果将应用缓存和 FalkorDB 放在同一个 Redis 实例中：

| 风险 | 严重程度 | 说明 |
|------|:-------:|------|
| 内存争用 | 🔴 高 | 图数据和缓存数据竞争 `maxmemory`，LRU 可能驱逐图数据导致图损坏 |
| 阻塞 | 🔴 高 | 深度图遍历阻塞 Redis 事件循环，缓存 GET/SET 延迟飙升 |
| 崩溃隔离 | 🟡 中 | FalkorDB 崩溃拖垮整个 Redis 进程，缓存和图数据同时丢失 |
| 驱逐策略冲突 | 🔴 高 | 缓存要 LRU 驱逐，图数据**绝对不能**被驱逐 |
| 备份耦合 | 🟡 中 | RDB/AOF 快照同时包含缓存和图数据，无法独立备份/恢复 |
| 扩展耦合 | 🟡 中 | 无法独立扩展图计算能力 vs 缓存能力 |

**推荐部署方式：**

```yaml
# docker-compose.yml 示例
services:
  postgres:
    image: postgres:16
    ports: ["5432:5432"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  falkordb:
    image: falkordb/falkordb:latest
    ports: ["6380:6379"]    # 映射到 6380 避免与 Redis 冲突
```

### 8.5 Graphiti 绕过 SQLite/PG 翻译层的优势

本次代码评审发现了 `dialect.py` 翻译层的多个严重 bug：
- `INSERT OR REPLACE` → `ON CONFLICT DO UPDATE SET pk=pk`（no-op）
- `?` → `%s` 盲替换破坏字符串常量
- `PRAGMA` 在 PostgreSQL 上无效
- `COALESCE(timestamptz, 0)` 类型不匹配

**Graphiti 使用 FalkorDB 完全绕过了这个翻译层**，通过 `falkordb` Python 客户端直接用 openCypher 查询，不经过 `dialect.py`。这是一个架构优势——知识图谱操作不受关系数据库翻译层 bug 的影响。

### 8.6 Scope 隔离策略（更新）

| 后端 | 隔离方式 | 策略 |
|------|---------|------|
| FalkorDB (Server) | 独立 keyspace/graph 名称 | Strong（始终） |
| FalkorDBLite (Embedded) | 独立 .db 文件 | Strong（始终） |
| Neo4j (Enterprise) | 独立 database | Strong |
| Neo4j (Community) | `group_id` 过滤 | Weak（fallback） |

FalkorDB 的多图支持天然适合 Intellect 的多租户模型：每个 member/team/project 可以有独立的图（graph name = `{member_id}` 或 `{team_id}`），共享同一个 FalkorDB 实例。

### 8.7 依赖清单（更新）

```toml
# pyproject.toml — 新增依赖
[project.optional-dependencies]
graphiti = [
    "graphiti-core[falkordb]>=1.0.0",  # 知识图谱引擎 + FalkorDB 驱动
    "falkordb>=0.3.0",                  # FalkorDB Python 客户端 (MIT)
    "mcp>=1.0.0",                       # MCP SDK (可选)
]
```

**注意：** `falkordblite` 不再作为缺省依赖。仅在需要嵌入式模式时单独安装：
```bash
pip install intellect-agent[graphiti,falkordblite]
```

### 8.8 部署模式（更新）

| 模式 | FalkorDB | Redis | PostgreSQL | 适用场景 |
|------|----------|-------|------------|---------|
| **开发** | FalkorDB Docker | 内存模拟 | SQLite | 本地开发 |
| **单机生产** | FalkorDB Docker | Redis Docker | PostgreSQL Docker | 单服务器部署 |
| **高可用** | FalkorDB Cloud / 集群 | Redis Sentinel / ElastiCache | PostgreSQL HA | 多节点生产 |

### 8.9 plugin.yaml（更新）

```yaml
name: graphiti
version: 0.1.0
description: >
  Graphiti — temporal knowledge graph memory. Tracks facts with
  bi-temporal validity windows, episode-level provenance, and
  hybrid search (semantic + BM25 + graph traversal).
  Requires FalkorDB server (Docker recommended).
pip_dependencies:
  - graphiti-core[falkordb]>=1.0.0
  - falkordb>=0.3.0
  - mcp>=1.0.0
hooks:
  - on_session_end
  - on_pre_compress
```
