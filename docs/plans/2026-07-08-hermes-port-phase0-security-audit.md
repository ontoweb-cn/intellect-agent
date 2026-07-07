# Hermes v0.16–v0.18 安全轮移植 — Phase 0 审计表

> 文档日期：2026-07-08
> 基线：hermes-agent `v2026.6.5`（v0.16.0）→ `HEAD`（含 v0.18 窗口变更）
> 对照：intellect-agent `pre-desktop` 分支
> 关联：`2026-07-08-hermes-v0.16-v0.18-port-todo.md` HP-001 / HP-002

## 审计结论摘要

| 类别 | 已等价 / 已有 | 本 Phase 已移植 | 不适用 / 暂缓 |
|------|---------------|-----------------|---------------|
| Slack `xapp-` 脱敏 | — | ✅ redact + gateway helpers | — |
| MCP auth env key 消毒 | 部分（仅 `-`） | ✅ `_env_key_for_server` 全字符 | — |
| 浏览器 cloud-metadata 下限 | ✅ sandbox + browser_tool | — | — |
| aiohttp `client_max_size` | api_server、line 已有 | ✅ bluebubbles、teams、proxy | feishu/sms 等（Hermes 亦排除） |
| MCP 配置整表持久化 | — | — | ⏸️ 需 WebUI 删除语义，单独 PR |
| cron profile secret scope | — | — | ⏸️ intellect 无 `set_secret_scope` API |
| 依赖 CVE 下限（aiohttp 等） | ✅ `aiohttp==3.14.1` 已 pin | — | 随 Dependabot/发版例行 bump |

---

## 逐项对照表

| # | Hermes 安全项 | 代表 commit / 区域 | Intellect 状态 | 处置 |
|---|---------------|-------------------|----------------|------|
| 1 | Slack App-Level token 脱敏 | `fdb9620ac` `agent/redact.py`, `gateway/run.py` | **已移植** — `agent/redact.py`, `gateway/helpers.py` | HP-001 ✅ |
| 2 | MCP server 名 → env key 消毒 | `e53e8a782` `_env_key_for_server` | **已移植** — `intellect_cli/mcp_config.py` | HP-001 ✅ |
| 3 | Gateway aiohttp body 上限 | `8986981df` bluebubbles/teams/proxy | **已移植** — 三处 `client_max_size` | HP-001 ✅ |
| 4 | 浏览器 cloud-metadata floor | `0a7561651`, `4612ee946` | **已有** — `rust-core/src/sandbox.rs`, `tools/browser_tool.py` | 无需改动 |
| 5 | MCP 配置持久化（整表 replace） | `_replace_mcp_servers` | **缺失** — 仅 per-key upsert | Phase 1+ / WebUI 专项 |
| 6 | cron profile secret scope | `fdab380a1` | **不适用** — 无 Hermes 式 secret scope | 若引入 P6 secret scope 再对齐 |
| 7 | cron 凭据外泄路径（delivery/redact） | 多个 cron fix | **部分已有** — scheduler 输出 redact | 逐 commit 低优先级跟进 |
| 8 | aiohttp / 依赖 CVE 下限 | 各 security PR | **已有 pin** — `pyproject.toml` `aiohttp==3.14.1` | 发版时 `uv lock` 例行 |
| 9 | AGENTS.md 命令注册表路径 | — | **已修正** | HP-002 ✅ |
| 10 | Rust 架构文档版本号 | — | **已修正** → 0.6.7 对齐 | HP-002 ✅ |

---

## 本 Phase 代码变更清单

| 文件 | 变更 |
|------|------|
| `agent/redact.py` | 增加 `xapp-\d+-` prefix pattern |
| `gateway/helpers.py` | `_GATEWAY_SECRET_PATTERNS` 增加 xapp |
| `intellect_cli/mcp_config.py` | `_env_key_for_server` 非 env-safe 字符 → `_` |
| `plugins/platforms/bluebubbles/adapter.py` | `client_max_size=1 MiB` |
| `plugins/platforms/teams/adapter.py` | `client_max_size=1 MiB` |
| `intellect_cli/proxy/server.py` | `client_max_size=10 MB` |
| `tests/agent/test_redact.py` | xapp token 测试 |
| `tests/intellect_cli/test_mcp_config.py` | env key 边界测试 |
| `AGENTS.md` | commands 注册表路径 |
| `docs/architecture/rust-python-interaction.md` | 版本 0.6.7 对齐说明 |

---

## 测试

```bash
scripts/run_tests.sh tests/agent/test_redact.py tests/intellect_cli/test_mcp_config.py -q
```

---

## 后续（不在 Phase 0）

- MCP `_replace_mcp_servers` 整表写入（Hermes GUI 持久化 bug 修复）
- cron `set_secret_scope` 对齐（依赖 profile secret scope 基础设施）
- feishu / sms / wecom webhook `client_max_size`（Hermes 亦 deferred，按需单独 PR）
