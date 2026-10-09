# WebUI 消息网关（平台）配置 UI — 设计方案

日期：2026-10-09
状态：**已实施（P0 批次）+ 评审通过**（2026-10-09；D1 按方案 A 落地，评审 3 个 P1 已修，遗留 P2 见 §8）

## 0. 结论先行

1. **hermes-webui（~/workspace/hermes-webui）没有网关配置 UI。** 它的 "gateway" 确实是消息平台网关
   （Telegram/Discord/Slack/Weixin/WeCom/Email/Matrix/Signal），但全部平台触点都是展示与控制类：
   状态卡（`static/index.html:1617-1622` + `static/panels.js:13448` `_renderGatewayStatus`）、
   Start/Stop/Restart（`POST /api/gateway/{start|stop|restart}`）、gateway 会话 SSE 流、approval 转发。
   **没有任何平台凭据表单**（无 bot token / app secret / webhook URL 输入框）。它刻意把 agent 侧配置
   排除在 WebUI 之外——MCP 区块原话 "read-only here for now. Edit config.yaml and restart Hermes"
   （`static/index.html:1626-1628`），平台凭据同理留给 hermes-agent 的 config.yaml/.env。
2. 因此本方案**完全自行设计**；hermes-webui 仅能提供"同源架构下的模式参考"（两边 WebUI 前端文件结构
   与 api/routes.py 分发器明显同源）：状态卡布局、settings 表单"收集→POST→服务端合并写回"链路。
   它的边界决策（平台凭据不进 WebUI）本方案**有意反其道而行**，理由见 §1。
3. 目标：intellect-agent WebUI Settings 面板新增 **Gateway pane**——平台凭据/行为配置表单、
   per-platform 连接状态、启用开关、保存并重启网关，形成"接入一个新平台不出 WebUI"的闭环。

## 1. 为什么把网关配置产品化进 WebUI（与 hermes-webui 相反的边界选择）

- 平台凭据是最典型的"setup wizard 式"配置：BotFather 发 token、飞书开放平台拿 App ID/Secret——
  用户此时就在浏览器里，让他再去手改 `config.yaml` + `.env` 两个文件并重启进程，体验割裂。
- intellect-agent 已有全部基础设施，缺的只是 UI 与两个 API：
  写入侧 `save_env_value()` / `_set_nested`+`atomic_yaml_write()`（`intellect_cli/config.py`）、
  读取/校验侧 `gateway/config.py`（`load_gateway_config` / `_PLATFORM_CONNECTED_CHECKERS` /
  `_validate_gateway_config`）、生效侧 `webui/api/gateway_lifecycle.py`（subprocess 重启网关）、
  状态侧 `gateway/status.py` 的 per-platform runtime status（已存在，WebUI 未消费）。
- 吸取 hermes-webui 的正确原则：**WebUI 自身不持久化网关状态**——一切写进 active profile 的
  `config.yaml` / `.env`（INTELLECT_HOME 内），WebUI 的 `settings.json` 不存任何网关数据。
  多 agent（profile）隔离天然成立（`get_active_intellect_home()` 定位）。

## 2. 现状要点（设计所依赖的已核实事实)

