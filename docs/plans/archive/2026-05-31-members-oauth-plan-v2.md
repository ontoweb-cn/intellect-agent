# Members OAuth — Revised Plan (v2.1, CLI-only + Git Integration)

> **Based on:** `2026-05-21-members-oauth-design.md` (Approved) + `2026-05-21-members-oauth-p0-p1-plan.md`  
> **Revised:** 2026-05-31 — Dashboard removed; plan adapted for CLI + API Server  
> **Updated:** 2026-06-01 — OAuth-Git integration (GitHub + Gitee) added; project impact incorporated
> **Status:** Draft for review

---

## 1. What changed from v1 plan

| v1 (May 21) | v2 (May 31) | Reason |
|-------------|-------------|--------|
| Dashboard `/login` page (LoginPage.tsx) | **Removed** | Dashboard removed from project |
| `intellect_cli/web_server.py` middleware | **Removed** | Web server removed |
| `intellect_cli/dashboard_members_api.py` routes | **CLI + API Server** | Routes migrate to `gateway/platforms/api_server.py` or stay CLI |
| `agent/dashboard_session.py` cookies | **`.cli-session.json`** | Already implemented (P12) |
| Member picker lockdown (P0) | **Removed** | No picker to lock down |
| Frontend auth redirect guard | **Removed** | No frontend |
| `web/src/*` — LoginPage, App.tsx, api.ts | **Removed** | No web UI |

### What stays the same

| Component | Status |
|-----------|--------|
| `agent/members_oauth.py` — OAuth engine | ✅ Keep, adapt |
| PKCE + state management | ✅ Keep |
| OIDC provider presets (GitHub, Google, Gitee, Azure AD) | ✅ Keep |
| `resolve_oauth_member()` resolution logic | ✅ Keep |
| `identities` table usage | ✅ Already in place |
| Invite + OAuth flow (E2) | ✅ Adapt to CLI |
| External identity binding (E3) | ✅ Already mostly done |
| `members.oauth` config block | ✅ Keep, simplify |
| `intellect doctor` checks | ✅ Keep, adapt |
| Server-side session files | ✅ Replace with `.cli-session.json` |

---

## 2. Revised architecture

### 2.1 OAuth in a CLI-first world

```
User on any machine (SSH, local, remote)
  │
  ▼
intellect members login --oauth github
  │
  ├─ [Loopback mode]  Opens browser on localhost, receives callback
  └─ [Device code mode] Prints URL + code, polls token endpoint
  │
  ▼
OAuth callback → token exchange → claims
  │
  ▼
resolve_oauth_member(provider, claims)
  ├─ Existing identity? → login as that member
  ├─ Invite code in state? → redeem + bind identity
  └─ auto_provision? → create member + bind
  │
  ▼
resolve_oauth_member(provider, claims)
  ├─ Existing identity? → login as that member
  ├─ Invite code in state? → redeem + bind identity
  └─ auto_provision? → create member + bind
  │
  ▼
Write .cli-session.json + bind (cli, device_id) identity
  │
  ▼
Store OAuth access token → {HOME}/.oauth-tokens/<member_id>/<provider>.json (mode 0600)
  │
  ▼
Auto-resolve project context (if single project → set active)
  │
  ▼
✓ "Logged in as alice (GitHub). Active project: web-app.
   Git: auto-authenticated for github.com repos."
```

### 2.2 OAuth-Git Integration (NEW — v2.1)

OAuth providers that are also git hosts (GitHub, Gitee) double as **git credential sources**. When a member logs in via GitHub OAuth and has projects with `repo_url` pointing to `github.com`, the OAuth access token is automatically used for `git clone`/`pull` operations — no manual `GIT_TOKEN` in project `.env` needed.

#### Supported providers (P1)

| Provider | Git host | OAuth scope | Auto-detection |
|----------|----------|-------------|----------------|
| **GitHub** | `github.com` | `repo` (private repos) or none (public only) | `repo_url` contains `github.com` |
| **Gitee** | `gitee.com` | `projects` (private repos) or none (public only) | `repo_url` contains `gitee.com` |

#### Future providers (P2+)

| Provider | Git host | Notes |
|----------|----------|-------|
| GitLab | `gitlab.com`, self-hosted | OIDC + `read_repository` scope |
| Azure DevOps | `dev.azure.com` | Azure AD OAuth already supports this |
| Gitea / Forgejo | self-hosted | Generic OIDC provider with custom `api_base` |

