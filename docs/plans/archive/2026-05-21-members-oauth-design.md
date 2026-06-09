# Members OAuth — Design Spec

> **Status:** Approved (May 2026) — **Superseded by [2026-05-31-members-oauth-plan-v2.md](./2026-05-31-members-oauth-plan-v2.md)** (Dashboard removed, CLI-only architecture)  
> **Related:** `docs/plans/2026-05-19-profile-teams-members-spec.md` §9, §22, §24  
> **Goal:** Cover all member OAuth scenarios (E) in phased PRs without breaking legacy single-user mode.

---

## 1. Scope (scenario E)

| # | Scenario | v1 deliverable | Phase |
|---|----------|----------------|-------|
| E1 | **Dashboard remote login** — prove identity in browser, stop member-picker impersonation | OIDC login page + session cookie | P1 |
| E2 | **New member onboarding** — invite + OAuth in one flow | Redeem binds OAuth identity atomically | P1 |
| E3 | **External identity binding** — link GitHub/Google to existing member | Members UI + CLI + admin bind | P2 |
| E4 | **Enterprise SSO** — Keycloak/LDAP via proxy or trusted header | `trusted_header` + docs; optional ONTOWEB preset | P3 |
| — | **CLI / headless** — SSH, no browser | `intellect members login --oauth` (device code or loopback) | P2 |
| — | **Security baseline** — close impersonation hole | Gate member picker when OAuth required | P0 |

**Non-goals (this spec):**

- Cross-profile federation or central user directory
- OAuth replacing `imt_*` API tokens (tokens remain for automation)
- Per-member OAuth for LLM providers (unchanged; see `intellect auth`)
- Password-based local accounts

---

## 2. Design principles

1. **Reuse `identities` table** — OAuth subjects map to `(platform, external_id)` → `member_id`; no parallel user store.
2. **OAuth proves identity once** — exchange IdP tokens for an Intellect **member session** (cookie + optional signed JWT); routine API calls do not hit the IdP.
3. **Never auto-merge** (spec §9.3) — two OAuth accounts cannot map to one member without admin action or invite redemption in the same flow.
4. **Legacy parity** — `members.enabled: false` unchanged; `members.oauth.enabled: false` preserves today’s member picker.
5. **Profile-local secrets** — client secrets in `.env`; config references env var names only.

---

## 3. Identity model

### 3.1 Platform naming

| Kind | `platform` | `external_id` | Example |
|------|------------|---------------|---------|
| Generic OIDC | `oauth:<provider_id>` | IdP `sub` (stable) | `oauth:github`, `550e8400-…` |
| Trusted header SSO | `oauth:header` | normalized email or username | `oauth:header`, `alice@corp.example` |
| ONTOWEB preset | `oauth:ontoweb` | ONTOWEB user id | `oauth:ontoweb`, `usr_abc` |
| Dashboard device (existing) | `dashboard` | `device_id` | unchanged |
| CLI device (existing) | `cli` | `device_id` | unchanged |

**Email is not `external_id`** — store in `identities.metadata` for display and admin matching only.

### 3.2 Resolution after OAuth callback

```python
def resolve_oauth_member(
    provider_id: str,
    claims: dict,
    *,
    store: MembershipStore,
    config: dict,
    invite_code: str | None = None,
) -> str:
    platform = f"oauth:{provider_id}"
    external_id = claims["sub"]  # required

    if existing := store.resolve_identity(platform, external_id):
        return existing

    if invite_code:
        mid = store.redeem_invite(invite_code, member_id=...)
        store.bind_identity(platform, external_id, mid, metadata={...})
        return mid

    if config["members"]["oauth"].get("auto_provision"):
        mid = allocate_member_id_from_claims(claims)  # slug from email local-part + suffix
        store.create_member(mid, ...)
        store.bind_identity(...)
        return mid

    raise OAuthMemberNotLinkedError(...)
```

### 3.3 Invite + OAuth (E2)

**Flow:**

1. Admin creates invite (optional reserved `member_id`).
2. User opens `/members/login?invite=FAM-…` (or enters code on login page).
3. OAuth redirect includes `state` with encrypted invite code.
4. On callback: redeem invite **then** bind identity in one transaction.
5. Issue dashboard session; redirect to `/chat` or team picker.

If invite specifies reserved `member_id` and OAuth identity already bound to another member → **403** (no hijack).

