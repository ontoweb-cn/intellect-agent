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

| # | 问题 | 位置 | 方案 |
|---|------|------|------|
| **P5** | `get_session()` 用 `SELECT *` 加载 35 列含 system_prompt（可 100K+） | `intellect_state.py:1321` | 按调用方需求只 SELECT 需要的列 |
| **P6** | `list_sessions_rich()` 压缩链每个 session 一次查询 | `intellect_state.py:1700-1707` | CTE 批量化或缓存压缩 tip |
| **P7** | `_save_session_log` 每次读整个日志文件再解析仅为了跳过写入 | `run_agent.py:1863-1865` | 内存侧追踪 `_last_message_count` 避免磁盘读 |
| **P8** | System prompt 压缩事件后全量重建（含 skills/media 扫描） | `conversation_loop.py:582-594` | 区分 stable/volatile 层，仅重建变化的部分 |
| **P9** | `_flush_messages_to_session_db` 每条消息单独序列化 6 次 `json.dumps` | `run_agent.py:1492-1530` | 批量 `replace_messages` 或预序列化 |
| **P10** | `model_metadata.py` endpoint 缓存无 LRU 上限 | `model_metadata.py:78` | 加 `OrderedDict` + 上限 32 条目 |

## 🟡 架构优化（中影响）

| # | 问题 | 位置 | 方案 | 状态 |
|---|------|------|------|:--:|
| A1 | `gateway/run.py` 19,799 行单体，296 方法 | 整个文件 | 提取消息回放、错误分类、interrupt 逻辑为独立模块 | ⬜ |
| A2 | `cli.py` 15,313 行 | 整个文件 | 提取 worktree 管理 (~400行) 和终端显示 (~330行) | ⬜ |
| A3 | `conversation_loop.py` `run_conversation()` 单函数 4,439 行 | 整个函数 | 按阶段拆：预处理 / API 调用 / 流式 / 后处理 / 错误恢复 | ⬜ |
| A4 | 18 个平台适配器 ~34,000 行，消息格式化/错误分类逻辑重复 | `plugins/platforms/*` | ✅ `check_platform_requirements()` 共享 helper；其余逐步迁移 |

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
| SessionDB 读写 Rust 统一 | ✅ M16 |
| 错误分类器 Rust 迁移 | ✅ M1 |
| 消息清理 Rust 迁移 | ✅ M4 |
| Token/Model 工具 Rust 迁移 | ✅ M2/M3 |
| Prompt Caching Rust | ✅ M9 |
| Gitee/GitHub 双源 wheel 分发 | ✅ |
| Gitee 原生 release pipeline | ✅ |
| Rust/Python 版本对齐 0.6.6 | ✅ |
