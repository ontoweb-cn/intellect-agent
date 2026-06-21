# A1: gateway/run.py 单体拆分 — 详细方案

> 生成: 2026-06-21 | 状态: 📋 计划阶段

## 当前状态

| 指标 | 数值 |
|------|------|
| 文件 | `gateway/run.py` |
| 总行数 | 19,808 |
| 模块级函数 | 47 (1,688 行) |
| GatewayRunner 类方法 | 234 (17,452 行) |
| 类后函数 | 3 (687 行) |

### 结构概览

```
L1     - L1669   模块级: imports + 45 私有 helpers (1,201行) + ~468行 imports/globals
L1670  - L19121  class GatewayRunner: 234 methods (17,452行)
L19122 - L19808  模块级后: _run_planned_stop_watcher + _start_cron_ticker + start_gateway + main (687行)
```

### GatewayRunner 234 个方法按领域分组

| 领域 | 数量 | 行数 | 说明 |
|------|:---:|------|------|
| **命令处理器** | 64 | 6,546 | /help, /status, /model, ... — 45-branch if/elif 派发链 |
| **Agent/任务** | 20 | 3,416 | `_run_agent`(2,388行!), agent 缓存/生命周期 |
| **其他私有** | 55 | 2,067 | 语音、队列、adapter 连接、kanban 等杂项 |
| **通知/状态** | 12 | 859 | 通知发送、watcher、状态消息 |
| **平台** | 24 | 761 | Telegram topic, platform reconnect, 平台通知 |
| **会话** | 15 | 572 | session key, 过期 watcher, 会话格式化 |
| **消息** | 9 | 533 | 消息预处理、富化(voice/image) |
| **初始化/公共** | 9 | 1,130 | `__init__`(228行), `start`(546行), `stop`(329行) |
| **Cron/Watcher** | 2 | 547 | handoff watcher, kanban dispatcher watcher |
| **配置** | 12 | 213 | runtime config, provider routing, env |
| **文件/媒体** | 4 | 178 | 媒体投递, Docker 媒体警告 |
| **模型** | 4 | 55 | provider routing, fallback model |
| **ACL** | 2 | 64 | slash 访问控制 |
| **MCP/Tool** | 1 | 106 | MCP reload |
| **Sandbox** | 1 | 5 | executor context |

### 派发机制（关键发现）

命令派发**不是** `getattr(self, handler)` 模式，而是一个 **45-branch 的 if/elif 链** (L7755-L7934+)：

```python
if canonical == "new":      return await self._handle_reset_command(event)
if canonical == "topic":    return await self._handle_topic_command(event)
if canonical == "help":     return await self._handle_help_command(event)
...
if canonical == "debug":    return await self._handle_debug_command(event)
```

这改变了 Phase 2 的设计——不是简单的 mixin，而是需要同时重构派发链。

---

## 拆分方案

### Phase 1: 模块级 helpers → gateway/helpers.py 扩展

**目标**: 45 个模块级私有函数 (~1,201 行) 迁出，零风险
**模式**: 沿用 A2/A3 已验证模式 — 纯函数提取，原位置保留 re-export shim
**Gate**: ruff + pytest 全量通过方可继续

**分组迁移**:

| 批次 | 函数 | 行数 | 目标文件 |
|------|------|:---:|------|
| 1.1 网络/错误 | `_is_transient_network_error`, `_gateway_loop_exception_handler`, `_ensure_ssl_certs`, `_gateway_provider_error_reply`, `_looks_like_gateway_provider_error`, `_sanitize_gateway_final_response` | ~136 | `helpers.py` (已有) |
| 1.2 消息构建 | `_build_replay_entry`, `_build_gateway_agent_history`, `_wrap_current_message_with_observed_context`, `_uses_telegram_observed_group_context`, `_prepare_gateway_status_message`, `_send_or_update_status_coro`, `_telegramize_command_mentions` | ~170 | `gateway/message_helpers.py` (新) |
| 1.3 配置/运行时 | `_load_gateway_config`, `_load_gateway_runtime_config`, `_resolve_gateway_model`, `_resolve_intellect_bin`, `_resolve_runtime_agent_kwargs`, `_try_resolve_fallback_provider`, `_try_resolve_fallback_provider_inner`, `_home_target_env_var`, `_home_thread_env_var`, `_restart_notification_pending`, `_reload_runtime_env_preserving_config_authority` | ~289 | `gateway/config_helpers.py` (新) |
| 1.4 时间/媒体/杂项 | `_auto_continue_freshness_window`, `_is_fresh_gateway_interruption`, `_last_transcript_timestamp`, `_format_duration`, `_probe_audio_duration`, `_build_media_placeholder`, `_log_non_critical`, `_redact_gateway_user_facing_secrets` | ~171 | `gateway/helpers.py` 扩展 |
| 1.5 Skill/Session | `_skill_slug_from_frontmatter`, `_check_unavailable_skill`, `_parse_session_key`, `_platform_config_key`, `_teams_pipeline_plugin_enabled`, `_normalize_empty_agent_response`, `_should_clear_resume_pending_after_turn`, `_preserve_queued_followup_history_offset`, `_dequeue_pending_event`, `_is_control_interrupt_message` | ~247 | `gateway/skill_session_helpers.py` (新) |

**re-export shim 示例** (放在原 run.py 中):
```python
from gateway.helpers import _is_transient_network_error as _is_transient_network_error
```

**Phase 1 预计减少行数**: ~1,000 行 (5.0%)
**风险**: 零 — 纯函数剪切粘贴，不改变任何调用语义

