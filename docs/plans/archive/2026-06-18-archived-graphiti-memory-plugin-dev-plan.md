# Graphiti Memory Plugin — 开发计划

**日期：** 2026-06-06
**基线：** [`graphiti-memory-plugin-gap-analysis.md`](graphiti-memory-plugin-gap-analysis.md)（538 行 gap 对齐已完成）
**当前状态：** `plugins/memory/graphiti/` 完整实现（~2200 行），105 个 unit test 全绿，Phase 0–5c 完成

**关联文档：**
- [`graphiti-memory-plugin-gap-analysis.md`](graphiti-memory-plugin-gap-analysis.md) — 现有 plugin 机制对齐 + FalkorDB 架构分析
- [`2026-06-02-multi-database-cache-mq-design.md`](2026-06-02-multi-database-cache-mq-design.md) — 三库架构（PG + Redis + FalkorDB）联合 PR
- [`oauth-follow-up-tasks.md`](oauth-follow-up-tasks.md) — `v0.4.2` backlog 联动

---

## 0. 进度总览

| Wave | 主题 | 状态 | 阻塞 |
|------|------|------|------|
| **W1** | Phase 0 + Phase 1（基础设施 + 插件核心） | ✅ 已完成 (commits `576e6431b` + `c5d3b9f11`) | — |
| **W2** | Phase 2 多租户 / 多 worker | ✅ 已完成 (commit `d28a77384`) | — |
| **W3** | Phase 3 MCP Bridge（graphiti tools → MCP stdio server） | ✅ 已完成 (commit pending) | — |
| **W4** | Phase 4 部署 / WebUI 联动 | ✅ 已完成 (agent `67240a2ba` + webui `c8046359` + fix `685188961`) | — |
| **W5** | Phase 5 进阶能力（ontology 5.1 + Neo4j 5.4 + community 5.5） | ✅ 已完成 (commit `66892b243`) | — |
| **W5b** | Phase 5b（5.2 时间线渲染 + 5.6 备份导出 + driver 缓存 + .gitignore polish） | ✅ 已完成 (commit `7c190c00b`) | — |
| **W5c** | Phase 5c（LLM endpoint 配置 + local embedder + 本地部署全链路） | ✅ 已完成 (commit pending) | — |

---

## 1. Phase 0 — 前置基础设施改动（阻塞所有后续）

| # | 文件 | 改动 | 行数 | 风险 |
|---|------|------|------|------|
| 0.1 | `pyproject.toml` | `requires-python >= 3.12`（绑 `v0.5.0`）+ `[project.optional-dependencies] graphiti = [...]` | 3 | 🟠 中（断 3.11 用户，需发版说明） |
| 0.2 | `agent/agent_init.py` L1112+ | `_init_kwargs` 注入 `member_id` / `team_id` / `config`，源自 `agent.runtime_context.member_id` / `.team_id` + `_agent_cfg` | ~10 | 🟢 低（其他 provider 接受 `**kwargs` 自动忽略） |
| 0.3 | `pyproject.toml` extras | `falkordblite` 不进默认；预留 `[graphiti,falkordblite]` extras 占位 | 2 | 🟢 低 |

**注：** gap analysis §3.2 写的是 `agent._member_id` / `agent._team_id`，但实际 codebase 用 `agent.runtime_context.member_id` / `.team_id`（见 `agent/runtime_context.py:264`）。本计划以代码为准。

**MCP 决策：** Phase 3 (MCP Bridge) 延后到独立 release，不与 W1/W2 同发；`plugin.yaml` Phase 1 不加 `mcp` 依赖。

**Exit gate：** 现有 8 个 memory provider 测试全绿；`intellect agent` 启动不报错。

---

## 2. Phase 1 — 插件核心实现（~700–900 行）