| 事实 | 位置 |
|---|---|
| `Platform` 枚举 22+ 平台（含 `plugins/platforms/*` 动态成员） | `gateway/config.py:100-194` |
| `PlatformConfig{enabled, token, api_key, home_channel, reply_to_mode, extra{}}`，平台特有键全走 `extra` | `gateway/config.py:294-355` |
| 配置优先级：**env > config.yaml（顶层平台节 `telegram:` 等与 `platforms.<name>:`, 后者合并进前者）> `gateway.json`(legacy) > 默认** | `load_gateway_config()` :836-1337；共享键桥接循环 :996-1068 |
| **凭据型 env 出现即无条件 `enabled=True`**：telegram/discord/dingtalk/feishu/wecom/wecom_callback/weixin 全部如此；仅 Slack（`_enabled_explicit`）与 WhatsApp（`WHATSAPP_ENABLED=false`）尊重显式停用 | `_apply_env_overrides()` :1409-1977 |
| per-platform 连接检查 `_PLATFORM_CONNECTED_CHECKERS`（如 weixin 需 `account_id`+`token`，feishu 需 `app_id`，dingtalk 需 `client_id`+`client_secret`） | :549-580, :641-680 |
| 占位符 token 检测 + 空凭据告警（`_validate_gateway_config`） | :1340-1406 |
| gateway 进程实时上报 **per-platform 运行状态** `{state: connected/disconnected/fatal, error_code, error_message, updated_at}` 到 `gateway_state.json` 的 `payload["platforms"]` | `gateway/status.py:540-594`；`gateway/platforms/base.py:1840-1876`（`_mark_connected/_mark_disconnected/_set_fatal_error`） |
| WebUI `/api/gateway/status` 只有守护进程存活 + **会话来源**推导的平台标签（写死 6 个），未消费上述 per-platform 状态 | `webui/api/routes.py:6828-6915` |
| 写入工具：`save_env_value()`（原子写 + 权限保持 + ASCII/换行清洗；**写空值=留 `KEY=` 空行**，dotenv 载入后为 falsy，语义上等于清除） | `intellect_cli/config.py:5454` |
| `set_config_value()` 的 env 路由启发式只认 `_API_KEY`/`_TOKEN` 后缀，**不认 `_SECRET`**（钉钉/飞书/企微的 secret 键覆盖不到）→ 新 API 必须用显式 env 键清单，不能依赖通用启发式 | `intellect_cli/config.py:5477-5486` |
| `{fields, secrets}` 分离保存范式已存在于 memory providers 表单（fields→`save_config`，secrets→`save_env_value`） | `webui/api/routes.py:11397-11445` |
| 网关生命周期控制（subprocess `intellect gateway <action>`，带锁、带 INTELLECT_HOME、120s 超时） | `webui/api/gateway_lifecycle.py` |
| dingtalk/feishu/wecom/wecom_callback/weixin **没有 DEFAULT_CONFIG 节**，YAML 节由 gateway/config.py 动态桥接 | `intellect_cli/config.py` DEFAULT_CONFIG vs `gateway/config.py:996-1068` |

## 3. 前端设计（Settings → Gateway pane）

### 3.1 信息架构

- Settings 侧菜单（`webui/static/index.html:396-435`）新增第七个 pane：**Gateway / 网关**。
- pane 布局（自上而下）：
  1. **守护进程状态条**：复用现有 `loadGatewayStatus()`（panels.js:8395）与 Start/Stop/Restart
     按钮（从 System pane 的 gatewayStatusCard 迁移过来；System 保留一张只读摘要卡）。
  2. **平台卡片网格**：每个受支持平台一张卡——名称、配置徽章（未配置/已配置）、运行状态点
     （灰=未启用，绿=connected，黄=disconnected/reconnecting，红=fatal+错误 tooltip）、启用开关。
  3. 点卡片展开**平台详情表单**（模态或行内展开）：凭据字段（`type=password`，已设置时显示
     "已配置 ••••，留空保持不变"）+ 行为字段 + `保存` / `保存并重启网关` 按钮 + 文档链接。

### 3.2 分期覆盖范围

- **P0**：`telegram`、`dingtalk`、`feishu`、`wecom`、`wecom_callback`、`weixin`（微信/钉钉/飞书为
  本次需求点名项；telegram 作为最成熟平台的样板）。
- **P1**：`slack`、`discord`、`whatsapp`、`qqbot`、`email`。
- **P2**：其余内置平台（matrix/signal/mattermost/...）+ **通用键值编辑器**兜底任意
  `Platform` 枚举成员（含第三方插件平台）：无 schema 时退化为 `extra` 的 JSON/键值编辑。

### 3.3 表单渲染：schema 驱动，不写 22 个表单

新增 `webui/api/gateway_platform_schema.py`，集中声明每平台的字段 schema：

