---
sidebar_position: 5
title: "RAG Providers"
description: "文档知识库插件 — LightRAG 及通过 rag.provider 接入的第三方 RAG 后端"
---

# RAG Providers

Intellect Agent 通过 RAG（检索增强生成）插件管理**文档语料库**——规范、手册、政策、上传的 PDF 和入库笔记。RAG 与[持久化记忆](/user-guide/features/memory)、[Memory Providers](/user-guide/features/memory-providers) 是不同层次：

| 层次 | 存储内容 | 典型来源 |
|------|----------|----------|
| **内置 memory** | MEMORY.md / USER.md 中的精选事实 | Agent `memory` 工具 |
| **Memory provider** | 对话图、用户模型、长期回忆 | [Graphiti](/user-guide/features/memory-providers#graphiti)、Honcho、Hindsight 等 |
| **RAG provider** | 已索引文档与知识图谱 | 上传、插入、可选的对话摘要入库 |

同一时间只能激活**一个** RAG provider（`config.yaml` 中的 `rag.provider`）。Memory 与 RAG 可并存，例如 [Graphiti](/user-guide/features/memory-providers#graphiti) 管对话记忆 + LightRAG 管文档库。

## 快速开始

```bash
intellect lightrag setup          # 配置当前 RAG 插件
intellect lightrag status         # 服务地址与健康检查
intellect config set rag.provider lightrag
```

启用 **`rag` 工具集**后 Agent 才能调用 RAG 工具（`intellect tools`，或在 `tools.cli.enabled` / 对应平台的工具列表中加入 `rag`）。

```yaml
# ~/.intellect/config.yaml
rag:
  provider: lightrag
  prefetch_policy: hybrid
  max_prefetch_tokens: 2000
```

## 工作原理

RAG provider 激活后，Intellect 会：

1. **在符合条件的轮次前预取文档上下文**（以 `<rag-context>` 注入，位于 `<memory-context>` 之后）
2. **注册 provider 工具**（`lightrag_search`、`lightrag_query`、上传/插入等），需启用 `rag` 工具集
3. **可选地将对话摘要入库**（因 provider 而异；LightRAG 通过 `ingest.auto_mode` 控制）
4. **按成员 / 团队 / 项目 / 会话划分 workspace**（多用户功能开启时）

预取**不会**破坏 prompt 缓存——仅在轮次开始时执行，不会在对话中途改写上下文。

### 预取策略（`rag.prefetch_policy`）

| 策略 | 行为 |
|------|------|
| `off` | 不预取 |
| `always` | 每轮都预取 |
| `intent` | 消息包含 `rag.prefetch_keywords` 时预取 |
| `hybrid`（默认） | 命中关键词 **或** 长度 ≥ `prefetch_min_chars`（40）**或** 含 `?` / `？` |

预取文本受 `max_prefetch_tokens`（默认 2000）限制。

## 可用 Providers

### LightRAG

通过**远程 API 服务**接入 [LightRAG](https://github.com/HKUDS/LightRAG) 文档知识图谱。Intellect 仅为 HTTP 客户端——EXTRACT、QUERY、向量化均在 server 端完成。

| | |
|---|---|
| **适用场景** | 团队文档库、规范/政策、多模态上传（PDF、Office） |
| **依赖** | 运行中的 `lightrag-server`（Docker compose、远程主机或 intellect-webui 栈） |
| **数据存储** | Server 端（文件、Postgres+pgvector 或上游后端） |
| **成本** | **Server** 侧的 LLM/embedding API；摘要入库可选用 `auxiliary.lightrag` 指定便宜模型 |

**工具（7 个）：** `lightrag_search`（片段）、`lightrag_query`（答案+引用）、`lightrag_insert_text`、`lightrag_upload_document`、`lightrag_list_documents`、`lightrag_delete_document`（admin）、`lightrag_clear_workspace`（admin）

**两套模型平面：**

| 工作负载 | 运行位置 | 配置 |
|----------|----------|------|
| 对话摘要入库 | Intellect | `config.yaml` → `auxiliary.lightrag` |
| 文档 EXTRACT / QUERY / 向量化 | LightRAG server | `deploy/lightrag/.env` 或主机环境变量 |

**安装步骤：**

```bash
# 从当前 Intellect 模型生成 server .env（可选）
intellect lightrag sync-server-env --docker

# 启动开发 server
cd deploy/lightrag && docker compose up -d

# 配置插件并激活
intellect lightrag setup
intellect config set rag.provider lightrag

# 冒烟（仅 health，无需 LLM）
scripts/smoke_lightrag_compose.sh
```

**CLI：**

```bash
intellect lightrag setup | status | health | workspaces | doctor
intellect lightrag sync-server-env [--docker] [--dry-run]
intellect lightrag mcp start | mcp config
```

激活 LightRAG 时，`intellect doctor` 会显示 **RAG Provider** 检查项。

<details>
<summary>LightRAG 配置参考</summary>

**`~/.intellect/config.yaml`**

```yaml
rag:
  provider: lightrag
  prefetch_policy: hybrid
  max_prefetch_tokens: 2000

auxiliary:
  lightrag:
    provider: auto
    model: ""               # 例如 google/gemini-2.5-flash 用于廉价摘要
```

**`~/.intellect/lightrag/config.json`**

| 键 | 默认 | 说明 |
|----|------|------|
| `server.base_url` | `http://127.0.0.1:9621` | LightRAG API 地址 |
| `ingest.auto_mode` | `off` | `off` / `summary` / `full`；setup 向导可 opt-in |
| `ingest.summary_max_tokens` | `256` | 摘要入库 token 上限 |
| `query.default_mode` | `mix` | 工具默认检索模式 |
| `query.prefetch_mode` | `hybrid` | `rag.prefetch_policy` 为 hybrid 时使用 |
| `upload.default_parse_engine` | `""` | 多模态上传默认解析引擎 |
| `upload.analyze_images` | `false` | 默认是否做 VLM 图像分析 |

环境变量覆盖（按 profile）：`LIGHTRAG_BASE_URL`、`LIGHTRAG_API_KEY`。

</details>

<details>
<summary>部署与共存</summary>

**部署模板：** `deploy/lightrag/docker-compose.yml`（开发）与 `docker-compose.webui.yml`（Postgres 叠加）。详见[部署 README](https://gitee.com/ontoweb/intellect-agent/blob/main/deploy/lightrag/README.md)。

**与 [Graphiti](/user-guide/features/memory-providers#graphiti) memory 共存：**

```yaml
memory:
  provider: graphiti   # 对话知识图谱 — 见 Memory Providers
rag:
  provider: lightrag   # 文档语料库
```

内置 `memory` 工具行为不变。同一轮次：先 memory 预取，再 RAG 预取。

**插件 README：** [plugins/rag/lightrag/README.md](https://gitee.com/ontoweb/intellect-agent/blob/main/plugins/rag/lightrag/README.md)

</details>

---

## Provider 对比

| Provider | 运行时 | 存储 | 工具数 | 需要 Server | 特点 |
|----------|--------|------|--------|-------------|------|
| **LightRAG** | 远程 HTTP | Server 端图+向量 | 7 | `lightrag-server` | 知识图谱 RAG、多模态上传、MCP、`sync-server-env` |

## Profile 与 Workspace 隔离

- 插件配置位于 `$INTELLECT_HOME/lightrag/`，每个[配置档案](/user-guide/profiles)有独立的 `config.json` 与凭据。
- 文档 **workspace** 由运行时上下文推导（`member_*`、`team_*`、`project_*`、`session_*`），按请求发给 server。Compose 中 `WORKSPACE` 留空即可，由 Intellect 自动设置 scope。

## 第三方 RAG 插件

可将额外 provider 安装到：

- `~/.intellect/plugins/rag/<name>/`
- 或 `~/.intellect/plugins/<name>/`（`plugin.yaml` 中 `kind: rag`）

设置 `rag.provider: <name>` 并运行对应 CLI（如已接入则为 `intellect <name> setup`）。仓库内置 provider 从 `plugins/rag/` 发现。

## 开发 RAG Provider

RAG 插件实现 `agent/rag_provider.py` 中的 `RAGProvider` ABC，并通过 `plugins/rag/` 注册。设计参考：[lightrag-memory-plugin-design.md](https://gitee.com/ontoweb/intellect-agent/blob/main/docs/plans/lightrag-memory-plugin-design.md)（§3–4）。开发者指南专页尚未上线，目前以 LightRAG 实现为范例。
