# Gateway 改进任务分解（GW-xxx）— 分阶段细化

> **日期**：2026-08-31
> **状态**：📋 已评审，逐项实施中
> **依据**：`docs/plans/2026-08-31-gateway-hermes-gap-analysis.md`（差距分析）+ 二轮接线点深挖
> **纪律**：每个任务**独立小模块 + best-effort + 失败关闭 + 有界兜底**；不机械移植 Hermes 数字。

---

## 〇、二轮评审修正（相对差距分析文档）

| # | 修正 | 影响 |
|---|---|---|
| R1 | P0-3 接线目标：最终回复走 `stream_consumer.py`（流式）+ adapter 包装器（非流式），**不是** `run.py` 的 notice `adapter.send()` | GW-105/106/107 |
| R2 | P1-1 方案：`SessionDB.__init__` 内重试，**不**移植 `RecoverableHandleCache`（无 handle-cache 层） | GW-108 |
| R3 | P0-2 释放点：`_handle_message_with_agent` 内独立 try/finally，wrapper finally（`run.py:5405`）作用域不匹配 | GW-104 |
| R4 | `write_runtime_status`（`status.py:518`）需加 `session_store` kwarg（当前签名严格，无任意 key） | GW-108/GW-201 |

---

## Phase 0 — 可靠性地基（第一批，独立小模块可单测）

### GW-101 · P0-1a 事件循环阻塞点审计
- **目标**：产出全量阻塞点清单（async 函数内的 `time.sleep`/`subprocess.run`/`os.system`/同步 HTTP/大文件 `open().read()`）。
- **动作**：`grep -rnE` 扫 `gateway/`、`gateway/platforms/`、`hermes_cli/`（对应 intellect 的 `intellect_cli/`）、`webui/`；用 ruff 的 `ASYNC210/220/221/251` 作为机器清单（先 `ruff check --select ASYNC210,ASYNC220,ASYNC221,ASYNC251` 看基线）。
- **产出**：分文件清单，标注每处「改 `asyncio.to_thread`」或「改 `await asyncio.sleep`」或「豁免（注明原因）」。
- **验收**：清单覆盖 `gateway/` 全部 async 阻塞点；无遗漏的裸 `time.sleep` in async def。

### GW-102 · P0-1b 阻塞点修复 + lint 门禁
- **动作**：
  1. 修 GW-101 清单里的热路径（重点 `run.py:991` 附近 `--replace` 的 sleep、`shutdown_forensics.py` 诊断子进程、webhook/tts IO）；
  2. `pyproject.toml` `[tool.ruff.lint]` `select` 增 `ASYNC210/220/221/251`，存量豁免进 `[tool.ruff.lint.per-file-ignores]`（`:323` 已存在）建 frozen 基线，新文件/新违规 CI fail。
- **策略（评审标注）**：分「修复 PR」+「门禁 PR」两步，避免 30k 行遗留代码一次性引爆 CI。
- **验收**：`ruff check .` 绿；sabotage 测试（async def 里加 `time.sleep`）应 fail。

### GW-103 · P0-2a turn-lease 模块
- **动作**：新增 `gateway/turn_lease.py`，移植 Hermes `turn_lease.py` 语义（`SessionTurnLeaseRegistry`/`TurnLeaseToken`/`TurnLeaseTimeoutError`）：按 session_id 串行、generation+identity 校验释放、超时 fail-closed、有界注册表、压缩旋转 `rebind`。
- **验收**：纯单测覆盖 acquire/release/release 幂等/超时拒 turn/rebind 别名窗口/有界驱逐。

### GW-104 · P0-2b turn-lease 接线
- **锚点**（已核实）：会话解析 `run.py:5919→6003(switch_session)→6013-6025(rebind)`；`session_entry.session_id` 自 6091 起为解析后 durable id；load `run.py:6309`；run `run.py:7063`；flush `run.py:7723`。
- **动作**：在 `run.py:6025` 后、`6309` 前按 `session_entry.session_id` `acquire`；用**独立 try/finally** 包住 6309→7723 区域（含 7100-7127 的 stale 早退路径），`finally` 释放 token；超时 `TurnLeaseTimeoutError` 时拒 turn 并回可见 resend 提示。
- **验收**：`switch_session` 多对一键→id 场景下，第二 turn 等待第一 turn flush，不再并发写脏 transcript；早退路径不泄漏租约。

### GW-105 · P0-3a delivery ledger 模块
- **动作**：新增 `gateway/delivery_ledger.py`，移植 Hermes `delivery_ledger.py` 语义：`record_obligation/mark_attempting/mark_delivered/mark_failed` + `sweep_recoverable`（owner pid+start-time 判死）+ `sweep_failed_for_runtime`（allowlist 瞬时错误）+ `♻️ Recovered reply` 标记 + attempts/stale 兜底 `abandoned`。
- **适配**：`get_hermes_home` → `get_intellect_home()`（`intellect_constants.py:43`）；复用 `state.db` + 既有 WAL 写重试；`owner pid+start-time` 复用 `gateway/status.py` 既有 primitives。
- **验收**：纯单测覆盖三检查点状态机、死进程 sweep、allowlist runtime sweep、毒行 abandon。

