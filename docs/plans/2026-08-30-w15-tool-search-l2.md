# W15 — Tools 渐进披露对齐 Hermes（Tool Search L2）

> **日期**：2026-08-30  
> **状态**：**REVISED**（技术评审后：L7 并行准入钉死剥桥；新增 L11 兼容锁；P3 补并行回归测；R 表扩到 R10）  
> **前置**：`tools/tool_search.py` 基线已合（阈值门控 auto）；Hermes 分析：[`../../hermes-agent/docs/tool-search-progressive-disclosure.md`](../../hermes-agent/docs/tool-search-progressive-disclosure.md)  
> **产品锁**：永久单用户；**prompt-cache 不破**（会话中途不改 toolsets；披露走 bridge + history）

> **For agentic workers:** 本拍 = **A 路线**（对齐 Hermes 主线 Phase 2+），**不含** core 二次 deferral（可选 P5，另 tip）。评审签 R 表。

**Goal:** 把 Intellect 的 `tool_search` 从「阈值门控裸桥」升级为「有 deferrable 即激活 + 分层目录清单 + 检索/调用硬化」，对齐 Hermes 已验证路径；不动 `_INTELLECT_CORE_TOOLS` 契约。

**Architecture:** 单模块演进 `tools/tool_search.py` + `model_tools.py` 接缝 + `agent/tool_executor.py` 并行准入；配置 `tools.tool_search.*`；测试 `tests/tools/test_tool_search.py`（+ 可选 `test_deferral_fixes.py`）。

**Tech Stack:** BM25 + 可选 Snowball；OpenAI tools 数组；`_INTELLECT_CORE_TOOLS`；MCP/plugin registry。

---

## 0. 方案对比与推荐

| 路线 | 内容 | 估时 | 评价 |
|------|------|------|------|
| **A** | 对齐 Hermes 主线（listing + 检索 + 硬化） | 3–5d | **推荐**；解决「激活后零清单」 |
| **B** | A + core 二次 deferral（~15–20 eager） | +2–3d | 风险高；另 tip |
| **C** | 仅调阈值/文档 | 0.5d | 不解决核心缺口 |

**推荐默认：** **A**。决策口诀：要「多 MCP 可用性」→ A；要「再砍一半 schema」→ 另开 B。

---

## 1. 锁（L1–L10）

| ID | 锁 | 决议 |
|----|-----|------|
| **L1** | 激活策略 | **L2**：存在任一 deferrable（MCP/非 core 插件）即激活桥；`threshold_pct` **不再**决定是否激活，只参与 listing 预算（见 L3）。`enabled: auto` = L2 的别名（与 Hermes 一致）；`on` = 恒激活；`off` = 关。 |
| **L2** | Core 契约 | `_INTELLECT_CORE_TOOLS` **永不** deferral；桥接三工具自身永不 deferral；GUI 面（若有）保持直连。 |
| **L3** | Listing 预算 | `listing_max_tokens`（默认 4000）+ 可选 `listing_pct`（默认 5% 上下文）取 min；超预算按 **full → names → mixed → groups → none** 降级；**逐服务器/逐 toolset** 折叠（大 MCP 不拖垮小 MCP）。 |
| **L4** | 确定性 | 同一 catalog → 同一 listing 字节（排序稳定、无时间戳）；listing 嵌入 bridge 的 `tool_search` description 或固定 preamble，**不**改 system prompt 结构、**不**热改 tools 数组。 |
| **L5** | 检索 | `tool_search` 支持 `queries: []`（≤10）+ `limit`（默认 5，上限 25，硬顶 50）；`tool_describe` 支持 `names: []`（≤10）。BM25 + 可选 Snowball 词干；源标签入索引；精确名 `inf`；空命中附 `available_sources`。 |
| **L6** | 调用硬化 | `tool_call` 前 `validate_deferred_call_args`：缺 required → 回传 schema + "NOT invoked"；反递归；拒经桥调 core；会话作用域门保留。 |
| **L7** | 并行准入 | `tool_executor` 并发路径已 unwrap `tool_call`；**`_should_parallelize_tool_batch` 须先剥桥**：`tool_call` → 底层名后再判定 `_NEVER_PARALLEL` / `_PARALLEL_SAFE` / `_PATH_SCOPED` / MCP `supports_parallel_tool_calls`；`tool_search`/`tool_describe` 视为只读并行安全；坏桥调用（无 name / 非 dict arguments）保持串行屏障；准入结果与直连**完全对等**（不升级不降级）。 |
| **L8** | 缓存 | 披露结果进 **对话历史**（`tool` message），后续轮次缓存；listing 字节稳定以保前缀缓存。 |
| **L9** | 非目标 | 不做 core deferral（P5 另 tip）；不做全仓工具懒 import；不改 WebUI workspace_io；不引入 SessionChannel。 |
| **L10** | 宣称 | 文档写：「MCP/插件工具按需加载；core 永 eager；listing 随规模伸缩」。**禁止**「全工具 TOCTOU/全量懒加载」。 |
| **L11** | 兼容 | 旧配置 `tools.tool_search: true/false` 与 `{enabled: auto/on/off, threshold_pct}` 仍解析；`threshold_pct` 语义改变需在 changelog 注明；`enabled: auto` 行为从「阈值门控」变为「L2」。 |

