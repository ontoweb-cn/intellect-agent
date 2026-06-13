# cli.py 重构计划

> **状态**: 进行中 | **最后更新**: 2026-06-12

## Context

cli.py 是 Intellect Agent 的核心 CLI 模块，包含 TUI 事件循环、命令分发、Agent 交互等功能。
原始版本 15,527 行 / 392 def，通过提取嵌套函数、去重辅助函数、添加 dispatch table 等手段持续优化。

## 已完成

| 轮次 | 内容 | 行数变化 |
|------|------|---------|
| Phase 1 | 修复 mixin NameError（21 个方法 lazy import） | — |
| Phase 1 | commands.py → commands/registry.py 包迁移 | — |
| Phase 1 | 恢复 12 个缺失方法 + 清除 ~240 行死代码 | — |
| Phase 2 | 提取 `_process_loop` (149L) 从 run() | run() -149 |
| Phase 2 | 提取 `_handle_enter` (190L) 从 run() | run() -190 |
| Phase 2 | 添加 `_COMMAND_DISPATCH` dispatch table (44 entries) | +65 |
| Phase 2 | 提取 `_maybe_auto_title` (27L) 从 chat() | chat() -27 |
| Phase 2 | 提取 `_display_chat_response` (35L) 从 chat() | chat() -35 |
| Phase 2 | 辅助函数模块化（`_panel_box_width` 等 4 个） | -100 |
| **总计** | | **-394 行** |

## 当前状态

```
cli.py:        15,133 行 / 386 def / 187 class methods
run():          2,022 行（原 2,341，-319 行）
chat():           573 行（原 627，-54 行）
dispatch table:  44 entries
测试:           163 pass
```

## 待办

### P2 — 架构优化

| # | 任务 | 预估收益 |
|---|------|---------|
| 1 | 提取 `_get_clarify_display` (185L) 从 run() | run() -185 |
| 2 | 提取 `_get_model_picker_display` (61L) 从 run() | run() -61 |
| 3 | `from utils import` 包安全化（81 处） | 包安装兼容 |

### P3 — Rust + 新功能

| # | 任务 |
|---|------|
| 4 | Rust Phase B: Stage 2 — 工具沙箱 |
| 5 | Rust Phase B: Stage 3 — Agent 核心循环 |
| 6 | RAG + Memory 协同架构 |
| 7 | Graphiti 记忆体插件 |
| 8 | i18n 全量 parity |

## 关键文件

| 文件 | 说明 |
|------|------|
| `cli.py` | 主 CLI 模块 |
| `intellect_cli/cli_slash_handlers.py` | SlashCommandMixin（命令处理） |
| `intellect_cli/cli_voice.py` | VoiceMixin（语音） |
| `intellect_cli/commands/registry.py` | 命令注册表 |
| `tests/intellect_cli/test_mixin_lazy_imports.py` | Mixin lazy import 验证测试 |
