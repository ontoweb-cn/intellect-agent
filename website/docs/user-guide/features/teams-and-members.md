---
sidebar_position: 15
title: "Teams, Projects & Members"
description: "Multi-user access inside one Intellect profile — members, invites, registration approval, teams, and projects"
---

# Teams, Projects & Members

> **Scope:** One **profile** (`~/.intellect` or `~/.intellect/profiles/<name>`), many **members**. This is not the same as [Profiles](./../profiles) (separate Intellect homes on one machine).

When `members.enabled` is true, a single Intellect profile can host multiple human users. Each member has their own id, optional password, OAuth identities, memories (when scoped per member), and API tokens. **Teams** add shared collaboration context; **projects** add shared workspaces (often git repos) with their own secrets and conventions.

All of this is stored in the profile's `state.db` under `~/.intellect/` (or your active profile directory).

## Enable the feature

```yaml
# ~/.intellect/config.yaml
members:
  enabled: true
  teams:
    enabled: true      # optional
  projects:
    enabled: true      # optional
  bootstrap:
    default_admin_login: alice   # optional; first member login when DB is empty
  registration:
    invite_ttl_hours: 168
    local_requires_approval: true   # default: local WebUI/CLI sign-ups need approval
  oauth:
    enabled: true
    callback_base_url: http://127.0.0.1:9119   # WebUI origin when using Intellect WebUI
    providers: []
    trusted_header:              # optional reverse-proxy SSO
      enabled: false
      header: X-Authenticated-User
  rbac:
    version: 1                   # set to 2 for database-driven custom roles
```

Feature flags default to `false`. Turning on `members.enabled` alone does not enable teams or projects.

## First-time setup (bootstrap)

Create the initial owner, default team, and default project in one step:

```bash
intellect members bootstrap
# or with explicit names:
intellect members bootstrap --admin-login alice --team family --project default
```

The first member is always **owner** (full privileges). After bootstrap, sign in:

```bash
intellect members login alice
```

If no password exists yet, the CLI prompts you to set one on first login.

## Roles and permissions (summary)

| Role | Typical use |
|------|-------------|
| **owner** | Profile owner — add/delete members, activate/deactivate, grant-owner, reset passwords |
| **admin** | Day-to-day admin — invite new members, manage API tokens, bind OAuth, team/project admin actions |
| **member** | Normal user — join teams/projects (often pending approval), use the agent |
| **guest** | Read-heavy / limited actions |

**Owner-only (CLI):** `add`, `activate`, `deactivate`, `delete`, `grant-owner`, `reset`.

**Owner or admin:** `invite` (create invite codes).

**Anyone with a valid invite code:** `register`.

Team and project membership can also be approved by **team/project admins** even when the actor is only a global `member` (dual-gating). See the developer spec in `docs/plans/2026-05-31-profile-teams-members-projects-spec-v2.md` for the full matrix.

## Member lifecycle (CLI)

| Command | Who | What it does |
|---------|-----|----------------|
| `intellect members add <login> [--name] [--email] [--id]` | owner | Create a member directly (no invite); optional custom member id |
| `intellect members invite [login] [--email] [--ttl] [--id]` | owner, admin | Create invite code; `--id` reserves the member id for redemption |
| `intellect members register <code>` | anyone | Register with invite → choose id (if not reserved) + password → logged in |
| `intellect members activate <login>` | owner | Re-enable after deactivation |
| `intellect members deactivate <login>` | owner | Soft-disable (`enabled=0`, not pending approval) |
| `intellect members delete <login>` | owner | Permanent delete + cascade (sessions, tokens, owned projects, etc.) |
| `intellect members grant-owner <login>` | owner | Promote to owner (confirmation) |
| `intellect members list` / `show` / `whoami` | signed-in | Directory and self; `whoami` shows login, role, teams, projects |
| `intellect members login <login>` | active member | Password login; writes `~/.intellect/.cli-session.json` |
| `intellect members login --oauth <provider>` | bound identity | OAuth login (GitHub, Google, Azure AD, WeCom, DingTalk, …) |
| `intellect members register <code> [--oauth <provider>]` | anyone | Invite registration; optional OAuth bind during signup |
| `intellect members logout` | signed-in | Clears CLI session file |
| `intellect members passwd` | self / owner | Change password |
| `intellect members bind [--login <login>] --oauth <provider>` | self, owner, admin | Link OAuth identity; admins may bind for others |
| `intellect members identities <login>` | admin+ | List linked OAuth identities |
| `intellect members role …` | owner, admin | Custom roles when `members.rbac.version: 2` (see below) |

Deletion requires typing the login name to confirm. It removes the member row and related data from `state.db`, clears `sessions.member_id`, and purges file-backed member sessions where applicable.

