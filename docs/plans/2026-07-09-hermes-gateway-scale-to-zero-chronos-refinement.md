# 下一阶段细化 — Gateway scale-to-zero（HP-406）+ Chronos 托管 cron（HP-408）

> 文档日期：2026-07-09
> 上游：`2026-07-08-hermes-v0.16-v0.18-port-todo.md`（HP-406 / HP-408）、`…-port-design.md` §5.9.2
> 触发决议：Q4 scale-to-zero 已确认需求（2026-07-09）；Chronos 由"不移植"改为"移植"
> 状态：⬜ 待评审签字后开工

---

## 一、范围与目标

一个内聚的"Gateway 生产化"小阶段，两件事：

1. **HP-406 scale-to-zero**：gateway 空闲自停 + webhook 唤醒，降低常驻算力成本。
2. **HP-408 Chronos 托管 cron**：外部触发 provider，解决 scale-to-zero 停机窗口内进程内 tick 死亡导致 cron 不触发的问题。

**不做**：relay 连接器（§5.9.3，仅需求调研）；Chronos 托管服务本体（我们只做 Intellect 侧的 provider + 触发端点，托管由用户自建 systemd timer / 云 scheduler / 外部 Chronos 实例承担）。

**默认关闭**：`gateway.scale_to_zero.enabled` 默认 `false`；`cron.provider` 默认 `builtin`。零配置行为与现状完全一致。

---

## 二、已核实的代码事实（细化依据）

| 事实 | 位置 | 对设计的影响 |
|------|------|-------------|
| gateway 已有优雅 drain：`_draining` / `_shutdown_event` / `_restart_drain_timeout` | `gateway/run.py:995,1295,1271` | HP-406a 复用 drain，不新造 |
| gateway 已有 systemd 定时对齐检查 `check_systemd_timing_alignment` + `intellect gateway service install --replace` | `gateway/run.py:1890`、`gateway/shutdown_forensics.py` | HP-406b **扩展**现有 unit 生成器，非从零 |
| 存在 `ServiceManager` 抽象（systemd/launchd/windows/s6） | `intellect_cli/service_manager.py:194` | socket 单元加在 `SystemdServiceManager` |
| session idle 是**会话级**自动重置（`policy.idle_minutes`、`mode∈{idle,daily,both}`、Rust `_rust_check_expiry_batch`） | `gateway/session.py:879,933` | HP-406a 需**进程级**"全局无活跃会话 X 分钟"，是新逻辑但可复用 idle 时间戳 |
| cron 为进程内 tick：`tick()` + 文件锁 `~/.intellect/cron/.tick.lock` + `get_due_jobs/mark_job_run/advance_next_run/run_job` | `cron/scheduler.py`、`cron/__init__.py:14` | HP-408 的 provider 抽象包住 `tick()`；文件锁天然给幂等 |
| gateway 配置直读 `load_gateway_config()` + `_validate_gateway_config()` | `gateway/config.py:710,1194` | 新键的校验挂这里 |
| 平台 transport **无结构化声明**，仅 manifest 描述文字 | `plugins/platforms/*/plugin.yaml` | HP-406c 需新增 `transport` 能力字段 |
| `deploy/` 无任何 systemd 静态资产（仅 `lightrag/`） | `deploy/` | socket 单元由生成器产出，不放静态文件 |

> ⚠️ **纠偏**：port-todo 早前写"systemd 从零"。准确表述：**unit 生成器已存在**（`service_manager.py` + `gateway service install`），socket-activation 是**新增单元类型**，工作量比"从零"低，比"改字段"高。

---

## 三、实施顺序

```text
HP-406a idle 自停 ─┐
HP-406c transport 门控 ─┼─► HP-406b socket 单元 ─► HP-406d 集成测试
                        │
HP-408a provider 抽象 ──┴─► HP-408b chronos 插件 ─► HP-408c run-due 端点 ─► HP-408d 幂等 ─► HP-408e 唤醒对齐 ─► HP-408f 测试+文档
```

- **硬门禁**：HP-406b（socket 唤醒路径）必须先于 HP-408c（run-due 端点靠 socket 激活）。
- HP-406a / HP-406c / HP-408a 可并行开工（互不依赖）。
- 建议一个 PR：HP-406 全量；HP-408 单独 PR（依赖 406 merge）。

---

## 四、HP-406 scale-to-zero 细化

