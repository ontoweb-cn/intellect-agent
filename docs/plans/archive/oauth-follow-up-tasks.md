# OAuth 后续任务清单（agent + webui 对照）

**日期：** 2026-06-05  
**基线分支：** `v0.4.2`（联合 PR P1–P4 已落地；**OAuth §9 人工 QA** 仍为合并 `main` 前门禁）

---

## intellect-agent

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 计划文档对齐（oauth-db-only §2.2、A0–A2） | ✅ |
| P0 | 第三方评审 10 项修复 | ✅ `c45c02738` |
| P0 | `auth.active_provider` 迁出 `auth.json` | ✅ `bf2fd36ac` |
| P1 | [auth-json-deprecation §9](auth-json-deprecation-pr-plan.md) 联调（QA 签字） | 🔄 自动化 #0/#7/#8 + 企业 `/login` E2E；**人工 8 项 + 真机 E1–E3** → [oauth-qa-signoff.md](oauth-qa-signoff.md) |
| P1 | [oauth-db-only §9](oauth-db-only-migration-pr-plan.md) 项 0–1 | ✅ setup 种子；飞书/企微/钉钉 DB + authorize 自动化 |
| P2 | `create_oauth_state` + `registration_member_id` / `redeem_member_id` | ✅ agent + WebUI |
| P2 | OAuth `state` 文件 → DB（`oauth_pending_states` v23 + file fallback） | ✅ `2344c84af` |
| P2 | W5 读副本滞后剔除 | ✅ `pg_replica_pool` + tests |
| P2 | MCP / Spotify 外置 OAuth 策略文档 | 📋 |
| P2 | `intellect_cli/auth.py` 进一步收薄（仅迁移/回滚触盘 `auth.json`） | 📋 |
| P3 | `v0.4.2` → `main` 合并与发版说明 | 📋（待 §9 签字） |
| P3 | OAuth 设备码统一 / MSAL 原生 | 📋 见 [oauth-device-code-msal-p3.md](oauth-device-code-msal-p3.md) |

### §9 联调（agent，复制到 QA 工单）

1. 新装：无 `auth.json`，`intellect auth add openai-codex` → DB 有 token + pool 行  
2. 重命名 `auth.json` 后 Codex 推理仍成功  
3. Pool 双条目 429/DEAD 轮换  
4. Profile OAuth 与 global shadow  
5. `intellect auth list` 与 WebUI Providers 一致  
6. WebUI Codex 设备码后 `auth.json` mtime 不变  
7. `intellect oauth migrate-from-auth-json` + `doctor` 无漂移  
8. `intellect logout openai-codex` 清空 DB  
9. 删 `.anthropic_oauth.json` / `google_oauth.json` 后 status 仍 OK（仅 DB）  
10. `INTELLECT_OAUTH_READ_AUTH_JSON=1` 回滚读文件  

---

## intellect-webui

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | W5–W7（DB OAuth、disconnect pool、onboarding） | ✅ `b2575ecb`+ |
| P1 | 与 agent `v0.4.2` 联调（上表 §9） | 📋 人工 QA |
| P2 | **W0**：`oauth-providers.js` / 登录 `logo_svg` + Settings `credential_fields` | ✅ |
| P2 | `test_member_db_session_sync.py` 纳入 CI | ✅ `0f76b0a6` |
| P2 | `registration_token` 授权 | ✅ |
| P2 | 团队/项目管理员 UI + 踢人 API + 企业 OAuth E2E | ✅ `78a98c53` / `23b12a16` |
| P2 | teams/projects i18n（9 语言） | ✅ `9afe875d` |
| P3 | CSP `font-src` + jsdelivr | ✅ `0f76b0a6` |
| P3 | i18n 全量 key parity（成员审计等 ~53 key） | 📋 |
| P3 | OAuth 设备码 WebUI 成员登录统一 | 📋 见 [oauth-device-code-msal-p3.md](oauth-device-code-msal-p3.md) |

**最低 agent 版本：** `v0.4.2`（`oauth_pool_entries`、`oauth_pending_states` v23、`login_flow`、`pg_replica_pool`）。