| # | 文件 | 责任 | 估算 |
|---|------|------|------|
| 1.1 | `__init__.py` — `GraphitiMemoryProvider` | MemoryProvider ABC：`is_available` / `initialize` / `system_prompt_block` / `prefetch` / `sync_turn` / `shutdown` / 5 个工具 schema + handler / 熔断器 | ~500 行 |
| 1.2 | `client.py` — `GraphitiClient` | 异步事件循环线程 + `graphiti_core.Graphiti` 包装 + FalkorDB 连接生命周期 | ~200 行 |
| 1.3 | `client.py` — `GraphitiClientManager` | scope 路由（member / team / project 三图）+ 合并搜索 | ~100 行 |
| 1.4 | `cli.py` — `register_cli` + `graphiti_command` | `status` / `stats` / `setup`（委托 `cmd_setup_provider`） | ~80 行 |
| 1.5 | `config.py`（已存在 141 行） | 补 `get_config_schema()` 与 secret 标注（OpenAI key、Falkor URL） | +30 行 |
| 1.6 | `README.md` | Docker compose 片段、env 矩阵、scope 模型说明 | ~150 行 |

### 1.7 Lifecycle hooks
- `on_session_end(session)` → 把整段对话作为 episode 入图
- `on_pre_compress(messages)` → 提取本轮事实存图，避免压缩丢失

### 1.8 五个 tool schema
1. `graphiti_add_episode`
2. `graphiti_search_facts`（hybrid: semantic + BM25 + traversal）
3. `graphiti_search_nodes`
4. `graphiti_get_node_timeline`（bi-temporal）
5. `graphiti_delete_episode`（带 RBAC：只允许 owner/admin）

**Exit gate：** `intellect memory setup` 能选 graphiti；FalkorDB Docker up 后 `intellect graphiti status` 返回 OK；写入 + 检索往返通过。

---

## 3. Phase 2 — 多租户 / 多 worker 集成 ✅

| # | 项 | 说明 | 状态 |
|---|----|------|------|
| 2.1 | scope 命名规范 | graph name = `member_{member_id}` / `team_{team_id}` / `project_{project_id}` / `global` | ✅ `client.py:_Scope` |
| 2.2 | `MemoryManager.initialize_all` 透传 | Phase 0.2 已落地；`test_initialize_threads_member_team_project_into_scope` 覆盖 | ✅ |
| 2.3 | 与 v2 RBAC 集成 | 5 个 graphiti 工具加入 `agent/member_rbac.py:_EXPLICIT_TOOL_ACTIONS`（read/chat/admin 分级）；provider 端 `_check_delete_rbac` 改为调同一 `check_member_tool_permission` 做防御性二次校验 | ✅ |
| 2.4 | 熔断器 | Phase 1 已落地（`CircuitBreaker`）；3 失败开闸、30s 冷却半开 | ✅ |
| 2.5 | 工具级 RBAC | 与 2.3 同时落地 | ✅ |

**Phase 2 测试（`tests/agent/test_graphiti_scope.py`，18 cases，全绿）：**
- 多 member 写入 graph 互不重叠
- 共享 team → `auto` 范围交集 = team graph，member graph 仍私有
- `scope=team` 读不会触及任何 `member_*` 图
- RBAC allowlist 覆盖全部 5 个工具 + action 等级与 sensitivity 一致 + 所有字符串映射到真 `Action` enum
- members 未启用时 gate 透传；启用但无 actor 时返回 denial
- `delete_episode` 无 reason 在 provider 端被拦
- delete 走真 RBAC gate（patched）：拒/准/RBAC 异常时 fail-closed
- 无 member 上下文的 CLI 不调 RBAC，只校验 reason
- `system_prompt_block` 正确反映绑定的 scope

**Exit gate（已满足）：** 两个不同 member 并发会话写入互不串图；team 成员能搜到团队图但搜不到他人 member 图（scope 层数学已证；live FalkorDB 回归留给 Phase 4 docker 测试）。

---

## 4. Phase 3 — MCP Bridge ✅（`graphiti_*` → MCP stdio server）

将 5 个 graphiti 工具暴露为标准 MCP stdio server，使 Cursor / Claude Desktop / VS Code (Copilot) 等 MCP 客户端可以直接操作用户的知识图谱。

**决策记录：** Option A（plugin-internal MCP），不动 plugin 基础设施。将来若 honcho/mem0 也要 MCP，再 refactor 为 Option B（`agent/mcp_memory_bridge.py`）。

### 4.1 实现：`plugins/memory/graphiti/mcp_server.py`

