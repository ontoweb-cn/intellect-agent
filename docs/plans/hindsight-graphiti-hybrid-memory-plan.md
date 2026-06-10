# Hindsight + Graphiti 混合记忆方案

## 1. 问题定义

Hindsight 和 Graphiti 各有独特优势，但 Intellect Agent 当前只允许激活一个外部记忆提供者：

| 能力 | Hindsight | Graphiti |
|------|:---------:|:--------:|
| 语义检索 + 实体图谱 | ✅ | ✅ |
| 跨记忆 LLM 综合推理 (`reflect`) | ✅ **独有** | ❌ |
| 观察层整合 (observations) | ✅ **独有** | ❌ |
| 双时态事实追踪 (when said vs when true) | ❌ | ✅ **独有** |
| Episode 级溯源 | ❌ | ✅ **独有** |
| 本地嵌入 (无需 API key) | ❌ | ✅ |
| Pre-compress hook (压缩前持久化) | ❌ | ✅ **独有** |
| 云端/本地双模式 | ✅ | ❌ (仅自托管) |

**目标：** 让用户同时获得 Hindsight 的 reflect 综合推理 + Graphiti 的双时态溯源，而不需要手动切换提供者。

---

## 2. 约束分析

### 2.1 当前硬限制

`MemoryManager.add_provider()` (agent/memory_manager.py:258-302) 强制只允许一个外部提供者：

```python
if not is_builtin:
    if self._has_external:
        logger.warning("Rejected memory provider '%s' ...")
        return
    self._has_external = True
```

### 2.2 为什么有这个限制

1. **工具名冲突**：两个提供者可能有同名工具
2. **System prompt 膨胀**：多个提供者各自注入上下文块
3. **配置复杂度**：用户需要分别配置两个后端
4. **同步开销**：每个 turn 都要 sync 到两个后端

### 2.3 为什么可以突破

1. Hindsight 和 Graphiti 的工具名天然不冲突（`hindsight_*` vs `graphiti_*`）
2. System prompt 块可以合并/去重
3. 两者定位互补而非重叠——不会产生冗余信息
4. 同步可以异步并行，不增加感知延迟

---

## 3. 方案设计：Graphiti 作为 Hindsight 的时序增强层

### 3.1 架构概览

```
┌─────────────────────────────────────────────────────┐
│                   MemoryManager                      │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │   builtin    │  │  Hindsight   │  │ Graphiti  │ │
│  │  (MEMORY.md) │  │  (主记忆)     │  │ (时序索引) │ │
│  └──────────────┘  └──────┬───────┘  └─────┬─────┘ │
│                           │                │        │
│                           │  互操作接口     │        │
│                           └────────────────┘        │
│                                                     │
│  检索流程:                                           │
│  1. Hindsight.prefetch() → 语义召回 + observations  │
│  2. Graphiti.prefetch()  → 双时态事实 + episode     │
│  3. 合并去重 → 注入 system prompt                    │
│                                                     │
│  存储流程:                                           │
│  1. Hindsight.sync_turn()  → 事实提取 + 观察合成     │
│  2. Graphiti.sync_turn()   → 双时态索引 + episode   │
│  3. 并行执行，互不阻塞                               │
└─────────────────────────────────────────────────────┘
```

### 3.2 角色分工

| 角色 | 提供者 | 职责 |
|------|--------|------|
| **主记忆** | Hindsight | 语义理解、实体解析、观察合成、reflect 综合推理 |
| **时序增强** | Graphiti | 双时态事实索引、episode 溯源、pre-compress 持久化 |

Hindsight 是"知识是什么"，Graphiti 是"知识什么时候产生的、当时是否有效"。

---

## 4. 实施路径

### Phase 1：解除单提供者限制（改动最小）

**改动文件：** `agent/memory_manager.py`

将 `_has_external: bool` 改为 `_external_providers: set[str]`，允许多个外部提供者，但增加冲突检测：

```python
class MemoryManager:
    def __init__(self) -> None:
        self._providers: List[MemoryProvider] = []
        self._tool_to_provider: Dict[str, MemoryProvider] = {}
        self._external_providers: set[str] = set()  # 替代 _has_external

    def add_provider(self, provider: MemoryProvider) -> None:
        is_builtin = provider.name == "builtin"

        if not is_builtin:
            # 检查工具名冲突
            new_tools = {s.get("name", "") for s in provider.get_tool_schemas()}
            for existing_name, existing_provider in self._tool_to_provider.items():
                if existing_name in new_tools and existing_provider.name != "builtin":
                    logger.warning(
                        "Rejected memory provider '%s' — tool '%s' conflicts "
                        "with existing provider '%s'",
                        provider.name, existing_name, existing_provider.name,
                    )
                    return

            self._external_providers.add(provider.name)

        self._providers.append(provider)
        # ... tool indexing unchanged
```