---

### Phase 2: 命令处理器 → gateway/command_handlers.py (Mixin)

**目标**: 64 个命令处理器方法 (~6,546 行) + 派发链 (~180 行) 迁出
**模式**: Mixin 类 + 字典/注册表派发替代 if/elif 链
**风险**: 低-中 — 需要同时重构派发逻辑

**2.1 创建 `GatewayCommandHandlers` Mixin**:

```python
# gateway/command_handlers.py
class GatewayCommandHandlers:
    """Mixin for GatewayRunner — slash command handler methods."""
    
    # All 64 handler methods: _handle_xxx_command, _handle_yyy_command, ...
```

**2.2 派发链重构** — 替代 45-branch if/elif:

**方案 A: 注册表派发 (推荐)**

```python
# gateway/command_handlers.py
_COMMAND_DISPATCH: dict[str, str] = {
    "new":           "_handle_reset_command",
    "topic":         "_handle_topic_command",
    "help":          "_handle_help_command",
    # ... 40+ entries
}

async def _dispatch_command(self, canonical: str, event) -> Any:
    """Dispatch a slash command by canonical name."""
    handler_name = _COMMAND_DISPATCH.get(canonical)
    if handler_name is None:
        return None  # let caller handle fallthrough
    handler = getattr(self, handler_name)
    return await handler(event)
```

优点: 表驱动，一行 `getattr` 替代 45 个 `if`，新增命令只需加一行注册
缺点: `getattr` 有微小运行时开销 (~1μs，可忽略)
兼容: `routing.py` 的 `COMMAND_ROUTES` 不受影响——它只定义哪些命令存在

**2.3 特殊处理**: 部分命令有 `_maybe_confirm_destructive_slash` 包裹、deprecated 提示、或 fallthrough 逻辑。这些保留在原位置，仅将 handler 方法本身迁入 mixin。

**2.4 GatewayRunner 继承链**:
```python
class GatewayRunner(GatewayCommandHandlers):
```

**Phase 2 预计减少行数**: ~6,000 行 (30.3%)
**风险**: 低 — Mixin 通过 Python MRO 自然解析，不改变方法签名

---

### Phase 3: Agent 生命周期 → gateway/agent_runner.py

**目标**: 20 个 agent 相关方法 (~3,416 行)，尤其是 `_run_agent` (2,388 行)
**模式**: 延续 mixin 或独立委托类
**风险**: 中 — `_run_agent` 是核心热路径

**包含**:
- `_run_agent` (2,388行) — 核心 agent 运行循环
- `_run_agent_via_proxy` (282行)
- `_run_background_task` (173行)
- `_resolve_session_agent_runtime`, `_resolve_turn_agent_config`
- Agent 缓存管理: `_init_cached_agent_for_turn`, `_evict_cached_agent`, `_enforce_agent_cache_cap`, `_sweep_idle_cached_agents`
- Agent 资源清理: `_cleanup_agent_resources`, `_finalize_shutdown_agents`, `_release_running_agent_state`
- Agent 状态: `_agent_config_signature`, `_bind_adapter_run_generation`

**Phase 3 预计减少行数**: ~3,000 行 (15.2%)

---

### Phase 4: 平台适配 → gateway/platform_handlers.py

**目标**: 24 个平台方法 (~761 行) → 独立 mixin
**风险**: 低

---

### Phase 5: 会话 + 通知 → gateway/session_handlers.py

**目标**: 15 个会话方法 (~572 行) + 12 个通知方法 (~859 行)
**风险**: 低

---

### Phase 6: 剩余方法 + 收尾

- `__init__`, `start`, `stop` 留在 `run.py` 中作为骨架
- `_kanban_dispatcher_watcher` (498行) → 独立文件
- `_create_adapter` (209行) → 独立文件
- `_is_user_authorized` (225行) → 独立文件

---

## 预计结果

| 阶段 | 迁移内容 | 减少行数 | 累计 | run.py 剩余 |
|------|----------|:---:|------|:---:|
| **Phase 1** | 45 模块级 helpers | ~1,000 | 5.0% | 18,808 |
| **Phase 2** | 64 命令处理器 + 派发链 | ~6,000 | 35.3% | 12,808 |
| **Phase 3** | 20 agent 方法 | ~3,000 | 50.4% | 9,808 |
| **Phase 4** | 24 平台方法 | ~700 | 53.9% | 9,108 |
| **Phase 5** | 15 会话 + 12 通知 | ~1,200 | 60.0% | 7,908 |
| **Phase 6** | 杂项 + kanban + adapter | ~3,000 | 75.1% | ~4,900 |

**最终目标**: `run.py` < 5,000 行 (~75% 减少)，每个提取模块 < 2,000 行

---

## 质量门禁

每个 Phase 完成后:
1. `ruff check gateway/` — 零新增警告
2. `pytest tests/gateway/ -x --timeout=120` — 全量通过
3. `python -c "from gateway.run import GatewayRunner"` — 导入零错误
4. Git diff 确认无意外改动

---

## 兼容性保证

1. **所有公开 API 保持不变**: `start_gateway()`, `main()`, `GatewayRunner` 的公开方法
2. **re-export shim**: 提取到 helper 的私有函数在 `run.py` 中保留 `from x import _func as _func` 行
3. **测试文件不改动**: 95+ 测试文件零修改
4. **MRO 保证**: Mixin 在 `GatewayRunner` 之前，Python 的方法解析顺序保证 `self._handle_xxx()` 正确找到 mixin 方法

---

最后更新: 2026-06-21
