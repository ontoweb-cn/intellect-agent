# ACP 版本漂移修复 + 0.12.1 迁移计划

日期：2026-09-12
状态：待实施
关联：issue #125（ACP clarify 曾以"SDK 不支持 elicitation"为由暂缓——该结论已更正）

---

## 0. 背景与一处结论更正

上一轮评估 issue #125 时，我判定"pinned 的 `agent-client-protocol==0.12.1` 没有
elicitation，所以 ACP clarify 被 SDK 卡住"。**该结论是错的**，因为当时读的是
`.venv` 里实际安装的 **0.9.0** 源码，而非 `pyproject.toml` 声明的 0.12.1。

实测（隔离安装 0.12.1）：

| 版本 | elicitation | helper 导出 |
|---|---|---|
| 0.9.0 | **无**（`ClientCapabilities` 仅 auth/fs/terminal） | 20 个齐全 |
| 0.12.1 | **有**（`elicitation` capability、`create_elicitation()`） | 20 个齐全 |
| 1.0.0rc1 | 有 | **全部删除**（`acp.helpers` 模块消失） |

**结论：ACP clarify 不需要升级 SDK，用 0.12.1 就能实现。** issue #125 建议的
elicitation 桥接方案在 0.12.1 上完全可行。

---

## 1. 漂移根因（已定位到具体提交）

`tools/lazy_deps.py:96-98` 明文契约：

> Pinned to exact versions to match pyproject.toml's no-ranges policy.
> **When bumping, update both this map AND the corresponding extra in
> pyproject.toml.**

但 dependabot 的 `chore(deps): Bump the python-patch group`（commit `20a06f8`，
经 `6a11312` 合入 main）只 bump 了 `pyproject.toml` 的 `acp` extra
（0.9.0 → 0.12.1），**没有同步 `lazy_deps.py`**。

### 影响不止 ACP：26 个包存在同类冲突

实测发现 `lazy_deps.py` 与 `pyproject.toml` 之间 **26 个包版本不一致**。
且 `.venv` 的实际安装显示两面性：

- 12 个包跟的是 `lazy_deps` pin（如 `anthropic` 0.87.0、`elevenlabs` 1.59.0）
- 4 个包跟的是 `pyproject` pin（含 `agent-client-protocol` 0.12.1）

### 具体风险：`intellect update` 会静默降级

`active_features()`（`lazy_deps.py:511-525`）用**存在性**判断某功能是否"活跃"
（只要装了就算），随后 `refresh_active_features()` 会按 `LAZY_DEPS` 的 pin 重装。
在本 venv 上实测：

```
active features: 16 of 26
  platform.slack   missing=('slack-bolt==1.27.0', 'slack-sdk==3.40.1', 'aiohttp==3.13.4')  <== WOULD REINSTALL/DOWNGRADE
  tool.acp         missing=('agent-client-protocol==0.9.0',)                                <== WOULD REINSTALL/DOWNGRADE
```

即：跑一次 `intellect update` 会把刚装好的 `agent-client-protocol` 0.12.1
**降级回 0.9.0**，ACP clarify 的实现会随之失效。这不是"环境旧了"，而是两份
pin 表的真实矛盾。

---

## 2. 范围决策

本计划只处理 **ACP** 的漂移（用户明确要求），但把 26 个冲突做成**可检测的回归测试**，
避免同类问题再次静默发生。其余 25 个包的 pin 同步列为后续项（不在本次动手）。

---

## 3. 实施步骤

### Step 1 — 同步 `lazy_deps.py` 的 ACP pin

`tools/lazy_deps.py:171`：

```python
"tool.acp": ("agent-client-protocol==0.9.0",),
```

改为与 `pyproject.toml` 的 `acp` extra 一致的 0.12.1：

```python
"tool.acp": ("agent-client-protocol==0.12.1",),
```

同时把该行所在注释补全为"须与 pyproject `acp` extra 同步"的提示，避免下次再漏。

验收：`LAZY_DEPS["tool.acp"]` 与 `pyproject.toml` `acp` extra 完全一致。

### Step 2 — 修正 `.venv` 安装