- 使用 `FastMCP` (MCP SDK) 构建 stdio transport server
- 5 个 MCP tool 与原 `graphiti_*` agent tools 保持相同的 name / description / parameter schema：
  - `graphiti_add_episode` — 写入 episode
  - `graphiti_search_facts` — 混合搜索事实
  - `graphiti_search_nodes` — 搜索实体节点
  - `graphiti_get_node_timeline` — 双时间时间线
  - `graphiti_delete_episode` — 带审计理由删除
- 每个 MCP tool 通过 `asyncio.to_thread` 调用 `GraphitiClientManager` 的 sync 方法，避免阻塞 MCP event loop
- Manager 缓存为进程级单例（`_init_manager` idempotent），避免每调用一次 tool 就重建一次连接
- Scope 在 server 启动时固定（`--scope auto|member|team|project|all`），不动态切换
- 依赖缺失时返回 `RuntimeError`（含安装指引），不 crash

### 4.2 CLI 扩展（`cli.py`）

```
intellect graphiti mcp start --scope auto
    → 启动 MCP stdio server（阻塞直到客户端断开）

intellect graphiti mcp config [--scope auto]
    → 打印 Claude Desktop / Cursor / VS Code 的 MCP 客户端 JSON 配置
    → server name 编码 scope：graphiti-auto / graphiti-team 等
    → 自动检测 intellect CLI 二进制路径（$INTELLECT_BIN 优先）
```

### 4.3 MCP 客户端配置示例

```json
{
  "mcpServers": {
    "graphiti-auto": {
      "command": "intellect",
      "args": ["graphiti", "mcp", "start", "--scope", "auto"]
    }
  }
}
```

### 4.4 依赖更新

`plugin.yaml` 新增 `mcp>=1.0.0` + `fastembed>=0.3.0`（后者为 Phase 5c 一并补上）。

### Phase 3 测试

**`tests/agent/test_graphiti_mcp.py`**（**23 cases，全绿**）：

- Server creation（3 cases）：5 tools 注册、descriptions 非空、server name = graphiti
- Client config（5 cases）：Claude/Cursor/VS Code 均在输出中、intellect path 正确、scope 编码为 server name
- CLI（4 cases）：mcp start + mcp config argparse 正确、默认 scope=auto、invalid scope → exit(1)
- Tool delegation（6 cases）：5 个 tool + return-json-string 类型检查
- Error handling（2 cases）：manager init 失败 → RuntimeError、tool 调用时 manager unavailable
- Idempotent init（1 case）：_init_manager 重复调用不重建
- Path resolution（2 cases）：_intellect_path 默认 + env override

**Exit gate（已满足）：**
- 128 个 unit tests 全绿（Phase 1 + 2 + 3 + 4 doctor + 5 + 5b + 5c）
- `create_graphiti_mcp()` 返回的 FastMCP 实例可直接 `run_stdio_async()` 接入 MCP 客户端
- `intellect graphiti mcp config` 输出有效的 JSON 配置，复制到 Claude Desktop 配置即可使用
- 所有 MCP tool delegate 到相同的 `GraphitiClientManager` 方法，语义与 agent 工具一致

---

## 5. Phase 4 — Docker / 部署 / WebUI 联动 ✅

| # | 项 | 仓 | 状态 |
|---|----|----|------|
| 4.1 | `docker-compose.three-container.yml` 增加 `falkordb` 服务（host 6380 → container 6379）+ `intellect-agent` 注入 `GRAPHITI_FALKORDB_HOST=falkordb` / `PORT=6379` + `depends_on: [falkordb]` + 命名 volume `falkordb-data` | intellect-webui | ✅ |
| 4.2 | `docker-compose.two-container.yml` 不加 FalkorDB；graphiti 仅在 three-container 启用 | intellect-webui | ✅（不动） |
| 4.3 | WebUI Settings → Memory 面板：复用现有 provider 元数据，无需新代码 | intellect-webui | ✅（自动） |
| 4.4 | `intellect doctor` 加 graphiti 分支：依赖检查、FalkorDB ping、per-graph 状态、`_fail_and_issue` 接入 issues 列表 | intellect-agent | ✅ |
| 4.5 | `intellect graphiti dump` 备份命令 | intellect-agent | 📋 留待 Phase 5（联合 PR T6 后续） |