#### Credential resolution order

When `clone_project_repo()` is called (in `agent/project_workspace.py`):

1. **OAuth token** — check `{HOME}/.oauth-tokens/<member_id>/<provider>.json`; if token matches repo host → use it
2. **Project .env** — fallback to `GIT_USERNAME` + `GIT_TOKEN` or `GIT_SSH_KEY`
3. **Git credential helpers** — system default (e.g., `git credential-osxkeychain`)

#### Token storage

```
{INTELLECT_HOME}/.oauth-tokens/
  <member_id>/
    github.json    # { access_token, expires_at, refresh_token?, scopes }
    gitee.json     # { access_token, expires_at, refresh_token?, scopes }
```

Files are `chmod 0600`. Tokens are never logged or exposed in `intellect doctor` output.

#### Auto-refresh

- GitHub tokens don't expire by default (until revoked). If a `refresh_token` is present (GitHub App), use it.
- Gitee tokens expire per server settings. Refresh if `refresh_token` is available.
- On `401` during git operation → clear stored token → prompt user to `intellect members login --oauth github` again.

### 2.3 API Server OAuth

For headless API access, OAuth is not the primary auth method — `imt_*` bearer tokens remain the standard. OAuth for API is deferred to P2+.

---

## 3. What already exists (no need to rebuild)

| Capability | Module | Status |
|-----------|--------|--------|
| Feature flags | `agent/membership.py` | ✅ `is_members_enabled()` etc. |
| `identities` table | `intellect_state.py` (v15) | ✅ `(platform, external_id)` → `member_id` |
| `member_invites` table | `intellect_state.py` (v15) | ✅ invite codes, TTL, accepted tracking |
| Member CRUD | `agent/membership.py` | ✅ `create_member()`, `get_member_by_login()` |
| Invite/redeem CLI | `intellect_cli/main.py` | ✅ `members invite`, `members redeem` |
| Member login (device) | `intellect_cli/main.py` | ✅ `.cli-session.json` + identity bind |
| `members.oauth` config stub | `intellect_cli/config.py` | ⬜ Not yet added |
| PKCE helpers | `intellect_cli/auth.py` | ✅ `_oauth_pkce_*` (reuse or extract) |
| OAuth state files | — | ⬜ New: `{HOME}/.oauth-pending/` |
| Project workspace + git | `agent/project_workspace.py` | ✅ `clone_project_repo()`, credential handling |
| Project env management | `agent/project_env.py` | ✅ `.env` read/write + audit log |
| Git cred fallback | Project `.env` (`GIT_TOKEN`, `GIT_SSH_KEY`) | ✅ already supported |
| OAuth token storage | — | ⬜ New: `{HOME}/.oauth-tokens/` |

---

## 4. Revised phases

| Phase | Scope | Est. | User-visible |
|-------|-------|------|-------------|
| **P0** | `members.oauth` config skeleton + `agent/members_oauth.py` core (PKCE, state, token exchange, resolution) + doctor checks + OAuth token storage dir | S | `intellect doctor` shows OAuth status; config validated |
| **P1** | CLI `login --oauth` (loopback + device code) + invite-in-OAuth-state + provider presets (GitHub, Google, Gitee, Azure AD) + **OAuth-Git integration for GitHub + Gitee** | M | `intellect members login --oauth github` → auto git auth for github.com repos |
| **P2** | `intellect members bind --oauth` (link additional providers) + API Server OAuth token endpoint + **project-scoped token from OAuth** | M | E3: link identities; API token exchange with project scope |
| **P2.5** | **GitLab + Azure DevOps OAuth-Git integration** | S | Extend auto git auth to GitLab/Azure DevOps |
| **P3** | Enterprise SSO (trusted_header), WeCom/DingTalk adapters, **Gitea/Forgejo generic OAuth** | S | E4 + self-hosted git |

**Each phase:** tests green with `members.oauth.enabled: false`.

---

## 5. P0 — Config + OAuth engine

### 5.1 Config schema

Add `members.oauth` block to `DEFAULT_CONFIG`:

```yaml
members:
  oauth:
    enabled: false
    session_ttl_hours: 168
    auto_provision: false
    auto_provision_id_claim: email_local
    callback_base_url: null
    # OAuth-Git integration (v2.1)
    store_git_token: true       # store OAuth token for git operations
    git_token_scope_github: "repo"  # scope for GitHub provider ("" = public only)
    git_token_scope_gitee: "projects"  # scope for Gitee provider
    providers: []
```