**改动量：** ~15 行

**风险：** 低。现有单提供者行为不变，只是把 `bool` 改成 `set`。

### Phase 2：配置支持多提供者

**改动文件：** `agent/agent_init.py`

当前配置格式：
```yaml
memory:
  provider: hindsight
```

新配置格式（向后兼容）：
```yaml
memory:
  provider: hindsight           # 主提供者（向后兼容）
  providers:                    # 新增：多提供者列表
    - hindsight
    - graphiti
```

初始化逻辑：
```python
# agent_init.py 改动
_provider_names = []
if mem_config.get("providers"):
    _provider_names = mem_config["providers"]
elif mem_config.get("provider"):
    _provider_names = [mem_config["provider"]]

for _name in _provider_names:
    _mp = _load_mem(_name)
    if _mp and _mp.is_available():
        agent._memory_manager.add_provider(_mp)
```

**改动量：** ~20 行

**风险：** 低。单提供者路径完全保留。

### Phase 3：Graphiti 插件实现（核心工作）

**新建目录：** `plugins/memory/graphiti/`

这是工作量最大的部分。参考 `docs/plans/graphiti-memory-plugin-gap-analysis.md` 的设计，但调整为"时序增强"定位：

```
plugins/memory/graphiti/
├── __init__.py        # GraphitiMemoryProvider (~500 lines)
├── plugin.yaml        # 清单
├── config.py          # 配置管理
├── cli.py             # CLI 命令
└── README.md          # 文档
```

关键设计决策：

#### 3.1 工具集精简

作为增强层，Graphiti 不需要完整的 5 个工具。保留 3 个核心工具：

| 工具 | 用途 |
|------|------|
| `graphiti_search_facts` | 双时态事实搜索（含时间范围过滤） |
| `graphiti_search_nodes` | 实体关系查询 |
| `graphiti_get_node_timeline` | 实体时间线（事实变迁历史） |

去掉 `graphiti_add_episode`（自动 sync_turn 已处理）和 `graphiti_delete_episode`（增强层不应删除）。

#### 3.2 System prompt 块最小化

Graphiti 的 system prompt 只声明能力，不重复 Hindsight 已有的说明：

```python
def system_prompt_block(self) -> str:
    return (
        "[Graphiti — Temporal Knowledge Graph]\n"
        "Use graphiti_search_facts to find WHEN facts were established "
        "and whether they are still valid. Use graphiti_get_node_timeline "
        "to trace how an entity's properties changed over time."
    )
```

#### 3.3 Prefetch 结果标注来源

```python
def prefetch(self, query: str, *, session_id: str = "") -> str:
    facts = self._search(query)
    if not facts:
        return ""
    return (
        "[Temporal context from Graphiti — bi-temporal facts with validity windows]\n"
        + "\n".join(f"- [{f.valid_from} → {f.valid_to or 'present'}] {f.content}"
                    for f in facts)
    )
```

#### 3.4 Pre-compress hook（Graphiti 独有优势）

这是 Hindsight 没有的能力——在上下文压缩前，将即将被丢弃的消息持久化为 episode：

```python
def on_pre_compress(self, messages: List[Dict]) -> str:
    """Extract facts from messages about to be compressed."""
    episode_text = self._format_episode(messages)
    self._client.add_episode(
        content=episode_text,
        valid_at=datetime.now(timezone.utc),
    )
    # 返回压缩提示给 LLM
    facts = self._client.search_facts(episode_text[:500])
    return self._format_facts_for_compression(facts)
```

### Phase 4：检索结果合并与去重

**改动文件：** `agent/memory_manager.py` 的 `prefetch_all()`

当多个提供者返回结果时，需要智能合并：

```python
def prefetch_all(self, query: str, *, session_id: str = "") -> str:
    parts = []
    for provider in self._providers:
        try:
            result = provider.prefetch(query, session_id=session_id)
            if result and result.strip():
                # 标注来源提供者
                parts.append(f"<!-- source: {provider.name} -->\n{result}")
        except Exception as e:
            logger.debug(...)
    
    if len(parts) <= 1:
        return "\n\n".join(parts)
    
    # 多提供者时：Hindsight 在前（主记忆），Graphiti 在后（时序补充）
    return "\n\n---\n\n".join(parts)
```

**改动量：** ~10 行

---

## 5. 配置示例

### 完整配置