**关键设计：**
- FalkorDB 在 docker 网络内监听 6379（与 Redis 默认相同），主机映射到 6380 避免和开发者本地 Redis 冲突
- agent 容器通过 service name `falkordb:6379` 访问，host 端口仅供调试
- **绝不与应用 Redis 共用 instance**（gap analysis §8.4 风险论证已固化在 compose 注释里）
- `intellect doctor` 分三档：deps 缺失 → install hint；deps 在但 FalkorDB 不可达 → connection-failed issue；全通过 → check_ok + per-graph 列表

**Phase 4 测试：**

`tests/intellect_cli/test_doctor_graphiti.py`（3 cases，全绿）：
- 缺依赖 → emit install hint
- ping 返回 down → 写 issue
- 全通过 → 无 issue

`tests/integration/test_graphiti_docker.py`（4 cases，标 `@pytest.mark.integration`，默认 skip；`INTELLECT_TEST_GRAPHITI_DOCKER=1` + falkordb 启动后启用）：
- `test_falkordb_reachable` — ping 真 FalkorDB
- `test_add_episode_round_trip` — write + search 真链路
- `test_two_members_are_isolated` — alice 写入的 XYZZY 密码，bob 用同样 query 搜不到（**实际多租户证明**）
- `test_circuit_breaker_opens_on_bad_host` — 错误 host 触发熔断

**本地启动 FalkorDB 跑集成测试：**

```bash
docker run -d --rm --name falkordb-test -p 6380:6379 falkordb/falkordb:latest
uv pip install 'intellect-agent[graphiti]'
INTELLECT_TEST_GRAPHITI_DOCKER=1 \
    pytest tests/integration/test_graphiti_docker.py -v -m integration
```

**Exit gate（已满足）：** docker compose three-container 启动后，intellect-agent 容器中 `intellect doctor` 应能 ping 通 falkordb；`intellect memory setup` 选 graphiti 后 chat 可调 `graphiti_*` 工具完成往返。

---

## 6. Phase 5 — 进阶能力 ✅（5.1 + 5.4 + 5.5）

5.1 **Ontology 配置化** — `plugins/memory/graphiti/ontology.py`
- 读 `$INTELLECT_HOME/graphiti/ontology.yaml`，解析 `entities` / `edges` / `edge_map` 三块
- 用 `pydantic.create_model` 动态构造 BaseModel 子类，splat 进 `Graphiti.add_episode(entity_types=..., edge_types=..., edge_type_map=...)`
- 白名单类型：`str` / `int` / `float` / `bool` / `date` / `datetime` / `list[str]` / `list[int]` — 未知类型在 load 阶段就拒，不静默 coerce
- 校验：edge_map 引用的 entity / edge 必须已声明；type name 必须 CamelCase 或 UPPER_SNAKE；property name 必须合法 Python 标识符
- 文件不存在 / parse 失败 → 回落到 graphiti-core learned mode（与 Phase 0-4 行为一致）
- YAML 形状文档化在模块 docstring 顶部

5.4 **Neo4j 后端** — `client.py:_build_driver`
- `config.backend` 选择 `falkordb`（默认）或 `neo4j`
- Neo4j Enterprise（`neo4j_multi_db: true`）：`database = graph_name`，每个租户一个 database
- Neo4j Community（`neo4j_multi_db: false`）：`database = "neo4j"`（唯一允许的 db），靠 `group_id` 在 query 层做租户隔离 — 复用 Phase 1 已经写入每个 episode 的同一 group_id tag，纯 defense-in-depth 复用
- 配置 schema 加 `neo4j_uri` / `neo4j_user` / `neo4j_multi_db`，setup wizard 用 `when: {backend: neo4j}` 条件展示
- Env var：`GRAPHITI_BACKEND` / `GRAPHITI_NEO4J_URI` / `GRAPHITI_NEO4J_USER` / `GRAPHITI_NEO4J_MULTI_DB` / `GRAPHITI_NEO4J_PASSWORD`（共用 `falkordb_password` 槽）
- Driver 导入 deferred（不装 neo4j 也能装 graphiti）

5.5 **Community detection** — `intellect graphiti rebuild-communities`
- `GraphitiClient._build_communities` 调 `Graphiti.build_communities(group_ids=[graph_name])`，超时给 120s
- `GraphitiClientManager.rebuild_communities(scope=...)` 按 scope 路由到对应 graphs，per-graph 错误隔离（一个图失败不影响其他）
- CLI：`--scope auto|member|team|project|all`（默认 all）+ `--json` 输出
- 设计意图：cron-friendly；不要在每轮对话触发

