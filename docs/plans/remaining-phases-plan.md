# 剩余 Phase 实现方案

**日期：** 2026-06-01（初稿）  
**最后核对：** 2026-06-05（`v0.4.2` 联合 PR + OAuth state DB + W5）  
**状态：** ✅ Phase 1–2 + P0–P2 backlog 已完成；见下方 **后续 backlog**

**关联文档：**

- `docs/plans/member-login-logout-design.md` — Gateway `/login` `/logout`、CLI `members whoami`
- `docs/plans/2026-06-02-members-webui-hardening-design.md` — WebUI/会话隔离（跨仓）

---

## 进度总览

| Phase | 主题 | 状态 |
|-------|------|------|
| **OAuth P2** | Register/Login OAuth + identities 收尾 | ✅ |
| **OAuth P3** | 企业 SSO + trusted_header | ✅ |
| **Gateway RBAC** | 成员角色、slash、membership 校验 | ✅ |
| **v2 DB RBAC** | `role_definitions` + `authorize_v2()` + CLI | ✅（默认 v1，配置开启 v2） |
| **Member session UX** | CLI whoami、resolve 链修复 | ✅ |
| **G8 工具 RBAC** | `member_rbac` + `handle_function_call` 敏感工具门控 | ✅ |
| **M6 用户文档** | `teams-and-members.md`（中英） | ✅ |
| **CLI 会话 TTL** | `.cli-session.json` `expires_at` + 过期清理 | ✅ |
| **P1 非交互 hard_stop** | `merge_tool_loop_guardrails_for_platform()` + `init_agent` | ✅ |
| **P2 OAuth 深化** | WeCom/DingTalk/Azure AD URL + token exchange + CLI 校验 | ✅ |
| **P0 Gateway 会话戳记** | `get_or_create_session` → `resolve_member_id` → DB | ✅ |
| **WebUI 侧栏 fail-closed** | actor 未解析时隐藏他人会话行 | ✅（`intellect-webui`） |

**启用 v2 RBAC：**

```yaml
members:
  enabled: true
  rbac:
    version: 2
```

---

## 实现摘要（2026-06-04）

### OAuth P2
- `members bind --oauth`：owner/admin 可代绑；`link_member_id` OAuth state；冲突检测 `OAuthIdentityConflictError`
- `identities` 查询统一为 `provider` / `provider_id` 列（修复 `resolve_member_id` 误用 `platform`）
- 测试：`tests/agent/test_members_oauth.py`（Resolution、TrustedHeader）

### OAuth P3
- `resolve_trusted_header_member_from_headers()` + API Server `_resolve_member_context`
- Doctor：`trusted_header` 配置检查
- WeCom/DingTalk/Azure AD preset 保留；测试覆盖 preset/URL

### Gateway RBAC
- Slash：`/login` `/logout`（`members.enabled` gate）
- `_resolve_gateway_member()`、`session_key` sticky meta
- `/team` `/project` 校验 active membership
- `/whoami` 显示 member login/role/在线状态

### v2 DB RBAC
- Schema v21：`role_definitions` + 内置角色 seed（`seed_builtin_role_definitions`）
- `authorize_v2()`、`members role` CLI（list/show/create/delete/grant/revoke）
- 测试：`tests/agent/test_authorize_v2.py`

### Member UX
- `intellect members whoami`
- `resolve_member_id()`：`session_key`、trusted header、CLI-only session、登出 marker

### 系统提示
- `agent/system_prompt.py`：有 `runtime_context` 时注入 member/team/project 摘要（session 边界，不破坏 cache 策略）

### G8 工具级 RBAC
- `agent/member_rbac.py`：`check_member_tool_permission()`、敏感工具 allowlist、INTELLECT_HOME 路径守卫
- `model_tools.handle_function_call()`、`invoke_tool()` 在成员上下文中执行前校验
- 测试：`tests/agent/test_member_tool_rbac.py`

### M6 文档
- `website/docs/user-guide/features/teams-and-members.md`
- `website/i18n/zh-Hans/.../teams-and-members.md`