---

## 4. Session layer (OAuth → dashboard)

Today: `write_dashboard_session(member_id)` + HttpOnly cookies.

**Add (optional, recommended for P1):**

```text
{INTELLECT_HOME}/.member-sessions/
  <session_id>.json   # { member_id, oauth_provider, external_id, expires_at, device_id }
```

| Artifact | Purpose | TTL |
|----------|---------|-----|
| `intellect_dashboard_member` cookie | member_id (existing) | config `session_ttl_hours` (default 168) |
| `intellect_member_session` cookie (new) | opaque session_id → server-side record | same |
| `imt_*` bearer | API/automation (unchanged) | until revoked |

**Why server-side session file:** revoke on logout; bind OAuth subject for “linked accounts” UI; rotate without re-OAuth.

**P0/P1 minimum:** keep existing member cookie but **forbid** `POST /api/members/session` (picker) when OAuth is required (see §7).

---

## 5. Configuration

```yaml
members:
  enabled: true
  oauth:
    enabled: false          # master switch
    require_for_dashboard: auto   # auto | always | never
    # auto = require OAuth when bound to 0.0.0.0/:: OR allow_picker is false
    allow_picker_on_localhost: true # dev: pick member without OAuth on 127.0.0.1
    session_ttl_hours: 168
    auto_provision: false   # JIT member on first OAuth (small teams only)
    auto_provision_id_claim: email_local  # email_local | sub
    callback_base_url: null # null = infer from request Host (see §8)
    providers: []           # see §5.1
    trusted_header:         # E4 — enterprise (P3)
      enabled: false
      header: X-Forwarded-User
      require_localhost_upstream: true  # only trust from 127.0.0.1
      map: email            # email | username
      domain_strip: ""      # optional @corp.example suffix normalization
```

### 5.1 Provider entry (OIDC)

```yaml
- id: github
  type: oidc
  display_name: GitHub
  enabled: true
  client_id: "Ov23li..."
  client_secret_env: GITHUB_OAUTH_CLIENT_SECRET
  issuer: https://token.actions.githubusercontent.com
  # OR discovery_url for generic OIDC
  scopes: [openid, profile, email]
  pkce: true
  claim_email: email
  claim_name: name
  # Optional: restrict login to email domain
  allowed_email_domains: []
```

**Preset providers** (built-in defaults, user fills client_id/secret):

| Provider | Phase | Protocol |
|----------|-------|----------|
| `github`, `google`, `gitee`, `azure_ad` | P1 | OIDC / standard OAuth2 + PKCE |
| `wecom` (企业微信), `dingtalk` (钉钉) | P1.5–P2 | Vendor OAuth2 adapters (corpId/agentId etc.) |
| `keycloak` | P2+ | Generic OIDC discovery URL |
| `ontoweb` | P3 | Fixed issuer `https://auth.ontoweb.cn` |

Secrets only in `.env` via `OPTIONAL_ENV_VARS` entries.

---

## 6. HTTP API (Dashboard)

New routes under `intellect_cli/dashboard_members_api.py` (or `agent/members_oauth.py` handlers registered from there).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/members/oauth/providers` | public (+ SPA token) | List enabled providers + login URLs |
| GET | `/api/members/oauth/authorize` | public | Redirect to IdP (query: `provider`, `invite?`, `return_to?`) |
| GET | `/api/members/oauth/callback` | public | IdP redirect; sets cookies; redirects to UI |
| POST | `/api/members/oauth/logout` | member session | Clear cookies + delete session file |
| GET | `/api/members/me/identities` | member session | List linked identities (E3) |
| DELETE | `/api/members/me/identities/{platform}/{external_id}` | member session | Unlink (forbid if last auth method) |
| POST | `/api/members/me/identities/link` | member session | Start link flow (OAuth with `link=1` state) |

**Public paths:** add `/api/members/oauth/callback`, `/api/members/oauth/providers`, `/api/members/oauth/authorize` to `_PUBLIC_API_PATHS` (callback must not require SPA token — IdP redirects have no header).

**CSRF:** OAuth `state` = signed payload `{nonce, provider, invite?, return_to, link?}` stored server-side with 10m TTL.

---

## 7. Security rules

### 7.1 Member picker lockdown (P0)

When `oauth_require_for_dashboard(request)` is true:

- Reject `POST /api/members/session` with **403** unless `allow_picker_on_localhost` and client is loopback.
- `GET /api/members/list` may still list members for admin UI but picker UI hidden.
- Existing `imt_*` bearer and valid OAuth session still work.

```python
def oauth_require_for_dashboard(config, bound_host, client_host) -> bool:
    mode = config["members"]["oauth"]["require_for_dashboard"]
    if mode == "never":
        return False
    if mode == "always":
        return config["members"]["oauth"]["enabled"]
    # auto
    if bound_host in ("0.0.0.0", "::"):
        return config["members"]["oauth"]["enabled"]
    if not config["members"]["oauth"]["allow_picker_on_localhost"]:
        return config["members"]["oauth"]["enabled"]
    return False