**Phase 5 测试**（`tests/agent/test_graphiti_phase5.py`，**20 cases，全绿**）：

5.1 ontology（8 cases）：
- 文件缺失返回 empty
- 完整 yaml 解析 entity / edge / edge_map
- `as_add_episode_kwargs` 形状匹配 graphiti-core
- 未知类型 → empty + warn
- edge_map 引用未声明 entity → empty + warn
- edge_map 引用未声明 edge → empty + warn
- 非法 yaml → empty + warn
- provider.initialize 把 ontology 透传给 manager

5.4 Neo4j 后端（7 cases）：
- 默认 falkordb 选 FalkorDriver + `database = graph_name`
- neo4j 多 db → Neo4jDriver + `database = graph_name`
- neo4j 单 db → Neo4jDriver + `database = "neo4j"`（fallback）
- uri 未配置 → 从 `host:port` 拼 `bolt://`
- 未知 backend → `ValueError`
- manager 把 backend 参数透传给 GraphitiClient
- config schema 含 backend 字段 + 条件 when

5.5 community（5 cases）：
- `rebuild_communities(scope=all)` 派发到全 3 个 scope graph
- `scope=team` 跳过 member / project graph
- 单图失败被 per-graph 隔离，其他图继续
- CLI 注册 subcommand + scope 参数
- CLI 默认 scope=all

**未做（推迟到独立 PR）：**
- 5.3 Embedding 复用 `embedding_providers/` — 调查后发现 intellect-agent 没有 embedding 子系统可复用；graphiti-core 自带 `EmbedderClient` ABC 已够用。不再列为 backlog。

---

## 7. Phase 5b — 时间线渲染 + 备份导出 + 性能/卫生 ✅

5.2 **Bi-temporal timeline rendering** — `plugins/memory/graphiti/timeline.py`
- 两个 renderer：`render_timeline_text`（ASCII，分 Historical / Currently valid 两段）+ `render_timeline_json`（结构化输出 + `active` 标志）
- 输入容忍：ISO Z 后缀、naive datetime、缺失 `observed_at`（fallback 到 `created_at`）
- 长 fact 智能截断（默认 100 字符 + `…`）
- 排序：按 `valid_at` 升序、`observed_at` tiebreak、fact 字符串 stable
- `intellect graphiti timeline <node_id> [--since ISO] [--until ISO] [--json]`
- `client.py:_get_node_timeline` 升级，Cypher select 加 `r.created_at`，data 里加 `observed_at` 字段

5.6 **`intellect graphiti dump`** — `client.py:_dump`
- Cypher-level 导出（不依赖 RDB 快照，FalkorDB + Neo4j 通用）
- 每个 scope graph 一个 `.jsonl` 文件，第一行是 header（kind=header + node_count/edge_count + format_version），后续每行一个 `{kind: node|edge, ...}` 记录
- WHERE `n.group_id = $g` / `r.group_id = $g` —— 即便后端是 Neo4j Community 共享 database 也不会泄漏跨租户数据
- 默认输出到 `$INTELLECT_HOME/graphiti/dumps/<YYYYMMDD-HHMMSS>/`，可 `--out` 覆盖
- per-graph 错误隔离：一个图 dump 失败不影响其他
- `--scope auto|member|team|project|all` —— 与 rebuild-communities 一致

**Driver 复用性能优化：**
- `GraphitiClient._driver_for_queries()` —— `_ping` / `_dump` / `_get_node_timeline` 共用一个缓存的 driver
- 之前每次 query 都新建一次 driver；graphiti-core 0.29 的 `FalkorDriver.__init__` 会在每次构造时 schedule 一个后台 `build_indices_and_constraints` task，导致每次 query 都会泄漏一个 "Task exception was never retrieved" warning
- 缓存后：一个 client 实例一辈子只触发一次后台任务，doctor 反复 ping 也不会刷屏

**FalkorDB 返回值兼容：**
- 新增 `_normalize_query_result()` —— 处理 FalkorDB 的 `(rows, headers, summary)` 三元组 + Neo4j 的 list-of-mappings
- `_first_count` 用同一套抽象，stats CLI 在两种后端下都对