### P0–P2 backlog（2026-06-04）
- **hard_stop：** `merge_tool_loop_guardrails_for_platform()` — 非 `cli`/`tui`/`acp` 平台在 `init_agent` 时强制 `hard_stop_enabled=True`
- **OAuth：** `build_authorization_url` / `exchange_code_for_tokens` 分发 wecom、dingtalk；`provider_oauth_login_ready()`；CLI 友好错误信息
- **Gateway 会话：** `SessionStore.get_or_create_session` 在 `members.enabled` 时调用 `resolve_member_id` 写入 DB `member_id`
- **WebUI：** `sessions.js` 在 members status 已加载且 actor 为空时 fail-closed 隐藏侧栏行

---

## 认证方式总览

| 场景 | 方式 | 状态 |
|------|------|------|
| CLI 密码登录 | `members login <name>` | ✅ |
| CLI OAuth 登录 | `members login --oauth <provider>` | ✅ |
| CLI OAuth 注册 | `members register <code> --oauth` | ✅ |
| CLI 登出 / whoami | `members logout` / `members whoami` | ✅ |
| Gateway 平台 identity | identities 表 | ✅ |
| Gateway `/login` `/logout` | sticky `state_meta` | ✅ |
| Enterprise SSO | trusted header + Azure/WeCom/DingTalk | ✅ |
| v2 自定义角色 | `members role` + DB bindings | ✅（需 `rbac.version: 2`） |
| CI/CD Token | `imt_...` | ✅ |

---

## 测试

```bash
scripts/run_tests.sh tests/agent/test_authorize_v2.py \
  tests/agent/test_members_oauth.py \
  tests/agent/test_member_tool_rbac.py \
  tests/agent/test_cli_session_ttl.py \
  tests/gateway/test_gateway_member_rbac.py
```

---

## 后续 backlog（建议优先级）

| 优先级 | 主题 | 说明 | 仓 |
|--------|------|------|-----|
| P1 | **OAuth §9 人工 QA** | [oauth-qa-signoff.md](oauth-qa-signoff.md) — 合并 `main` 前门禁 | agent + webui |
| P2 | **i18n 全量 parity** | teams/projects 已补 9 语言；成员审计等 ~53 key 仍缺 | webui |
| P2 | **Profile 管理恢复** | `profiles.management_enabled` — [2026-06-profile-management-disabled-restore.md](2026-06-profile-management-disabled-restore.md) | 两仓 |
| P3 | **模型注册表迁移** | `2026-06-02-model-registry-migration-runbook.md` | agent |
| P3 | **OAuth 设备码 / MSAL 原生** | 可选；详见 [oauth-device-code-msal-p3.md](oauth-device-code-msal-p3.md) | agent + CLI |

### OAuth 设备码 / MSAL 原生（P3 摘要）

不挡 `v0.4.2` 发版。完整说明见 **[oauth-device-code-msal-p3.md](oauth-device-code-msal-p3.md)**。

**设备码（RFC 8628）** — 远程 SSH / 无浏览器场景；Intellect 已有 Codex WebUI onboarding、ONTOWEB `_ontoweb_device_code_login`、`intellect members login --oauth github --device`，但实现分散。P3 目标：抽出 `agent/oauth/flows/device_code.py`，与 `oauth_pending_states` 及 WebUI 成员登录统一。

**MSAL 原生** — 仅 **Azure AD 成员登录**；用 `msal-python` 替代手写 OIDC（WAM broker、条件访问）。与已有 `azure_identity_adapter`（Foundry 推理、`DefaultAzureCredential`）是不同链路。

### 2026-06-04 追加（任务 1 / 3 / 4）

| 项 | 状态 |
|----|------|
| `@pytest.mark.no_isolate` + `run_tests_parallel` 批量子进程 | ✅ |
| `intellect members sessions audit-null` + doctor 会话隔离检查 | ✅ |
| 非交互 `hard_stop` 集成测试（`init_agent` + platform） | ✅ |
| `authorize()` 拒绝 DEBUG 日志 | ✅（已有 `_authorize_role`） |