`.venv` 已在本轮实测中安装为 0.12.1（与 `pyproject.toml` / `uv.lock` 一致），
无需再动。若在别处复现，用：

```bash
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
.venv/bin/python -m pip install -i "$UV_INDEX_URL" 'agent-client-protocol==0.12.1'
```

验收：`importlib.metadata.version("agent-client-protocol") == "0.12.1"`。

### Step 3 — 新增漂移回归测试（防同类问题）

在 `tests/tools/test_lazy_deps.py` 增加一条**契约测试**：对每个在
`LAZY_DEPS` 和 `pyproject.toml` extras 中同时出现的包，断言两者的 pin 一致。

设计要点（避免写成 change-detector 测试）：

- 断言的是**关系**（"同名包的 pin 必须一致"），不是"ACP 必须是 0.12.1"这种快照值
  ——后者会在每次 dependabot bump 时误报，违反仓库的测试规范。
- 允许显式豁免清单（当前 25 个历史冲突），并在测试里注明"豁免项应逐步清零"。
  这样测试立刻对 ACP 生效，又不会因为一次性修 25 个包而阻塞。
- 断言豁免清单**只能缩小不能增长**已有条目之外的新增冲突：新引入的不一致会让
  测试失败，正是我们要的信号。

验收：把 `lazy_deps.py` 的 ACP pin 临时改回 0.9.0，测试必须失败（证明有约束力）；
改回 0.12.1 后通过。

### Step 4 — 在 0.12.1 下回归 ACP 全套件

```bash
.venv/bin/python -m pytest tests/acp/ tests/acp_adapter/ -q
```

已预跑结果：**304 passed, 1 failed**，唯一失败是既有的
`tests/acp/test_session.py::TestPersistence::test_update_cwd_restores_from_db`
（0.9.0 下同样失败，与版本无关）。验收：失败集合与 0.9.0 基线一致，无新增。

### Step 5 — 更新文档

- `pyproject.toml` 的 `acp` extra 注释：说明该 pin 须与 `lazy_deps.py` 的
  `tool.acp` 保持同步。
- 内存记录更正：先前"A3/ACP clarify 被 SDK 卡住"的结论改为"0.12.1 已支持
  elicitation，可实现"。

---

## 4. 明确不在本次范围

| 项 | 原因 |
|---|---|
| 升级到 `1.0.0rc1` | 预发布版；PyPI 稳定版仍是 0.12.1；且要动 45 个 helper 调用点，收益不明确 |
| ACP clarify 的实现 | 属于 issue #125 的功能实现，应作为独立批次（现在已确认技术上可行） |
| 其余 25 个包的 pin 同步 | 独立风险面（每个都要验证运行时行为），建议单独立项 |
| `aiohttp` / `filelock` / `pytest` 等 "NEITHER" 项 | 实测既不跟 lazy_deps 也不跟 pyproject，另有成因，需单独排查 |

---

## 5. 风险与回滚

| 风险 | 评估 | 缓解 |
|---|---|---|
| 0.12.1 破坏现有 ACP 行为 | **低** — 已实测 304 passed，失败集合与 0.9.0 一致 | 失败即回退 pin 到 0.9.0 |
| pin 同步后 `intellect update` 行为变化 | 低 — 正是修复目标（不再降级） | Step 3 的测试提供保护 |
| 新测试对合法 bump 误报 | 中 — 若 dependabot 再只改一侧 | 这正是设计意图（暴露漏同步）；豁免清单可显式登记 |

回滚：`git revert` 本批提交即可；`.venv` 侧重新安装目标版本。

---

## 6. 验收清单

- [ ] `LAZY_DEPS["tool.acp"]` == `pyproject.toml` `acp` extra（均为 0.12.1）
- [ ] `.venv` 中 `agent-client-protocol` 为 0.12.1
- [ ] 新增的 pin 一致性测试对 ACP 有约束力（故意破坏时失败）
- [ ] `tests/acp/` + `tests/acp_adapter/` 失败集合不劣于 0.9.0 基线
- [ ] 无新增 ruff 告警（对比 `01097e7` 基线）