**`.gitignore` 卫生：**
- `.idea/` / `.vscode/` / `graphify-out/` 加入 ignore，与 `.intellect/` / `scripts/out/` 风格一致

**Phase 5b 测试**（`tests/agent/test_graphiti_phase5b.py`，**18 cases，全绿**）：

5.2 timeline（7 cases）：
- 空 → 占位符
- 含 invalid_at 的 fact → Historical；invalid_at=None → Currently valid；排序正确
- ISO `Z` 后缀正常解析
- JSON renderer 的 `active` 标志：未来 invalid_at = active；过去 invalid_at = inactive；None = active
- 超长 fact 截断到 `--max-fact-len`
- 缺 `observed_at` 时 fallback 到 `created_at`
- 缺 `valid_at` 也能 render，不抛

5.6 dump（5 cases + 2 helper）：
- Manager.dump 按 scope 派发到全 graph
- per-graph error 隔离
- 默认 scope = all
- Cypher 必须含 `group_id` 过滤（用 inspect.getsource 静态检查 — 防回归）
- `_row_to_dict` 处理 dict / mapping protocol / 未知对象
- `_to_jsonable` 处理 datetime / set 等非 JSON 类型

CLI 集成（3 cases）：
- argparse 注册 timeline + dump
- `dump --scope` 默认 all
- end-to-end `_cmd_dump(args)` 写入 JSON-lines 文件（含 header + node + edge），错误图不生成文件

性能（1 case）：
- `_driver_for_queries()` 跨调用复用同一 driver 实例（call_count == 1）

`_get_node_timeline` cypher（1 case）：
- Cypher select 必须含 `created_at`（静态检查 — 否则 observed_at 字段就空了）

**Exit gate（已满足）：**
- 真 FalkorDB 上 `mgr.dump()` 返回 `{nodes: [], edges: []}`（空图正常）
- `mgr.get_node_timeline('non-existent')` 返回 `[]`（不抛）
- 73 个 unit tests 全绿（Phase 1 + 2 + 4 doctor + 5 + 5b）
- 2 个 live integration test 仍 pass，FalkorDB driver caching 移除了 "Task exception was never retrieved" warning 的重复噪音

---

## 8. Phase 5c — LLM endpoint + Local Embedder + 本地部署全链路 ✅

Phase 5c 完成本地部署"最后一公里"：用户不再需要 `OPENAI_API_KEY` 即可运行完整的 knowledge graph pipeline——embedding 走本地 fastembed（CPU/ONNX），entity extraction 走 Ollama/vLLM/LiteLLM 等 OpenAI 兼容端点。

### 5c.1 Local Embedder — `plugins/memory/graphiti/embedder_local.py`

- `FastembedEmbedder` 实现 graphiti-core 的 `EmbedderClient` ABC
- 后端：`fastembed.TextEmbedding`（CPU-friendly ONNX 模型，bge / jina / nomic 等）
- 默认模型：**BAAI/bge-m3**（1024-dim，多语言通用 embedding）
- **Lazy-loading**：`__init__` 只记模型名（~ms）；第一次 `create()` 时在 `asyncio.to_thread` 里下载/加载（~100-300 MB weights，5-30 s）
- 模型别名表：`bge-m3` → `BAAI/bge-m3`、`bge-small` → `BAAI/bge-small-en-v1.5` 等
- 同步 `embedding_dim` 属性：进 probe 失败则 fallback 1024；在 async context 内直接返回 1024 避免阻塞
- 配置入口：`embedding_provider: local` + `embedding_model: bge-m3`

### 5c.2 LLM Endpoint 配置 — `client.py:_build_llm_client`

- 支持所有 OpenAI 兼容端点：`openai` / `openai_compat` / `ollama` / `vllm` / `litellm`
- `base_url` 为空时 → 返回 `None`，让 graphiti-core 用默认 `OpenAIClient`（需 `OPENAI_API_KEY`）
- `base_url` 为非空 → 构造 `LLMConfig` + `OpenAIClient` 返回
- `api_key` 为空时自动填 `"sk-not-used-by-local-endpoint"`（OpenAI SDK 拒绝构造无 key 的 client，本地服务忽略此值）
- 支持 `model` / `small_model` 分别指定主模型和轻量模型

### 5c.3 配置 Schema 扩展