No `require_for_dashboard` (removed with Dashboard). No `allow_picker_on_localhost` (removed). No `trusted_header` (deferred to P3).

Add to `OPTIONAL_ENV_VARS` / `.env.example`:
```
GITHUB_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_CLIENT_SECRET, GITEE_OAUTH_CLIENT_SECRET, AZURE_AD_OAUTH_CLIENT_SECRET
```

### 5.2 `agent/members_oauth.py` — core engine

Create with:
- `is_oauth_enabled(config) -> bool`
- `get_oauth_config(config) -> dict`
- `OAUTH_PROVIDER_PRESETS` — GitHub, Google, Gitee, Azure AD
- `list_enabled_providers(config) -> list[dict]`
- `resolve_provider(config, provider_id) -> dict`
- PKCE helpers (extracted from `intellect_cli/auth.py` or duplicated)
- `create_oauth_state(provider_id, ...) -> str` — signed state
- `verify_and_consume_oauth_state(state) -> dict`
- `build_authorization_url(provider, redirect_uri, state, code_challenge) -> str`
- `exchange_code_for_tokens(provider, code, redirect_uri, code_verifier) -> dict`
- `resolve_oauth_member(provider_id, claims, *, store, config, invite_code=None) -> str`

### 5.3 Doctor checks

- WARN: `oauth.enabled` + `providers` empty
- WARN: enabled provider missing `client_id` or env secret
- INFO: OAuth not available via Dashboard (removed); use CLI `members login --oauth`

---

## 6. P1 — CLI OAuth login

### 6.1 `intellect members login --oauth <provider>`

Two modes:

**Loopback mode** (local machine with browser):
```
$ intellect members login --oauth github
Opening browser to https://github.com/login/oauth/authorize?...
Waiting for OAuth callback on http://127.0.0.1:18923/callback ...
✓ Authenticated as alice (GitHub: alice-smith)
✓ Session saved to ~/.intellect/.cli-session.json
```

Uses a temporary `http.server.HTTPServer` on a random port (same pattern as `intellect_cli/auth.py`).

**Device code mode** (remote SSH, no browser on server):
```
$ intellect members login --oauth github --device
Visit: https://github.com/login/device
Code:  ABCD-1234
Waiting for authentication...
✓ Authenticated as alice (GitHub: alice-smith)
```

Polls the IdP token endpoint until the user completes the flow.

### 6.2 Invite + OAuth

```
$ intellect members redeem FAM-2026 --oauth github
Opening browser...
✓ Welcome, Bob! Identity bound: oauth:github:550e8400-...
✓ Session saved.
```

The invite code is embedded in the OAuth `state` parameter. On callback, `resolve_oauth_member` redeems the invite and binds the identity in one transaction.

### 6.3 Session + Git token storage

On success:
1. Writes `.cli-session.json` (existing format from P12)
2. Creates/updates identity row in `identities` table
3. **If provider is a git host** (GitHub, Gitee):
   - Stores access token in `{HOME}/.oauth-tokens/<member_id>/<provider>.json` (mode 0600)
   - Prints git auth status: `Git: auto-authenticated for github.com repos`
4. Resolves project context (if single project → set active)

The stored OAuth token is then used by `agent/project_workspace.py` for git operations on matching repos — no manual credential setup needed.

---

## 7. P2 — Identity linking + API Server

### 7.1 `intellect members bind --oauth <provider>`

Link an additional OAuth provider to the currently logged-in member:
```
$ intellect members login alice
$ intellect members bind --oauth google
Opening browser...
✓ Google identity linked to alice.
```

Creates a second `identities` row for the same member.

### 7.2 `intellect members identities`

List linked identities for a member:
```
$ intellect members identities alice
  oauth:github    550e8400-...  (alice-smith)
  oauth:google    660f9511-...  (alice@gmail.com)
  cli             device:abc123
```

### 7.3 API Server OAuth token endpoint

`POST /api/members/oauth/token` — exchange OAuth code for a bearer token:

```json
// Request
{ "provider": "github", "code": "...", "project_id?": "web-app" }

// Response (member-scoped)
{ "token": "imt_xxx", "scope": "member", "member_id": "alice" }

// Response (project-scoped, if project_id provided and member has access)
{ "token": "imt_p_xxx", "scope": "project", "project_id": "web-app" }
```

