# W12 细化稿 — Journey 理想层（主）+ openat 窄切片（旁路）

> **日期**：2026-07-12  
> **状态**：**APPROVED → 执行中**（用户 Approve 2026-07-12）  
> **策略**：用户选组合 **4** — **A 为主** + **C 旁路小 PR**；**D Gateway 层 C 后置**  
> **前置**：W0–W11 已合入 main（tip @ `3fba844` / #59）  
> **计划评审**：[`code-reviewer`](2aafaafc-92ba-4f4c-9352-fdf574c715a4) — Request changes → 本修订钉死 L4/L8/L9 + Task 1.4 测改写  
> **父文档**：[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md) §1.2–1.3；[`2026-07-12-w7-p2-security.md`](./2026-07-12-w7-p2-security.md)；[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md)  
> **产品锁**：永久单用户不变；SessionChannel / outline / 默认开 Profiles **不进**

> **For agentic workers:** 用户 Approve 后再执行；用 subagent-driven-development 或 executing-plans，按任务勾选推进。

**Goal:** 消除 Journey memory 全局下标漂移根因；把 WebUI 文件 delete/rename **集中到**与 W7 一致的 `workspace_io` helper（非「首次引入 containment」）；不碰 Gateway 层 C / 完整 openat 漫步。

**Architecture:** 双轨可拆 PR — **12a** = Task 1 only（memory 本地 id）；**12c** = Task 3（delete/rename helper）。P1-2 stretch = **可丢弃的独立 tip commit**，不进 12a merge gate。

**Tech Stack:** `agent/learning_graph.py`、`agent/learning_mutations.py`、`webui/api/workspace_io.py`、`webui/api/routes.py`、既有 Journey / workspace 单测。

---

## 0. 方案对比（组合 4 落地）

| 方案 | 内容 | 估时 | 取舍 |
|------|------|------|------|
| **A-only** | 仅 P1-1 本地 memory id | 3–4d | 最干净；安全债不动 |
| **C-only** | 仅 delete/rename 收进 workspace_io | 2–3d | 无 Journey UX 收益 |
| **A+C（选定）** | 12a 主 + 12c 旁路 | 5–7d | 体验 + 安全各进一步；拆 2 PR |
| **A+D** | Journey + Gateway start/stop | 7–10d+ | D 需先 RFC → **本拍不做** |

**选定**：**A+C**。旁路 = **C 窄切片**（helper 集中 + 测；非完整 directory-fd）。**D 延期**。

---

## 1. 锁（L1–L12）

| ID | 锁 | 决议 |
|----|-----|------|
| **L1** | 主轴 | **Journey P1-1 理想**：`memory:{source}:{local}`。nodes **与** edges **必须**共用同一 helper（禁止 edges 仍 `enumerate` 全局 idx） |
| **L2** | 兼容 | **不**兼容旧全局下标客户端缓存；刷新图即新 id。保留 409 `stale` 安全网 |
| **L3** | 指纹 | **本拍不做** content fingerprint v2 |
| **L4** | P1-2 stretch | **理想打磨**（MVP 已有 `_resolve_journey_skill` / hub uninstall）。**独立可丢 tip commit**；12a merge gate = **仅 Task 1**。若动 uninstall：必须保持 `_uninstall_hub_skill` 的 `INTELLECT_HOME` / hub 路径语义（或证明 `uninstall_skill` 同等 profile-safe）；E3 绿才能保留该 tip |
| **L5** | P1-3 / members | **不改** |
| **L6** | 旁路 C | delete + rename 经 `workspace_io` helper。**诚实**：今日已有 `safe_resolve`→`resolve_under_root`；12c = **集中化 + 测**，非「首次 containment」 |
| **L7** | C 非目标 | **不做**完整 `openat` 漫步；**不**改 `list_dir`；**不**改 agent `tools/` |
| **L8** | delete 语义 | 对齐 **W7 S3**：in-root symlink resolve 后作用于 **resolved target**（今日语义）。escape symlink → ValueError/400。**禁止**「一律拒 symlink leaf」。`unlink`/`rmtree` 在 resolve 与 mutate 之间仍有 TOCTOU residual（记入 W13，勿宣传为 openat 等价）。目录仍要 `recursive`；拒删 workspace root（`""` / `"."`） |
| **L9** | rename 语义 | src/dst 均 `resolve_under_root`；`new_name` 禁 `/` `..`；同父目录 **含非空目录** rename 成功；dst 已存在 → 400；拒 rename root；同样记录 TOCTOU residual |
| **L10** | D Gateway 层 C | **后置** |
| **L11** | SessionChannel / outline | **不进** |
| **L12** | 拆 PR | **W12a = Task 1 only**；stretch 可选 follow-up tip；**W12c** 可在 Approve 后与 12a 并行开发、串行或随后合入 |