新增 5 个 config key + env var 映射：

| Config Key | Env Var | 说明 |
|---|---|---|
| `llm_provider` | `GRAPHITI_LLM_PROVIDER` | LLM 提供商（openai/openai_compat/ollama/vllm/litellm） |
| `llm_base_url` | `GRAPHITI_LLM_BASE_URL` | OpenAI 兼容端点的 base URL |
| `llm_api_key` | `GRAPHITI_LLM_API_KEY` | API key（secret，不写 config.json） |
| `llm_model` | `GRAPHITI_LLM_MODEL` | 主模型名 |
| `llm_small_model` | `GRAPHITI_LLM_SMALL_MODEL` | 轻量模型名 |

- `save_config()` 排除 `llm_api_key`（与 `falkordb_password` 同一逻辑）
- `get_config_schema()` 新增 5 个 schema field，wizard 可见

### 5c.4 Client / Manager 集成

- `GraphitiClient.__init__` 接受 5 个新参数：`llm_provider` / `llm_base_url` / `llm_api_key` / `llm_model` / `llm_small_model`
- `_ensure()` 调用 `_build_embedder()` + `_build_llm_client()`，将构建结果 splat 进 `Graphiti(embedder=..., llm_client=...)`
- `GraphitiClientManager._client()` 从 config 透传 llm/embedding 参数到 client

### Phase 5c 测试

**`tests/agent/test_graphiti_phase5c.py`**（**30 cases，全绿**）：

5c.1 LLM client builder（10 cases）：
- openai / empty + no base_url → None
- openai / openai_compat / ollama / vllm / litellm + base_url → OpenAIClient
- api_key 为空时自动填 placeholder
- model / small_model 正确透传到 LLMConfig
- unknown provider → ValueError

5c.2 Embedder builder（6 cases）：
- local → FastembedEmbedder（默认 bge-m3，可 override model）
- openai / empty → None
- unknown → ValueError

5c.3 Config schema（3 cases）：
- get_config_schema 含全部 5 个 llm_* field + llm_api_key 标 secret
- env var 正确映射
- save_config 排除 llm_api_key

5c.4 FastembedEmbedder（6 cases）：
- 默认模型名含 bge-m3
- 别名解析（bge-small → BAAI/bge-small-en-v1.5）
- 未知模型名直通
- cache_dir / threads 存储
- init 不加载模型（_model is None）
- embedding_dim：fastembed 未安装时 RuntimeError（含安装指引）；已安装时返回正整数

5c.5 Client/Manager 集成（5 cases）：
- GraphitiClient 存储 LLM 参数
- 默认 llm_provider = openai
- Manager 透传 LLM config → client
- Manager 默认 llm = openai
- Manager 透传 embedding config → client

**本地部署全链路（无需 `OPENAI_API_KEY`）：**
```bash
# 1. 启动 Ollama
ollama pull llama3
# 2. 配置 graphiti
export GRAPHITI_EMBEDDING_PROVIDER=local
export GRAPHITI_LLM_PROVIDER=ollama
export GRAPHITI_LLM_BASE_URL=http://localhost:11434/v1
export GRAPHITI_LLM_API_KEY=not-used
export GRAPHITI_LLM_MODEL=llama3
# 3. 正常使用 — embedding 走本地 bge-m3，extraction 走 Ollama
```

**Exit gate（已满足）：**
- 105 个 unit tests 全绿（Phase 1 plugin + 2 scope + 5 + 5b + 5c + 4 doctor）
- 无 OPENAI_API_KEY 环境下 `_build_embedder(provider="local")` 返回 FastembedEmbedder（不抛）
- LLM 配置值与 client 属性一一对应
- Config schema 支持 setup wizard 展示 LLM field
- `llm_api_key` 正确从磁盘 config.json 中排除

---

## 9. 测试矩阵