```yaml
# ~/.intellect/config.yaml
memory:
  providers:
    - hindsight
    - graphiti

# Hindsight 配置（不变）
# ~/.intellect/hindsight/config.json
{
  "mode": "cloud",
  "bank_id": "intellect",
  "recall_budget": "mid",
  "auto_retain": true,
  "auto_recall": true
}

# Graphiti 配置（新增）
# ~/.intellect/graphiti/config.json
{
  "backend": "falkordb",
  "falkordb_host": "localhost",
  "falkordb_port": 6380,
  "embedding_provider": "local",
  "auto_ingest": true,
  "default_max_nodes": 5
}
```

### 环境变量

```bash
# Hindsight
HINDSIGHT_API_KEY=hs_xxx

# Graphiti（本地嵌入，无需额外 API key）
# 如果使用 OpenAI 做提取：
# GRAPHITI_LLM_API_KEY=sk-xxx
```

---

## 6. 使用场景示例

### 场景 1：追溯决策变迁

```
用户: 我们之前为什么决定用 PostgreSQL 而不是 MySQL？

Agent 内部:
  1. Hindsight.prefetch("PostgreSQL MySQL 决策")
     → "用户偏好 PostgreSQL，因为对 JSONB 和 CTE 的支持更好"
  
  2. Graphiti.prefetch("PostgreSQL MySQL 决策")
     → "[2025-11-03 → 2025-12-15] 考虑 MySQL 因为团队熟悉度
        [2025-12-15 → present] 决定 PostgreSQL 因为 JSONB 需求"
  
  3. 合并后注入 system prompt:
     Hindsight 提供"当前结论"，Graphiti 提供"决策演变时间线"

Agent 回复:
  最初（11月3日）你们考虑 MySQL 因为团队熟悉度，但到 12月15日，
  因为 JSONB 和 CTE 的需求，决定改用 PostgreSQL。这个决定至今有效。
```

### 场景 2：Pre-compress 持久化

```
[上下文窗口接近上限，触发压缩]

Agent 内部:
  1. MemoryManager.on_pre_compress(messages)
  2. Graphiti 提取即将丢弃的消息中的事实 → 写入 FalkorDB
  3. Graphiti 返回提取的事实摘要 → 注入压缩 prompt
  4. LLM 压缩时保留 Graphiti 提取的关键事实
  5. Hindsight 不受影响（它没有 pre-compress hook）
```

---

## 7. 存储与数据库分析

### 7.1 Hindsight 数据能否存入 FalkorDB？

**不能。** Hindsight 的存储后端是其内部实现细节，不可替换：

| Hindsight 模式 | 存储后端 | 控制方 |
|:---|:---|:---|
| Cloud | Hindsight 云端基础设施 | vectorize.io |
| Local Embedded | 内置 PostgreSQL（守护进程自动管理） | Hindsight 守护进程 |
| Local External | 用户自托管 Hindsight 实例 | 用户（但仍是 PG） |

Hindsight 内部用 PostgreSQL 存储文档、嵌入向量、实体、观察结论、事实等，其数据模型和查询逻辑深度依赖关系型数据库。FalkorDB 是图数据库（Redis 模块），两者的数据模型完全不同——这不是换连接串能解决的事。

### 7.2 部署方案与数据库数量

#### 方案 A：Hindsight Cloud + Graphiti（推荐）

```
┌────────────────────────────────────┐
│  本地只需要维护一个数据库           │
│                                    │
│  FalkorDB (Docker, port 6380)      │
│  └─ Graphiti 知识图谱              │
│                                    │
│  Hindsight → 云端 API（零本地存储） │
└────────────────────────────────────┘
```

- **本地数据库：1 个**（FalkorDB）
- Hindsight 数据全在云端，本地零存储
- 运维负担最低

#### 方案 B：Hindsight Local + Graphiti

```
┌─────────────────────────────────────┐
│  hindsight-embed 守护进程            │
│  └─ 内置 PostgreSQL（自动管理）      │
│     端口: 自动分配                   │
│     无需手动运维                     │
│                                     │
│  FalkorDB (Docker, port 6380)       │
│  └─ Graphiti 知识图谱               │
└─────────────────────────────────────┘
```

- **本地数据库：2 个**（PG + FalkorDB）
- Hindsight 的 PG 由守护进程自动管理——随 Intellect 启动，空闲 5 分钟后自动关闭
- 本质上和 Intellect 自带的 SQLite（`state.db`）一样，属于框架自管理组件
- 真正需要手动运维的只有 FalkorDB

#### 方案 C：纯 Graphiti + 自建 reflect

```
┌────────────────────────────────────┐
│  FalkorDB (Docker, port 6380)      │
│  └─ Graphiti 知识图谱              │
│     + 自建 reflect（LLM 综合推理）  │
└────────────────────────────────────┘
```

