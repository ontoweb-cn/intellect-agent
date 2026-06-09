# Members OAuth — P0–P1 Implementation Plan

> **For agentic workers:** Implement task-by-task; each task ends with tests green and `members.oauth.enabled: false` parity preserved.  
> **Design spec:** `docs/plans/2026-05-21-members-oauth-design.md` (Approved)  
> **Goal:** P0 closes the member-picker impersonation hole; P1 delivers OIDC login (`/login`) + invite-onboarding for GitHub, Google, Gitee, Azure AD.

**Architecture:** Reuse `identities(platform, external_id)`; OAuth Code+PKCE via new `agent/members_oauth.py`; server-side session files in `{INTELLECT_HOME}/.member-sessions/`; hybrid UX — `/login` for E1/E2 only.

**Tech stack:** Python 3.11+, FastAPI, httpx, existing `MembershipStore`; React Router, existing dashboard `fetchJSON` + session token header.

---

## File map (P0–P1)

| File | Responsibility |
|------|----------------|
| `agent/members_oauth.py` | **New.** Config load, `oauth_require_for_dashboard()`, PKCE, state sign/verify, provider presets, token exchange, member resolution |
| `agent/member_session.py` | **New.** Create/read/delete server-side session files + cookie name helper |
| `agent/dashboard_session.py` | Extend: call member session on OAuth success; validate session id on read |
| `agent/membership.py` | Optional: `get_oauth_config()` helper next to `get_members_config()` |
| `intellect_cli/config.py` | `DEFAULT_CONFIG["members"]["oauth"]` block + `.env.example` secret placeholders |
| `intellect_cli/dashboard_members_api.py` | P0: gate `POST /api/members/session`; extend `GET /api/members/status`; P1: OAuth routes |
| `intellect_cli/web_server.py` | `_PUBLIC_API_PATHS`, middleware uses `oauth_require_for_dashboard` |
| `intellect_cli/doctor.py` | Warnings: OAuth enabled without providers; missing secrets; callback URL hint |
| `web/src/pages/LoginPage.tsx` | **New.** Provider buttons, invite field, localhost picker fallback |
| `web/src/App.tsx` | Register `/login` route; auth redirect guard |
| `web/src/lib/api.ts` | OAuth provider list, authorize URL builder, logout |
| `web/src/i18n/en.ts` (+ zh) | Login page strings |
| `tests/agent/test_members_oauth.py` | **New.** Unit tests for require logic, PKCE, state, resolution |
| `tests/intellect_cli/test_dashboard_members_oauth.py` | **New.** HTTP integration tests |

---

# Phase P0 — Security baseline + config skeleton

**PR title suggestion:** `feat(members): OAuth config skeleton and dashboard picker lockdown (P0)`

**Exit criteria:** With `members.oauth.enabled: true` and dashboard on `0.0.0.0`, unauthenticated `POST /api/members/session` returns 403; with `oauth.enabled: false`, behavior unchanged.

---

### Task P0-1: Config schema

**Files:**
- Modify: `intellect_cli/config.py` (`DEFAULT_CONFIG["members"]`)
- Modify: `.env.example` (delimited block for OAuth secrets only)

- [ ] Add `members.oauth` block per design §5 (`enabled`, `require_for_dashboard`, `allow_picker_on_localhost`, `session_ttl_hours`, `auto_provision`, `auto_provision_id_claim`, `callback_base_url`, `providers: []`, `trusted_header` stub with `enabled: false`).
- [ ] Do **not** bump `_config_version` (new keys deep-merge automatically).
- [ ] Add `OPTIONAL_ENV_VARS` entries: `GITHUB_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GITEE_OAUTH_CLIENT_SECRET`, `AZURE_AD_OAUTH_CLIENT_SECRET` (password, category `setting`).
- [ ] Document env vars in `.env.example` with comment pointing to design doc.

**Verify:** `python -c "from intellect_cli.config import DEFAULT_CONFIG; assert 'oauth' in DEFAULT_CONFIG['members']"`

---

### Task P0-2: `oauth_require_for_dashboard()` helper

**Files:**
- Create: `agent/members_oauth.py` (minimal — config helpers only first)
- Create: `tests/agent/test_members_oauth.py`

- [ ] Implement `get_oauth_config(config) -> dict`.
- [ ] Implement `is_oauth_enabled(config) -> bool`.
- [ ] Implement `oauth_require_for_dashboard(config, *, bound_host: str, client_host: str) -> bool` per design §7.1:
  - `require_for_dashboard: never` → False
  - `always` → `is_oauth_enabled`
  - `auto` → enabled AND (`bound_host in {0.0.0.0, ::}` OR NOT (`allow_picker_on_localhost` AND `_is_loopback(client_host)`))
