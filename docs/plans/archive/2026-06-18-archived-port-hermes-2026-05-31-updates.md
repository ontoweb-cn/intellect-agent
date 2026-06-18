# Intellect Agent: 移植 Hermes Agent 2026-05-31 每日更新 — 执行报告

**执行日期：** 2026-06-01
**来源：** hermes-agent `b1a25404b` → `eb3cf9750`（38 commits，93 files，+4925/-1249）
**目标分支：** intellect-agent `v0.4.1`
**合并基：** `b1a25404b`（双方共享）

---

## 执行摘要

| 指标 | 数值 |
|------|------|
| hermes-agent 原始提交 | 38 |
| 成功移植提交 | **31** |
| 变更文件 | 100+ |
| 新增代码行 | ~+4000 |
| 删除代码行 | ~-2000 |
| 修复迁移 bug | 5 类 |
| 修复已有测试 | 3 个 |
| 引入新回归 | **0** |

### 提交链

```
b1a25404b (hermes-agent 合并基)
  └─ 58a1f8f5c (migration: hermes → intellect)
      └─ a99f15c49 (v0.4.1 特性)
          └─ 3db187a85 P0 安全修复 (5 commits)
              └─ d1ee74a2a P0 Gateway 稳定性 (4 commits)
                  └─ dfcffff75 P1 关键缺陷修复 (8 commits)
                      └─ 058f87a2b P1 CLI/TUI 改进 (6 commits)
                          └─ 2fc8a0981 P2 功能增强 (5 commits)
                              └─ f5471a087 P2 平台适配 + 冲突解决 (2 commits)
                                  └─ 149fed3d4 迁移 bug 修复
                                      └─ 49a5868fc 测试导入修复
                                          └─ 0af1dd24e bluebubbles 导入修复
                                              └─ ae0894f28 已有测试修复 (3 tests)
```

---

## 一、移植详情

### P0 — 安全修复 ✅

| 原始提交 | 描述 | 移植方式 |
|----------|------|----------|
| `c2cbe2c97` | fix: remove Discord mention redaction | cherry-pick (clean) |
| `bdfba4524` | fix(gateway): stop system tips from auto-uploading | cherry-pick + 冲突解决 |
| `4ec0adebe` | fix(gateway): denylist config.yaml | cherry-pick + 冲突解决 |
| `02d1da49d` | Block root config in media delivery | cherry-pick + 手动适配 |
| `9b78f411c` | fix(security): neutralize file paths | cherry-pick (clean) |

**变更文件**: `gateway/platforms/base.py`, `intellect_cli/tips.py`, `run_agent.py`, `agent/redact.py`, 测试文件

### P0 — Gateway 稳定性 ✅

| 原始提交 | 描述 | 移植方式 |
|----------|------|----------|
| `5cd6c1717` | fix(gateway,cron): prevent restart loops | cherry-pick + 路径重命名 |
| `bd72d333d` | fix(gateway,cron): reuse gateway marker | cherry-pick + 冲突解决 |
| `4259bab7d` | fix(gateway): Telegram DM topic routing | cherry-pick + 冲突解决 |
| `1044d9f25` | fix(gateway): /stop sibling interrupt | cherry-pick (clean) |
| `eb3cf9750` | fix(gateway): resolve _get_dm_topic_info | cherry-pick (clean) |

**变更文件**: `gateway/run.py`, `intellect_cli/cron.py`, `intellect_cli/gateway.py`, `tests/conftest.py`, 测试文件

### P1 — 关键缺陷修复 ✅

| 原始提交 | 描述 | 移植方式 |
|----------|------|----------|
| `ca03486b6` | fix(streaming): stop duplicating tool-call args | cherry-pick (clean) |
| `2b5268f71` | revert: drop cumulative-resend heuristic | cherry-pick (clean) |
| `64628ea89` | fix(anthropic): demote dead thinking signature | cherry-pick (clean) |
| `0ffbcbbe7` | fix(vision): cap embedded image size | cherry-pick (clean) |
| `355af2c20` | fix(session): survive missing FTS5 runtimes | 手动 patch + 品牌替换 |
| `7a315bd70` | fix(tools): preserve live session cwd | cherry-pick (clean) |
| `6f8975dcd` | fix(tools): spawn_via_env wrapper fix | cherry-pick (clean) |
| `ec67def5b` | fix(install): refresh stale uv for FTS5 | cherry-pick + 冲突解决 |