### 4.1 HP-406a — 进程级 idle 自停（1–2d）
- 新增 gateway 后台协程 `_scale_to_zero_watcher`：周期检查"全局最近活跃时间戳"，无活跃会话且超 `idle_timeout_minutes` → 触发优雅 drain（复用 `_draining` + `_shutdown_event`）→ 进程退出码 0。
- 活跃判定复用 session 层 `updated_at` 的**全局最大值**；drain 期间禁止再自停（幂等 guard）。
- **拒绝条件**：存在 in-flight 委派（HP-202 background children）或未 drain 的回合 → 推迟自停。
- 配置：`gateway.scale_to_zero.idle_timeout_minutes`（默认 30）。

### 4.2 HP-406b — systemd socket activation（1–2d）
- 扩展 `SystemdServiceManager`：新增 `intellect gateway service install --socket-activation`，生成一对单元：
  - `intellect-gateway.socket`：`ListenStream=` 列出所有 webhook 平台端口 + WebUI 端口 + run-due 端点端口；`Accept=no`。
  - `intellect-gateway.service`：改为 socket-activated（`Requires=…socket`，去掉 `WantedBy=multi-user.target` 的 auto-start）。
- 复用 `check_systemd_timing_alignment`：`TimeoutStopSec` ≥ drain timeout（否则 SIGKILL 打断 drain）。
- launchd/windows/s6：本轮**仅 systemd**；其余管理器 `--socket-activation` 报"未支持"清晰错误。

### 4.3 HP-406c — transport 门控（0.5d）
- 平台 manifest 新增能力字段 `transport: webhook | persistent`（缺省 `persistent` = 安全默认，不可 scale-to-zero）。首批标注：
  - `webhook`：`api_server`、`webhook`、`msgraph_webhook`、`whatsapp_cloud`、`line`、`teams`、`sms`、`bluebubbles`、`slack`、`telegram`(webhook 模式)
  - `persistent`：`discord`、`irc`、`signal`、`simplex`、`matrix`、`feishu`、`homeassistant`、`mattermost`、`wecom`、`email`、`telegram`(polling 模式)
- `_validate_gateway_config()`：`scale_to_zero.enabled=true` 且任一活跃平台 `transport=persistent`（或 telegram polling 模式）→ **拒绝启动**，可读错误列出冲突平台。

### 4.4 HP-406d — 集成测试（1d）
- E2E：idle → 自停 → 模拟 webhook 入站 → socket-activate 拉起 → drain → 消息不丢。
- 校验测试：persistent 平台 + enabled → 启动被拒。
- 时序测试：`TimeoutStopSec < drain` → 告警命中。

**HP-406 验收**：默认 false 零回归；webhook-only 部署可自停/唤醒；persistent 平台配置层拒绝；drain 不被 SIGKILL 截断。

---

## 五、HP-408 Chronos 托管 cron 细化

### 5.1 HP-408a — cron provider 抽象（1d）
- `cron/scheduler.py` 抽出 `run_due()`（= 现 `tick()` 的到期执行主体，去掉"每 60s 被调用"的假设）。
- 引入 `cron.provider`：
  - `builtin`（默认）：gateway 每 60s 调 `run_due()`（现状）。
  - `chronos`：gateway **不**自 tick；由外部触发调 `run_due()`。
- 保持文件锁 `~/.intellect/cron/.tick.lock` 不变（两 provider 共用，天然防并发）。

### 5.2 HP-408b — chronos provider 插件（1d）
- `plugins/cron_providers/chronos/`（新目录，仿 `plugins/platforms/*` 结构）：`plugin.yaml` + 配置 schema。
- 配置键覆盖三条 loader（`load_cli_config` / `load_config` / gateway 直读）：`cron.provider`、`cron.chronos.{trigger_token, allowed_source_cidr?}`。

### 5.3 HP-408c — 鉴权 run-due 端点（1d）
- gateway HTTP 面新增 `POST /cron/run-due`（随 webhook 服务器同端口/独立端口，进 socket 的 `ListenStream`）。
- 鉴权：`cron.chronos.trigger_token`（bearer / HMAC）；可选源 IP CIDR 白名单。
- 行为：验证 → 调 `run_due()` → 返回执行摘要（跑了几条、下次到期）。外部 Chronos/systemd timer/云 scheduler 按时 ping 此端点。

### 5.4 HP-408d — 幂等/去重（0.5d）
- 复用文件锁 + `mark_job_run`/`advance_next_run` 的 `last_run_at` 比对：重复 ping 同一分钟不重复 fire。
- 并发 ping：文件锁串行化，第二个 ping 拿不到锁即返回"已在跑"。