---

## 2. 任务切片（DoD）

### P0 — 激活策略切换（L2）

- [ ] `should_activate`：`auto`/`on` → 有 deferrable 即 True；`off` → False  
- [ ] `threshold_pct` 语义改为「listing 预算参考」；`auto` 不再因低于阈值而透传  
- [ ] 配置向后兼容：旧 `enabled: auto` 行为变为 L2；文档说明

### P1 — Catalog listing（Tier 1/2）

- [ ] `build_catalog_listing_with_form`：按 toolset 分组（`mcp-*` / plugin），渲染 `full` / `names` / `mixed` / `groups` / `none`  
- [ ] 短描述：首句、≤60 字符、正确处理 `e.g.` / `i.e.` / `v1.2`  
- [ ] 逐组贪心折叠；确定性排序；预算封顶  
- [ ] 注入点：bridge `tool_search` description 或独立 preamble（二选一，需保缓存稳定）

### P2 — 检索升级

- [ ] `queries[]` 并行 + 批量 `describe`  
- [ ] Snowball 词干（可选依赖；无则降级为现有 tokenize）  
- [ ] 源标签索引；`mcp__` 前缀剥词；精确名 `inf`；子串 fallback  
- [ ] 空结果：`available_sources` + hint

### P3 — 调用硬化

- [ ] `validate_deferred_call_args`（缺 required → schema + NOT invoked）  
- [ ] **并行准入剥桥**：`_should_parallelize_tool_batch` 在判定前把 `tool_call` 解包到底层名；`tool_search`/`tool_describe` 标记为只读并行安全；坏桥调用保持串行屏障；准入与直连对等（不升级不降级）  
- [ ] 回归：OpenClaw#84141 式「core 在混合工具集中存活」；作用域不泄漏  
- [ ] 回归：两个桥接并行安全 MCP 调用仍并行；未 opt-in 的桥接调用保持串行；核心文件工具不能经桥偷渡并发

### P4 — 配置 / 文档 / 测试

- [ ] `tools.tool_search`：`enabled`, `listing`, `listing_max_tokens`, `listing_pct`, `search_default_limit`, `max_search_limit`  
- [ ] 刷新 `website/docs/user-guide/features/tool-search.md`  
- [ ] `tests/tools/test_tool_search.py` 按 Hermes §10 对照表补行为测（不抄私有实现）

### P5 —（可选，另 tip）Core deferral

- [ ] `_INTELLECT_EAGER_TOOLS ⊂ _INTELLECT_CORE_TOOLS`；旧名别名；默认 off

---

## 3. 非目标

- 会话中途改 toolsets / 热注入 tools 数组  
- 把 schema 写进 system prompt  
- 全仓工具懒 import（registry 仍 import-time）  
- agent `tools/` 与 WebUI workspace_io 混谈  
- core 二次 deferral 进本 merge gate

---

## 4. 风险与回滚

| 风险 | 缓解 |
|------|------|
| L2 激活导致小工具集也付桥开销 | `enabled: off` 可关；listing `none` 时桥 schema ~300 tokens |
| Listing 字节不稳破缓存 | 确定性排序 + 无时间戳；测试断言同 catalog 同字节 |
| 巨型 MCP 拖垮 listing | 逐组折叠；`groups` 形态只列服务器名+工具数 |
| 盲调用缺参 | `validate_deferred_call_args` 回传 schema |
| 并行准入降级 | `_should_parallelize_tool_batch` 剥桥后按真实名判定；回归测 |
| 旧配置语义漂移 | changelog 注明 `auto` 行为变化；`off` 仍可一键回滚 |

| 回滚 | 做法 |
|------|------|
| 整拍 | `tools.tool_search.enabled: off` 即回到全量 eager |
| 仅 listing | `listing: off` 保留裸桥 |
| 仅 L2 | 恢复 `should_activate` 阈值分支（git revert P0） |

---

## 5. 评审清单（R1–R8）

| ID | 问题 | 期望 | 签 |
|----|------|------|-----|
| R1 | 激活策略 = L2？ | **是** | ☐ |
| R2 | Core 永不 deferral？ | **是** | ☐ |
| R3 | Listing 逐组降级？ | **是** | ☐ |
| R4 | 确定性字节？ | **是** | ☐ |
| R5 | 检索多查询/批量 describe？ | **是** | ☐ |
| R6 | 盲调用探针？ | **是** | ☐ |
| R7 | 并行准入剥桥且对等？ | **是** | ☐ |
| R8 | 不做 core deferral？ | **是** | ☐ |
| R9 | 旧配置兼容 + changelog？ | **是** | ☐ |
| R10 | 非目标不越界？ | **是** | ☐ |

---

## 6. 现状锚点速查

| 项 | 现状 |
|----|------|
| 桥 | `tool_search` / `tool_describe` / `tool_call` 已存在 |
| 激活 | `auto` 阈值门控（10% context） |
| Listing | 无（激活后裸桥） |
| 检索 | 单 query BM25 + 子串 fallback |
| 调用 | executor unwrap + 作用域门；无盲调用探针 |
| 配置 | `enabled` / `threshold_pct` / `search_default_limit` / `max_search_limit` |
