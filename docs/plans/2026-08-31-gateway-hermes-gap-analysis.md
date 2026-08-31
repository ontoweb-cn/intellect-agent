# Gateway 改进方案 — 基于 Hermes 更新分析（2026-06-15 → 2026-08-30）

> **日期**：2026-08-31 分析 → 2026-08-31 技术评审通过（待实施）
> **状态**：📋 **已评审，待实施**——P0/P1/P2 方案已定，见第三/四节；评审记录见第五节
> **数据来源**：`../hermes-agent/docs/update-summary-2026-06-15-to-2026-08-30.md` §3.2「Gateway（fix 679 / feat 73）」+ 逐提交 `git log`（HEAD `4209d371aa`）
> **本仓 HEAD**：分析时 `feat/agent-core-hermes-gap-p1`（`8564e89`）
> **关联**：与 `2026-08-30-agent-core-hermes-gap-analysis.md` 为**姊妹篇**（前者 agent core，本文 gateway）。scale-to-zero 已由 `2026-07-09-hermes-gateway-scale-to-zero-chronos-refinement.md` 专项覆盖，gateway 生命周期已由 `2026-07-12-w13-gateway-lifecycle-and-openat.md` 覆盖，本文**不重复跟踪**。

---

## 一、结论先行

与 agent core 篇的结论一致：intellect-agent 的 gateway 与 Hermes 的差距**不是「缺很多」，而是「缺几个关键原语 + 几个半成品」**。本窗口 Hermes 的 679 个 `fix(gateway)` 可归纳为 **8 个主题**，其中真正构成差距、且尚未被既有文档覆盖的只有 **4 条硬差距 + 2 条半成品**：

- **硬差距（intellect 完全缺失）**：①事件循环阻塞防护（off-loop + ASYNC lint 门禁）②turn-lease（会话级串行化租约）③可靠投递账本（delivery ledger）④循环存活看门狗 + systemd 通知。
- **半成品（intellect 已有雏形但缺关键一层）**：⑤SessionDB **开库**恢复（写侧重试已有，开库单次即抛）⑥WS 断连事件重放（tui_gateway 无 seq）。
- **已被既有文档覆盖 / 不建议现在做**：scale-to-zero、profile 路由、control socket（后两者依赖多 profile/多进程部署形态，intellect 当前单实例）。

**最重要的判断（沿用 W15 P5 备忘纪律）**：改进应聚焦「补关键原语」，**不机械移植 Hermes 的数字**。本文每一处都标注了 intellect 的实际锚点（file:line），供实施时对照。

---

## 二、Hermes 窗口 gateway 改动全景（8 主题）

| # | 主题 | Hermes 关键提交 / 模块 | 一句话 |
|---|---|---|---|
| A | 事件循环阻塞防护 | `c0ff25a1f8`（off-loop 热路径 + ASYNC210/220/221/251 lint 门禁） | async 里阻塞调用冻结整个 loop |
| B | 循环存活看门狗 + systemd | `gateway/shutdown_watchdog.py`、`gateway/systemd_notify.py` | 进程活着 ≠ loop 活着 |
| C | turn-lease | `gateway/turn_lease.py`（#64934） | 按 session_id 串行 [load→run→flush] |
| D | 可靠投递账本 | `gateway/delivery_ledger.py`（#58818/#41696/#63695） | 最终回复不再无痕丢失 |
| E | scale-to-zero / drain / readiness | `scale_to_zero.py`、`drain_control.py`、`readiness.py` | 自挂起 + 外部 drain 标记 + 健康探测 |
| F | 控制面 control socket | `gateway/control_socket.py`（#92091 step 1） | gateway 自持的 identify/status 面 |
| G | SessionDB 恢复 + 模型愈合 | `session_db_recovery.py`、`4b659f0e33`、`99a6852019` | 开库退避重试 + resume 时愈合失效 provider |
| H | WS 重连 / 多 profile 路由 | `tui_gateway/event_replay.py`、`profile_routing.py`、`6cb1085d3d` | seq 重放 + 层级路由 fail-closed |

---

## 三、差距总览（主题 × 现状 × 优先级）