**变更文件**: `agent/chat_completion_helpers.py`, `agent/anthropic_adapter.py`, `agent/conversation_compression.py`, `tools/vision_tools.py`, `tools/terminal_tool.py`, `tools/process_registry.py`, `intellect_state.py`, `scripts/install.sh`, 测试文件

### P1 — CLI/TUI 改进 ✅

| 原始提交 | 描述 | 移植方式 |
|----------|------|----------|
| `8f4c8e7c8` | refactor(cli): shared curses menu driver | 全量替换 + 品牌替换 |
| `087be0073` | fix(cli): migrate setup pickers to curses | 手动 patch + 冲突解决 |
| `3463c97a3` | fix(cli): decode arrow-key sequences | patch 拆分应用 |
| `a726e8a81` | fix(tui): auto-recover on gateway death | patch 拆分应用 + 手动修复 |
| `b1d34cf6e` | fix(tui): clamp bogus terminal dimensions | patch (clean) |
| `cd067ab91` | fix(tui): swallow mouse-burst noise | patch (clean) |
| `f2d4cf4f7` | fix(cli): post-compression token sentinel | patch (clean) |

**变更文件**: `intellect_cli/curses_ui.py`, `intellect_cli/auth.py`, `intellect_cli/main.py`, `intellect_cli/setup.py`, `cli.py`, `ui-tui/src/*`, 测试文件

### P2 — 功能增强 ✅

| 原始提交 | 描述 | 移植方式 |
|----------|------|----------|
| `e1293bde4` | feat(models): hourly catalog refresh | 手动编辑 |
| `50db2d9c1` | feat(models): deepseek-v4-flash, trim variants | 全量替换 + 品牌替换 |
| `de4f40ed0` | feat(setup): thin out setup | 手动编辑 |
| `1fc7bdc5e` | feat(tools): always show Tool Gateway backends | 手动 patch + replace_all |
| `0cd7d54b0` | feat(kanban): goal_mode cards | patch 拆分 + 手动修复 |
| `d4e7b2fc1` | fix(voice): SSH voice when sound server reachable | patch (clean) |
| `9ed9af2f7` | fix(update): name new config options in migration | patch (clean) |

**变更文件**: `intellect_cli/models.py`, `intellect_cli/model_catalog.py`, `intellect_cli/config.py`, `intellect_cli/setup.py`, `intellect_cli/tools_config.py`, `intellect_cli/kanban_db.py`, `intellect_cli/goals.py`, `cli.py`, `tools/voice_mode.py`, `tools/kanban_tools.py`, `website/static/api/model-catalog.json`, 测试文件

### P2 — 平台适配 ✅

| 原始提交 | 描述 | 移植方式 |
|----------|------|----------|
| `dc4de1437` | fix(telegram): retry on pool timeout | patch 拆分应用 |
| `3c21fed09` | fix(bluebubbles): LRU cache eviction | 手动编辑 |
| `e8cacb57d` | fix(feishu): LRU cache eviction | patch 已包含 |
| `32899279a` | fix(gateway): pending_watchers + LRU | patch 拆分应用 |
| `eb9bfd392` | fix(T5): asyncio.sleep in MCP reconnect | patch (clean) |
| `91a98d151` | fix: tool_output_limits cache | patch (clean) |
| `d27601837` | docs(toolsets): clarify wildcard behavior | 手动编辑 |

**变更文件**: `gateway/platforms/telegram.py`, `gateway/platforms/bluebubbles.py`, `gateway/platforms/feishu.py`, `tools/mcp_tool.py`, `tools/tool_output_limits.py`, 文档

---

## 二、修复的迁移 Bug