- [ ] Implement `_is_loopback(host)` — handle `127.0.0.1`, `::1`, `[::1]`, strip port.

- [ ] **Test:** `test_oauth_require_auto_on_wildcard_bind`
- [ ] **Test:** `test_oauth_require_localhost_allows_picker_when_flag_true`
- [ ] **Test:** `test_oauth_require_never`
- [ ] **Test:** `test_oauth_disabled_always_false`

**Run:** `scripts/run_tests.sh tests/agent/test_members_oauth.py -q`

---

### Task P0-3: Lock down member picker API

**Files:**
- Modify: `intellect_cli/dashboard_members_api.py`
- Modify: `tests/intellect_cli/test_dashboard_members_api.py`

- [ ] In `post_members_session`: if `oauth_require_for_dashboard(...)` → raise HTTP 403 with message directing user to `/login`.
- [ ] Exempt loopback when `allow_picker_on_localhost` (read `request.client.host`).
- [ ] Pass `bound_host` from `request.app.state.bound_host` (default `127.0.0.1` in tests).

- [ ] **Test:** oauth off → POST session still 200
- [ ] **Test:** oauth on + bound `0.0.0.0` + no member cookie → POST session 403
- [ ] **Test:** oauth on + bound `127.0.0.1` + client loopback → POST session 200

**Run:** `scripts/run_tests.sh tests/intellect_cli/test_dashboard_members_api.py -q`

---

### Task P0-4: Extend `GET /api/members/status` for frontend

**Files:**
- Modify: `intellect_cli/dashboard_members_api.py`
- Modify: `web/src/lib/api.ts` (`MembersStatusResponse` type — optional fields)
- Modify: `tests/intellect_cli/test_dashboard_members_api.py`

- [ ] Add response fields (when `members.enabled`):
  - `oauth_enabled: bool`
  - `oauth_required: bool` (computed for current request: bound host + client)
  - `allow_picker_on_localhost: bool`
- [ ] Keep endpoint on `_PUBLIC_API_PATHS` (no SPA token needed for status).

- [ ] **Test:** status payload includes new keys when oauth config present

---

### Task P0-5: Middleware alignment

**Files:**
- Modify: `intellect_cli/web_server.py`
- Modify: `tests/intellect_cli/test_web_server.py`

- [ ] Update `_dashboard_requires_member_auth` docstring — note OAuth session counts once P1 lands; for P0 keep existing check (member cookie OR imt_ bearer).
- [ ] Optional P0: when `oauth_require_for_dashboard` and no member resolved, return 401 JSON with `"login_url": "/login"` hint field for future frontend.

- [ ] **Test:** existing `0.0.0.0` member auth test still passes

---

### Task P0-6: Doctor checks

**Files:**
- Modify: `intellect_cli/doctor.py`
- Modify: `tests/intellect_cli/test_doctor_members.py`

- [ ] In `members_teams_doctor_checks`:
  - WARN if `members.oauth.enabled` and `providers` empty
  - WARN if enabled provider missing `client_id` or env secret
  - INFO line: effective OAuth callback URL template when any provider configured
- [ ] WARN if `oauth.enabled` + `auto_provision` + `members.teams.enabled` (optional safety nudge)

- [ ] **Test:** doctor warns on enabled oauth without providers

**Run:** `scripts/run_tests.sh tests/intellect_cli/test_doctor_members.py -q`

---

### Task P0-7: P0 docs snippet

**Files:**
- Modify: `website/docs/user-guide/features/teams-and-members.md` (short §OAuth coming / P0 picker lockdown)

- [ ] Note: when `members.oauth.enabled: true` and dashboard bound to all interfaces, member picker via API is disabled; full login lands in P1.

---

**P0 merge checklist**

- [ ] Full test module green: `scripts/run_tests.sh tests/agent/test_members_oauth.py tests/intellect_cli/test_dashboard_members_api.py tests/intellect_cli/test_doctor_members.py -q`
- [ ] `members.oauth.enabled: false` — legacy parity test file still green: `tests/agent/test_membership_legacy_parity.py`

---

# Phase P1 — OIDC engine + `/login` (E1 + E2)

**PR title suggestion:** `feat(members): OIDC dashboard login and invite onboarding (P1)`

**Exit criteria:** User completes GitHub (or mock IdP) OAuth → dashboard cookie → `/api/members/status` shows `actor_member_id`; invite in state creates member + identity; `/login` page live.

---

### Task P1-1: Provider presets + config validation

**Files:**
- Modify: `agent/members_oauth.py`
- Modify: `tests/agent/test_members_oauth.py`

- [ ] Define `OAUTH_PROVIDER_PRESETS: dict[str, dict]` for `github`, `google`, `gitee`, `azure_ad` (issuer/discovery URLs, default scopes, pkce=True).
- [ ] `resolve_provider(config, provider_id)` merges user YAML entry over preset.
- [ ] `list_enabled_providers(config) -> list[dict]` — public fields only (id, display_name, type); no secrets.
- [ ] Validate: provider enabled requires `client_id` + resolvable secret from `client_secret_env`.