- **本地数据库：1 个**（FalkorDB）
- 零 Hindsight 依赖
- 需要自建 reflect 能力（LLM prompt 工程），质量可能达不到 Hindsight 原生水平

### 7.3 方案对比

| 方案 | 本地数据库 | Hindsight reflect | 双时态 | 运维负担 |
|:---|:---:|:---:|:---:|:---:|
| A. Hindsight Cloud + Graphiti | **1** (FalkorDB) | ✅ 原生 | ✅ | 低 |
| B. Hindsight Local + Graphiti | 2 (PG + FalkorDB) | ✅ 原生 | ✅ | 中（PG 自动管理） |
| C. 纯 Graphiti + 自建 reflect | **1** (FalkorDB) | ⚠️ 自建 | ✅ | 最低 |

### 7.4 推荐路径

**首选方案 A（Hindsight Cloud + Graphiti）**：本地只维护 FalkorDB 一个容器，Hindsight 的 reflect 质量经过大量迭代打磨，自建很难短期达到同等水平。如果未来 Cloud 模式的延迟或成本不可接受，再考虑方案 C。

---

## 8. `hindsight_reflect` 综合推理机制详解

> 本节详细分析 Hindsight 最核心的差异化能力——`reflect` 跨记忆 LLM 综合推理的实现原理。理解这一机制对于评估"自建 reflect vs 依赖 Hindsight 原生 reflect"的取舍至关重要。

### 8.1 一句话总结

`hindsight_reflect` 的本质是 **Hindsight 服务端执行的 RAG（检索增强生成）管道**——服务端先做语义检索找到相关记忆，再调用 LLM 对检索结果进行综合推理，返回一个连贯的合成答案。Intellect 插件只负责传递查询和接收结果，**推理逻辑完全在 Hindsight 服务端**。

### 8.2 调用链路

```
Intellect Agent (LLM)
  │  调用工具: hindsight_reflect(query="用户喜欢什么编程语言?")
  ▼
HindsightMemoryProvider.handle_tool_call()
  │  plugins/memory/hindsight/__init__.py:1566-1582
  │  提取 query 参数，调用:
  ▼
self._run_hindsight_operation(
    lambda client: client.areflect(
        bank_id=self._bank_id,
        query=query,
        budget=self._budget     # "low" / "mid" / "high"
    )
)
  │
  ▼
Hindsight API 服务端 (Cloud 或 Local Embedded)
  │  1. 语义搜索 → 找到相关 facts/observations
  │  2. 将 facts + query 喂给 LLM
  │  3. LLM 综合推理 → 生成连贯答案
  │  4. 返回 resp.text
  ▼
返回 JSON: {"result": "<LLM 合成的答案>"}
```

### 8.3 与 `recall` 的关键区别

| | `hindsight_recall` | `hindsight_reflect` |
|---|---|---|
| **API 调用** | `client.arecall()` | `client.areflect()` |
| **返回格式** | `resp.results` — 事实列表 | `resp.text` — 一段合成文本 |
| **LLM 参与** | ❌ 不需要（纯检索） | ✅ 服务端 LLM 综合推理 |
| **输出示例** | `1. 用户偏好 Python\n2. 用户不喜欢 Java` | "根据记忆，用户偏好 Python 因为其简洁的语法和丰富的库生态，对 Java 的冗长语法表示过不满。在最近的项目中选择了 FastAPI 作为后端框架。" |
| **适用场景** | "有哪些相关记忆？" | "基于记忆，综合回答一个问题" |

源码对比（`__init__.py`）：

```python
# recall — 返回原始事实列表 (line 1555-1561)
resp = client.arecall(bank_id=..., query=..., budget=..., max_tokens=...)
lines = [f"{i}. {r.text}" for i, r in enumerate(resp.results, 1)]
return json.dumps({"result": "\n".join(lines)})

# reflect — 返回 LLM 合成文本 (line 1573-1579)
resp = client.areflect(bank_id=..., query=..., budget=...)
return json.dumps({"result": resp.text})
```

### 8.4 两种使用路径

#### 路径 A：工具调用（LLM 主动触发）

当 `memory_mode` 为 `hybrid` 或 `tools` 时，`hindsight_reflect` 作为工具暴露给 Intellect Agent 的 LLM。LLM 可以自主决定何时调用它：

```python
# get_tool_schemas() — line 1513-1516
def get_tool_schemas(self):
    if self._memory_mode == "context":
        return []
    return [RETAIN_SCHEMA, RECALL_SCHEMA, REFLECT_SCHEMA]
```

#### 路径 B：自动 prefetch（每轮自动触发）