| 主题 | intellect-agent 现状（已核实锚点） | 关键差距 | 优先级 |
|---|---|---|---|
| A 事件循环阻塞 | 仅 `loop.set_exception_handler` 吞瞬时网络错误；`pyproject.toml` `select` 只含 `PLW1514`+`F`（`:317`），无 ASYNC 门禁 | 阻塞点未审计，无 off-loop，无防回归门禁 | **P0** |
| C turn-lease | `_running_agents` 按 `_quick_key = _session_key_for_source()`（`run.py:3741`）串行；`switch_session`（`session.py:1329`，调用点 `run.py:6003`）制造 key→id 多对一 | 无 session_id 级租约，存在并发写脏 transcript 楔子 | **P0** |
| D 可靠投递 | 最终回复经 `await adapter.send(...)`（`run.py:3689/3709/5849/6221/7805`）一次性发送，无账本/重试/DLQ | finalize→平台 ACK 间崩溃丢最终回复（token 已烧） | **P0** |
| B 循环看门狗 | 无 heartbeat/watchdog/sd_notify/faulthandler（全仓 grep 零命中） | loop 冻结时所有 asyncio 恢复失效，服务管理器只救死进程不救僵尸 | **P1** |
| G SessionDB 开库恢复 | 写侧重试已有（`intellect_state.py:297` `_WRITE_MAX_RETRIES=15` 抖动）；**开库**单次 `create_backend()+initialize()`，失败置 `_last_init_error` 即抛（`intellect_state.py:303`） | 瞬时 `OperationalError` 让 gateway 起不来，无退避/健康发布 | **P1** |
| H WS 事件重放 | `tui_gateway/ws.py` 无 seq/replay/heartbeat（grep 零命中）；断连只把 transport 指回 stdio | 中 turn 断连丢事件，无 catch-up | **P1** |
| E scale-to-zero | `ScaleToZeroConfig` 惰性空壳（`config.py:433`，注释自述 "not yet wired" HP-406） | — | **已覆盖**（见 `2026-07-09-…-scale-to-zero-chronos-refinement.md`） |
| F control socket | 磁盘 JSON 标记 + 信号（`status.py:518/563`） | — | **P2（门控）** |
| H profile 路由 | 无多 profile（`run.py` 注释标注 "future"） | — | **P2（门控）** |

> **不列为改进项**（intellect 已存在且更成熟）：崩溃恢复流水线（`resume_pending` + `suspend_recently_active` + `.clean_shutdown` + stuck-loop 计数，`run.py:1852/2270-2314`）、WAL 写重试退避、作用域锁过期驱逐、takeover/planned-stop 标记、systemd TimeStopSec 对齐校验（`run.py:1894` → `shutdown_forensics.py:322`）。

---

## 四、分优先级方案

### P0 —— 可靠性地基（首批，1–2 周，全部独立小模块可单测）

**P0-1 事件循环阻塞审计 + ASYNC lint 门禁（主题 A）**
- **问题**：intellect 单一 asyncio loop 驱动所有平台适配器（Telegram long-poll / Discord shard 都在 loop 内），任何阻塞调用冻结全部平台且无检测。Hermes 已知事故：`getaddrinfo` 冻结 17 min、`start_gateway --replace` 冻结 10 s。
- **动作**：
  1. 审计 `run.py` / `platform_handlers.py` / `platforms/` 中 async 函数内的 `time.sleep` / `subprocess.run` / 同步 HTTP / 大文件 `open().read()`；
  2. 热路径改 `asyncio.to_thread` / `await asyncio.sleep`（重点：`run.py:991` 附近 `--replace` 的 sleep、`shutdown_forensics.py` 诊断子进程、webhook/tts 的 IO）；
  3. `pyproject.toml` `[tool.ruff.lint]` `select` 增 `ASYNC210/220/221/251`（flake8-async，ruff 内置），对存量点位用 `per-file-ignores`（`:323` 已存在）建 frozen 基线逐条 burn-down，新文件/新违规 CI fail。
- **评审标注**：这是**策略变更**——当前 `select` 刻意极简（注释「wrangle typechecks 期间其余 lint 关闭」）。建议分两步：先出「阻塞点清单 + 修复」PR，再单独上 lint 门禁 PR，避免在 30k 行遗留代码上一次性引爆 CI。

