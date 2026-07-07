# Hermes 移植 Phase 1 — 任务细化与评审

> 文档日期：2026-07-08
> 范围：HP-101 / HP-102 / HP-103（TODO Phase 1，设计 §5.1 / §5.2 / §5.8）
> 上游：`2026-07-08-hermes-v0.16-v0.18-port-design.md` §九评审记录
> 前置：Phase 0 已完成（`2026-07-08-hermes-port-phase0-security-audit.md`）

## 1. Phase 1 定位

| 维度 | 内容 |
|------|------|
| **目标** | 三项互相独立、低风险的 P0 能力补齐，不触碰 conversation_loop / Rust 新模块 |
| **并行度** | HP-101 与 HP-102 可完全并行；HP-103 建议排在末位（或 OpenAI 子集先行） |
| **总工期** | 设计估 6 人日；评审修订后 **5–7 人日**（HP-103 FAL 可溢出） |
| **出口** | 全量 `scripts/run_tests.sh`；无 `cargo test` 要求 |

---

## 2. HP-101 — memory 批量操作（细化）

### 2.1 现状核查

- `tools/memory_tool.py`：单 action API（add/replace/remove）；`read` 在 prose 中提及但 **未实现**（与 §9.1 一致）。
- 持久化：`{INTELLECT_HOME}/memories/MEMORY.md` / `USER.md`（member scope 时 per-member 目录）。
- 已有：per-target `.lock`、`atomic_replace`、drift guard（#26045）、threat scan、char limit。
- Provider 桥接：`agent/tool_executor.py` 对单条 `add`/`replace` 调 `MemoryManager.on_memory_write()`；**`remove` 未桥接**（既有缺口，本任务不强制补齐）。

### 2.2 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-101a | Schema：`operations: [{action, target, content?, old_text?}]`；与顶层 `action` **互斥**（同 call 二选一） | `memory_tool.py` schema | 1h |
| HP-101b | `MemoryStore.apply_batch(ops)`：按 target 分组；锁顺序 **固定 `memory` → `user`** 防死锁；内存中 simulate → 全过才写盘 | `memory_tool.py` | 3h |
| HP-101c | 失败语义：任一条 replace/remove 无匹配、threat、超限 → **整批失败、磁盘不变**；返回 `{success, results[], summary}` | handler | 1h |
| HP-101d | Provider 桥接：批量成功后对每条 add/replace 调 `on_memory_write()`（与单条 parity）；remove 仍不桥接 | `tool_executor.py` | 1h |
| HP-101e | 测试：成功多步；中途校验失败回滚；drift 中途触发；互斥参数；跨 target 批量 | `test_memory_tool.py` | 2h |

### 2.3 验收补充

- [ ] 单 action 路径行为 **零回归**（现有 ~646 行测试全绿）
- [ ] 批量与 frozen snapshot 不变（仍不 mid-session 改 system prompt）
- [ ] `MemoryProvider` ABC **零改动**

### 2.4 风险

| 级别 | 项 | 缓解 |
|------|-----|------|
| 低 | 跨 target 双锁 | 固定 lock order |
| 低 | 批量内 duplicate add | simulate 阶段拒绝 |
| 中 | provider 桥接部分成功 | 先写盘再桥接；桥接 fail 只 log，不回滚文件（与单条一致） |

**评审结论：✅ 可立即开工，风险最低，建议 Phase 1 第一项。**

---

## 3. HP-102 — Gateway 按通道模型覆盖（细化）

### 3.1 现状核查

- 会话级覆盖 **已存在**：`_session_model_overrides` + `_resolve_session_agent_runtime()`（`gateway/agent_runner.py` L46+）。
- `/model` 写入覆盖：`gateway/command_handlers.py`；清除：`/new`、session reset。
- 全局默认：`_resolve_gateway_model()`（`gateway/config_helpers.py`）。
- **不存在**：`gateway.model_overrides` 配置键；platform/channel 级 model 分辨。
- 可复用模式：`gateway/display_config.py` 的 **platform → global → default** 分辨顺序（仅 display，非 model）。

