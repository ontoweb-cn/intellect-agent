# RAG 知识库与记忆体协同架构

**日期：** 2026-06-10
**状态：** 📋 架构分析
**关联：** [LightRAG 插件设计](lightrag-memory-plugin-design.md) · [Graphiti 插件计划](graphiti-memory-plugin-dev-plan.md) · [Hindsight+Graphiti 混合方案](hindsight-graphiti-hybrid-memory-plan.md)

---

## 1. 概念边界：知识 vs 记忆

| 维度 | RAG 知识库 | 记忆体 (Memory) |
|------|-----------|----------------|
| **内容性质** | 领域知识、文档、规范、FAQ——**客观的、可共享的** | 用户偏好、对话历史、决策轨迹——**主观的、个人的** |
| **时效性** | 相对稳定，版本化管理 | 持续演化，每次对话都可能更新 |
| **粒度** | 文档/段落级，chunk → embedding | 事实/偏好级，key → value 或 timeline |
| **检索方式** | 语义相似度 (vector search) | 关键词/时间/重要性混合召回 |
| **写入方** | 人工录入或文档管道 | Agent 自动记录 (save/update/delete) |
| **共享范围** | 团队/组织级 | 用户/会话级 |

**一句话区分：RAG 回答"这个领域知道什么"，记忆体回答"关于这个用户我知道什么"。**

---

## 2. 协同模式

### 模式 A：分层检索 (Layered Retrieval)

```
用户问题
  ├─→ 记忆体检索 ──→ 用户偏好、历史上下文
  ├─→ RAG 检索   ──→ 领域知识、文档片段
  └─→ 合并注入 System Prompt / Context Window
```

这是最常见的模式。两者独立检索，结果合并后一起注入 LLM 上下文。记忆体提供"谁在问、之前聊过什么"，RAG 提供"这个领域的事实是什么"。

**关键设计决策：检索顺序。** 记忆体先检索可以改写 query（例如用户上次说"别用英文术语"，本次自动翻译），改写后的 query 再走 RAG，召回质量更高。

### 模式 B：记忆体作为 RAG 的过滤器/路由器

```
用户问题
  ├─→ 记忆体检索 ──→ 用户角色、权限、知识水平
  └─→ RAG 检索   ──→ 全量召回
       └─→ 记忆体过滤 ──→ 按用户角色过滤文档
       └─→ 记忆体重排 ──→ 按用户偏好调整排序
```

例如：用户是"前端工程师"，RAG 召回了全栈文档，记忆体中的角色标签过滤掉后端运维部分，只保留前端相关内容。

### 模式 C：RAG 结果写入记忆体 (Learn from Retrieval)

```
用户问题
  ├─→ RAG 检索 ──→ 返回相关文档
  ├─→ LLM 回答
  └─→ 记忆体写入 ──→ "用户对 X 主题感兴趣"
       └─→ 下次对话时，记忆体直接提示"用户关注 X 领域"
```

Agent 观察到用户反复查询某个知识领域后，在记忆体中记录兴趣标签。后续对话中即使不触发 RAG，也能基于记忆体提供个性化。

### 模式 D：记忆体作为 RAG 的缓存 (Memory-Cached RAG)

```
用户问题
  ├─→ 记忆体检索 ──→ 命中缓存?
  │     ├─ 是 ──→ 直接使用缓存的 RAG 结果
  │     └─ 否 ──→ RAG 检索 ──→ 结果写入记忆体缓存
  └─→ LLM 回答
```

对于高频重复查询（如"公司请假政策"），记忆体缓存上次的 RAG 结果，减少向量数据库的调用成本和延迟。

---

## 3. Intellect Agent 中的实现映射

### 3.1 记忆体层

```
~/.intellect/
├── state.db          ← SessionDB (FTS5 全文搜索，对话历史)
├── memories/         ← Memory 插件 (用户偏好、长期记忆)
└── config.yaml       ← 用户配置
```

| 组件 | 实现 | 职责 |
|------|------|------|
| **SessionDB** | `intellect_state.py` — SQLite + FTS5 | 存储对话消息，支持 `session_search()` 做时间线检索 |
| **Memory 插件** | `plugins/memory/` — Mem0、SuperMemory、Honcho、Graphiti、Hindsight | 存储结构化的用户事实和偏好；`memory` tool 的 add/replace/remove 操作写入 |
| **注入时机** | `AIAgent.__init__` 中 `skip_memory=False`（默认） | 每次对话开始时自动加载相关记忆到 system prompt |

### 3.2 RAG 知识库层

Intellect Agent 本身不内置 RAG 引擎，但提供了接入点：