**P0-2 引入 turn-lease（主题 C）**
- **问题**：busy guard 按 routing key 串行，transcript 归属 session_id，`switch_session` / `/resume` / topic tip-walk 使 key→id 多对一——两个 key 映射同一 session_id 时两个 agent 并发跑，flush 交错，产生永久 `user;user` 交替楔子（`repair_message_sequence` 反复重修）。
- **动作**：新增 `gateway/turn_lease.py`（移植 Hermes 语义）：在 `switch_session`/tip-walk 之后、transcript load 之前，按**解析后 session_id** `acquire`，dispatch 层 `finally` 释放；超时 `TurnLeaseTimeoutError` 拒掉该 turn 并回可见 resend 提示；保留 generation+identity 校验释放、有界注册表、压缩旋转 `rebind`。
- **评审标注**：intellect 已有 `_release_session_guard`（`platforms/base.py:3236`，adapter 级）与 `_release_running_agent_state`（`agent_runner.py:1243`），都是 routing-key 粒度。租约是**叠加**在它们之上的 session_id 粒度串行，不是替换。

**P0-3 可靠投递账本（主题 D）**
- **问题**：最终 agent 回复经 `await adapter.send(...)` 一次性发送，finalize 与平台 ACK 之间崩溃 → 回复无痕丢失（token 已烧）。
- **动作**：新增 `gateway/delivery_ledger.py`，**接线目标不是 `gateway/delivery.py`**（那是 cron 输出的 `DeliveryRouter`，另一条路径），而是 `run.py` 的最终回复 `adapter.send(...)` 站点 + `stream_consumer.py` finalize：复用 `state.db`（`get_intellect_home()` / `state.db`，`intellect_constants.py:43`）+ 已有 WAL 写重试；三条检查点 `record_obligation` / `mark_attempting` / `mark_delivered|failed`；启动 `sweep_recoverable`（owner pid+进程启动时间判死 + `deliverable_platforms` 限定避免烧预算）；reconnect 后 `sweep_failed_for_runtime` 只认领 allowlist 瞬时错误；`attempting/failed` 重投带 `♻️ Recovered reply` 可见标记（诚实 at-least-once，绝不静默重复）；attempts cap + stale 兜底 → `abandoned`。所有调用 try/except，账本失败**绝不阻塞真实发送**。
- **评审标注（本节最关键的评审修正）**：初稿把问题锚定在 `delivery.py:195 deliver()`，**有误**——那是 cron 输出路由。Hermes `delivery_ledger` 包裹的是「最终回复 → 平台发送」路径，两者是不同的 delivery 域。已核实最终回复发送点为 `run.py:3689/3709/5849/6221/7805` 等 `await adapter.send(...)`。

### P1 —— 可观测 + 开库恢复 + 断连重放（第二批）

**P1-1 SessionDB 开库恢复（主题 G）**
- **动作**：新增 `gateway/session_db_recovery.py`：`RecoverableHandleCache` 单飞 + 指数退避（1s→60s）打开失败句柄；把 `ok/retrying/unavailable` 发布进 `write_runtime_status`（`status.py:518`）。
- **评审标注**：与 agent-core 篇 P0-4「SessionDB 读方法脱离写锁」**是两件事**（读锁分离已实施完成，本项是「开库」维度），勿混淆。

**P1-2 循环存活看门狗 + systemd 通知（主题 B）**
- **动作**：新增 `gateway/shutdown_watchdog.py` + `gateway/systemd_notify.py`：OS 线程关停看门狗（drain 超时 + grace 后 `faulthandler` 转储 → `os._exit`，退出码复用 `GATEWAY_SERVICE_RESTART_EXIT_CODE=75`，`restart.py:7`）；心跳文件；带 strikes 的 loop 存活探测线程；floor timer；`READY=1`/`WATCHDOG=1` 仅在 loop 滞后预算内喂。
- **评审标注**：复用 intellect 已有 `restart_drain_timeout`（`run.py:991`）与 `check_systemd_timing_alignment`（`run.py:1894`）。**心跳写必须 off-loop 线程**（Hermes #90502 教训：看门狗自己的 on-loop 心跳 fsync 会冻结它监控的 loop）。

**P1-3 WS 事件重放 + 心跳（主题 H）**
- **动作**：新增 `tui_gateway/event_replay.py`：每 session 单调 `seq` + 有界 ring buffer + `session.events.since` RPC + epoch（进程 uuid）重启检测 + `is_truncated`；`ws.py` 加 TCP keepalive + `gateway.ping`。