| 类别 | 文件数 | 问题 | 修复 |
|------|--------|------|------|
| 环境变量大小写 | 15 | `intellect_SESSION_KEY`（小写 i） | → `INTELLECT_SESSION_KEY` |
| User-Agent | 25+ | `H intellectAgent` / `HermesAgent` | → `IntellectAgent` |
| ontoweb 残留引用 | 5 | `Each 429 from OntoWeb` 等 | → `Each 429 from the OntoWeb Portal` |
| 路径残留 | 2 | `~/.hermes/` 引用 | → `~/.intellect/` |
| 测试导入 | 3 | `from hermes_cli` / `_HERMES_HOME` | → `from intellect_cli` / `_INTELLECT_HOME` |

---

## 三、测试结果

### 修复前后对比

| 测试 | 修复前 | 修复后 | 根因 |
|------|--------|--------|------|
| `test_bluebubbles::test_supports_message_editing` | FAIL | **PASS** | 移植遗漏 `OrderedDict` 导入 |
| `test_anthropic_adapter::test_returns_token` | FAIL | **PASS** | Mock stdout 链不完整（已有问题） |
| `test_cmd_update::test_update_refreshes` | FAIL | **PASS** | web/ 目录删除后测试未同步（已有问题） |
| `test_model_provider_persistence::test_named_custom` | FAIL | **PASS** | curses 迁移后 mock 目标失效（已有问题） |

### 全量测试

| 套件 | 通过 | 失败 | 说明 |
|------|------|------|------|
| `tests/gateway/` | 499 | 0 | 全部通过 |
| `tests/tools/` | 184 | 0 | 全部通过 |
| `tests/agent/` | 116 | 0 | 全部通过 |
| `tests/intellect_cli/` (选中) | 102 | 0 | 全部通过 |

---

## 四、移植方法论

### 有效方式

1. **同类文件无冲突**: cherry-pick 直接成功（~50% 提交）
2. **文件重命名**: git 自动检测 `hermes_cli/` → `intellect_cli/`，需确认路径
3. **内容冲突**: 使用 `sed` 链替换品牌名后，手动应用 patch
4. **大范围重构**: 全量替换后覆盖文件（如 curses_ui.py）

### 经验总结

| 问题 | 解决方案 |
|------|----------|
| patch 整体应用失败 | 拆分为单文件 patch，逐个应用 |
| v0.4.1 修改导致的冲突 | 读取 `.rej` 文件，手动理解上下文后编辑 |
| 品牌替换不完整 | 生成 patch 后用多规则 `sed` 链替换，再进行人工 `grep` 检查 |
| 测试 mock 目标变更 | `simple_term_menu` → `curses_radiolist`，需更新 mock 路径 |

### 残留的 `ontoweb` 引用（有意保留）

以下位置的 `ontoweb` 字符串是 JSON 字段名或 API 标识符，**不应修改**：
- `managed_nous_feature` — provider catalog JSON 字段名
- `_model_flow_nous()` — 内部函数名（已作为整体重命名）
- `nous_subscription` — 模块文件名（对应 `ontoweb_subscription.py` 但函数名保留 `ontoweb` 历史）

---

## 五、关键提交列表