## Invite → register flow

1. **Owner or admin** runs:

   ```bash
   intellect members invite charlie --email charlie@example.com
   # optional: reserve member id
   intellect members invite charlie --id charlie
   ```

2. Share the code (format like `IM-CH-XXXXXXXX`) and expiry (default 7 days from `invite_ttl_hours`).

3. **New user** runs:

   ```bash
   intellect members register IM-CH-XXXXXXXX
   ```

   They enter member id (fixed if the invite had `--id`), display name, and password twice. The agent validates the code, creates an **active** member (`enabled=1`), hashes the password, and marks the invite used.

### OAuth + invite

On **Intellect WebUI** (`/register`), the OAuth tab can carry an invite in OAuth `state` so that after IdP login the new account is bound to the redeemed invite. OAuth-only signup without an invite follows `members.oauth.auto_provision` (default `false` — registration may stop until an admin adds the user).

CLI equivalents:

```bash
intellect members register IM-CH-XXXXXXXX --oauth github
intellect members login --oauth github
intellect members bind alice --oauth github   # admin binding another user
```

If OAuth authorization is cancelled during register, the member account still exists; bind later with `members bind`.

## Sign-in on CLI and Gateway

| Surface | How to sign in | How to sign out |
|---------|----------------|-----------------|
| **CLI** | `intellect members login <login>` or `login --oauth <provider>` | `intellect members logout` |
| **Gateway** (Telegram, Slack, …) | Slash `/login <login>` or `/login <member_id>` (sticky per session) | `/logout` |
| **API / WebUI** | Bearer token `imt_…` or WebUI session cookie | Revoke token in UI or `members logout` on CLI |

Gateway `/login` stores `session:{session_key}:member_id` in `state_meta` (same pattern as `/team` and `/project`). Platform identities (Telegram user id, etc.) still resolve through the `identities` table when no sticky login is set.

`/whoami` on messaging platforms shows the resolved member login, global role, and online status when `members.enabled` is true.

**Guest** members (`role: guest`) can use read-oriented flows where allowed, but **cannot start agent conversations** (API server and Gateway enforce `Action.CHAT`). Tool calls that modify state (memory writes, cron jobs, project `.env` paths under `~/.intellect`, etc.) are blocked at the tool dispatcher when a member context is present.

## Database-driven RBAC (v2)

v1 (default) uses the fixed roles `owner` / `admin` / `member` / `guest` on each member row. v2 adds `role_definitions` and scoped `member_role_bindings` so you can grant custom permission sets globally or per team/project:

```yaml
members:
  enabled: true
  rbac:
    version: 2
```

```bash
intellect members role list
intellect members role create doc-editor --permissions chat,read,team:member:list
intellect members role grant charlie doc-editor --scope project --id web-app
intellect members role revoke charlie doc-editor --scope project --id web-app
```

Built-in roles are seeded automatically on schema upgrade. Owners always pass authorization checks.

## Enterprise SSO (trusted header)

When a reverse proxy authenticates users (OAuth2 Proxy, Authentik, corporate gateway), enable:

```yaml
members:
  oauth:
    trusted_header:
      enabled: true
      header: X-Authenticated-User   # or X-Forwarded-User, etc.
```

The gateway and API server map the header value to a member login or id (must already exist unless you use auto-provision). Run `intellect doctor` to validate the configuration.

Presets for direct OAuth providers include **Azure AD** (`azure_ad`), **WeCom** (`wecom`), **DingTalk** (`dingtalk`), and **Feishu** (`feishu`) in addition to GitHub/Google/Gitee.

Built-in login providers are stored in `state.db` (`oauth_providers`), seeded on first `intellect setup`. Configure credentials and enable providers in **Intellect WebUI → Settings → Auth Services** or with `intellect oauth enable <id>`.

If you still have a legacy `members.oauth.providers` list in `config.yaml`, migrate it once:

```bash
intellect oauth migrate-from-config --dry-run    # preview
intellect oauth migrate-from-config --write-config   # copy to DB and clear YAML list
```

After migration, keep `members.oauth.enabled: true` and other OAuth settings (`callback_base_url`, `trusted_header`, …); the `providers` array is no longer required.

## Local self-registration and approval

When `members.registration.local_requires_approval` is `true` (default):

| State | `enabled` | `registration_pending` | Meaning |
|-------|-----------|------------------------|---------|
| Pending local signup | `0` | `1` | Waiting for profile admin approval |
| Deactivated | `0` | `0` | Disabled account (not in approval queue) |
| Active | `1` | `0` | Can sign in |