```python
PLATFORM_SCHEMAS = {
    "dingtalk": PlatformSchema(
        label="钉钉", docs_url="...",
        fields=[
            Field(key="client_id",  env="DINGTALK_CLIENT_ID",  type="text",   required=True, label="Client ID"),
            Field(key="client_secret", env="DINGTALK_CLIENT_SECRET", type="secret", required=True, label="Client Secret"),
            Field(key="require_mention", yaml="dingtalk.require_mention", type="bool", label="群内需@才响应"),
            Field(key="allowed_chats",  yaml="dingtalk.allowed_chats", type="list", label="允许的会话"),
        ],
    ),
    ...
}
```

- `type ∈ {text, secret, bool, int, list, select}`；`env=` 的键写 `.env`，`yaml=` 的键写 config.yaml
  顶层平台节。secret 永不出现在任何 GET 响应里。
- **为什么放集中文件而非扩展 `plugins/platforms/*/plugin.yaml`**：WebUI 进程不 import 插件运行时，
  集中模块零依赖、可离线单测；插件平台 schema 下沉到 plugin.yaml 可作为 P2 演进方向（届时只读
  YAML 不 import 代码）。
- 前端按 schema 渲染表单，参考 memory providers 表单的 `{fields, secrets}` 分离交互。

## 4. 后端 API 设计

### 4.1 `GET /api/gateway/platforms`

返回 schema + 每平台当前状态的**脱敏**快照：

```json
{
  "platforms": [{
    "name": "dingtalk", "label": "钉钉",
    "enabled": true,
    "configured": true,            // 由 _PLATFORM_CONNECTED_CHECKERS 同源逻辑判定
    "secrets": {"DINGTALK_CLIENT_SECRET": {"set": true}},
    "fields": {"require_mention": true, "allowed_chats": ["..."]},
    "runtime": {"state": "connected", "error_code": null, "updated_at": "..."}
  }],
  "gateway": {"running": true, "pid": 123}
}
```

- `runtime` 直接读 `gateway/status.py::read_runtime_status()["platforms"]`（跨进程读 JSON 文件，
  WebUI 不 import gateway 运行时）。
- `configured`/`enabled` 的判定：在 active profile home 上跑 `load_gateway_config()` 的只读孪生
  （见 4.3），或直接 import `gateway.config` 纯函数（无网络副作用，安全）。

### 4.2 `PUT /api/gateway/platforms/<name>`

请求体：`{"fields": {...}, "secrets": {...}, "enabled": bool}`（沿用 memory providers 的
fields/secrets 分离；**secrets 中留空/缺失 = 不修改现有值**；显式清除用 `{"secrets": {"KEY": null}}` →
`save_env_value(KEY, "")`）。

写入路由（全部限定在 active profile home）：

| 数据 | 落点 | 工具 |
|---|---|---|
| secret 键（显式清单） | `.env`（`DINGTALK_CLIENT_SECRET` 等） | `save_env_value()` |
| 行为键 | config.yaml **顶层平台节**（`dingtalk.require_mention`） | `_set_nested` + `atomic_yaml_write` |
| `enabled` | config.yaml `platforms.<name>.enabled` | 同上 |

- 为什么写顶层平台节而非 `platforms.<name>.extra`：与 `load_gateway_config` 的桥接循环、
  DEFAULT_CONFIG 既有节（telegram/slack/discord...）方向一致，用户手改与 UI 写入同一处。
- 响应：`{"ok": true, "needs_restart": true, "warnings": [...]}`——warnings 复用
  `_validate_gateway_config` 思路（必填缺失、占位符 token 如 `123456:ABC-PLACEHOLDER`、
  weixin 缺 `account_id` 等）。
- **生效模型**：配置落盘即返回；网关在下次重启时读取。前端提供"保存并重启网关"
  （串行调 `gateway_lifecycle.request_gateway_restart`）。不做热重载、不动运行中进程——
  与 AGENTS.md 的 prompt-caching/中途不改上下文政策一致（网关重启产生的是新进程新会话）。

### 4.3 一个必须先修的加载器语义（D1，本方案唯一的核心代码改动）

**问题**：UI 提供"停用"开关后，如果只写 `platforms.<name>.enabled: false` 而 `.env` 里凭据仍在，
`_apply_env_overrides` 会在下次网关启动时无条件重新启用——开关失灵。