### 3.2 覆盖键规范（评审定案）

Hermes 示例 `telegram:<chat_id>` 与 intellect `build_session_key` 输出 **不一致**。Phase 1 采用 **双键查找**，避免改 Rust session key：

```yaml
gateway:
  model_overrides:
    # 平台级（Platform enum 名，小写）
    telegram: { provider: openrouter, model: "..." }
    discord: { model: "..." }          # provider 可省略，回落全局 credential
    # 通道级（与 session_key 后缀一致，见下）
    "telegram:dm:12345": { model: "..." }
    "telegram:group:-100123:67890": { model: "..." }
```

**查找顺序**（在 `_resolve_session_agent_runtime` 内，session override 之前）：

1. 从 `session_key` 剥离 prefix `agent:main:` → 得 `platform:kind:chat_id...`
2. 尝试完整后缀键 → 平台名键 → 无则跳过
3. 会话 `/model` override **仍最高优先级**（现有行为不变）

文档须在 `website/docs` gateway 配置节说明键格式（可随 PR 补最小 doc）。

### 3.3 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-102a | `DEFAULT_CONFIG["gateway"]["model_overrides"] = {}` + 类型注释 | `intellect_cli/config.py` | 30m |
| HP-102b | `load_gateway_config()` bridge（与 `display`/`session_reset` 同级） | `gateway/config.py` | 1h |
| HP-102c | `_resolve_config_model_override(session_key, user_config)` + session_key 规范化 | `gateway/config_helpers.py` | 3h |
| HP-102d | 并入 `_resolve_session_agent_runtime()`：config override → 再 session override | `gateway/agent_runner.py` | 2h |
| HP-102e | 三条 loader 测试：`load_cli_config` / `load_config` / gateway YAML 直读各 1 case | 新测试文件 | 2h |
| HP-102f | 优先级矩阵：4 层 × 部分字段 override（仅 model / 全 bundle） | `test_gateway_model_overrides.py` | 4h |
| HP-102g | （可选）`intellect doctor` 校验未知 platform 名 | `doctor.py` | 1h |

### 3.4 验收补充

- [ ] 无 override 时与当前 main **比特级行为一致**
- [ ] `_agent_cache`：session override 已有 invalidation 路径；config override 为静态 YAML，**进程内不变**，无需额外 cache bust
- [ ] cron / api_server 路径若共用 `_resolve_session_agent_runtime` 则自动受益；需在测试中 **点名** 一条 gateway 消息路径

### 3.5 风险

| 级别 | 项 | 缓解 |
|------|-----|------|
| 中 | 键空间文档不足导致用户配错 | 键规范写入 plan + 配置注释 |
| 低 | 三条 loader 遗漏 | HP-102e 硬性验收 |
| 低 | partial override（仅 model）credential 回落 | 复用 session override 的 `_resolve_runtime_agent_kwargs` 路径 |

**评审结论：✅ 可开工；HP-102a–c 为前置（键规范 + config），再动 agent_runner。**

---

## 4. HP-103 — 图像编辑（细化）

### 4.1 现状核查

- `ImageGenProvider` 仅 `generate()`；无 `supports_edit` / `edit()`。
- `image_generate` tool：仅 `prompt` + `aspect_ratio`；**无路径校验**。
- 插件：`openai/`、`fal/` 均为 text-to-image；`krea/` 有 style reference，**≠ Hermes edit 语义**。
- 安全：`is_forbidden_path()` / `rust_is_forbidden_path` 已存在，未接入 image 路径。

### 4.2 分期建议（评审修订）

| 分期 | 范围 | 工期 |
|------|------|------|
| **HP-103-MVP** | ABC + tool 路由 + path 安全 + **OpenAI** `images.edit` | **2 人日** |
| **HP-103-FAL** | FAL 编辑端点（需 API 调研 + catalog 扩展） | **+1–2 人日**，可溢出 Phase 2 首项 |