- **Local register** (WebUI `/register` local tab or equivalent API) creates a row with `registration_pending=1`.
- **OAuth pre-registration** does **not** use this queue; it uses a short-lived `registration_token` in OAuth state instead.
- **Approve** sets `enabled=1` and clears `registration_pending`.
- **Reject** **deletes** the pending row (API may return `status: "deleted"` — that is a label, not a stored status column).

Only **`owner`** and **`admin`** roles can approve or reject pending local sign-ups and create invite codes. They cannot delete members or grant owner (owner-only). **Admins cannot activate, deactivate, or reset passwords for `owner` accounts** — only a signed-in owner may perform those actions on another owner. In WebUI, use **Members** for the pending queue and invites.

**Display names** must be unique across all members in the profile — the register check API and server will reject duplicates.

To allow immediate local signup without a queue, set:

```yaml
members:
  registration:
    local_requires_approval: false
```

## Intellect WebUI

[Intellect WebUI](https://github.com/ONTOWEB/intellect-webui) embeds the same membership stack on port **9119** (not the legacy dashboard on 9009).

| Page / panel | Purpose |
|--------------|---------|
| `/login` | OAuth, member id + password, localhost dev picker |
| `/register` | Local account, OAuth, or invite code |
| **Members** panel | Invites, API tokens, identities, **pending local registrations** |
| **Teams** / **Teams** dropdown | Join teams, switch active team (`X-Intellect-Team`) |
| Title bar / headers | Active team and project (`X-Intellect-Project`) for API calls |

Configure `members.oauth.callback_base_url` to the WebUI origin behind your reverse proxy so OAuth redirect URIs match exactly.

Member **account passwords** are separate from the optional **WebUI access password** (`INTELLECT_WEBUI_PASSWORD`). In multi-user mode on non-localhost hosts, a valid member session is enough; the shared WebUI password is not required for every user.

## Teams and projects (overview)

With `members.teams.enabled` and `members.projects.enabled`:

- **Teams** — shared `SOUL`, skills, env, and workspace under `~/.intellect/teams/<team-id>/`. Members join via `intellect teams join <id>` (often **pending** until a team admin approves).
- **Projects** — per-project `SOUL.md`, `CONVENTIONS.md`, `.env`, and `workspace/` under `~/.intellect/projects/<project-id>/`. Join via `intellect projects join <id>` with similar approval flows.

CLI entry points:

```bash
intellect teams list
intellect teams join my-team
intellect projects list
intellect projects join my-app
```

Gateway and API server requests can pin context with `X-Intellect-Team`, `X-Intellect-Project`, and member bearer tokens (`imt_…`). See [API Server](./api-server) for HTTP integration and session headers.

## Memory scope

```yaml
members:
  memory_scope: profile   # default — shared MEMORY.md across members
  # memory_scope: member  — isolated under members/<id>/memories/
```

## Wiki scope

Each member, team, and project can have its own LLM Wiki under `members/{id}/wiki/`, `teams/{slug}/wiki/`, and `projects/{slug}/wiki/`. The organization **Global** wiki lives at `wiki/global/` — readable by everyone, writable only by `owner` and `admin`; members submit personal pages for admin review.

Session context (active team/project in WebUI or gateway headers) determines which wiki the agent writes to. See **[LLM Wiki & Vault](./llm-wiki)** for paths, the WebUI Wiki panel, Vault builds, and the contribution queue.

## Related docs

- [Profiles](../profiles) — multiple isolated Intellect homes on one machine
- [LLM Wiki & Vault](./llm-wiki) — scoped wikis, Global read-only + contribution review, WebUI Vault panel
- [API Server](./api-server) — OpenAI-compatible HTTP + headers for team/project/member context
- [Security](../security) — secrets, tokens, and hardening
- Developer specs: `docs/plans/2026-05-31-profile-teams-members-projects-spec-v2.md`, `docs/plans/member-management-redesign.md`

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Pending user never appears in queue | Account was deactivated (`registration_pending=0`) or registered via OAuth pending flow |
| Cannot delete member by id in WebUI | Member id may be a reserved word (e.g. `admin`); use login name or migrate id |
| Invite rejected at register | Expired code, already used, or reserved id taken |
| OAuth redirect mismatch | `callback_base_url` must match the browser origin (including `localhost` vs `127.0.0.1`) |
| Gateway agent says “linked member account is required” | Use `/login`, link a platform identity, pass `imt_…`, or enable trusted header SSO |
| Tool error “Permission denied” for guest | Guest role lacks `chat` / write permissions; ask an admin to change role or grant a scoped v2 role |
| OAuth login says account not linked | Register with `--oauth` or run `members bind` after password login |

Run `intellect doctor` after config changes; member and OAuth health checks are included when `members.enabled` is true.
