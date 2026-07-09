# 下一阶段细化 — Gateway scale-to-zero（HP-406）+ Chronos 托管 cron（HP-408）

> 文档日期：2026-07-09
> 上游：`2026-07-08-hermes-v0.16-v0.18-port-todo.md`（HP-406 / HP-408）、`…-port-design.md` §5.9.2
> 触发决议：Q4 scale-to-zero 已确认需求（2026-07-09）；Chronos 由"不移植"改为"移植"
> 状态：🔄 二轮评审（approve-with-revisions，§十/§十一）；B2 定案 (b)；HP-406e 真正 aiohttp 直改=7（slack/whatsapp_cloud 不可 activation，telegram 单列）；开工前须解 B1、B3–B8 + 十一.5

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
| gateway 已有 systemd 定时对齐检查 `check_systemd_timing_alignment` + unit 生成器 | `gateway/run.py:1890`、`gateway/shutdown_forensics.py`、**生成器在 `intellect_cli/gateway.py:2193 generate_systemd_unit()`（F1 修订）** | HP-406b **扩展** `gateway.py` 生成器 + staleness/refresh/scope helpers，非从零 |
| 存在 `ServiceManager` 抽象（systemd/launchd/windows/s6） | `intellect_cli/service_manager.py:194`（仅 systemctl 包装，**不生成 unit 文本**） | socket 单元加在 `gateway.py` 生成器，**非** `service_manager.py` |
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
HP-406c wake_class 门控 ─┼─► HP-406b socket 单元 ─► HP-406e adapter fd 继承 ─► HP-406d 集成测试
                        │