设计 §9.2 修订 5 已允许 HP-103 溢出；评审 **采纳 MVP 分期**，TODO 中 FAL 标记为 HP-103b。

### 4.3 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-103a | ABC：`supports_edit: bool = False`；默认 `edit()` → `error_response(capability)` | `image_gen_provider.py` | 1h |
| HP-103b | Tool schema `source_image?`；存在时 `is_forbidden_path` + 文件存在性检查 | `image_generation_tool.py` | 2h |
| HP-103c | 路由：`source_image` + `supports_edit` → `edit()`；否则明确错误（非 silent fallback 到 generate） | 同上 | 2h |
| HP-103d | OpenAI plugin：`images.edit`（或 gpt-image 等价 API）；输出走 `save_b64_image` → cache | `plugins/image_gen/openai/` | 6h |
| HP-103e | 安全测试：`.env` / `~/.ssh` 源图路径被拒；不支持 provider 报错 | `test_image_edit.py` | 3h |
| HP-103f | FAL 编辑端点 + catalog（**可 Phase 2**） | `plugins/image_gen/fal/` + pipeline | 8h |

### 4.4 明确不做（Phase 1）

- URL 作 `source_image`（Hermes 首版仅 path；URL 需 SSRF 审查，单列 follow-up）
- mask / inpaint 高级参数（除非 OpenAI API 强制）
- 修改 `image_gen` 以外 toolset

### 4.5 风险

| 级别 | 项 | 缓解 |
|------|-----|------|
| 高 | OpenAI edit API 面与 generate 不一致 | HP-103d 前 1h API spike |
| 中 | FAL 端点分散 | 溢出 + 单独 PR |
| 中 | 源图经 gateway 传入路径 | 仅允许 workspace 可读路径 + forbidden path |

**评审结论：⚠️ 条件通过 — MVP（OpenAI）纳入 Phase 1；FAL 为 HP-103b 可选/溢出。**

---

## 5. 推荐执行顺序

```text
Week 1
├── HP-101 ────────────────► merge（1d）
├── HP-102a→f ─────────────► merge（2d）  ∥ 与 101 并行
└── HP-103a→e（MVP）───────► merge（2d）  建议 101/102 合并后再开，减并行 review 负担

Week 2（缓冲 / 溢出）
└── HP-103f（FAL edit）或 Phase 2 首项
```

---

## 6. 评审总表

| 任务 | 设计估时 | 评审估时 | 风险 | 决议 |
|------|----------|----------|------|------|
| HP-101 | 1d | 1d | 低 | ✅ **批准** |
| HP-102 | 2d | 2d | 低-中 | ✅ **批准**（键规范 §3.2 为前置） |
| HP-103 | 3d | 2d MVP + 1–2d FAL | 中 | ⚠️ **分期批准**（OpenAI MVP in Phase 1） |

### 6.1 通过条件

1. Phase 1 **不得**修改 `run_agent.py` conversation loop（HP-102 仅 gateway agent 构建路径）。
2. HP-102 必须含 **三条 config loader** 测试（设计 §9.2 修订 4）。
3. HP-103 必须含 **forbidden path** 测试；无 `source_image` 时行为与 main 一致。
4. 每项独立 PR 可审（建议 3 PR，或 101+102 合并 + 103 单独）。

### 6.2 与 Phase 2 边界

- HP-101/102/103 **不** 阻塞 HP-201（后台委派 spike）。
- HP-103 FAL 若溢出，**不** 阻塞 HP-204（/learn）启动。

---

## 7. 评审签字

| 角色 | 结论 | 日期 |
|------|------|------|
| 技术评审 | Phase 1 三项方案细化完成，按 §6 条件实施 | 2026-07-08 |
| 待产品确认 | HP-102 通道键 UX（是否需 `intellect gateway model-overrides` CLI） | 可选 follow-up |
