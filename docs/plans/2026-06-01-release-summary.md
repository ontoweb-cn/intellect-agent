# Intellect Agent Multi-User System — Release Summary

> **Date:** 2026-06-01  
> **Branch:** `v0.4.1`  
> **Tests:** 403 tests, zero regressions  
> **Status:** Production-ready for single-user; beta for multi-user

---

## Feature Overview

### Multi-User Foundation

Three independent feature flags (all default `false`):

```yaml
members:
  enabled: false          # master switch
  teams:
    enabled: false        # team collaboration
  projects:
    enabled: false        # project workspaces
```

**Zero impact** when all flags are off — the system behaves identically to single-user Intellect.

### Members

| Command | Description |
|---------|-------------|
| `members bootstrap` | One-shot: create default member + team + project + session |
| `members list` | List all members |
| `members show <login>` | Member details + API tokens |
| `members create <login> [--name] [--email]` | Create a member |
| `members login <login>` | Set active CLI member |
| `members workspace <login>` | Show member workspace path |
| `members invite [--email] [--ttl]` | Create invite code |
| `members redeem <code> [--login] [--name]` | Redeem invite → create member |
| `members bind --oauth <provider>` | Link OAuth provider |
| `members identities [login]` | List linked identities |

### Teams (10 commands)

| Command | Description |
|---------|-------------|
| `members teams create <slug> [--name]` | Create team |
| `members teams list [--member]` | List teams |
| `members teams show <slug>` | Team details |
| `members teams archive <slug>` | Archive team |
| `members teams join/leave <slug>` | Join/leave team |
| `members teams approve <slug> <member>` | Approve member |
| `members teams admin add/remove <slug> <member>` | Manage admins |
| `members teams soul refresh <slug>` | Synthesize team SOUL from members |
| `members teams workspace <slug>` | Show team workspace path |

### Projects (23 commands)

| Category | Commands |
|----------|----------|
| CRUD | `create`, `show`, `list`, `archive` |
| Membership | `join`, `leave`, `approve`, `reject`, `admin add/remove` |
| Team links | `link-team`, `unlink-team` |
| Env vars | `env set`, `env unset`, `env list` (0600, audit logged) |
| SOUL | `soul show`, `soul edit` |
| Git | `clone [--url] [--branch]`, `workspace` |
| Tokens | `token create [--name]`, `token list`, `token revoke` |

### OAuth Integration

```bash
# Loopback login (local browser)
intellect members login --oauth github

# Device code login (remote SSH)
intellect members login --oauth github --device

# Invite + OAuth
intellect members redeem IM-CODE --oauth github --login bob

# Link additional provider
intellect members bind --oauth google

# List linked identities
intellect members identities alice
```

**Supported providers:** GitHub, Google, Gitee, Azure AD, GitLab, Azure DevOps, Gitea/Forgejo, WeCom, DingTalk

**OAuth-Git integration:** GitHub, Gitee, GitLab, and Azure DevOps OAuth tokens automatically authenticate git operations for matching repos.

### Gateway Integration

Slash commands in messaging platforms:
- `/team <id>` — set active team (sticky per session)
- `/teams` — list your teams
- `/project <id>` — set active project (sticky per session)
- `/projects` — list your projects

RuntimeContext is injected into AIAgent, enabling team/project SOUL and workspace in gateway sessions.

### API Server

```bash
# Member-scoped bearer token
curl -H "Authorization: Bearer imt_xxx" \
     -H "X-Intellect-Team: kitchen" \
     -H "X-Intellect-Project: web-app" \
     http://localhost:18921/v1/chat/completions \
     -d '{"model":"gpt-5","messages":[{"role":"user","content":"Hello"}]}'
```

Capabilities endpoint reports `members`, `teams`, `projects` status.

### Security

- Secret access audit log (`secret_access_log` table)
- Project-scoped API tokens (`imt_p_*`)
- `.env` files chmod 0600
- OAuth token storage chmod 0600
- Invite codes with TTL
- Doctor checks: 7 project + 3 OAuth

---

## Module Inventory

| Module | Lines | Purpose |
|--------|-------|---------|
| `agent/membership.py` | 345 | Feature flags, RBAC, MembershipDB, member CRUD, API tokens, dir helpers |
| `agent/teams.py` | 153 | TeamDB, team CRUD, memberships, dir helpers |
| `agent/projects.py` | 409 | ProjectDB, project CRUD, memberships, team links, project tokens |
| `agent/runtime_context.py` | 500 | RuntimeContext, resolution, SOUL assembly, cwd/env |
| `agent/project_env.py` | 220 | Project .env I/O (0600), SOUL I/O, audit logging |
| `agent/project_workspace.py` | 155 | Git clone/pull, OAuth credential chain, workspace paths |
| `agent/members_oauth.py` | 420 | OAuth engine: PKCE, state, presets, token exchange, resolution |
| `agent/oauth_tokens.py` | 85 | OAuth token persistence (0600), git auth integration |
| `agent/team_soul.py` | 120 | Team SOUL synthesis from member SOULs |

---

## Schema (v15)

| Table | Purpose |
|-------|---------|
| `members` | People |
| `teams` | Collaboration groups |
| `projects` | Work contexts |
| `team_memberships` | Member ↔ Team |
| `project_memberships` | Member ↔ Project |
| `project_teams` | Project ↔ Team |
| `identities` | External account → Member |
| `member_api_tokens` | Bearer tokens (member + project scoped) |
| `member_invites` | Invite codes |
| `secret_access_log` | Audit trail |

---

## Quick Start

```bash
# Enable multi-user
intellect config set members.enabled true
intellect config set members.teams.enabled true
intellect config set members.projects.enabled true

# Bootstrap
intellect members bootstrap

# Create members
intellect members create alice --name "Alice"
intellect members invite --email bob@example.com

# Create team and project
intellect members teams create engineering --name "Engineering Team"
intellect members projects create web-app --name "Web App"

# Add members to team and project
intellect members teams approve engineering bob
intellect members projects approve web-app bob

# Set up git workspace
intellect members projects env set web-app GIT_TOKEN "ghp_xxx"
intellect members projects clone web-app --url https://github.com/acme/web-app.git

# OAuth login (optional)
intellect config set members.oauth.enabled true
# Add provider to config.yaml + client_secret to .env
intellect members login --oauth github
```

---

*End of release summary.*