### GW-106 · P0-3b 接线：流式路径
- **锚点**：`stream_consumer.py` `_send_or_edit`(1115，首消息 1292 / edit+finalize 1213)、`_send_fallback_final`(743→806)；成功信号 = `SendResult.success`（`base.py:1424`）。
- **动作**：在 `_send_or_edit` 与 `_send_fallback_final` 实际发送前 `record_obligation`、发前 `mark_attempting`、据 `SendResult.success` `mark_delivered/failed`；启动时对账本 `sweep_recoverable` 重投。
- **验收**：流式最终回复崩溃于发送中途，重启后带 `♻️` 标记重投一次；发送成功不重复。

### GW-107 · P0-3c 接线：非流式路径
- **锚点**：`run.py:7821` return response → adapter 消息处理器包装器发送（`run.py:2384`）。
- **动作**：在非流式 return 前 `record_obligation`，在 adapter 包装器发送后据 `SendResult` 结清（`mark_delivered/failed`）。
- **验收**：非流式回复同样受账本保护；无重复发送。

### GW-108 · P1-1 SessionDB 开库重试
- **锚点**：`intellect_state.py:303` `SessionDB.__init__`（单次 `create_backend()+initialize()`，失败置 `_last_init_error` 后抛）。
- **动作**：在 `__init__` 内加有界重试 + 退避（如 `_WRITE_RETRY_*` 风格，20ms→150ms 抖动 × N 次），重试间 close 半初始化 backend；失败仍置 `_last_init_error` 后抛（保留既有降级语义）。
- **验收**：注入瞬时 `OperationalError`（前 2 次失败、第 3 次成功）→ 构造成功；持续失败 → 仍抛且 `_last_init_error` 正确；无 WAL fd 泄漏。

---

## Phase 1 — 可观测 + 断连重放（第二批）

### GW-201 · P1-2 循环存活看门狗 + systemd 通知
- **动作**：新增 `gateway/shutdown_watchdog.py` + `gateway/systemd_notify.py`（OS 线程关停看门狗、心跳文件、strikes 探测、floor timer、`READY=1`/`WATCHDOG=1`）。复用 `GATEWAY_SERVICE_RESTART_EXIT_CODE=75`（`restart.py:7`）、`restart_drain_timeout`（`run.py:991`）、`check_systemd_timing_alignment`（`run.py:1894`）。心跳写走 off-loop 线程。
- **验收**：loop 冻结 N 秒 → 看门狗 `os._exit`；systemd 下喂 `WATCHDOG=1`。

### GW-202 · P1-3 WS 事件重放 + 心跳
- **锚点**：`tui_gateway/server.py` `write_json`(379)/`_emit`(400)；`ws.py` `WSTransport.write`(69)→`_safe_send`(105)；重连 teardown `ws.py:186-198`；session dict `server.py:2090`。
- **动作**：新增 `tui_gateway/event_replay.py`（seq + 有界 ring + epoch + `is_truncated`）；在 `_emit` 打 seq、buffer；加 `session.events.since` RPC；重连后回放；`ws.py` 加 TCP keepalive + `gateway.ping`。
- **验收**：客户端断连重连后 `events.since(n)` 回放漏掉事件；重启后 epoch 变化客户端重置水位。

---

## Phase 2 — 门控项（按部署形态裁剪，本窗口不做）

### GW-301 · P2-1 control socket
- 新增 `gateway/control_socket.py`（`identify`/`status` v1），CLI/updater 优先 socket 回退扫描层。

### GW-302 · P2-2 profile 路由 fail-closed
- 复用安全：未实现多 profile 时 `/p/<profile>` 显式拒；再按需移植 `profile_routing.py`。

---

## 实施顺序与依赖

```
GW-101 → GW-102            （审计先行，产出阻塞点清单供 GW-102 修）
GW-103 → GW-104            （模块先行，接线后置）
GW-105 → GW-106 → GW-107   （模块先行，两条路径各自接线）
GW-108                     （独立，可并行）
GW-201 / GW-202            （依赖 Phase 0 的 status 扩展 R4）
GW-301 / GW-302            （门控，视部署形态）
```

- **并行建议**：GW-103/105/108 是纯模块 + 单测，可与 GW-101 审计并行；接线任务（104/106/107）依赖各自模块。
- **每任务交付物**：模块代码 + 单元测试 + （接线类）一个行为测试证明「loop 存活 / 无重复投递 / 租约串行」。

## 验收总闸（Phase 0 完成）
- `pytest` 相关新测试全绿；既有 26,823 测试不回归；
- `ruff check .` 绿（GW-102 门禁生效）；
- gateway 正常起停、正常收发消息、崩溃后能恢复（ledger 重投 + SessionDB 重试 + 看门狗）。