**方案 A（推荐）**：把 Slack 的 `_enabled_explicit` 模式推广到所有平台——
`load_gateway_config` 桥接循环中，任何平台的顶层节出现显式 `enabled:` 即记录
`extra["_enabled_explicit"]`；`_apply_env_overrides` 统一改为
"env 凭据自动启用，**除非该平台被 yaml 显式停用**"（token 仍写入，供 skills 发消息用，
与 Slack 现状注释的语义完全一致）。

- 优点：停用/启用无需删凭据，重新启用零成本；与 Slack/WhatsApp 已确立的语义对齐；
  UI 逻辑最简。
- 兼容性：行为变化面 = "yaml 显式 `enabled: false` + env 凭据存在"的用户，现状是 env 赢
  （平台被启用），改后是 yaml 赢（平台停用）。这恰是受影响用户在 yaml 里写下 `enabled: false`
  的字面意图，属于修复而非破坏；评审时按此口径确认。
- 方案 B（备选）：UI 停用 = 清除 `.env` 凭据。无需改 loader，但凭据丢失（重新启用要重输）、
  且会破坏"token 供 skills 使用但网关适配器不跑"的 Slack 型场景。不推荐。

### 4.4 访问控制与安全

- 与 `/api/config`、memory providers 同款门禁：localhost 绑定 + WebUI 既有 auth（实现时对齐
  `webui/api/auth.py` 的现行机制；两个新端点全部要求登录态）。
- **secret 单向**：任何 GET 响应只含 `{set: true/false}` 布尔；PUT 的 secret 不落日志、不回显
  （响应只有 `needs_restart`/`warnings`）。审计日志仅记 "platform=dingtalk secrets=[CLIENT_SECRET] updated"。
- `.env` 写入走 `save_env_value` 现有的原子写 + 权限保持 + `_secure_file` 收紧。
- schema 中 secret 字段在前端渲染为 `type=password`，浏览器自动填充禁用（`autocomplete="off"`）。
- SSRF 面：无（本 API 不发起任何网络请求；连接性测试按钮明确排除在 V1 外，见 D3）。

## 5. 关键决策汇总（请评审拍板）

| # | 决策 | 推荐 | 备选 |
|---|---|---|---|
| D1 | 停用开关语义 | **方案 A**：推广 `_enabled_explicit` 到全部平台，yaml 显式停用压过 env 自动启用 | 方案 B：停用=清 .env 凭据（不推荐） |
| D2 | 行为键写入位置 | config.yaml 顶层平台节（`dingtalk.require_mention`） | `platforms.<name>.extra`（与手改习惯分叉，不推荐） |
| D3 | 连接性测试（getMe / tenant_access_token 探测） | **V1 不做**，"保存并重启→看运行状态点"闭环已够；P2 再加显式 Test 按钮 | V1 就带测试按钮（增加网络依赖与滥用面） |
| D4 | 平台覆盖节奏 | P0 六平台（telegram/dingtalk/feishu/wecom/wecom_callback/weixin）→ P1 五平台 → P2 通用编辑器 | 一次做全 22+（工作量与评审面不成比例） |
| D5 | schema 载体 | 集中 `webui/api/gateway_platform_schema.py` | 下沉 `plugins/platforms/*/plugin.yaml`（P2 演进方向） |

## 6. 实施拆分（评审通过后执行）

1. **批次 1（后端）**：D1 loader 修复 + 回归测试；`gateway_platform_schema.py`（P0 六平台）；
   `GET/PUT /api/gateway/platforms`（`webui/api/gateway_platform_config.py` 新模块 + routes.py
   挂载）；tests：`tests/webui/test_gateway_platform_config.py`（tmp INTELLECT_HOME，无网络，
   覆盖 secret 不回显、enabled 压制 env、占位符告警、wecom_callback int 字段）。
2. **批次 2（前端）**：Gateway pane 骨架 + 状态条迁移 + 平台卡片网格 + 六平台表单 + i18n。
3. **批次 3**：P1 平台 schema + 通用键值编辑器兜底 + `docs`（website/docs user-guide）。
4. 全程遵循 `scripts/run_tests.sh`；secret 相关代码过一遍 quality/security review（按惯例评审批次）。