---

## 2. Definition of Done

### 12a — Journey memory 本地 id（必须）

| 必须 | 不在 12a DoD |
|------|-------------|
| 图节点 id = `memory:{source}:{local}`（同文件内从 0） | fingerprint v2 |
| 共用 id helper；**删除** `_memory_local_index` | 改 Gateway `/journey` |
| **验收**：删 `memory:memory:0` 后 `memory:profile:0` 仍指向同一 USER.md 正文（跨源稳定，父文档 §1.2） | P1-2 stretch |
| OOB local → stale 409；`journey.js` 刷新仍绿 | 多用户 |
| `test_learning_mutations` / e2e / api_profile 按 §1.4 改写后绿 | |

### 12c — delete/rename helper 集中（必须，可后合）

| 必须 | 不在 12c DoD |
|------|-------------|
| 路由改走 `unlink_under_root` / `rmtree_under_root` / `rename_under_root` | directory-fd openat 全量 |
| 穿越 / 出界 symlink → 400；§3.5 边缘测全绿 | list_dir / mkdir / agent tools |
| 文档/注释标明：非 TOCTOU-closed；residual → W13 | 「首次」引入 containment 的营销表述 |

### Stretch（可选 tip）

| 项 | DoD |
|----|-----|
| P1-2 理想打磨 | `restoreHint` 服务端字段 + 轻量路由器；**不**从零重建 provenance；E3 绿；可整体丢弃 |

---

## 3. 实施切片

```text
0  计划 REVISED → 用户 Approve
        │
        ├──────────────────┐
        ▼                  ▼
12a Task 1 only        12c Task 3（可并行开发）
        │                  │
        ▼                  ▼
   merge W12a          merge W12c
        │
        └─ 可选 tip：Task 2（可丢）
```

| PR | 内容 |
|----|------|
| **W12-plan** | 本文件 + parity 指针 + W11 DONE 标注 |
| **W12a** | Task 1 **only** |
| **W12c** | Task 3 |
| **W12a-stretch**（可选） | Task 2 tip |

---

## 4. 任务清单

### Task 0 — 计划与评审

- [x] 0.1 本文件状态 **REVISED**（吸收评审）  
- [x] 0.2 parity 分析追加 W12 指针  
- [x] 0.3 W11 计划头标 **DONE @ 3fba844**  
- [ ] 0.4 Docs PR（可选先合） / 用户 Approve 后开 12a  

### Task 1 — P1-1 本地 memory id（12a）

**Files:** `agent/learning_graph.py`；`agent/learning_mutations.py`；`tests/agent/test_learning_mutations.py`；（必要时）`test_learning_e2e.py` / `test_learning_api_profile.py`

- [ ] 1.1 抽出 `memory_node_id(source, local) -> str`（或等价）；构图按 source 赋 local  
- [ ] 1.2 `_memory_skill_edges` **禁止** `enumerate` 全局 idx；必须用同一 helper  
- [ ] 1.3 `_locate_memory(source, local)` 直接索引 `chunks[local]`；**删除** `_memory_local_index`。说明：`MemoryStore._read_file` **已丢空块**，与 card 下标对齐，勿「修」非 bug  
- [ ] 1.4 **测改写（钉死）**：  
  - 重命名 `test_memory_global_index_*` → `test_memory_local_index_per_source`  
  - 断言 `memory:memory:0/1` + `memory:profile:0`（不是 `:2`）  
  - **删除** `test_stale_memory_source_mismatch`（全局列表错源语义已消失）  
  - **替换为**：(a) 每源 local OOB → stale 409；(b) 删 `memory:memory:0` 后 `memory:profile:0` 仍解析到原 USER.md 正文  
  - 保留 `memory:memory:9` 类 OOB  
- [ ] 1.5 `scripts/run_tests.sh` 相关文件绿  

**伪代码：**

```python
def memory_node_id(source: str, local: int) -> str:
    return f"memory:{source}:{local}"

# nodes + edges 共用
local_by_source = {"memory": 0, "profile": 0}
for card in memory_cards:
    src = card["source"]
    local = local_by_source[src]
    local_by_source[src] = local + 1
    mid = memory_node_id(src, local)
```