通过配置 `recall_prefetch_method: reflect`，可以让每轮对话前的自动记忆召回使用 `reflect` 而非 `recall`：

```python
# queue_prefetch() — line 1335-1338
if self._prefetch_method == "reflect":
    resp = client.areflect(bank_id=..., query=query, budget=...)
    text = resp.text or ""
else:
    resp = client.arecall(...)
    text = "\n".join(f"- {r.text}" for r in resp.results)
```

这意味着你可以选择：每轮自动注入的是**原始事实列表**（recall）还是**LLM 综合推理后的结论**（reflect）。

### 8.5 服务端 reflect 的内部流程（推断）

Intellect 插件代码中看不到 reflect 的具体实现——它在 Hindsight 的服务端。根据 Hindsight 的 API 行为和文档，推断其内部流程：

```
┌─────────────────────────────────────────────────────┐
│              Hindsight 服务端 areflect()              │
│                                                     │
│  1. 语义检索                                         │
│     query="用户喜欢什么编程语言?"                      │
│     → 嵌入向量相似度搜索                              │
│     → 实体图谱遍历（关联实体扩展）                      │
│     → 重排序（reranking）                            │
│     → 返回 top-N 相关 facts + observations           │
│                                                     │
│  2. 上下文组装                                       │
│     → 按相关度排序                                   │
│     → 按 recall_budget 控制数量                       │
│     → 组装成 prompt 上下文                            │
│                                                     │
│  3. LLM 综合推理                                     │
│     → System: "基于以下记忆，综合回答用户问题"          │
│     → Context: [事实1] [事实2] [观察1] ...            │
│     → User: query                                   │
│     → LLM 生成连贯答案                                │
│                                                     │
│  4. 返回 resp.text                                  │
└─────────────────────────────────────────────────────┘
```

`budget` 参数（`low`/`mid`/`high`）控制步骤 1 中检索的深度和步骤 3 中 LLM 推理的投入程度。

### 8.6 影响 reflect 质量的配置项

| 配置项 | 位置 | 作用 | 影响 |
|--------|------|------|------|
| `recall_budget` | config.json | 检索和推理的投入程度 | `high` = 更多事实 + 更深推理 |
| `recall_types` | config.json | 检索的事实类型 | 默认仅 `observation`（已去重的整合结论），可选 `world`、`experience` |
| `bank_mission` | config.json | 记忆库使命声明 | 影响 reflect 推理的**视角和框架** |
| `bank_retain_mission` | config.json | 自定义提取 prompt | 影响**什么被记住**，间接影响 reflect 可用的素材 |
| `recall_max_tokens` | config.json | 返回结果的最大 token 数 | 限制 reflect 输出的长度 |

其中 `bank_mission` 特别值得关注——它定义了记忆库的"身份和目的"，会作为 reflect 推理时的 framing context。例如：

```json
{
  "bank_mission": "你是一个编程导师的记忆库，帮助追踪学生的学习进度和偏好"
}
```

这会让 reflect 在推理时带上"编程导师"的视角来组织答案。

### 8.7 线程模型

reflect 调用通过专用的后台事件循环执行：

```python
# _run_hindsight_operation() — line 1019-1030
def _run_hindsight_operation(self, operation):
    client = self._get_client()
    try:
        return self._run_sync(operation(client))  # 在共享 async loop 上执行
    except Exception as exc:
        if not self._is_retriable_embedded_connection_error(exc):
            raise
        # Local embedded 模式下，守护进程可能因空闲而关闭，重试一次
        self._client = None
        client = self._get_client()
        return self._run_sync(operation(client))
```

关键点：

- 所有 Hindsight API 调用共享一个**模块级** `asyncio` 事件循环（`_get_loop()`），在 daemon 线程上运行
- 同步调用通过 `_run_sync()` 桥接到异步世界（`safe_schedule_threadsafe` + `future.result()`）
- Local embedded 模式下有自动重试机制：守护进程空闲关闭后，检测到连接错误会自动重建 client 并重试一次
- 默认超时 120 秒（`_DEFAULT_TIMEOUT`），可通过 `HINDSIGHT_TIMEOUT` 环境变量或 `config.json` 中的 `timeout` 字段调整

### 8.8 核心结论

`hindsight_reflect` 的"智能"**不在 Intellect 插件代码中，而在 Hindsight 服务端**。插件只是一个薄薄的适配层：

```
Intellect 插件 (~30 行)     Hindsight 服务端 (核心)
─────────────────────────   ─────────────────────
  解析 query 参数              语义检索
  调用 client.areflect()       实体图谱遍历
  返回 resp.text               重排序
                              LLM 综合推理
                              生成连贯答案
```

这意味着：