## 7. hermes-webui 可借鉴点清单（明确标注：均为模式参考，非配置 UI 本体）

- 状态卡信息密度与"绿点+平台列表+last_active"布局：`static/panels.js:13448-13475`。
- settings 表单"payload 收集器 → autosave/显式保存 → POST → 服务端合并写回 + 缓存失效"链路：
  `static/panels.js:8800-8936` → `api/config.py save_settings()`。
- **反面借鉴**：hermes-webui 把 MCP/网关配置一律推给手改 config.yaml，导致平台接入无产品化路径；
  且其源集合不含钉钉/飞书——intellect-agent 的平台面（22+）远大于它，更需要 schema 驱动的配置 UI。

## 8. 实施记录（2026-10-09）

落地范围：§6 批次 1 + 批次 2（P0 全量）。改动清单：

| 文件 | 内容 |
|---|---|
| `gateway/config.py` | D1：`_enabled_explicit` 推广全平台（合并 map + 共享键循环两处设置点）；`_env_auto_enable` helper 替换约 18 个 env 块的无条件启用；插件平台注册表驱动路径加显式停用短路（在 `check_fn` 之前，避免 SDK 懒安装副作用）；`load_gateway_config` 收尾统一清理标记；抽出公共 `platform_is_connected()`（`_is_platform_connected` 委托） |
| `webui/api/gateway_platform_schema.py` | P0 六平台字段 schema + `normalize_value` 类型归一 |
| `webui/api/gateway_platform_config.py` | `GET build_payload`（脱敏）/`PUT save_platform`（env→.env、yaml→顶层节、enabled→platforms.<name>；loopback 门禁 + 解析失败守卫 + 必填启用校验 + 占位符/清凭据告警 + 错误消息打码） |
| `webui/api/routes.py` | handle_get/handle_put 各挂一分支 |
| `webui/static/{index.html,panels.js,i18n.js}` | Settings → Gateway pane（菜单项、守护进程状态条复用参数化的 `loadGatewayStatus(cardId)`、平台卡片网格、schema 驱动表单、保存/保存并重启）；i18n en/zh-CN/zh-TW |
| `tests/gateway/test_config_explicit_enabled.py` | D1 语义 11 测（显式停用压 env、行为键不阻断自动启用、WhatsApp 三态保留、插件短路、无标记泄漏不变式） |
| `tests/webui/test_gateway_platform_config.py` | API 13 测（脱敏、写路由、启用校验、loopback 403、int 归一、顶层节优先、yaml 坏损 400、清凭据告警、开关往返） |

验证：新增 24 测全绿；`tests/gateway/` 全目录与 HEAD 基线 diff = 零新增失败（唯一差异是本方案的
D1 测试在 HEAD 上如预期失败，证明测试有效）；`tests/webui/` 失败集与 HEAD 一致（pre-existing）。

实施后评审（安全/质量）：**无 P0**；3 个 P1 已当场修复——
(1) config.yaml 解析失败时 PUT 先 400 拒绝，不再有整体覆盖用户配置的数据丢失面；
(2) GET 的 enabled 优先级对齐 loader（顶层节胜过 `platforms.<name>`），消除 UI 开关显示与重启实效
不一致；(3) 插件平台显式停用短路挪到 `check_fn` 之前，注释声称的 SDK 懒安装防护真正生效。
另修 P2#2（runtime error_message 的 token 形子串打码）、P2#5（yaml 解析失败只记异常类型不记原文）、
P2#6（清除必填凭据时返回 warning）。

遗留 P2（后续批次）：
- loader 侧 weixin 半凭据（只有 token）仍会自动启用，GET 显示与实际有漂移（§4.3 之外的行为，另议）。
- managed 安装下 `save_env_value` 静默 no-op 而 yaml 照写 → 半保存仍 ok:true。
- 凭据只在 legacy `gateway.json` 的用户在 UI 显示"未配置"（写入后自愈，纯展示）。
- 手写 yaml 字符串 `"false"` 在 bool 勾选框显示为勾选（保存时归一，纯外观）。
- 批次 3：P1 平台 schema（slack/discord/whatsapp/qqbot/email）+ 通用键值编辑器兜底 + 用户文档。