### Task 2 — P1-2 理想打磨（stretch / 可丢 tip）

**Files:** `agent/learning_mutations.py`；可选 `journey.js`；E2E

- [ ] 2.1 轻量统一 `restore_hint` / `deleteMode` 字段（**不**从零造 provenance 表）  
- [ ] 2.2 仅当证明 profile-safe 时才委托 `skills_hub.uninstall_skill`；否则保留 `_uninstall_hub_skill` 并只做 FE/hint 对齐  
- [ ] 2.3 E3 绿；否则 **整 tip 丢弃**  

### Task 3 — delete/rename helper 集中（12c）

**Files:** `webui/api/workspace_io.py`；`webui/api/routes.py`；`tests/webui/test_workspace_anchored_io.py`

**Preamble（钉死）：** Helpers 统一 containment + 错误形态。**不**宣称 TOCTOU-closed。residual = W7 openat backlog（W13）。`rmtree` 前再检 containment 仅为 best-effort。

- [ ] 3.1 `unlink_under_root(root, rel)`  
- [ ] 3.2 `rmtree_under_root(root, rel)`  
- [ ] 3.3 `rename_under_root(root, src_rel, dest_rel)`  
- [ ] 3.4 路由改调用；错误仍 `_sanitize_error`  
- [ ] 3.5 测：  
  - `../` 穿越 → 400  
  - in-tree symlink→outside → 400  
  - 同目录文件 rename 成功  
  - **非空目录**同父 rename 成功  
  - `recursive=false` 删目录 → 400  
  - dest 已存在 → 400  
  - 拒对 `""` / `"."`（workspace root）delete/rename  
  - recursive 目录删成功  

### Task 4 — 合入

- [ ] 4.1 W12a merge（Task 1）  
- [ ] 4.2 W12c merge（Task 3）  
- [ ] 4.3 可选 stretch tip  
- [ ] 4.4 本文件勾选 + parity 索引  

---

## 5. 触点速查

| 层 | 路径 |
|----|------|
| Graph | `agent/learning_graph.py`（`_memory_skill_edges` ~227；nodes ~292） |
| Mutations | `agent/learning_mutations.py`（`_memory_local_index` ~40–63） |
| API | `webui/api/learning.py`（409 保留） |
| FE | `webui/static/journey.js`（12a 预期无硬编码全局 id；stretch 才动 hint） |
| I/O | `webui/api/workspace_io.py`；`routes.py` ~13014 / ~13091（今日已 `safe_resolve`） |
| Tests | `tests/agent/test_learning_mutations.py`；`tests/webui/test_workspace_anchored_io.py` |
| Keep out | Gateway 层 C；SessionChannel；完整 openat；`list_dir` |

---

## 6. 非目标

- Gateway 层 C / start/stop/restart 面板  
- 完整 directory-fd `openat`；agent `tools/` 硬化  
- SessionChannel；outline；Secure cookie；wiki_merge  
- fingerprint v2；默认开 Profiles；真 multi-user  

---

## 7. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 旧全局 id 缓存 | 409 + auto refresh；跨源稳定测钉死 |
| 夸大 12c 安全收益 | L6/L8：集中化 ≠ TOCTOU-closed |
| stretch 拖垮 12a | L4/L12：merge gate 仅 Task 1 |
| hub uninstall 回归 | L4 profile-safe 门禁；否则丢 tip |

---

## 8. 签字表

| # | 问题 | 建议 | ☐ |
|---|------|------|---|
| R1 | 组合 4 = A+C？ | **是** | ☑ |
| R2 | D 本拍后置？ | **是** | ☑ |
| R3 | 旧全局 memory id 不兼容？ | **是** | ☑ |
| R4 | P1-2 为可丢 tip？ | **是** | ☑ |
| R5 | W12a = Task 1 only？ | **是** | ☑ |
| R6 | C 不做完整 openat / 不拒 in-root symlink？ | **是** | ☑ |
| R7 | 测改写含删 `test_stale_memory_source_mismatch`？ | **是** | ☑ |

---

## 9. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT — 用户选组合 4 |
| 2026-07-12 | REVISED — 评审：L8↔W7 S3；Task 1.4 测改写；L4 tip 可丢；L6/Task3 TOCTOU 诚实；L9 目录/root 边缘 |
| 2026-07-12 | APPROVED — 用户 Approve；进入执行 |