### P2 —— 门控项（按部署形态裁剪，不在本窗口做）

**P2-1 control socket**（主题 F）：`identify`/`status` v1，CLI/updater 优先 socket 回退扫描层。依赖「多进程/多 profile」部署才有收益，当前单实例可缓。

**P2-2 profile 路由 fail-closed**（主题 H）：先做复用安全（未实现多 profile 时 `/p/<profile>` 显式拒而非误服务 owner profile），再按需移植 `profile_routing.py` 层级路由。

---

## 五、评审记录（2026-08-31）

> 技术评审为「通过」，核对方式：对负荷最重的论断直接读 intellect-agent 源码 + grep 验证。

**已核实成立的论断**：

| 论断 | 核实结果 |
|---|---|
| turn_lease / turn_hold / TurnLease 不存在 | ✅ 全仓 grep 零命中（仅 `_release_session_guard`/`_release_running_agent_state` 等无关项） |
| delivery_ledger / obligation 不存在 | ✅ grep 零命中 |
| ScaleToZero 惰性 | ✅ 仅 `config.py` 出现，注释自述 "not yet wired"（HP-406） |
| 无循环看门狗/heartbeat/sd_notify/faulthandler | ✅ grep 零命中（命中的 heartbeat 均为 agent_runner 进度心跳/yuanbao proto） |
| ASYNC lint 未启用 | ✅ `select = ["PLW1514", "F"]`，`per-file-ignores` 已存在 |
| `_running_agents` 按 routing key | ✅ `run.py:3741` `_quick_key = _session_key_for_source(source)` |
| 多对一键→id 楔子存在 | ✅ `session.py:1329` `switch_session(session_key, target_session_id)` + 调用点 `run.py:6003` |
| SessionDB 开库单次即抛 | ✅ `intellect_state.py:303` 单次 `create_backend()+initialize()`，失败置 `_last_init_error` 抛；写侧 `_WRITE_MAX_RETRIES=15` 抖动确在 |
| WS 无重放/心跳 | ✅ `tui_gateway/ws.py` grep 零命中 |
| 移植锚点齐备 | ✅ `get_intellect_home`（`intellect_constants.py:43`）、`write_runtime_status`/`read_runtime_status`（`status.py:518/563`）、`GATEWAY_SERVICE_RESTART_EXIT_CODE=75`（`restart.py:7`）、`restart_drain_timeout`、`check_systemd_timing_alignment`（`run.py:1894`） |

**评审修正（2 处）**：

1. **P0-3 接线目标**：初稿将问题锚在 `gateway/delivery.py`（cron 输出路由），**有误**。最终回复发送点是 `run.py` 的 `await adapter.send(...)`（`3689/3709/5849/6221/7805` 等）+ `stream_consumer.py` finalize，与 cron `delivery.py` 是两条独立路径。方案已改。
2. **P0-1 策略变更**：`pyproject.toml` 当前刻意极简 lint，上 ASYNC 门禁是策略决定，须分「修复 PR」+「门禁 PR」两步，避免一次性引爆 CI。

**评审结论**：P0-1/2/3、P1-1/2/3 方案在 intellect 上的差距真实存在、锚点齐备、可独立小模块落地；P2 项门控。**通过，可进入实施。**

---

## 六、实施顺序与统一原则

**顺序**：
1. **第一批（可靠性地基）**：P0-1（阻塞审计+门禁）、P0-2（turn-lease）、P0-3（delivery ledger）、P1-1（开库恢复）——四项独立、互不依赖，直接对应真实事故类。
2. **第二批（可观测/断连）**：P1-2（看门狗+systemd）、P1-3（事件重放+心跳）。
3. **第三批（门控）**：P2-1、P2-2，按多 profile/多进程部署形态决定是否启动。

**统一原则**（从 Hermes 实践提炼，务必沿用）：
- 所有新增模块 **best-effort、永不阻塞主路径**（ledger 写失败、sd_notify 失败、control socket 绑不上都只是降级）；
- **失败关闭优先于失败开放**（lease 超时拒 turn、multiplex 未实现时 fail-closed、看门狗 `os._exit` 交给服务管理器救）；
- 每个「有界」都用**显式 max + abandon/epoch/max-age 兜底**，毒行/孤儿标记必须自愈。