HP-408a provider 抽象 ──┴─► HP-408b chronos 插件 ─► HP-408c run-due 端点 ─► HP-408d 幂等 ─► HP-408e 唤醒对齐 ─► HP-408f 测试+文档
```

- **硬门禁**：HP-406b（socket 单元）→ HP-406e（adapter fd 继承）→ 才有 webhook 唤醒；HP-406b 也先于 HP-408c（run-due 靠 socket 激活）。
- HP-406a / HP-406c / HP-408a 可并行开工（互不依赖）。
- 建议一个 PR：HP-406 全量（含 406e：7 个 aiohttp adapter 直改 + telegram 单列，见 §4.5/§十一）；HP-408 单独 PR（依赖 406 merge）。

---

## 四、HP-406 scale-to-zero 细化

### 4.1 HP-406a — 进程级 idle 自停（1–2d）
- 新增 gateway 后台协程 `_scale_to_zero_watcher`：满足**自停主门禁（AND，B8）**时触发优雅 drain（复用 `_draining` + `_shutdown_event`）→ 退出码 0。
- **自停主门禁（AND，5 项 + 时间）**：`_running_agents` 无活代理（含 pending sentinel，覆盖 streaming）**且** `process_registry.count_running()==0` **且** `async_delegation.count_running_delegations()==0`（HP-202 委派，`tools/async_delegation.py:80`；第二轮评审补）**且** 无 in-flight `run_due`（覆盖 `_deliver_result`）**且** 全局 `updated_at` 空闲超 `idle_timeout_minutes`。任一不满足 → 推迟自停。
- **下限保护**：进程启动后至少存活 `min_uptime_seconds`（默认 120）才允许自停，防"拉起即停"抖动（定案 §8.2）；drain 期间禁止再自停。
- 配置：`gateway.scale_to_zero.idle_timeout_minutes`（默认 30）、`min_uptime_seconds`（默认 120）。

### 4.2 HP-406b — systemd socket activation（评审后 2d；配合 406e fd 继承，见 B2 定案 (b)）
- 扩展 **`intellect_cli/gateway.py` 的 `generate_systemd_unit()` + `systemd_install()` + staleness helpers**（F1）：新增 `intellect gateway service install --socket-activation`，生成一对单元：
  - `intellect-gateway.socket`：`ListenStream=` 列出**所有 wake_class=webhook 平台端口 + WebUI + run-due 端口**（B2 定案 (b)）；`Accept=no`；平台 socket 由 HP-406e 的 adapter fd 继承消费。
  - `intellect-gateway.service`：改为 socket-activated（`Requires=…socket`，去掉 `WantedBy` auto-start）。
- 复用 `check_systemd_timing_alignment`：`TimeoutStopSec` ≥ drain timeout（drain 默认 180s → ≥ 210s）。
- **socket-handoff 不变量**：自停时进程**不得**提前关闭 systemd 传入的监听 fd，入站连接排入 `.socket` backlog 由重激活实例服务（防 drain 窗口丢消息）。
- launchd/windows/s6：本轮**仅 systemd**；其余管理器 `--socket-activation` 报清晰"未支持"。

### 4.3 HP-406c — wake_class 门控（评审后 0.5–1d，依赖 PlatformEntry 承载）
- 平台 manifest 新增能力字段 **`wake_class: webhook | persistent | dual`**（改名避开与 `Platform.transport` 冲突，B6；缺省 `persistent` = 安全默认，不可 scale-to-zero）。首批标注：
  - `webhook`：`api_server`、`webhook`、`msgraph_webhook`、`line`、`teams`、`sms`、`bluebubbles`
  - `dual`（校验时从 `TELEGRAM_WEBHOOK_URL` 重导，F4；webhook 模式走 tornado fd，见 §4.5）：`telegram`
  - `persistent`：`discord`、`irc`、`signal`、`simplex`、`matrix`、`feishu`、`homeassistant`、`mattermost`、`wecom`、`email`、**`slack`**（Socket Mode 出站 WS、无入站端口，二轮评审重分类）、**`whatsapp_cloud`**（自身无入站 server；webhook 若需唤醒须经 generic `webhook` 平台端口）
- 接线（F2）：`PluginManifest` 解析 `wake_class`（`plugins.py:1267`）→ 承载到 `PlatformEntry`（`platform_registry.py:39`）→ `_validate_gateway_config()` 查表；**须先核对 registry 在 validation 时已填充**。
- 校验：`scale_to_zero.enabled=true` 且任一活跃平台 `persistent`（或 `dual` 平台处 polling）→ **拒绝启动**，可读错误列冲突平台。

### 4.4 HP-406d — 集成测试（1d）
- E2E：idle → 自停 → 模拟 webhook 入站 → socket-activate 拉起 → drain → 消息不丢。
- 校验测试：persistent 平台 + enabled → 启动被拒。
- 时序测试：`TimeoutStopSec < drain` → 告警命中。
- webhook 唤醒测试：停机 → 入站 webhook 打平台端口 → socket-activate 拉起 → 正常处理（验证 406e fd 继承生效）。

### 4.5 HP-406e — webhook adapter fd 继承（SockSite 改造，B2 定案 (b)，评审后 ~4.5–6.5d）
- 共享 helper `get_activation_socket(port) -> socket|None`：读 systemd `LISTEN_FDS`/`LISTEN_PID`，校验 `LISTEN_PID==os.getpid()`，按 `getsockname()` 端口匹配、**每 fd 单次消费**，返回继承 socket。
- **原生 aiohttp 直改（7 个，EASY）**：`api_server`、`webhook`、`msgraph_webhook`、`line`、`teams`、`sms`、`bluebubbles` —— 起站处有继承 socket → `web.SockSite(runner, sock)`；否则回退 `web.TCPSite`（非 activation 部署**零回归**）。
- **telegram 单列（HARD，二轮评审）**：webhook 模式走 PTB `Updater.start_webhook(unix=<socket>)` → tornado `add_socket`，**非** `SockSite`；须验证 TCP socket 被接受 + `UNIX_AVAILABLE`。
- **api_server guard**：其预检端口 `connect()`（`adapter.py:4285-4292`）在 activation 下会误判"端口占用"而拒启动 → 有继承 socket 时绕过。
- **descope**：`slack`（Socket Mode 无入站端口）与 `whatsapp_cloud`（无入站 server）**不可 socket-activation**，已重分类出 webhook 集（§4.3）。
- 不变量：activation 下配置 host/port 变 advisory，须与 `.socket` 的 `ListenStream` 端口锁定（加测试）。

**HP-406 验收**：默认 false 零回归；webhook 部署可自停 + **任意入站 webhook 唤醒**；persistent 平台配置层拒绝；drain 不被 SIGKILL 截断；非 activation 部署 `TCPSite` 回退零回归。

---

## 五、HP-408 Chronos 托管 cron 细化

### 5.1 HP-408a — cron provider 抽象（1d）
- `cron/scheduler.py` 抽出 `run_due()`（= 现 `tick()` 的到期执行主体，去掉"每 60s 被调用"的假设）。
- **`run_due(catch_up=False)`**：默认 builtin 路径行为不变；**外部触发传 `catch_up=True`**，绕过 `get_due_jobs()` 的快进丢弃（B1，编辑落点 `cron/jobs.py get_due_jobs`），逐条补跑过期周期任务，每任务**恰好一次**（`advance_next_run` 跳下一未来点）。催群受 `cron.max_parallel_jobs`（默认无上界）约束，chronos 下建议设上限。
- 引入 `cron.provider`：
  - `builtin`（默认）：gateway 每 60s 调 `run_due()`（现状）。
  - `chronos`：gateway **不**自 tick；外部触发调 `run_due(catch_up=True)`。**但 `_start_cron_ticker` 搭载的维护任务（频道刷新/缓存清理/paste/curator）须迁到 cadence-independent keepalive，勿随 ticker 一起停（B7）**。
- 保持文件锁 `~/.intellect/cron/.tick.lock` 不变（两 provider 共用，天然防并发）。

### 5.2 HP-408b — chronos provider 插件（1d）
- `plugins/cron_providers/chronos/`（新目录，仿 `plugins/platforms/*` 结构）：`plugin.yaml` + 配置 schema。
- 配置键覆盖三条 loader（`load_cli_config` / `load_config` / gateway 直读）：`cron.provider`、`cron.chronos.{trigger_token, allowed_source_cidr?}`。

### 5.3 HP-408c — 鉴权 run-due 端点（1d）
- gateway 起**专用极小 aiohttp app**（仅 `POST /cron/run-due` + `GET /healthz`），独立端口 `gateway.scale_to_zero.cron_trigger_port`（默认 8722），仅 `cron.provider=chronos` 时绑定，端口进 socket 的 `ListenStream`（定案 §8.1）。
- 鉴权：`cron.chronos.trigger_token`（bearer / HMAC）；可选源 IP CIDR 白名单。
- 行为：验证 → 调 `run_due()` → 返回执行摘要（跑了几条、下次到期）。外部 Chronos / systemd timer / 云 scheduler 按时 ping 此端点。

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
    min_uptime_seconds: 120   # 拉起后最少存活，防"拉起即停"抖动
    cron_trigger_port: 8722   # run-due 专用端口，仅 provider=chronos 时绑定
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

## 八、开工前定案（已细化，2026-07-09）

> 4 项均已定案；**#1 由原倾向「共端口」修正为「专用端口」**（依据：webhook 平台各占独立端口，无共享 HTTP server）。

### 8.1 run-due 端口拓扑 → **专用 run-due 端口**（修正）
- 代码事实：webhook 平台各自起独立 aiohttp server、各占独立端口（Teams `:3978`、LINE `:8646`），`gateway/platform_handlers.py` 逐个拉起，**无共享 gateway HTTP server**。
- 定案：gateway 起一个极小 aiohttp app，仅 `POST /cron/run-due` + `GET /healthz`，**独立端口** `gateway.scale_to_zero.cron_trigger_port`（默认 `8722`），仅在 `cron.provider=chronos` 时绑定。
- 理由：与平台解耦（不依赖某平台被启用）、独立鉴权与生命周期；socket unit 本就列所有端口，多一个成本近零。

### 8.2 idle 自停默认值 → **30 min + min_uptime 下限**
- 定案：`idle_timeout_minutes` 默认 **30**；新增下限 `min_uptime_seconds` 默认 **120**（刚拉起至少活 2 分钟，防「拉起即停」抖动）。均可配。
- 画像指引（写入 HP-408f 文档）：serverless / 成本敏感 5–10 min；高频 webhook 60 min 或关闭。
- 理由：冷启动数秒量级，30 min 摊薄抖动；下限防边界抖动。

### 8.3 wake_class 落地形式 → **manifest 静态 + dual 从 env 重导**
- 代码事实：`plugin.yaml` 已有顶层 `kind:` 标量可平行加字段；telegram 运行时双模，但**校验期无 adapter 实例**，须从 `TELEGRAM_WEBHOOK_URL`（env/config）重导模式（F4），抽共享 helper 防漂移。
- 定案：manifest 加 **`wake_class: webhook | persistent | dual`**（改名避 `Platform.transport` 冲突，B6），缺省 `persistent`（安全默认）。仅 `dual`（telegram）校验时按 `TELEGRAM_WEBHOOK_URL` 判：有=webhook 放行、无=polling 拒绝。
- 接线须落到 `PlatformEntry`（F2），首批分类见 §4.3。

### 8.4 Chronos 客户端范围 → **被动端点 + .timer 样例 + curl 文档，无常驻 client**
- 认知：scale-to-zero 下不能有常驻客户端（违背自停）。
- 定案：① 被动 `/cron/run-due` 端点（契约）；② systemd `.timer` + `.service` 样例由 HP-406b 的 `service install` 生成器产出；③ 云 scheduler / k8s CronJob 的 curl 契约 + token 轮换写入 HP-408f 文档。**不做**常驻 Chronos client daemon。

---

## 九、阶段出口标准

- [ ] HP-406a–e：webhook 部署可 idle 自停 + **任意入站 webhook 唤醒**（406e fd 继承）；persistent 平台配置拒绝；drain 不截断
- [ ] HP-408a–f：`cron.provider=chronos` + scale-to-zero 下定时任务不丢；builtin 零回归；触发鉴权+幂等
- [ ] 新键三 loader 全覆盖 + `_validate_gateway_config` 三类校验
- [ ] `scripts/run_tests.sh` 全绿；涉及 Rust（如无）不改 schema
- [ ] design §5.9.2 与本细化一致（已对齐）；port-todo HP-406/408 标记随实现推进

---

## 十、评审记录（2026-07-09，三方独立评审）

**评审方式**：三个独立子代理，分别按 **架构/正确性**、**安全**、**代码可行性** 视角对照真实代码核查。
**综合结论**：三方一致 **approve-with-revisions**。方向成立、复用点大体属实，但有 **1 个会证伪核心承诺的正确性缺陷**、多处安全必修、4 处事实定位错误、1 个 socket 拓扑架构缺口。**下列 B1–B8 为开工前必修**；本文档 §二/§4/§6/§8 已按 F1–F4 修订。

### 阻塞级（开工前必解）

| ID | 视角 | 问题 | 证据 | 处置 |
|----|------|------|------|------|
| **B1** | 正确性 | `get_due_jobs()` 对**周期任务**有快进丢弃：scheduled 时间早于 grace（½ 周期，clamp[120s,7200s]）即被 `continue` 跳过。scale-to-zero 蓄意停机 → 触发间隔常 > grace → **周期 cron 默认静默丢**，直接证伪"定时任务不丢" | `cron/jobs.py:1070-1093,344-373` | HP-408a 增 `run_due(catch_up=True)`：外部触发时绕过快进，逐条补跑过期周期任务（幂等仍由 `advance_next_run` 保护）。one-shot(kind=once) 不受影响 |
| **B2** | 可行性 | **socket 拓扑冲突**：§4.2 让 `.socket` 列出所有平台端口（`Accept=no`），但现有 ~9 个 webhook adapter 各自 `web.TCPSite` 新 `bind()` 同端口 → `EADDRINUSE` | `teams/adapter.py:697-700`、`line/adapter.py:786-790`、`gateway/run.py` 无共享 server | **✅ 定案 (b) 全平台 fd 继承**（见文末）→ 新增 HP-406e，HP-406 → ~8–11d |
| **B3** | 安全 | 空 token + `provider=chronos` = 网络端口上的**无鉴权任务执行触发** | schema 默认 `trigger_token:""` | `_validate_gateway_config()` 硬拒：`provider=chronos` 且 `has_usable_secret(token)` 假 且 网络 bind → 拒启动（loopback 例外）。复用 `intellect_cli/auth.py:587` `has_usable_secret` |
| **B4** | 安全 | 静态 bearer 是本仓库标准的倒退（可重放，重放即重复触发任务/LLM 花费） | 仓库标准 = Svix HMAC `webhook/adapter.py:692-740` | 改 HMAC-SHA256 over `timestamp+body` + replay 窗口 + `hmac.compare_digest` |
| **B5** | 安全 | socket 激活唤醒发生在**应用层鉴权之前**：裸 TCP connect 即唤醒 gateway → 无鉴权冷启动 DoS/thrash | `Accept=no` 语义 | run-due 端口仅 `provider=chronos` 时进 `ListenStream`；L3/L4（防火墙/CIDR）门禁；token 只能是唤醒后的第二层 |
| **B6** | 正确性 | 新字段 `transport` 与既有 `Platform.transport`（消息流模式 `edit\|draft\|plain`）**同名冲突** | `gateway/config.py:381` | 新能力字段改名 **`wake_class`**（本文已改） |
| **B7** | 正确性 | chronos 下若关内置 ticker，则 `_start_cron_ticker` 搭载的维护任务（频道刷新/5、缓存清理/60、paste 清扫/60、curator/60）**静默停摆** | `gateway/run.py:9175-9296` | HP-408a 明确 chronos 下这些维护迁到 cadence-independent keepalive 或折进 `run_due` |
| **B8** | 正确性 | idle 信号（session `updated_at` 全局 max）过弱：长回合/HP-202 后台子代理/in-flight `run_due` 均可能 `updated_at` 陈旧却仍在跑 → 停机截断 | `session.py:879,961` | 自停主门禁改 **5 项 AND**：`_running_agents` 空 **且** `process_registry.count_running()==0` **且** `async_delegation.count_running_delegations()==0`（HP-202，二轮评审补）**且** 无 in-flight `run_due` **且** 过 `min_uptime`（§4.1） |

### 事实定位错误（本文档已修订 F1–F4）

| ID | 原文 | 实况 |
|----|------|------|
| **F1** | systemd 生成器在 `service_manager.py`（`SystemdServiceManager`） | 实为 `intellect_cli/gateway.py:2193 generate_systemd_unit()` + `systemd_install():2494` + staleness helpers（`SystemdServiceManager` 仅 systemctl 包装）。**socket 单元须改 `gateway.py` 生成器，并教会 staleness/refresh/scope 认识第二种单元** |
| **F2** | transport「已接入 `_validate_gateway_config`」 | 未接入：需 `PluginManifest` 解析（`plugins.py:1267`）+ 承载到 `PlatformEntry`（`platform_registry.py:39`）+ validator 查表；**须核对 registry 在 validation 时已填充**（`config.py:1189` 时序） |
| **F3** | 「键落 3 loader」，`load_cli_config` 在 config.py | `load_cli_config` 在 `cli.py:348`（自带 defaults）。gateway 键须加 `GatewayConfig` dataclass（`config.py:466`）+ `from_dict`（:617）+ `load_gateway_config` 显式映射；仅加 `DEFAULT_CONFIG` **不**驱动 gateway 行为 |
| **F4** | 校验时读 telegram 运行时 `_webhook_mode` | validation 期无 adapter 实例，读不到。须从 `TELEGRAM_WEBHOOK_URL`（env/config）重导，抽共享 helper 防漂移（`telegram/adapter.py:355,1590-1631`） |

### 应修（建议同 PR）
- run-due 加 per-source 限流 + body 上限 + 鉴权先于读 body（仿 `webhook/adapter.py:136-143`）
- `trigger_token`/`cron_trigger_token` 纳入 `agent/redact.py` `_SENSITIVE_BODY_KEYS`（当前**不覆盖**，会明文入日志/摘要）
- token 支持 env/secret store 来源 + 多 token 轮换窗口；落盘 `0600`
- CIDR 只信 socket peer（`request.remote`），不信 XFF；network bind fail-closed；确认 `Accept=no` 下 `request.remote` 非 `127.0.0.1`
- 端点忽略 body 选择任务（无 `job_id`/`force`），返回 HTTP 200（并发第二 ping 拿不到锁返回 0，勿当错误重试）

### 工期修订
- **HP-406：4–6d → ~8–11d**（B2 定案 (b) 全平台 fd 继承；adapter 数量为主摆动项）；6b 单元 ~2d + 6e ~10 adapter fd 继承 3–5d
- HP-406c：0.5d 边界（依赖 `PlatformEntry` 承载 + 注册时序核对）
- HP-408：~5d 基本成立（端点/插件/幂等均 greenfield，可干净继承 fd）

### 正向确认（无需改）
- one-shot(kind=once) 不受快进影响，照常补跑（`cron/jobs.py:1074`）
- 注入扫描器 + at-most-once 幂等 在外部触发下**正确保留**（`scheduler.py:1206,1378-1401,1913-1916`）
- `builtin` 零回归成立（前提：`run_due()` 为纯抽取，`tick()=lock+run_due()`）
- 每平台独立端口 / 无共享 HTTP server 属实 → §8.1「专用 run-due 端口」正确
- `check_systemd_timing_alignment` 可复用；drain 默认 180s → 单元 `TimeoutStopSec ≥ 210s`（`shutdown_forensics.py:322-406`、`config.py:696`）

### B2 socket 拓扑 — 定案：选 (b) 全平台 fd 继承（2026-07-09）
- **决定**：走 (b)——任意入站 webhook 唤醒 gateway，完整 scale-to-zero（交互式消息不被搁置到下次 ping）。
- **落地**：新增 **HP-406e**——~10 个 `wake_class=webhook` adapter 改 `web.SockSite` 继承 systemd fd（`get_activation_socket()` 共享 helper）；非 activation 部署回退 `TCPSite` 零回归。详见 §4.5。
- **工期影响**：HP-406 由评审 6–9d 上调至 **~8–11d**（adapter 数量为主摆动项）。
- 未选 (a)（仅 run-due 唤醒）：会把停机期用户消息搁置到下次 Chronos ping，不适合交互式主用法。

---

## 十一、第二轮评审（2026-07-09，B2 决议后针对新增 HP-406e + B1/B7/B8 修法）

**触发**：B2 定案 (b) 新增 HP-406e（首轮评审未覆盖）。双视角核查其可行性与 B1/B7/B8 修法。
**结论**：仍 **approve-with-revisions**；HP-406e"~10 adapter 统一 SockSite"前提**对 3/10 不成立**，B8 AND-gate 漏一类在途工作。

### 十一.1 HP-406e 可行性：真正可 SockSite 直改的只有 7/10
| adapter | 入站机制 | 可行性 | 证据 |
|---------|---------|--------|------|
| api_server / webhook / msgraph_webhook / line / sms / bluebubbles / teams | 原生 aiohttp `TCPSite` | **EASY** | 各 `adapter.py` `TCPSite` 行；teams 经 `_AiohttpBridgeAdapter` 仍 aiohttp（`teams/adapter.py:697-700`） |
| **slack** | slack-bolt **Socket Mode 出站 WS，无入站端口** | **BLOCKED** | `slack/adapter.py:666-668` |
| **whatsapp_cloud** | **无入站 server**（仅 send + `parse_webhook_event`） | **BLOCKED** | `whatsapp_cloud/adapter.py:66-79` |
| **telegram**(webhook) | PTB **tornado** `HTTPServer`，非 aiohttp | **HARD**（PTB `unix=<socket>`→tornado `add_socket`，非 `SockSite`） | `telegram/adapter.py:1622`、PTB `webhookhandler.py:80-91` |

**必改**：slack 重分类 `persistent`；whatsapp_cloud 移出 webhook 集；telegram 单列（tornado 路径）；api_server 预检端口 guard（`adapter.py:4285-4292`）在 activation 下须绕过；`get_activation_socket()` 校验 `LISTEN_PID==os.getpid()` + 每 fd 单次消费 + host/port 变 advisory。**真正 aiohttp 直改 = 7，非 ~10**；406e 3–5d → **~4.5–6.5d**（前提 slack+whatsapp descope）。

### 十一.2 B1/B7/B8 修法
- **B1（catch_up）✅ 健全**：`advance_next_run` 用 `compute_next_run(schedule, now)` 跳到**下一未来点**（非逐周期回放）→ 停机后每周期任务**恰好补跑一次**。补充：编辑落点在 `cron/jobs.py get_due_jobs`（非 scheduler.py）；催群受 `cron.max_parallel_jobs`（**默认无上界**）约束，chronos 下建议设上限（写入 §7）。
- **B7（维护迁移）⚠️ 被低估**：cache 清理 + paste 清扫**无内部时间门**（仅 `tick_count%60`），迁移须**新增持久化 wall-clock last-run 戳**；curator 已自门控（`interval_hours`）；频道刷新依赖 live adapters。升为 HP-408a 显式子项，+0.5d。
- **B8（idle AND-gate）⚠️ 漏 HP-202 委派**：后台委派子代理由 `tools/async_delegation.py:80 count_running_delegations()` 跟踪，**既不在 `_running_agents` 也不在 `process_registry`**，且存活于父回合之后 → 现 gate 会 SIGTERM 截断。**须加第 5 项 `count_running_delegations()==0`**（§4.1 本已列此拒停条件，B8 形式化时漏了）。"无 in-flight run_due"是 greenfield 标志，须覆盖整个 tick 含 `_deliver_result`。

### 十一.3 工期再修订
- HP-406e：3–5d → **~4.5–6.5d**；**HP-406 合计 ~9–12d**（含 descope、telegram 单列、api_server guard、B8 第 5 项）；HP-408 +0.5d（B7 wall-clock 门）

### 十一.4 正向确认
- B1 恰好一次补跑；one-shot 不受影响；7 个 aiohttp adapter SockSite 为干净直改；teams 底层 aiohttp；B8 信号 `_running_agents`（含 pending sentinel，覆盖 streaming）+ `process_registry.count_running()` 语义正确、保守

### 十一.5 必改清单（开工前）
1. slack → persistent；whatsapp_cloud 移出 webhook 集（§4.3/§4.5 已改）
2. telegram fd 继承单列（tornado 路径，§4.5 已改）
3. B8 加 `count_running_delegations()==0` 第 5 项（§4.1 已补）
4. api_server 端口 guard 在 activation 下绕过
5. B1 catch_up 落点 `jobs.py` + `max_parallel_jobs` 上限；B7 wall-clock 门