## 9. 批次 3 + P2 实施记录（2026-10-09 第二轮）

§8 的遗留项已全部落地：

**P1 平台 schema（+5）**：`slack`（bot token / app token / require_mention / free_response_channels）、
`discord`（bot token / require_mention / free_response_channels / allowed_users）、`whatsapp`（无凭据，
QR 配对；dm/group policy + allowlist；`enable_env=("WHATSAPP_ENABLED",)` 三态镜像 loader）、
`qqbot`（app_id + client_secret + allowlist 键）、`email`（address/password/imap/smtp 四必填 + 端口/轮询
可选 int）。字段类型遵循适配器实际消费形态（list 与逗号串均兼容，已逐一核对 adapter）。

**通用键值编辑器兜底**：GET 为所有无 schema 平台（枚举成员 + 插件平台，共 16 个）发 `generic: true`
条目（extra 现值 + enabled/configured/runtime）；PUT `{"extra": {...}, "enabled": bool}` 走
`_save_generic_platform`——整块替换 `platforms.<name>.extra`、值经 JSON-parse-when-possible 保型
（"8645"→int、"true"→bool）、保留键（enabled/token/api_key/home_channel/extra）400 拒绝、启用时
connected 检查不通过给 warning 不硬拦（checker 视角可能漏 env）。前端为 key-value 行编辑器 + 增删行。

**四条 P2**：
1. weixin 半凭据：loader env 块改为"凭据对完整（env 或 yaml extra 凑齐 token+account_id）才自动
   启用"，凭据仍存储（skills 可用）；新增 2 个 loader 测试钉住。
2. managed 模式：`save_platform` 入口 `is_managed()` → 403 拒绝（杜绝 `save_env_value` 静默 no-op
   导致的 yaml/env 半保存）。
3. gateway.json legacy：GET 将 `<home>/gateway.json` 的 platforms 作为基底层合并（镜像 loader 的
   gateway.json < config.yaml 次序）；legacy `enabled` 视为非显式（与 loader 无 marker 一致）。
4. bool 字符串显示：前端仅 truthy 拼写（true/'true'/1/'1'）渲染为勾选。

**顺带修复**（测试过程发现）：`missing_required` 判定扩展到 merged 块的等价凭据来源
（legacy token / 手写 extra），否则 legacy-only 或手写 yaml 的已工作平台会被误报"未配置"；
隐式 enabled 判定改为镜像 loader 触发条件（env 凭据完整 / enable_env 三态 / 块存在），
修复了"legacy enabled:false + 无 env 凭据被 UI 显示为启用"的反向漂移。

**用户文档**：`website/docs/user-guide/features/messaging-gateway.md`（存储位置表、write-only 语义、
生效模型、11 平台凭据获取指引、generic 编辑器说明、enabled/configured/connected 三态 FAQ、
managed/坏 yaml 拒绝说明）。目录为 docusaurus 自动生成侧边栏，落文件即注册。

验证：36/36（14 D1 + 22 API）经 wrapper 全绿；`tests/gateway/` 全目录对 HEAD 基线零新增失败；
`tests/webui/` 失败集与 HEAD 一致（pre-existing）。27 平台 = 11 schema + 16 generic（含 6 插件平台）。

已知边界（记录，不修）：qqbot 半凭据（只设 app_id env）loader 仍会自动启用（与 weixin P2a 同类，
未在批准范围内故未动 loader）；generic 平台的隐式 enabled 为近似值（checker 视角，runtime 点为准）。

## 10. 第二轮评审与修复（2026-10-09）

批次 3 + P2 的实施后评审（探针验证型评审：构造配置组合实测 loader vs GET 行为，非纸上推演）。
**无 P0**；5 个 P1 + 一批便宜 P2，全部当场修复：