1. **reflect 的质量取决于 Hindsight 服务端的 LLM**：Cloud 模式用 Hindsight 的 LLM（由 vectorize.io 维护和优化），Local 模式用你配置的 LLM（OpenAI、Anthropic 等）
2. **无法在 Intellect 侧修改 reflect 的推理逻辑**——只能通过 `bank_mission`、`recall_budget`、`recall_types` 等配置间接影响
3. **如果要自建 reflect**（如在 Graphiti 中），需要自己实现完整的 RAG 管道：检索 → 组装 prompt → 调 LLM → 返回合成结果。关键是 prompt 工程和检索质量调优，这是一个非平凡的工程任务
4. **这也是为什么推荐保留 Hindsight 作为主记忆的原因**——reflect 是 Hindsight 的核心价值，自建很难在短期内达到同等质量

---

## 9. Graphiti 双时态事实索引与 Episode 溯源

> 本节详细分析 Graphiti 最核心的差异化能力——双时态（bi-temporal）事实模型和 episode 级溯源。理解这一机制对于评估"为什么需要 Graphiti 补充 Hindsight"至关重要。

> **注意：** Graphiti 插件已经完整实现（`plugins/memory/graphiti/`，~2200 行，128 个 unit test 全绿，Phase 0–5c 已完成）。本节描述的是其核心数据模型和机制，而非待开发功能。

### 9.1 核心概念：什么是"双时态"（Bi-temporal）？

普通记忆系统只记录"事实是什么"。Graphiti 为每个事实附加了**两个独立的时间维度**：

| 时间维度 | Cypher 字段 | 含义 | 例子 |
|----------|-------------|------|------|
| `valid_at` | `r.valid_at` | 事实在**现实世界**中何时开始成立 | "2025-11-03，团队开始考虑 MySQL" |
| `invalid_at` | `r.invalid_at` | 事实在**现实世界**中何时不再成立 | "2025-12-15，MySQL 方案被否决" |
| `observed_at` / `created_at` | `r.created_at` | Intellect **何时记录**了这个事实 | "2025-11-03 14:22，Agent 从对话中提取" |

关键洞察：`valid_at` 和 `observed_at` 可以不同。你可能在 12 月才告诉 Agent 一件 6 月发生的事——`valid_at` 是 6 月，`observed_at` 是 12 月。`invalid_at is None` 表示事实**当前仍然有效**。

### 9.2 数据模型：事实如何存储

Graphiti 在 FalkorDB 中用图结构存储知识。核心 Cypher 查询（`client.py:419-424`）：

```cypher
MATCH (n {uuid: $node_id})-[r:RELATES_TO]-(m)
RETURN r.uuid, r.fact, r.valid_at, r.invalid_at, r.created_at, r.episodes
```

每个事实是一条**边**（`RELATES_TO` 关系），连接两个实体节点。边上携带：

| 字段 | 类型 | 说明 |
|------|------|------|
| `uuid` | string | 事实唯一标识 |
| `fact` | string | 事实文本内容 |
| `valid_at` | datetime | 事实生效时间 |
| `invalid_at` | datetime \| null | 事实失效时间（null = 仍有效） |
| `created_at` | datetime | 记录创建时间 |
| `episodes` | list[uuid] | 来源 episode 的 UUID 列表 |

### 9.3 工作流程

```
对话发生
  │
  ▼
sync_turn() / add_episode()
  │  将对话内容作为 episode 写入 Graphiti
  │  graphiti-core 自动做实体提取 + 关系抽取
  │  每条提取出的事实自动获得双时态时间戳
  │  group_id 标签确保租户隔离
  ▼
FalkorDB 图数据库
  │
  ├── 实体节点 (EntityNode)
  │   uuid, name, summary, group_id
  │
  └── 事实边 (RELATES_TO)
      uuid, fact, valid_at, invalid_at, created_at, episodes[]
```

写入代码（`client.py:306-345`）：

```python
async def _add_episode(self, content, source_description, reference_time):
    ts = datetime.fromisoformat(reference_time) if reference_time \
         else datetime.now(timezone.utc)
    result = await self._g.add_episode(
        name=f"{self.graph_name}:{ts.isoformat()}",
        episode_body=content,
        source_description=source_description or "agent",
        reference_time=ts,
        group_ids=[self.graph_name],  # 租户隔离
    )
```

搜索返回的事实自带双时态信息（`client.py:347-372`）：

```python
async def _search_facts(self, query, max_results):
    edges = await self._g.search(query=query, num_results=max_results,
                                  group_ids=[self.graph_name])
    return [{
        "fact": getattr(e, "fact", str(e)),
        "valid_at": str(getattr(e, "valid_at", "")) or None,
        "invalid_at": str(getattr(e, "invalid_at", "")) or None,
        "episode_id": getattr(e, "episodes", None)[0] if ... else None,
    } for e in edges]
```