- [ ] **Test:** preset merge
- [ ] **Test:** list_enabled_providers strips secrets

---

### Task P1-2: PKCE + signed OAuth state

**Files:**
- Modify: `agent/members_oauth.py`
- Modify: `tests/agent/test_members_oauth.py`

- [ ] Extract or duplicate PKCE helpers (prefer small copy from `intellect_cli/auth.py` `_oauth_pkce_*` to avoid circular imports — or move to `agent/oauth_pkce.py` if cleaner).
- [ ] `create_oauth_state(*, provider_id, invite_code?, return_to?, link_member_id?) -> str` — random nonce + HMAC/sign with key derived from `{INTELLECT_HOME}/.oauth-state-key` (generate on first use, mode 0600).
- [ ] Persist pending state file `.oauth-pending/<nonce>.json` (code_verifier, fields, exp) TTL 600s.
- [ ] `verify_and_consume_oauth_state(state) -> dict` — one-time use, delete file.

- [ ] **Test:** state round-trip
- [ ] **Test:** expired state rejected
- [ ] **Test:** tampered state rejected

---

### Task P1-3: Authorize URL builder

**Files:**
- Modify: `agent/members_oauth.py`
- Modify: `tests/agent/test_members_oauth.py`

- [ ] `build_authorization_url(provider, redirect_uri, state, code_challenge) -> str`
- [ ] Fetch OIDC discovery document (cache in memory per process) for generic issuers; Azure uses `https://login.microsoftonline.com/{tenant}/v2.0` from config field `tenant: common`.
- [ ] `callback_base_url(config, request) -> str` — honor override or `request.base_url` + url_prefix.

- [ ] **Test:** GitHub authorize URL contains `client_id`, `code_challenge`, `state` (mock discovery)

---

### Task P1-4: Token exchange + claims

**Files:**
- Modify: `agent/members_oauth.py`
- Modify: `tests/agent/test_members_oauth.py`

- [ ] `exchange_code_for_tokens(provider, code, redirect_uri, code_verifier) -> dict` via httpx (mock in tests).
- [ ] Parse `id_token` JWT payload (verify sig optional v1 — document as follow-up; at minimum parse claims for `sub`, `email`).
- [ ] `external_id = claims["sub"]`; store email/name in identity metadata.

- [ ] **Test:** mock token endpoint → claims dict

---

### Task P1-5: Member resolution (login + invite)

**Files:**
- Modify: `agent/members_oauth.py`
- Modify: `tests/agent/test_members_oauth.py`
- Modify: `tests/agent/test_membership_invites.py` (if integration test fits better there)

- [ ] `resolve_oauth_member(provider_id, claims, *, store, config, invite_code=None) -> str`
  1. Existing identity → return member_id
  2. If `invite_code` → `redeem_invite` + `bind_identity` in same store transaction (wrap in try/except; rollback bind on redeem fail)
  3. If `auto_provision` → create member + bind
  4. Else raise `OAuthMemberNotLinkedError`
- [ ] **Test:** existing identity lookup
- [ ] **Test:** invite redeem + bind
- [ ] **Test:** identity already bound to other member + invite → error
- [ ] **Test:** unknown user without invite → error

---

### Task P1-6: Server-side member sessions

**Files:**
- Create: `agent/member_session.py`
- Modify: `agent/dashboard_session.py`
- Create: `tests/agent/test_member_session.py`

- [ ] `create_member_session(member_id, *, provider_id, external_id, ttl_hours) -> session_id`
- [ ] `resolve_member_session(session_id) -> Optional[dict]`
- [ ] `delete_member_session(session_id) -> None`
- [ ] Cookie name: `intellect_member_session` (constant in `member_session.py`)
- [ ] `write_dashboard_session` remains; OAuth success sets **both** member cookie and session cookie.

- [ ] **Test:** create → resolve → delete
- [ ] **Test:** expired session returns None

---

### Task P1-7: HTTP routes — OAuth API

**Files:**
- Modify: `intellect_cli/dashboard_members_api.py`
- Modify: `intellect_cli/web_server.py` (`_PUBLIC_API_PATHS`)
- Modify: `tests/intellect_cli/test_dashboard_members_oauth.py`