If `project_id` is provided and the member is an active project member → returns `imt_p_*` (project-scoped). Otherwise returns `imt_*` (member-scoped). This enables CI/CD pipelines to bootstrap with OAuth and get a project-scoped token.

---

## 8. P3 — Enterprise SSO

Same as original spec §11:
- Trusted header (`X-Forwarded-User`) from reverse proxy
- WeCom / DingTalk OAuth2 adapters
- ONTOWEB preset
- Documentation

---

## 9. Code touch list (revised)

| Area | Files | Phase |
|------|-------|-------|
| Core OAuth engine | `agent/members_oauth.py` (new) | P0 |
| Config | `intellect_cli/config.py` | P0 |
| CLI login | `intellect_cli/main.py` | P1 |
| CLI bind/identities | `intellect_cli/main.py` | P2 |
| Git integration | `agent/project_workspace.py` (extend credential resolution) | P1 |
| OAuth token storage | `agent/oauth_tokens.py` (new) — read/write/refresh stored tokens | P1 |
| PKCE reuse | `intellect_cli/auth.py` (extract helpers) | P0 |
| Doctor | `intellect_cli/doctor.py` | P0 |
| API Server | `gateway/platforms/api_server.py` | P2 |
| State files | `{HOME}/.oauth-pending/` (new dir) | P0 |
| Tests | `tests/agent/test_members_oauth.py` (new) | P0 |
| Tests | `tests/intellect_cli/test_members_oauth_cli.py` (new) | P1 |
| Docs | `website/docs/user-guide/features/teams-and-members.md` | P1 |

### Removed from v1 plan

| File | Reason |
|------|--------|
| `web/src/pages/LoginPage.tsx` | Dashboard removed |
| `web/src/App.tsx` | Dashboard removed |
| `web/src/lib/api.ts` | Dashboard removed |
| `web/src/i18n/*.ts` | Dashboard removed |
| `intellect_cli/dashboard_members_api.py` | Dashboard removed |
| `intellect_cli/web_server.py` | Dashboard removed |
| `agent/dashboard_session.py` | Dashboard removed |
| `agent/member_session.py` | Replaced by `.cli-session.json` |
| P0 picker lockdown tasks | No picker to lock down |

---

## 10. What this plan leverages from existing implementation

| Existing | How OAuth uses it |
|----------|-------------------|
| `identities` table | OAuth `sub` → `(oauth:<provider>, <sub>)` → `member_id` |
| `member_invites` table | Invite code in OAuth state → redeem on callback |
| `.cli-session.json` | Written on OAuth success (same as `members login`) |
| `resolve_member_id()` | Falls back to session file after OAuth login |
| `is_members_enabled()` | OAuth gated behind `members.enabled` |
| `MembershipDB` | Member CRUD used by `resolve_oauth_member()` |
| `agent/project_workspace.py` | Git clone/pull extended to check OAuth tokens first |
| `agent/project_env.py` | Fallback credential source if OAuth token unavailable |
| `intellect_cli/auth.py` | PKCE helpers reused for OAuth |
| `intellect doctor` | OAuth config warnings added to existing checks |
| Feature flag pattern | `members.oauth.enabled: false` → zero impact |

---

## 11. Testing strategy

| Test | Phase |
|------|-------|
| Config defaults + validation | P0 |
| PKCE: code_verifier ↔ code_challenge round-trip | P0 |
| State: create → verify → consume (one-time) | P0 |
| State: expired rejected | P0 |
| State: tampered rejected | P0 |
| Token exchange: mock IdP → claims dict | P0 |
| Member resolution: existing identity | P0 |
| Member resolution: invite redeem + bind | P0 |
| Member resolution: identity collision → error | P0 |
| Member resolution: auto_provision | P0 |
| CLI login loopback (mock HTTPServer) | P1 |
| CLI login device code (mock token endpoint) | P1 |
| CLI invite redeem with --oauth flag | P1 |
| Git: OAuth token auto-used for matching repo host | P1 |
| Git: fallback to .env when OAuth token missing/expired | P1 |
| Git: token refresh on 401 | P1 |
| CLI bind --oauth (link second provider) | P2 |
| API token exchange with project_id → imt_p_* | P2 |
| Doctor warnings | P0 |
| Legacy parity: oauth.enabled=false | All |

---

*End of revised plan.*