| # | 问题 | 修复 |
|---|---|---|
| 评审前自查 | `_check_managed` 依赖 `bad()` 返回值做真值判断——`j` 返回 None，生产环境 403 发出但保存继续执行（测试因 mock j 返回 payload 假绿） | 改为内联 `if is_managed(): return bad(...)`；managed 测试加"无任何写入"断言；`_Capture` 改为镜像真实 `j` 返回 None |
| P1-1 | weixin 隐式启用 trigger 只认双 env，loader 已支持 yaml extra 凑对 → 混合来源 UI 显示停用但网关实际启用 | build_payload 为 weixin 增加按来源混合的 pair-complete trigger |
| P1-2 | 手写 `enabled: "false"`（字符串）被裸 `bool()` 判为启用 | `_merged_platform_blocks` 改用 loader 同源 `_coerce_bool` |
| P1-3 | 顶层节手写 enabled 胜过 `platforms.<name>`，UI 开关写入嵌套节 = 200-OK no-op | 新增 `_write_platform_enabled`：写嵌套节同时 pop 顶层节 enabled（节空则删节） |
| P1-4 | PUT 启用门禁只查顶层节+本次提交，legacy/手写嵌套用户"显示已配置却拒绝启用" | 门禁改用 `_merged_platform_blocks` + `_required_satisfied` 合并视图 |
| P1-5 | generic extra 回显 yaml/legacy 的 secret 形键原值 | GET 掩码为 `"(set)"` 哨兵 + `extra_masked` 列表；PUT 哨兵=保留存量值（无存量则丢键）；对齐 memory providers 掩码先例 |
| P2 | `from_dict` extra:null 崩溃类（一行 `or {}` 消灭）；generic 隐式 enabled 移除 configured（方向改保守）；runtime 状态每平台重读 → 提升为单次；`_env_name` 重复定义；schema 路径空 body 400；strict 拒绝非 dict 根；onclick 单引号上下文 `_jsAttr`；enabled 开关仅 touched 才发送（不再冻结隐式态）；secret 清除 UI 入口（✕ 按钮） | 全部落地 |

验证：42/42 测试全绿（新增 8 个评审修复回归测试：weixin 混合来源、字符串 enabled、顶层节清理、
legacy 用户启用、掩码+哨兵往返、非 dict 根 400）；gateway/webui 全目录对 HEAD 基线零新增回归
（test_discord_document_handling 的 12 失败经 HEAD 复核为本机 DNS 环境间歇性 SSRF 误拦，
pre-existing）。

剩余 P2（记录，未修）：插件 `is_connected` 无超时包裹（第三方插件理论上可拖慢 GET，bundled 实现
已核实均为纯 env/extra 读取）；`gateway.platforms.<name>` 嵌套节对 GET 不可见（纯展示漂移）；
generic 保存与 GET 之间的并发手改窗口（单用户可忽略）。

## 11. 评审意见完善轮（2026-10-09 第三轮）

按第二轮评审的剩余意见逐项落地，至此评审账面清零：

1. **qqbot 半凭据对齐 weixin 语义**（消除最后一个同类漂移）：loader env 块改为凭据对完整
   （app_id+client_secret，env 或 yaml extra 凑齐）才自动启用；build_payload 增加同源混合 trigger
   镜像。§9/§10 的"qqbot 半凭据已知边界"就此关闭。
2. **`gateway.platforms.<name>` 嵌套节纳入 GET 视图**：`_merged_platform_blocks` 补上该层
   （次序 gateway.json < gateway.platforms < platforms.<name> < 顶层节）；因 D1 推广后
   `gateway.platforms.<name>.enabled` 同样带显式 marker，explicit 判定包含该层。
3. **插件 `is_connected` 加 2s 超时包裹**：`_platform_connected` 经模块级有界 executor
   （max_workers=4）+ `future.result(timeout)` 执行，超时按"未配置"处理并告警；第三方插件的
   慢检查不再能拖死 GET（线程本身无法终止，最多 4 个后台滞留——注释已写明）。

新增 6 个测试（qqbot 半凭据×2、qqbot 混合来源 GET、gateway.platforms 层×2、超时边界），
**48/48 全绿**；gateway/webui 全目录对 HEAD 基线仍为零新增回归。

至此三条已知边界只剩两条（均为可接受的近似）：generic 平台纯 env 配置显示偏关（runtime 点为准）；
generic 保存与 GET 之间的并发手改窗口（单用户可忽略）。
