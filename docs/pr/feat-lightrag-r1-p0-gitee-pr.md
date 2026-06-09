# Gitee PR — feat/lightrag-r1-p0 → main

**创建链接：** https://gitee.com/ontoweb/intellect-agent/pull/new/ontoweb:feat/lightrag-r1-p0...ontoweb:main

**标题：** `feat(rag): LightRAG R1 基础设施 + P0 远程 Provider`

---

## Summary

- 新增 `RAGProvider` / `RAGManager`，与 `MemoryManager` 平行；`plugins/rag/` 发现机制 + `config.yaml` 的 `rag:` 配置段
- Agent 接线：init、turn prefetch（`<memory-context>` 之后注入 `<rag-context>`）、system prompt、工具路由、post-turn sync
- 交付 `plugins/rag/lightrag/` 远程-only 插件：httpx 客户端、hybrid prefetch、`lightrag_search` + `lightrag_insert_text`、`intellect lightrag setup|status`、`deploy/lightrag/` dev compose

## 已确认决议（§14.3）

| 项 | 决议 |
|----|------|
| 入库 | `ingest.auto_mode: off` 默认；setup 向导 opt-in `summary`（P1 实现 sync_turn） |
| Prefetch | `hybrid`（关键词 OR 长度≥40 OR `?`） |
| RBAC | 方案 A（P1：`member_rbac.py` 注册） |
| 运行时 | 仅外置 `lightrag-server`，不嵌入 SDK |

## 与 Graphiti / 内置 memory 协同

- `memory.provider: graphiti` + `rag.provider: lightrag` **可并存**
- 内置 `memory` 工具不受影响；Graphiti 管对话图，LightRAG 管文档库
- 同一 turn：先 memory prefetch，再 rag prefetch；写入各走各的路

## 启用

```yaml
# ~/.intellect/config.yaml
rag:
  provider: lightrag
  prefetch_policy: hybrid
  max_prefetch_tokens: 2000

auxiliary:
  lightrag:
    provider: auto    # 摘要 ingest 专用；可 pin 便宜模型
```

```bash
# Server .env（与 Intellect 主模型对齐）
intellect lightrag sync-server-env --docker
cd deploy/lightrag && docker compose up -d

# 插件
intellect lightrag setup
intellect lightrag status
intellect lightrag health
```

用户文档：[`plugins/rag/lightrag/README.md`](../../plugins/rag/lightrag/README.md)

## Test plan

- [x] `scripts/run_tests.sh tests/agent/test_rag_provider.py tests/plugins/rag/ tests/intellect_cli/test_doctor_lightrag.py`（43 passed）
- [x] `scripts/smoke_lightrag_compose.sh`（health + plugin client；`--full` 需 server LLM/embedding）
- [ ] `docker compose -f deploy/lightrag/docker-compose.yml up -d` + `--full` 往返（需 `.env` API key 或 Ollama）
- [ ] `rag.provider: lightrag` CLI 对话：hybrid prefetch + insert/search 往返
- [ ] 与 `memory.provider: graphiti` 共存冒烟

## P1（同分支后续提交 `feat(rag): P1 tools, RBAC, summary ingest`)

- [x] RBAC 方案 A：`agent/member_rbac.py` 注册 7 个 `lightrag_*` 工具
- [x] 全工具面：`lightrag_query`、`lightrag_upload_document`、`lightrag_list_documents`、`lightrag_delete_document`、`lightrag_clear_workspace`
- [x] `ingest.auto_mode: summary|full` — `sync_turn` 经 auxiliary LLM 摘要后 `insert_text`
- [x] `on_session_end` / `on_pre_compress` hooks + `RAGManager` / `run_agent` / compression 接线
- [x] 插件内 admin 工具二次校验（`reason` 必填 + `check_member_tool_permission`）
- [x] Doctor 自检（P2，同 PR 后续提交）
- [x] `deploy/lightrag/docker-compose.webui.yml` + `.env.example`
- [x] 多 workspace 并行 query（ThreadPoolExecutor + per-thread httpx）

## P3+（`sync-server-env` + `auxiliary.lightrag`）

- [x] `intellect lightrag sync-server-env` — 从 Intellect `model.*` + runtime 生成 `deploy/lightrag/.env`
- [x] `auxiliary.lightrag` — 对话摘要 ingest 专用 auxiliary 任务（默认 `auto`）

## P3（同分支后续提交 `feat(rag): P3 multimodal upload, MCP bridge, kind rag`）

- [x] `kind: rag` 注册 + PluginManager 跳过加载（`plugins/rag/` 专用发现）
- [x] 多模态上传桥接：`lightrag_upload_document` 支持 `parse_engine` / `analyze_*` / `process_options`（filename hint → server RagAnything 管线）
- [x] `intellect lightrag mcp start|config` — 5 个 MCP 工具（search/query/list/insert/upload）
- [x] `intellect lightrag health` / `workspaces`

## P2（同分支后续提交 `feat(rag): P2 doctor, webui compose, parallel search`）

- [x] `intellect doctor` RAG Provider 段 + `intellect lightrag doctor`
- [x] `plugins/rag/lightrag/doctor.py` — base_url / health / workspace 诊断
- [x] `deploy/lightrag/docker-compose.webui.yml` — agent + lightrag-server + postgres
- [x] `deploy/lightrag/.env.example` + README
- [x] 多 workspace 并行 `query`：共享 mock transport 以通过单测

## CLI 一览

`setup` · `status` · `health` · `workspaces` · `doctor` · `sync-server-env` · `mcp start|config`

## 参考

- 用户指南：[`plugins/rag/lightrag/README.md`](../../plugins/rag/lightrag/README.md)
- 设计：[`docs/plans/lightrag-memory-plugin-design.md`](../plans/lightrag-memory-plugin-design.md)
- 计划：[`docs/plans/2026-06-06-lightrag-r1-p0-implementation-plan.md`](../plans/2026-06-06-lightrag-r1-p0-implementation-plan.md)
- 部署：[`deploy/lightrag/README.md`](../../deploy/lightrag/README.md)
