# W15 — P5 Core Deferral 决策备忘

> **日期**：2026-08-30  
> **状态**：**分析完成 — 建议暂缓（另 tip）**；若启动走方案 A  
> **关联**：[`2026-08-30-w15-tool-search-l2.md`](./2026-08-30-w15-tool-search-l2.md) 的 P5 切片  
> **参考实现**：Hermes `e16ad33a9d`（`feat(tool-search): core-tool deferral`，curated 19-tool，desktop schema 13.4K→6.9K，−49%）

---

## 1. P5 是什么

Hermes 的 core deferral **不是**字面的 `_EAGER_TOOLS ⊂ _CORE_TOOLS`，而是**一张 `defer_tools` 名单，优先于 core 成员**：

- 保留 core 集不变（Hermes 53 / Intellect 48）。
- 新增 curated 名单（Hermes 19 个）：**事件触发型**核心工具藏在桥后——computer_use、session_search、clarify、image_generate、todo_list、process_manage、cronjob_manage + 12 个 desktop GUI 工具。
- `is_deferrable_tool_name(name, defer_tools)`：`name ∈ defer_tools` → 直接 deferral（**即使它在 core 集**）；否则维持"core 永不 defer"。
- 配置 `tools.tool_search.defer`：`None`（curated 默认）｜`[]`（恢复旧行为）｜显式列表（整体替换）。
- 配套 5 个改名 + `_LEGACY_TOOL_ALIASES`（todo→todo_list、cronjob→cronjob_manage、process→process_manage、tour→gui_tour、tip→show_tip），在 `handle_function_call` 与两个 executor 的派发接缝做旧名→新名映射。

## 2. Intellect 实测数据（本机 venv，`get_tool_definitions(skip=True)` + `estimate_tokens_from_schemas`）

| 指标 | 数值 |
|---|---|
| 默认交互 schema（26 个 core 工具） | **10,930 tokens**（≈200K 上下文 5.5%） |
| P5 候选（默认环境实际在场 4 个） | `clarify` `process` `session_search` `todo` = **2,017 tokens** |
| core 缩减 | **−18.5%**（≈上下文 −1%） |
| 门控候选 | `computer_use` `image_generate` `cronjob` — check_fn 决定是否进 schema，**默认不在**；仅在完整桌面/多配置环境贡献收益 |

**与 Hermes 的关键差异**：Hermes 的 −49% 大头来自 12 个 desktop GUI 工具；**Intellect 无桌面 GUI 面**（L9：不改 WebUI workspace_io），故 P5 在 Intellect 的默认收益仅 ~18% core。

## 3. 收益

- 完整桌面/多工具环境可省 ~3–4K tokens（browser_*、computer_use、image_generate 全 gate 进 schema 时）。
- 与现有 listing/检索协同：被 defer 的 core 工具进 catalog，`tool_search` 可发现、`tool_call` 可经桥取回。
- 是"工具越挂越多"场景下最后一档压缩手段。

## 4. 成本与风险（按严重度）

1. **打破 L2/R2/R8「core 永不 deferral」不变量** — OpenClaw #84141 教训（core 工具 `exec` 被目录漂移静默丢弃）的**反面**。缓解：curated 固定名单 + 无状态目录每轮重建 + listing 可见性；但这是本项目安全模型中最重要的不变量，任何核心工具被误 defer 都是灾难级。
2. **`clarify` 不应 defer** — `_NEVER_PARALLEL_TOOLS` 唯一成员、基础交互工具；藏桥后每次澄清多一次 search→describe→call 往返。Hermes defer 了它，但对 Intellect 是负收益。
3. **改名 + 别名是大工程** — 13 个文件引用 `todo`/`cronjob`/`process`（guardrails、conversation_loop、prompt_builder、system_prompt、display、executor、model_tools、transports、run_agent 等）。纯编排改动、零 token 收益，只为 catalog 名字更清晰。
4. **收益偏小** — 默认 −1% 上下文；缺了 Hermes −49% 的最大来源（GUI 面）。
5. **`defer_tools` 必须穿透所有 deferral 调用点** — `is_deferrable_tool_name` 的每个调用（classify_tools、scoped_deferrable_names、resolve_underlying_call、dispatch_tool_describe、_describe_classification、listing）漏穿一处即造成"能 search 到但 tool_call 拒绝"的漂移。

## 5. 实施方案

### 方案 A — 最小可行（~0.5 天，推荐路径）
只加 `defer` 配置 + 穿透 deferral 逻辑。**不改名、不 defer clarify**，默认 `off`。
默认 defer 名单（Intellect 版，去 clarify）：`session_search`、`todo`、`cronjob`、`process`、`computer_use`、`image_generate`。
配置语义同 Hermes：`None`=curated 默认、`[]`=还原、列表=整体替换。

### 方案 B — Hermes 全量（~2–3 天）
A + 5 个改名 + `_LEGACY_TOOL_ALIASES` 派发接缝 + guardrails/display 引用同步。改动面 13+ 文件，回归面大。

## 6. 决策建议

**本轮不做 P5，保持"另 tip"。**

1. **收益/风险比不划算**：默认环境只省 ~1% 上下文（2K tokens），却要打破"core 永不 defer"不变量并承担 OpenClaw 类回归风险。
2. **真正的增量被门控或不存在**：browser_*、computer_use 等进 schema 时才贡献；桌面 GUI 面 Intellect 没有。等 Intellect 有桌面 GUI 面时再做，收益才对齐 Hermes −49%。
3. **Hermes 名单不可机械移植**：其默认名单里的 `clarify` 对 Intellect 是负收益，需逐工具权衡。
4. **P0–P4 已把非 core 的 MCP/插件工具全部压到桥后**；P5 是"连 core 也压"，在 Intellect 收益递减。

## 7. 未来触发条件（重新评估时机）

- Intellect 引入桌面 GUI 工具面（desktop_ui/project 类 toolset）时——届时 GUI schema 是 P5 的主要来源。
- 用户实测 core schema 在 200K 上下文占比 >10% 且非 core 压缩已到位。
- 桥的检索质量（tool_search 召回）在真实 MCP 环境稳定后。

## 8. 若执行（方案 A）的最小验收清单

- [ ] `tools.tool_search.defer` 配置：`None`/`[]`/列表 三态解析与文档
- [ ] `defer_tools` 穿透 `is_deferrable_tool_name` 全部调用点（classify/scoped/describe/listing）
- [ ] 默认名单排除 `clarify`；`defer: []` 还原旧行为
- [ ] OpenClaw 类回归：deferred core 在混合工具集中存活；`tool_call` 可经桥取回被 defer 的 core 工具；目录每轮重建不漂移
- [ ] `defer` 后 `valid_tool_names`/作用域门/guardrails 对 deferred core 工具仍生效
- [ ] R2/R8 重签（本备忘落地时）
