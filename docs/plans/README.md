# Plans Index & Status

> Updated 2026-06-19 | 58 documents (46 archived to `archive/`)

See also: [WebUI Documentation](../webui/) — user guide and architecture overview.

## Status Key
- ✅ Complete (archived) | 🔄 In Progress | 📋 Planned | 📦 Archive | 📖 Reference

## Active Plans

| Status | Date | Document | Topic |
|--------|------|----------|-------|
| 🔄 | 2026-06-12 | `2026-06-12-cli-refactoring-plan.md` | cli.py 重构（进行中） |
| 📋 | 2026-06-02 | `2026-06-02-multi-database-cache-mq-design.md` | 多数据库/缓存/MQ 架构（P1–P4 已完成，P5 TBD） |
| 📋 | 2026-06-02 | `2026-06-02-model-registry-agent-implementation.md` | Agent-side 模型注册表设计 |
| 📋 | 2026-06-02 | `2026-06-02-model-registry-migration-runbook.md` | 模型配置迁移 Runbook |
| 📋 | 2026-06-02 | `2026-06-02-provider-registry-db-unification-design.md` | Provider 注册表数据库统一设计 |
| 📋 | 2026-06-10 | `rag-memory-collaboration-architecture.md` | RAG + Memory 协同架构 |
| 📋 | — | `graphiti-memory-plugin-gap-analysis.md` | Graphiti gap 分析（参考文档） |
| 📋 | — | `hindsight-graphiti-hybrid-memory-plan.md` | Hindsight + Graphiti 混合记忆方案 |
| 📋 | — | `ontoweb-provider-architecture.md` | ONTOWEB Provider 架构 |
| 📋 | — | `oauth-device-code-msal-p3.md` | Device code + MSAL（P3 可选） |
| 📋 | 2026-06-28 | `2026-06-28-domestic-install-and-setup-scripts-plan.md` | 国内安装文档 + 源码 setup 脚本（Phase 1+2） |
| 📖 | 2026-06 | `2026-06-profile-management-disabled-restore.md` | Profile 管理恢复手册（参考） |

## Archived (📦) — 2026-06-18 Batch

15 completed plans archived on 2026-06-18. Key completions:

| Plan | Outcome |
|------|---------|
| `perf-security-optimization-plan` | 36/36 完成 — 性能/安全/架构优化 |
| `rust-migration-plan` + `rust-phase-a-plan` | Rust 核心层迁移 Phase A 完成（11 文件, ~4,528 行）。2026-06-19 审计：Agent Loop 仍有 ~30,200 行 Python 未迁移，详见 `docs/architecture/rust-python-interaction.md §6` |
| `lightrag-r1-p0-implementation-plan` + `lightrag-memory-plugin-design` | LightRAG R1+P0–P3+ 已落地 |
| `graphiti-memory-plugin-dev-plan` | Phase 0–5c 完成，105 单元测试全绿 |
| `members-webui-hardening-design` | 8/8 完成 + 会话隔离 Phase 1–3 |
| `auth-json-deprecation-pr-plan` | A5–A10 已实施 |
| `remaining-phases-plan` | Phase 1–2 + P0–P2 已完成 |
| `joint-pr-remaining-decisions` + `p2-gates-m1-m3-decision-brief` | 全部已拍板 |
| `code-review-improvement-plan` | 4/6 完成（剩余 2 项不再追踪） |
| `port-hermes-2026-05-31-updates` | 移植执行报告（已完成） |
| `release-summary-v0.4.2` | v0.4.2 发布总结（历史） |
| `oauth-qa-signoff` | QA 签字工单（已完成） |

## Earlier Archives

| Year-Month | Count | Topics |
|------------|-------|--------|
| 2026-06 (early) | 3 | Archived outdated plans (API server, streaming, Gemini OAuth) |
| 2026-05 | 10 | OAuth design, multi-entity spec, Telegram topics |
| 2026-04 | 18 | Architecture, RBAC, storage, teams, webui |
