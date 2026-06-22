# Intellect-Agent 系统优化路线图

> 生成日期: 2026-06-20
> 基于: 源码系统性分析

---

## 🔴 性能优化（高影响）

| # | 问题 | 位置 | 方案 | 状态 |
|---|------|------|------|:--:|
| P1 | 启动时模块级导入整个 agent 栈 | `intellect_cli/main.py:345-413` | `--version`/`--help` 快速路径绕过 init | ✅ |
| P2 | 每轮对话重算 token 2 次 | `conversation_loop.py:1096-1099` | 缓存估算值，消息数不变时跳过 | ✅ |
| P3 | 每轮 API 调用对 tool_call arguments 重复序列化 | `conversation_loop.py:1061-1086` | `_tc_normalized` 标记跳过已处理消息 | ✅ |
| P4 | `estimate_messages_tokens_rough` 构建 shadow dict | `model_metadata.py:1683-1713` | 内联字符计数，不创建中间对象 | ✅ |

## 🔴 性能优化 — 后续（P5-P10）

| # | 问题 | 位置 | 方案 | 状态 |
|---|------|------|------|:--:|
| **P5** | `get_session()` 用 `SELECT *` 加载 35 列含 system_prompt（可 100K+） | `intellect_state.py:1321` | 按调用方需求只 SELECT 需要的列 | ⬜ 可实现 |
| **P6** | `list_sessions_rich()` 压缩链每个 session 一次查询 | `intellect_state.py:1700-1707` | CTE 批量化或缓存压缩 tip | ⏸️ SQL 重构 |
| **P7** | `_save_session_log` 每次读整个日志文件再解析仅为了跳过写入 | `run_agent.py:1863-1865` | 内存侧追踪 `_last_message_count` 避免磁盘读 | ✅ 已实现 |
| **P8** | System prompt 压缩事件后全量重建（含 skills/media 扫描） | `conversation_loop.py:582-594` | 区分 stable/volatile 层，仅重建变化的部分 | ⏸️ 架构改动 |
| **P9** | `_flush_messages_to_session_db` 每条消息单独序列化 6 次 `json.dumps` | `run_agent.py:1492-1530` | 批量 `replace_messages` 或预序列化 | ⏸️ DB schema 依赖 |
| **P10** | `model_metadata.py` endpoint 缓存无 LRU 上限 | `model_metadata.py:78` | 加 `OrderedDict` + 上限 32 条目 | ✅ 已实现 |

## 🟡 架构优化（中影响）

| # | 问题 | 位置 | 方案 | 状态 |
|---|------|------|------|:--:|
| A1 | `gateway/run.py` 19,808 行单体 | 整个文件 | ✅ **已完成** — 19,808→10,510行 (-46.9%), 5 mixin (command/agent/platform/infrastructure) + 4 helper, MRO 注册表派发 |
| A2 | `cli.py` 15,313 行 | 整个文件 | ✅ worktree 管理提取 → `worktree_helpers.py`（349 行） |
| A3 | `conversation_loop.py` `run_conversation()` 4,439 行 | 整个函数 | ✅ helper 函数提取 → `conversation_helpers.py`（290 行）+ Phase 1-5 标记 |
| A4 | 18 个平台适配器 ~34,000 行 | `plugins/platforms/*` | ✅ `check_platform_requirements()` 共享 helper（12 适配器，-195 行） |

## 🟢 代码质量（低影响）

| # | 问题 | 位置 | 数量 | 状态 |
|---|------|------|------|:--:|
| Q1 | `"non-critical error"` 模板日志 159 处无上下文 | `gateway/run.py` + `webui/api/routes.py` | 159 | ✅ `_log_non_critical()` 自动注入函数名 |
| Q2 | `except Exception:` 宽泛捕获 | `run_agent.py`(67), `gateway/run.py`(230+) | 297+ | ⬜ |
| Q3 | 8 个独立 config 读取路径绕过缓存 | 多个文件 | 8 | 📝 已文档化（见下方） |
| Q4 | 60+ agent 模块无对应测试文件 | `agent/` 目录 | 60+ | ⬜ |
| Q5 | 所有 lint 规则仅启用 PLW1514 | `pyproject.toml:310` | 1/所有规则 | ✅ 已启用 `F` (PyFlakes) |
| Q6 | `model_metadata.py` endpoint 缓存无上限 | `model_metadata.py:78` | — | ✅ P10 LRU eviction |
| Q7 | `conversation_loop.py` (4,789行) 无专项测试 | `tests/agent/` | 缺失 | ⏸️ 独立测试工程 |
| Q8 | 5 个平台适配器完全无测试 | `plugins/platforms/{weixin,...}` | 5 | ⏸️ 独立测试工程 |

## ⚫ 已完成/进行中

| 项目 | 状态 |
|------|:--:|
| A1 gateway/run.py 拆分 (19,808→10,098行) | ✅ |
| A1 技术债务 (TB1-TB7) | ✅ |
| P5 get_session() SELECT * 优化 | ✅ |
| P7 _save_session_log 内存追踪 | ✅ 已实现 |
| P10 endpoint 缓存 LRU | ✅ 已实现 |
| WebUI 进程组终止 fix | ✅ |
| TODO-003 pytest 超时修复 | ✅ |
| SessionDB 读写 Rust 统一 | ✅ M16 |
| 错误分类器 Rust 迁移 | ✅ M1 |
| 消息清理 Rust 迁移 | ✅ M4 |
| Token/Model 工具 Rust 迁移 | ✅ M2/M3 |
| Prompt Caching Rust | ✅ M9 |
| Gitee/GitHub 双源 wheel 分发 | ✅ |
| Gitee 原生 release pipeline | ✅ |
| Rust/Python 版本对齐 0.6.6 | ✅ |
| Gateway Rust 迁移分析 (TODO-010) | 📋 已分析 |
| — session.py (1,495行) SQL CTE | ⬜ 可迁 |
| — stream_consumer.py (1,328行) SSE 解析 | ⬜ 可迁 |
| — delivery.py (433行) 投递路由 | ⬜ 可迁 |
| 发布机制优化 (TODO-011) | 🔵 部分实施 |
| — 冒烟测试 (3平台) | ✅ 已实施 |
| — Gitee SHA256SUMS 同步 | ✅ 已实施 |
| — 自动化 changelog | ⬜ |
| — GPG 签名 | ⬜ |
| — 国内 PyPI 镜像 | ⬜ |