- [ ] `GET /api/members/oauth/providers` — public (+ SPA token for consistency with other public routes that need it; callback truly public)
- [ ] `GET /api/members/oauth/authorize?provider=&invite=&return_to=` — RedirectResponse to IdP
- [ ] `GET /api/members/oauth/callback?code=&state=` — exchange, resolve member, set cookies, redirect to `return_to` or `/chat`
- [ ] `POST /api/members/oauth/logout` — clear cookies + delete session file (requires valid session)
- [ ] Register routes in `register_dashboard_members_routes`
- [ ] Add to `_PUBLIC_API_PATHS`: `/api/members/oauth/callback`, `/api/members/oauth/providers`, `/api/members/oauth/authorize`

- [ ] **Test:** providers list 200, no secrets
- [ ] **Test:** authorize redirects (302) with mock config
- [ ] **Test:** callback with mocked httpx IdP → Set-Cookie + 302
- [ ] **Test:** callback error → redirect `/login?error=...`

**Run:** `scripts/run_tests.sh tests/intellect_cli/test_dashboard_members_oauth.py -q`

---

### Task P1-8: Resolve dashboard member from session file

**Files:**
- Modify: `intellect_cli/dashboard_members_api.py` (`resolve_dashboard_member_id`)
- Modify: `tests/intellect_cli/test_dashboard_members_api.py`

- [ ] Order: member cookie → validate session id cookie → load member from session file → bearer imt_ → `.dashboard-session.json`
- [ ] If session file expired → clear cookies

- [ ] **Test:** OAuth session cookie resolves member

---

### Task P1-9: Frontend — `LoginPage.tsx`

**Files:**
- Create: `web/src/pages/LoginPage.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/i18n/en.ts`, `web/src/i18n/zh.ts`

- [ ] Route `/login` in `BUILTIN_ROUTES_CORE` (no sidebar nav item).
- [ ] On mount: `GET /api/members/status` + `GET /api/members/oauth/providers`.
- [ ] Render provider buttons → `window.location.href = /api/members/oauth/authorize?provider=...&invite=...&return_to=...`
- [ ] Invite field; read `?invite=` from query string.
- [ ] Show picker fallback link only when `allow_picker_on_localhost && !oauth_required` — links to simplified picker or existing Members flow.
- [ ] Display `?error=` from failed callback.
- [ ] i18n keys under `login.*`

- [ ] **Manual test:** `npm run type-check` in `ui-tui/` or `web/` per project layout

---

### Task P1-10: Frontend — auth redirect guard

**Files:**
- Modify: `web/src/App.tsx` or new `web/src/components/MemberAuthGuard.tsx`
- Modify: `web/src/contexts/MemberContext.tsx`

- [ ] When `membersEnabled && oauth_required && !actorId` and path not `/login` → `<Navigate to="/login?return_to=..." />`
- [ ] `/login` excluded from guard.
- [ ] Do **not** redirect when `oauth_enabled` false (picker still works).

---

### Task P1-11: Provider setup docs

**Files:**
- Modify: `website/docs/user-guide/features/teams-and-members.md`
- Create: `website/docs/user-guide/features/members-oauth.md` (optional dedicated page)

- [ ] Per-provider setup: callback URL, required scopes, config YAML example for GitHub, Google, Gitee, Azure AD.
- [ ] Link from teams-and-members.md.

---

### Task P1-12: Example config

**Files:**
- Modify: `cli-config.yaml.example` (if members section exists)

- [ ] Commented `members.oauth` example with one GitHub provider block.

---

**P1 merge checklist**

- [ ] `scripts/run_tests.sh tests/agent/test_members_oauth.py tests/agent/test_member_session.py tests/intellect_cli/test_dashboard_members_oauth.py tests/intellect_cli/test_dashboard_members_api.py -q`
- [ ] Legacy parity + membership tests unchanged
- [ ] `intellect doctor` shows callback URL when provider configured
- [ ] Manual smoke: enable oauth + mock provider OR GitHub test app → full login → Chat loads with correct actor

---

## Suggested commit order (within each PR)

```text
P0: config → helper + unit tests → API lockdown → status fields → doctor → docs
P1: presets → PKCE/state → token exchange → resolution → sessions → HTTP routes → dashboard resolve → UI → docs
```

---

## Out of scope for P0–P1 (explicit)

| Item | Phase |
|------|-------|
| Members page link/unlink (E3) | P2 |
| CLI `members login --oauth` | P2 |
| 企业微信 / 钉钉 | P1.5 |
| `trusted_header` enterprise SSO | P3 |
| ONTOWEB preset | P3 |
| id_token signature verification against IdP JWKS | P1 optional hardening / fast-follow |

---

## Risk notes

1. **Azure AD tenant** — require `tenant` in provider config (`common`, `organizations`, or GUID).
2. **Gitee OAuth** — confirm OAuth host (`gitee.com` vs enterprise); preset may need `api_base` override field.
3. **Callback behind reverse proxy** — operators must set `callback_base_url`; doctor should print it.
4. **id_token verify** — v1 may parse without JWKS verify; document threat model (code exchanged server-side over HTTPS only).
