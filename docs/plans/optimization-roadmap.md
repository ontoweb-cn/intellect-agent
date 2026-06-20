# Intellect-Agent 系统优化路线图

> 生成日期: 2026-06-20
> 基于: 源码系统性分析

---

## 🔴 性能优化（高影响）

| # | 问题 | 位置 | 方案 | 状态 |
|---|------|------|------|:--:|
| P1 | 启动时模块级导入整个 agent 栈，`--version` 也会加载 | `intellect_cli/main.py:345-413` | 将 config/logging/IPv4 设置移到子命令 handler 内部 | ⬜ |
| P2 | 每轮对话重算 token 2 次（完整消息遍历 + tool schema 序列化） | `conversation_loop.py:1096-1099` | 缓存估算值，仅在消息变化时重算 | ⬜ |
| P3 | 每轮 API 调用对 tool_call arguments 做 `json.loads` + `json.dumps(sort_keys=True)` | `conversation_loop.py:1061-1086` | 归一化在存储时做一次，不在每次发送时重复 | ⬜ |
| P4 | `estimate_messages_tokens_rough` 为每条消息构建 shadow dict 拷贝 | `model_metadata.py:1683-1713` | 改为字符计数循环，不构建中间对象 | ⬜ |

## 🟡 架构优化（中影响）

| # | 问题 | 位置 | 方案 | 状态 |
|---|------|------|------|:--:|
| A1 | `gateway/run.py` 19,799 行单体，296 方法 | 整个文件 | 提取消息回放、错误分类、interrupt 逻辑为独立模块 | ⬜ |
| A2 | `cli.py` 15,313 行 | 整个文件 | 提取 worktree 管理 (~400行) 和终端显示 (~330行) | ⬜ |
| A3 | `conversation_loop.py` `run_conversation()` 单函数 4,439 行 | 整个函数 | 按阶段拆：预处理 / API 调用 / 流式 / 后处理 / 错误恢复 | ⬜ |
| A4 | 18 个平台适配器 ~34,000 行，消息格式化/错误分类逻辑重复 | `plugins/platforms/*` | 提取共享基类 | ⬜ |

## 🟢 代码质量（低影响）

| # | 问题 | 位置 | 数量 | 状态 |
|---|------|------|------|:--:|
| Q1 | `"non-critical error"` 模板日志 119 处无上下文 | `gateway/run.py` | 119 | ⬜ |
| Q2 | `except Exception:` 宽泛捕获 | `run_agent.py`(67), `gateway/run.py`(230+) | 297+ | ⬜ |
| Q3 | 8 个独立 config 读取路径绕过缓存 | 多个文件 | 8 | ⬜ |
| Q4 | 60+ agent 模块无对应测试文件 | `agent/` 目录 | 60+ | ⬜ |
| Q5 | 所有 lint 规则仅启用 PLW1514 | `pyproject.toml:310` | 1/所有规则 | ⬜ |
| Q6 | `model_metadata.py` endpoint 缓存无上限 | `model_metadata.py:78` | — | ⬜ |
| Q7 | `conversation_loop.py` (4,789行) 无专项测试 | `tests/agent/` | 缺失 | ⬜ |
| Q8 | 5 个平台适配器完全无测试 | `plugins/platforms/{weixin,...}` | 5 | ⬜ |

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