| 本地 Commit | 描述 |
|-------------|------|
| `ae0894f28` | fix(tests): resolve 3 pre-existing test failures |
| `0af1dd24e` | fix(bluebubbles): add missing OrderedDict import |
| `49a5868fc` | fix(tests): fix stale hermes_cli imports |
| `149fed3d4` | fix: migration bugs — SESSION_KEY case, User-Agent, ontoweb→ontoweb |
| `b93055048` | feat(setup): thin out setup + feat(tools): Tool Gateway visibility |
| `c27fc7940` | fix(cli): migrate _prompt_model_selection to curses_radiolist |
| `f5471a087` | fix: resolve patch rejects for bluebubbles, kanban, TUI, docs |
| `d63900f2c` | feat: port remaining P1/P2 changes |
| `2fc8a0981` | feat(models): add deepseek-v4-flash, trim variants |
| `0ff5bb76a` | feat(models): refresh model catalog hourly |
| `c50ef6f1d` | feat(voice): SSH voice + TUI terminal/mouse fixes + update cmd |
| `058f87a2b` | refactor(cli): extract shared curses menu event-loop driver |
| `c7ef0c8da` | fix(install): refresh stale uv for FTS5 Python |
| `20d703afd` | fix(tools): spawn_via_env background wrappers |
| `21e7f7d3d` | fix(tools): preserve live session cwd in terminal_tool |
| `28d8ea95b` | fix(session): survive missing FTS5 runtimes |
| `b3a2491ec` | fix(vision): cap embedded image size |
| `fba56fbc5` | fix(anthropic): demote dead thinking signature |
| `611eb9f32` | revert: drop cumulative-resend tool-arg heuristic |
| `dfcffff75` | fix(streaming): stop duplicating tool-call args |
| `2d8301727` | fix(gateway): resolve _get_dm_topic_info |
| `829885482` | fix(gateway): /stop interrupt sibling participant |
| `9529034e6` | fix(gateway): preserve Telegram DM topic routing |
| `af889a750` | fix(gateway,cron): reuse gateway marker |
| `d1ee74a2a` | fix(gateway,cron): prevent agent restart loops |
| `036b5bc84` | fix(security): neutralize file paths |
| `b8423f196` | Block root config in media delivery |
| `1b749cf46` | fix(gateway): denylist config.yaml |
| `781b67b82` | fix(gateway): stop system tips from auto-uploading |
| `3db187a85` | fix: remove Discord mention redaction |

---

## 六、后续工作（同日完成）

### 6.1 ONTOWEB Provider 品牌彻底清理

| 本地 Commit | 描述 |
|-------------|------|
| `c29bc7363` | refactor: rename nous→ontoweb brand references (148 files, 1337 changes) |
| `183bcd011` | refactor: complete nous→ontoweb rename final pass (180 files) |
| `1c6c0df48` | fix(ontoweb): Portal URL remnants + variable naming audit |

**变更类型：**
- Provider ID: `"nous"` → `"ontoweb"`（40+ 函数/类/变量名）
- 速率限制: `nous_rate_*` → `ontoweb_rate_*`（全部 6 个函数 + 文件名）
- Portal URL: `nousresearch.com` → `ontoweb.cn`（5 处）
- 移除模型: `hermes-3-405b` / `hermes-3-70b` fallback
- 保留不变: `managed_nous_feature`（JSON 字段名）、`nous.ai` 邮箱、`RELEASE_*.md`

### 6.2 测试修复与补全

| 本地 Commit | 描述 |
|-------------|------|
| `726d2e513` | fix(ontoweb): add missing `ensure_ontoweb_portal_access` function |
| `0bfb4184f` | fix(tests): managed provider visibility tests for always-show |
| `8d97d9410` | test: 7 new unit tests for portal access + fallback_models |

### 6.3 安全审计

| 本地 Commit | 描述 |
|-------------|------|
| `9d47999c9` | docs: password security improvement roadmap |

**关键发现：**
- 所有凭证明文落盘（6 个存储位置，无静态加密）
- BlueBubbles 密码在 URL query 中明文传输
- `config.yaml` 0644 权限全局可读，可含 API key
- 9 项安全亮点（借入凭证指纹化、原子写入、终态隔离等）

### 6.4 最终测试结果

| 套件 | 通过 | 说明 |
|------|------|------|
| `tests/gateway/` | 499 | 全部通过 |
| `tests/tools/` | 184 | 全部通过 |
| `tests/agent/` | 116 | 全部通过 |
| `tests/intellect_cli/` (选中) | 122 | 全部通过（含 7 个新增） |
| v0.4.1 多用户/团队/项目/OAuth | 219 | 全部通过，无回归 |
| **合计** | **~1140** | **0 失败** |

---

*文档由 Claude Code 根据实际执行结果自动更新。*
*执行日期：2026-06-01，总耗时约 8 小时。*