| 接入方式 | 实现 | 说明 |
|----------|------|------|
| **Skills 系统** | `skills/` 目录 | 轻量 RAG——每个 skill 是结构化的领域知识，`skill_view()` 等价于"检索相关文档" |
| **Context Engine 插件** | `plugins/context_engine/` | 扩展点，可接入外部 RAG 系统（LangChain、LlamaIndex 构建的知识库） |
| **MCP 集成** | `native-mcp` skill | 通过 MCP 协议连接外部知识库工具（如向量数据库的 MCP server） |
| **LightRAG 插件** | `plugins/rag/lightrag/` | 图增强 RAG，外置 server 模式，通过 REST 通信 |

### 3.3 协同流程（实际运行）

```
用户: "Intellect 的 memory provider 怎么切换？"
  │
  ├─→ 1. SessionDB 检索 ──→ 发现上次聊过 Graphiti 和 Mem0
  │     注入: "用户之前关注 Graphiti 的 Neo4j 后端和 Mem0 的 Qdrant 后端"
  │
  ├─→ 2. Memory 检索 ──→ 用户偏好: "偏好源码级分析，关注实现机制"
  │     注入: "回答应包含配置路径、数据模型、存储后端等底层细节"
  │
  ├─→ 3. Skill 加载 ──→ skill_view("intellect-agent")
  │     注入: 完整的配置文档、CLI 命令、架构说明
  │
  └─→ 4. LLM 生成 ──→ 结合三层上下文，生成个性化+领域准确的回答
```

---

## 4. 架构决策指南

| 场景 | 推荐模式 | 原因 |
|------|---------|------|
| 企业客服 | A (分层) | 知识库稳定，用户画像独立 |
| 个性化学习 | B (过滤) | 按学员水平过滤教材内容 |
| 研究助手 | C (学习) | 从检索行为中学习用户兴趣 |
| 高频 FAQ | D (缓存) | 减少向量搜索成本 |
| 复杂 Agent | A+B+C 混合 | 多轮对话需要全部三种协同 |

---

## 5. 整体架构图

```
                    ┌──────────────────────────────┐
                    │       Context Window          │
                    │  ┌────────────────────────┐   │
                    │  │ System: 记忆体注入      │   │
                    │  │ (偏好/历史/决策)        │   │
                    │  ├────────────────────────┤   │
                    │  │ System: RAG 注入        │   │
                    │  │ (文档/知识/规范)        │   │
                    │  ├────────────────────────┤   │
                    │  │ User: 当前问题          │   │
                    │  └────────────────────────┘   │
                    └──────────────┬───────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼───────┐  ┌────────▼────────┐
     │  Memory Manager  │  │  RAG Pipeline  │  │  Session DB     │
     │  (Mem0/Super-    │  │  (Vector DB +  │  │  (SQLite+FTS5)  │
     │   memory/Honcho/ │  │   Embedding)   │  │                 │
     │   Graphiti/      │  │                │  │                 │
     │   Hindsight)     │  │                │  │                 │
     └────────┬────────┘  └───────┬───────┘  └────────┬────────┘
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼───────┐  ┌────────▼────────┐
     │  User Profile    │  │  Documents    │  │  Chat History   │
     │  Preferences     │  │  Knowledge    │  │  Timeline       │
     └─────────────────┘  └───────────────┘  └─────────────────┘
```

**核心原则：记忆体管理"谁"，RAG 管理"什么"，Session 管理"何时"。三者独立存储、独立检索、合并注入，各司其职。**

---

## 6. 与现有方案的关联

| 现有方案 | 本文档的关联 |
|----------|-------------|
| [LightRAG 插件设计](lightrag-memory-plugin-design.md) | LightRAG 是实现 RAG 层的具体方案，本文档定义 RAG 与 Memory 的协同接口 |
| [Graphiti 插件计划](graphiti-memory-plugin-dev-plan.md) | Graphiti 是记忆体层的一种实现，本文档定义其与 RAG 层的交互模式 |
| [Hindsight+Graphiti 混合方案](hindsight-graphiti-hybrid-memory-plan.md) | 多记忆体协同是记忆体层内部的问题，本文档聚焦记忆体层与 RAG 层的跨层协同 |

---

## 7. 待决问题

1. **Context Engine 插件是否应内置 RAG 协同逻辑？** 当前 `plugins/context_engine/` 是纯扩展点，是否应提供模式 A/B/C/D 的参考实现？
2. **LightRAG 的 `hybrid` 检索模式与 Memory 检索如何合并排序？** 两个不同语义空间的结果需要统一的融合策略（RRF？加权求和？LLM 重排？）
3. **记忆体缓存（模式 D）的失效策略？** 知识库更新后，缓存的 RAG 结果何时失效？
4. **跨用户/团队的 RAG 共享与记忆体隔离？** 同一团队的 RAG 知识库共享，但每个用户的记忆体隔离——权限边界如何设计？