### 5.5 HP-408e — 与 HP-406 唤醒对齐 + 校验（0.5d）
- socket 的 `ListenStream` 含 run-due 端口 → Chronos ping 可 socket-activate 停机的 gateway。
- `_validate_gateway_config()`：`scale_to_zero.enabled=true` 且存在启用的 cron job 且 `cron.provider != chronos` → **拒绝启动**（否则停机窗口 cron 静默丢失），错误指向本文件。

### 5.6 HP-408f — 测试 + 文档（1d）
- 测试：外部触发 mock（token 正确/错误/过期）、去重（同分钟双 ping）、provider 切换（builtin↔chronos 零回归）、scale-to-zero + chronos 联合 E2E。
- 文档：Chronos/systemd-timer/云 scheduler 三种外部触发接法；token 生成与轮换；与内置 tick 的取舍。

**HP-408 验收**：scale-to-zero 下定时任务不丢；`builtin` 零回归；外部触发鉴权且幂等（同分钟不重复 fire）。

---

## 六、配置 schema（三 loader 全覆盖）

```yaml
gateway:
  scale_to_zero:
    enabled: false            # 默认 false，零回归
    idle_timeout_minutes: 30  # 无活跃会话多久后自停
cron:
  provider: builtin           # builtin | chronos
  chronos:
    trigger_token: ""         # run-due 端点鉴权
    allowed_source_cidr: null # 可选源 IP 白名单
```

- 三处落地：`intellect_cli/config.py` `DEFAULT_CONFIG`（:674）；`load_config()`（:4813）路径；`gateway/config.py` `load_gateway_config()`（:710）+ `_validate_gateway_config()`（:1194）。
- 校验集中在 `_validate_gateway_config()`：transport 冲突、cron provider 冲突、systemd 时序对齐三类。

---

## 七、风险矩阵

| 风险 | 任务 | 级别 | 缓解 |
|------|------|------|------|
| drain 被 SIGKILL 截断丢消息 | HP-406b | 高 | 复用 `check_systemd_timing_alignment`；`TimeoutStopSec ≥ drain`；测试覆盖 |
| persistent 平台误开 scale-to-zero → 唤不醒 | HP-406c | 高 | manifest `transport` 缺省 persistent（安全默认）+ 启动拒绝 |
| scale-to-zero + builtin cron → 停机窗口丢任务 | HP-408e | 高 | 配置校验强制 `cron.provider=chronos`；否则拒绝启动 |
| run-due 端点成为未鉴权触发面 | HP-408c | 中 | 强制 token；可选 CIDR；默认端点不监听除非 provider=chronos |
| 重复 ping 重复 fire | HP-408d | 中 | 文件锁 + `last_run_at` 幂等 |
| 冷启动延迟（socket-activate 拉起 gateway）拖慢首个 webhook | HP-406b | 低 | 文档标注冷启动预算；idle_timeout 默认 30min 降低频次 |
| telegram 双模式（webhook/polling）分类歧义 | HP-406c | 低 | 按运行时实际模式判定，非按平台名 |

---

## 八、未决问题（开工前签字）

1. **run-due 端口拓扑**：run-due 与 webhook 平台**共端口**（同一 aiohttp app 加路由）还是**独立端口**？建议共端口（少一个 ListenStream，少一处鉴权面）。
2. **idle 自停默认值**：30min 是否合适？serverless 场景可能要更短（5–10min）。
3. **transport 字段落地形式**：manifest 静态字段 vs 运行时 adapter 能力查询？建议 manifest 静态 + telegram 运行时覆盖。
4. **Chronos 客户端范围**：只做"被动 run-due 端点"（外部任意调度器可用）还是也提供一个 `intellect cron chronos-ping` 客户端/systemd timer 模板？建议后者作为 HP-408f 文档附带的 `.timer` 样例，不单列任务。

---

## 九、阶段出口标准

- [ ] HP-406a–d：webhook-only 部署可 idle 自停 + socket 唤醒；persistent 平台配置拒绝；drain 不截断
- [ ] HP-408a–f：`cron.provider=chronos` + scale-to-zero 下定时任务不丢；builtin 零回归；触发鉴权+幂等
- [ ] 新键三 loader 全覆盖 + `_validate_gateway_config` 三类校验
- [ ] `scripts/run_tests.sh` 全绿；涉及 Rust（如无）不改 schema
- [ ] design §5.9.2 与本细化一致（已对齐）；port-todo HP-406/408 标记随实现推进