| 测试文件 | 覆盖 | 阶段 |
|----------|------|------|
| `tests/agent/test_graphiti_plugin.py` | discover、tool schema、scope routing、熔断器、lifecycle、message serialization | P1 |
| `tests/agent/test_graphiti_scope.py` | 多 member 隔离、team 合并搜索、RBAC、initialize threads | P2 |
| `tests/agent/test_graphiti_phase5.py` | ontology、Neo4j backend、community rebuild + CLI（20 cases） | P5 |
| `tests/agent/test_graphiti_phase5b.py` | timeline rendering、dump、driver caching、Cypher helpers（18 cases） | P5b |
| `tests/agent/test_graphiti_phase5c.py` | LLM client builder、embedder builder、FastembedEmbedder、config schema、client/manager integration（30 cases） | P5c |
| `tests/intellect_cli/test_doctor_graphiti.py` | doctor 分支：依赖缺失/连接失败/全通过（3 cases） | P4 |
| `tests/agent/test_graphiti_provider.py` | is_available、tool schema、save_config / get_config_schema（注：内容已合并至 test_graphiti_plugin.py） | P1 |
| `tests/agent/test_graphiti_client.py` | async 事件循环、连接重试、熔断（注：内容已合并至 test_graphiti_plugin.py + test_graphiti_scope.py） | P1 |
| `tests/agent/test_graphiti_lifecycle.py` | on_session_end / on_pre_compress（注：未单独创建，合并至 test_graphiti_plugin.py） | P1 |
| `tests/agent/test_graphiti_mcp.py` | tool 映射、scope 参数、server creation、config 生成、tool delegation、error handling（23 cases） | P3 ✅ |
| `tests/integration/test_graphiti_docker.py` | 真 FalkorDB Docker，标 `@pytest.mark.docker` | P4 |

**测试总计：128 个 unit test 全绿 + 4 个 integration test（需 Docker，标 skip）**

**CI 增量：** 新增 `graphiti-tests` job，矩阵 `python-version: [3.12, 3.13]` + FalkorDB service container。

---

## 10. 风险与决策项

| # | 风险 / 决策 | 等级 | 处理 |
|---|------------|------|------|
| 1 | **Python 3.12 升级** 影响所有用户 | 🔴 | 建议在 `v0.5.0` 发版而非 `v0.4.x` minor 引入；release notes 头条公告 |
| 2 | **FalkorDB 独立部署** 必须独立 Redis 实例 | 🟠 | 开发者用 `falkordb/falkordb` Docker；生产建议 FalkorDB Cloud（gap §8.4） |
| 3 | **Ontology 默认 learned** 不写 Pydantic 实体类 | 🟠 | 首发用 learned mode，5.1 再放配置化（gap §2.2 #4） |
| 4 | `honcho_command` hardcode 兜底 | 🟡 | 不影响 graphiti（命中第一分支），Phase 1 顺手清理 |
| 5 | **Setup UX**：`post_setup()` 自定义向导 vs schema walk | 🟡 | P1 用 schema walk；P2 收用户反馈后再决定 |
| 6 | **备份策略** FalkorDB 数据不进 tarball | 🟢 | 单列 `intellect graphiti dump` 命令 |

---

## 11. 决策已拍板（2026-06-06）

| # | 议题 | 结论 |
|---|------|------|
| 1 | Python 3.12 升级时机 | ✅ 绑 **`v0.5.0`** minor release |
| 2 | Phase 3 (MCP Bridge) 节奏 | ✅ **延后**，不与 W1/W2 同发；后续独立 release 单独评估 |
| 3 | 默认 backend | FalkorDB Server（Docker）— gap §8.2 |
| 4 | Ontology | learned mode 首发；5.1 再放配置化 |

## 12. 立即执行

W1 已可启动：开 PR 起 Phase 0（pyproject 3 行 + agent_init 10 行 + `[graphiti]` extras）与 graphiti `__init__.py` 骨架同 PR，先让 `intellect memory setup` 看到 graphiti 选项（即便 FalkorDB 未配置也能列出）。

---

## 13. 与其他 backlog 的依赖关系

| 依赖项 | 状态 | 影响 |
|--------|------|------|
| v2 RBAC (`authorize_v2`) | ✅ 已上线（`v0.4.2`） | Phase 2.3 可直接接入 |
| 联合 PR M1–M5 / T1–T12 | ✅ 拍板 | Phase 4 等 P3/P4 落地 |
| OAuth §9 人工 QA | 🔄 进行中 | 不阻塞；Phase 0 可与之并行 |
| 模型注册表迁移 (P3) | 📋 | 无依赖 |
| `intellect db backup` tarball (T6) | 📋 P3 | Phase 4.5 在其之后 |