### 9.4 Episode 溯源机制

**Episode** 是 Graphiti 的写入单元——一段对话（或对话片段）作为一个 episode 入图。graphiti-core 自动从中提取实体和关系，每条提取出的事实都**反向链接到来源 episode**。

```
Episode "2025-11-03 对话"
  │  graphiti-core 自动提取
  ├── Entity: "PostgreSQL"  ←──┐
  ├── Entity: "MySQL"          │
  └── Edge: RELATES_TO ────────┘
       fact: "团队考虑使用 MySQL"
       valid_at: 2025-11-03
       invalid_at: 2025-12-15
       episodes: ["ep-uuid-001"]  ← 溯源到原始对话
```

这意味着可以回答三个层次的问题：

| 问题层次 | 示例 | 通过什么回答 |
|----------|------|-------------|
| "事实是什么？" | "团队用什么数据库？" | `fact` 字段 |
| "事实什么时候变的？" | "什么时候从 MySQL 换到 PG？" | `valid_at` → `invalid_at` 时间线 |
| "这个事实从哪来的？" | "谁说的 / 哪次对话？" | `episode_id` → episode 内容 |

### 9.5 时间线渲染

`timeline.py`（185 行，Phase 5b 已实现）将原始双时态记录渲染为人类可读格式：

**文本格式**（`render_timeline_text`）：

```
  Graphiti timeline for node PostgreSQL

  Historical:
    ✓  2025-11-03 10:30 → 2025-12-15 08:00  团队考虑使用 MySQL  [observed 2025-11-03 10:30]

  Currently valid:
    ▶  2025-12-15 08:00 → still valid          团队决定使用 PostgreSQL 因为 JSONB 需求  [observed 2025-12-15 08:00]
```

- `✓` = 已失效的历史事实（`invalid_at` 在过去）
- `▶` = 当前有效的事实（`invalid_at is None` 或在未来）
- `?` = 时间未知的事实（`valid_at is None`）

**JSON 格式**（`render_timeline_json`）：

```json
{
  "node_id": "PostgreSQL",
  "records": [
    {
      "fact": "团队考虑使用 MySQL",
      "valid_at": "2025-11-03T10:30:00+00:00",
      "invalid_at": "2025-12-15T08:00:00+00:00",
      "observed_at": "2025-11-03T10:30:00+00:00",
      "episode_id": "ep-uuid-001",
      "active": false
    },
    {
      "fact": "团队决定使用 PostgreSQL 因为 JSONB 需求",
      "valid_at": "2025-12-15T08:00:00+00:00",
      "invalid_at": null,
      "observed_at": "2025-12-15T08:00:00+00:00",
      "episode_id": "ep-uuid-002",
      "active": true
    }
  ]
}
```

排序规则：按 `valid_at` 升序 → `observed_at` tiebreak → fact 字符串 stable sort。`valid_at` 缺失的记录排最后。

### 9.6 与 Hindsight 的关键差异

| 维度 | Hindsight | Graphiti |
|------|-----------|----------|
| **事实模型** | 原始事实 → 观察结论（两层） | 双时态事实（单层，带时间窗口） |
| **时间维度** | 仅记录时间戳 | `valid_at` + `invalid_at` + `observed_at` 三维 |
| **溯源粒度** | 文档级（session document） | Episode 级（每条事实可追溯到具体对话片段） |
| **"当前有效"判断** | 通过 observations 的 proof count 和 freshness | 通过 `invalid_at is None` 精确判断 |
| **事实变迁** | 观察结论随新事实更新（旧值被覆盖） | 旧事实保留（`invalid_at` 标记失效），新事实新增 |
| **查询能力** | "关于 X 有哪些记忆？" | "X 在 2025-11 到 2025-12 之间发生了什么变化？" |
| **LLM 推理** | ✅ `reflect` 服务端综合推理 | ❌ 无内置 reflect（需依赖外部 LLM） |

### 9.7 实际应用场景

**场景 1：追溯技术栈决策**

```
用户: 我们为什么从 MySQL 换到了 PostgreSQL？

Agent 调用 graphiti_get_node_timeline("database-decision"):
  → 返回两条事实:
    1. valid_at=2025-11-03, invalid_at=2025-12-15: "考虑 MySQL"
    2. valid_at=2025-12-15, invalid_at=null:      "决定 PostgreSQL"

Agent 回复:
  11月3日你们考虑 MySQL（因为团队熟悉度），但到12月15日改为 PostgreSQL
  （因为 JSONB 需求）。这个决定至今有效。
```