```

Aligns with spec §22 (“Dashboard on 0.0.0.0 requires member auth”) but upgrades from “any cookie” to “proved identity”.

### 7.2 Trusted header (E4, P3)

- Only honored when `trusted_header.enabled` and upstream IP is `127.0.0.1` / `::1` (oauth2-proxy on same host).
- Map header value → `oauth:header` identity; same resolution as OIDC.
- `intellect doctor` warns if enabled without reverse-proxy docs.

### 7.3 Token hygiene

- Pending OAuth state files: `{INTELLECT_HOME}/.oauth-pending/<nonce>.json`, mode 0600, TTL 10m.
- Never log authorization codes or raw IdP tokens.
- Client secrets only from env.

---

## 8. Callback URL and deployment

Default callback:

```text
https://<dashboard-host><url_prefix>/api/members/oauth/callback
```

- `callback_base_url` override for reverse-proxy / TLS termination mismatch.
- `intellect doctor` prints effective callback URLs per provider when OAuth enabled.
- Document SSH tunnel pattern (reuse `website/docs/guides/oauth-over-ssh.md`).

---

## 9. CLI (P2)

```bash
# Device code (remote SSH, no browser on server)
intellect members login --oauth github

# Loopback PKCE (local dev — reuse auth.py HTTPServer pattern)
intellect members login --oauth google --loopback

# Link OAuth to current INTELLECT_MEMBER session
intellect members bind --oauth github