**场景 2：验证事实是否仍然有效**

```
用户: 上次说的那个 API 限制还是 1000 req/min 吗？

Agent 调用 graphiti_search_facts("API rate limit"):
  → 返回事实:
    valid_at=2025-10-01, invalid_at=2025-11-15: "rate limit = 1000/min"
    valid_at=2025-11-15, invalid_at=null:        "rate limit = 5000/min"

Agent 回复:
  不，11月15日已经提升到 5000 req/min 了。之前的 1000/min 限制已经失效。
```

**场景 3：Pre-compress 持久化（Graphiti 独有）**

```
[上下文窗口接近上限，触发压缩]

Agent 内部:
  1. MemoryManager.on_pre_compress(messages)
  2. Graphiti 提取即将丢弃的消息中的事实 → 写入 FalkorDB
  3. Graphiti 返回提取的事实摘要 → 注入压缩 prompt
  4. LLM 压缩时保留 Graphiti 提取的关键事实
  5. Hindsight 不受影响（它没有 pre-compress hook）
```

### 9.8 总结

Graphiti 的双时态 + episode 溯源本质上是一个**可审计的知识演化史**：

- **双时态索引**告诉你"事实何时成立、何时失效"
- **Episode 溯源**告诉你"这个事实是从哪段对话来的"
- **时间线渲染**让你一眼看到实体的完整变迁历史

这是 Hindsight 完全不具备的能力——Hindsight 的 observations 是"当前最佳结论"，旧事实被整合后就不再单独可见。Graphiti 保留了完整的历史轨迹。

---

## 10. 风险与缓解

| 风险 | 严重度 | 缓解措施 |
|------|:------:|----------|
| 工具名冲突 | 低 | Phase 1 增加冲突检测；Hindsight 和 Graphiti 天然不冲突 |
| System prompt 过长 | 中 | Graphiti 的 system_prompt_block 保持最小化（~3 行） |
| 双写延迟 | 低 | sync_turn 已支持异步；两个提供者并行写入 |
| 配置复杂度 | 中 | `intellect memory setup` 支持分别配置；提供 `hybrid` 预设 |
| FalkorDB 运维负担 | 中 | 提供 docker-compose 一键启动；文档说明 |
| Graphiti 插件尚未实现 | ~~高~~ 低 | ✅ 已完整实现（~2200 行，128 tests 全绿） |

---

## 11. 工作量估算

> **更新：** Graphiti 插件已经完整实现（`plugins/memory/graphiti/`，~2200 行，128 个 unit test 全绿）。剩余工作仅 Phase 1+2+4 的多提供者支持改动。

| Phase | 内容 | 代码量 | 测试 | 状态 |
|-------|------|:------:|:----:|:----:|
| Phase 1 | MemoryManager 多提供者 | ~15 行 | ~50 行 | 📋 待开发 |
| Phase 2 | agent_init 多提供者配置 | ~20 行 | ~30 行 | 📋 待开发 |
| Phase 3 | Graphiti 插件实现 | ~2200 行 | ~600 行 | ✅ 已完成 |
| Phase 4 | 检索合并 | ~10 行 | ~20 行 | 📋 待开发 |
| **合计（剩余）** | | **~45 行** | **~100 行** | |

---

## 12. 替代方案对比

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. 本方案：多提供者并存** | 两者优势都保留；改动可控 | 需要维护两个后端；配置稍复杂 |
| **B. Graphiti 内嵌 reflect** | 单一后端，运维简单 | 重复造轮子；reflect 质量难达到 Hindsight 水平 |
| **C. Hindsight 扩展双时态** | 单一后端 | 需要 Hindsight 上游支持；不在我们控制范围内 |
| **D. 手动切换** | 零开发 | 用户体验差；数据分散在两个后端 |

**推荐方案 A**，分阶段交付。

---

## 13. 决策点

> **更新：** Graphiti 插件已完成，决策点大幅简化。

需要确认以下问题后才能进入实施：

1. **是否接受 FalkorDB 作为额外依赖？** Graphiti 需要 FalkorDB（Redis 模块）或 Neo4j。推荐 FalkorDB，Docker 一行启动：`docker run -d --name falkordb -p 6380:6379 falkordb/falkordb:latest`
2. **是否先做 Phase 1+2+4？** 解除单提供者限制（~45 行改动），让 Hindsight + Graphiti 可以并存。这是唯一的阻塞项——Graphiti 插件本身已经 ready。
3. **Graphiti 定位确认：** 作为"时序增强层"（工具更少、prompt 更短）还是"平等主提供者"？本方案假设前者。