# Admin: bind arbitrary external id (existing)
intellect members bind --member alice --platform oauth:github --external-id <sub>
```

CLI success writes `write_cli_session(member_id)` and binds `(cli, device_id)` as today.

---

## 10. Dashboard UI — hybrid UX (decision **C**, approved)

Login/onboarding and account linking use **different surfaces** so OAuth redirect flows stay simple and E3 does not collide with the member picker.

### P1 — `/login` page (E1 + E2)

- New route **`/login`** (full page, not a global modal).
- **401 / session expired / unauthenticated access** → redirect to `/login?return_to=…`.
- **Invite deep link:** `/login?invite=FAM-…` pre-fills invite field; OAuth `state` carries code.
- Content: enabled provider buttons (GitHub, Google, Gitee, Azure AD in P1); invite code field; footer link **「或选择成员（仅 localhost）」** when `allow_picker_on_localhost` applies.
- After OAuth success → redirect to `return_to` (default `/chat`) → existing `MemberTeamBar` / team gate unchanged.
- **Do not** use a full-screen modal for primary login — avoids callback URL / React state races.

### P2 — Members page — linked accounts (E3)

- Section **「Sign-in methods / 登录方式」** on `/members` (actor must already be logged in).
- Lists `GET /api/members/me/identities`; **Link** buttons start OAuth with `link=1` in signed `state` (binds to current member, no invite).
- **Unlink** with guard: cannot remove last auth method when OAuth is required.
- Profile admin read-only view of others’ identities deferred to future RBAC UI.

### WeCom / DingTalk buttons

- Shown on `/login` only when provider configured and enabled (P1.5/P2 adapters).

---

## 11. Enterprise SSO (E4, P3)

**Option 4a — Trusted header (in-tree, small):** §7.2

**Option 4b — External oauth2-proxy (docs only):**

```nginx
# oauth2-proxy → intellect dashboard
auth_request /oauth2/auth;
auth_request_set $user $upstream_http_x_auth_request_email;
proxy_set_header X-Forwarded-User $user;
```

User guide: `website/docs/user-guide/features/teams-and-members.md` (OAuth, invites, WebUI).

**Option 4c — ONTOWEB preset provider:** same OIDC path as GitHub with fixed discovery URL; optional for hosted deployments.

---

## 12. Implementation phases

| Phase | PR scope | Est. | User-visible |
|-------|----------|------|--------------|
| **P0** | Picker lockdown + `members.oauth` config skeleton + doctor checks | S | Remote dashboard cannot impersonate via picker when OAuth on |
| **P1** | `agent/members_oauth.py`, OIDC PKCE, callback, invite-in-state, **`/login` page**, presets: GitHub, Google, Gitee, Azure AD | M | E1 + E2 |
| **P1.5** | WeCom + DingTalk OAuth adapters | M | CN enterprise login |
| **P2** | Members page linked-accounts (E3), CLI `login --oauth` | M | E3 + CLI |
| **P3** | Trusted header, ONTOWEB preset, oauth2-proxy doc, `intellect doctor` callback helper | S | E4 |

Each PR: tests + green with `members.oauth.enabled: false`.

---

## 13. Code touch list

| Area | Files |
|------|--------|
| Core OAuth | `agent/members_oauth.py` (new) |
| Session | `agent/dashboard_session.py` (extend), optional `.member-sessions/` |
| Dashboard API | `intellect_cli/dashboard_members_api.py` |
| Middleware | `intellect_cli/web_server.py` (`_PUBLIC_API_PATHS`, auth gate) |
| CLI | `intellect_cli/members.py` |
| Config | `intellect_cli/config.py` (`DEFAULT_CONFIG`, `.env.example` block) |
| UI | `web/src/pages/LoginPage.tsx` (new), `MembersPage.tsx`, `MemberContext.tsx`, `web/src/lib/api.ts` |
| Reuse | `intellect_cli/auth.py` — PKCE helpers, loopback server (extract shared utils if needed) |
| Docs | `website/docs/user-guide/features/teams-and-members.md`, developer internals |
| Tests | `tests/agent/test_members_oauth.py`, `tests/intellect_cli/test_dashboard_members_oauth.py` |

**No schema migration required for v1** — `identities` + invite tables sufficient. Optional v2: `oauth_pending_states` table if file-based state proves flaky under multi-worker (gateway typically single process per profile).

---

## 14. Testing strategy

| Test | Asserts |
|------|---------|
| Legacy off | `members.oauth.enabled: false` → picker works, no new routes required |
| P0 lockdown | `0.0.0.0` + oauth on → `POST /api/members/session` → 403 |
| OIDC callback mock | Fake IdP token exchange → identity row + cookie |
| Invite + OAuth | state carries invite → redeem + bind atomic |
| Collision | OAuth sub already bound to bob → alice invite redeem fails |
| Link flow | logged-in alice links google → two identities, same member |
| Trusted header | header from 127.0.0.1 → resolves member; from remote IP → ignored |
| CLI device code | mocked token endpoint → cli session file |

Use `scripts/run_tests.sh`; no live network; no change-detector tests on provider list length.

---

## 15. Resolved decisions (approved May 2026)

| # | Decision | Choice |
|---|----------|--------|
| 1 | `require_for_dashboard` | **`auto`** — OAuth required on `0.0.0.0`/`::`; localhost may use picker when `allow_picker_on_localhost: true` |
| 2 | `auto_provision` | **Default false** — JIT member only when operator opts in |
| 3 | Providers | **P1:** GitHub, Google, Gitee, Azure AD — **P1.5–P2:** 企业微信, 钉钉 |
| 4 | Session storage | **Server-side session files** under `{INTELLECT_HOME}/.member-sessions/` + HttpOnly cookies |
| 5 | Login UX | **Hybrid C** — `/login` full page for E1/E2; Members page for E3 link/unlink (no global login modal) |

---

## 16. Acceptance criteria (scenario E)

- [ ] **E1:** User on LAN/WAN opens Dashboard → OAuth login → lands in Chat as correct member; cannot select another member without OAuth.
- [ ] **E2:** Admin invite → new user OAuth + invite → member tree created + identity bound.
- [ ] **E3:** Existing member links second provider; admin can bind manually via CLI.
- [ ] **E4:** Operator doc: oauth2-proxy + trusted header resolves corporate email to member.
- [ ] CLI OAuth login works over SSH (device code).
- [ ] `members.enabled: false` and `oauth.enabled: false` — zero behavior change.
