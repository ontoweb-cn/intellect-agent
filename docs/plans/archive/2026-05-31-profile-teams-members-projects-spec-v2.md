# Profile Teams, Members & Projects — Implementation Spec v2

> **Status:** Implemented (May–Jun 2026) — P6a–P14, OAuth P0–P1, API Server, LLM SOUL complete; Dashboard (P9) removed.
> **Base:** Extends [2026-05-19-profile-teams-members-spec.md](./2026-05-19-profile-teams-members-spec.md) (P1a–P5 complete on branch `v0.3`).
> **Related:** [2026-05-31-members-oauth-plan-v2.md](./2026-05-31-members-oauth-plan-v2.md) (OAuth), [2026-05-31-spec-change-list.md](./2026-05-31-spec-change-list.md) (change log)
> **Tests:** 403 tests, zero regressions. **Modules:** 14 agent modules. **CLI:** 40+ commands.

---

## 1. Summary

| Layer | Scope | Physical home |
|-------|--------|----------------|
| **Profile** | One agent instance home (`INTELLECT_HOME`) | `~/.intellect` or `~/.intellect/profiles/<name>/` |
| **Team** | Shared collaboration context (SOUL, skills, env, workspace) | `teams/<team_id>/` + `teams` / `team_memberships` tables |
| **Project** | Shared work context (code, resources, secrets, workspace) | `projects/<project_id>/` + `projects` / `project_memberships` tables |
| **Member** | A person (memory, personal SOUL/skills, API tokens) | `members/<member_id>/` + `members` table |
| **Identity** | External account → Member mapping | `identities` table |

**Fixed product decisions (v2 additions in bold):**

1. Profile admin creates teams and projects, appoints team/project admins; members join multiple teams and projects with admin approval; profile admin invites new members to register.
2. Team SOUL is synthesized from active members' personal SOUL files; team admins may override via `SOUL.override.md`. **Project SOUL is manually maintained (or optionally synthesized from project README/docs) and describes the project's technical context, not member personalities.**
3. **Memory follows the member** (`members.memory_scope: member`), not the team or project.
4. API auth: **one Bearer token per member** (hashed at rest); team context via `X-Intellect-Team`, **project context via `X-Intellect-Project`**.
5. Group chats: per-participant agent memory via existing `group_sessions_per_user` + `thread_sessions_per_user` (default on when teams enabled).
6. **Projects are orthogonal to teams: a member can work on a project without being in a team, and a team can have multiple projects. Project-team association is optional and many-to-many.**

**Non-goals (v1+v2):** Cross-profile federation; billing/quota; untrusted multi-tenant SaaS; per-team or per-project memory stores.

**Mode switch:** Legacy profile (`members.enabled: false`) vs multi-user profiles (`members.enabled: true`, optional teams/projects) is **only** controlled by `config.yaml` (§2). No second code path compiled differently; guards at runtime. Multi-user never reverts to legacy session behavior.

### 1.1 Team vs Project: Conceptual Distinction

| Dimension | Team | Project |
|-----------|------|---------|
| **Purpose** | People collaboration (WHO) | Work context (WHAT) |
| **SOUL** | Synthesized from member personalities | Manually authored (project identity, tech stack, conventions) |
| **Workspace** | Optional shared working directory | Primary workspace (often a git repo) |
| **Skills** | Team-specific tools/skills | Project-specific tools/skills |
| **Membership** | Members join teams | Members join projects |
| **Typical use** | "Engineering team", "Family" | "web-app", "mobile-app", "research-paper-q3" |
| **Can exist without** | Members (a team of one is valid) | Members (a personal side-project) |
| **Association** | Can be linked to multiple projects | Can be linked to multiple teams |

---

## 2. Operating modes (single-user vs multi-user vs multi-project)

### 2.1 Configuration gate (authoritative)

Three independent flags; **all** must be understood:

| `members.enabled` | `members.teams.enabled` | `members.projects.enabled` | Mode | User-visible behavior |
|-------------------|-------------------------|---------------------------|------|------------------------|
| `false` (default) | ignored | ignored | **Legacy single-user** | Identical to today's Intellect. No `members/`, `teams/`, or `projects/` dirs. |
| `true` | `false` | `false` | **Solo member** | One logical member (`default`); no teams, no projects. Still **multi-user profile** (member-scoped sessions, RBAC) — not legacy single-user. |
| `true` | `true` | `false` | **Multi-user + multi-team** | Full team spec; no project features. |
| `true` | `true` | `true` | **Multi-user + multi-team + multi-project** | Full spec: teams + projects. |
| `true` | `false` | `true` | **Multi-user + solo projects** | Members have personal projects; no team features. |

**Default in `DEFAULT_CONFIG`:** `members.enabled: false`, `members.teams.enabled: false`, `members.projects.enabled: false`.

**Dependency:** `projects.enabled` implies `members.enabled: true`. Setting `projects.enabled: true` while `members.enabled: false` is a config error caught by `intellect doctor`.

### 2.2 Zero-impact guarantee (legacy single-user)

Unchanged from original spec §2.2. When `members.enabled` is `false`, no project code paths execute.

**Extended rule for projects:**
```python
if not is_projects_enabled(config):  # members.enabled and members.projects.enabled
    return legacy_path(...)
```

### 2.3 Enabling multi-project mode (operator)

```bash
intellect config set members.enabled true
intellect config set members.teams.enabled true   # optional
intellect config set members.projects.enabled true
intellect members bootstrap   # idempotent; creates default member + team + project + session
```

`intellect members bootstrap` is the **single** entry point — it creates the default member, team, project, and CLI session file in one step. Fully idempotent; safe to re-run.

---

## 3. Glossary

| Term | Meaning |
|------|---------|
| `member_id` | Stable slug (`alice`), `[a-z0-9][a-z0-9_-]{0,63}` |
| `team_id` | Stable slug (`kitchen`), same pattern |
| `project_id` | Stable slug (`web-app`), same pattern |
| `platform` | `telegram`, `discord`, `cli`, `dashboard`, `api_server`, … |
| `external_id` | Platform-native user id or `token:<token_id>` for API |
| `session_key` | Existing gateway session lane id; extended with member + team + **project** |
| `RuntimeContext` | Resolved `(member_id, team_id, project_id, identities…)` for one agent run |

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Profile (INTELLECT_HOME) — single gateway process typical          │
│  config.yaml · state.db · profile admins · invites                 │
│                                                                     │
│  teams/kitchen/          teams/dev/                                 │
│    SOUL·skills·env       …                                          │
│                                                                     │
│  projects/web-app/       projects/mobile-app/                       │
│    SOUL·skills·env        …                                         │
│    workspace/(git repo)                                              │
│                                                                     │
│  members/alice/          members/bob/                               │
│    memories·SOUL·skills  …                                          │
│    tokens/ (optional)                                               │
│                                                                     │
│  DB: teams, projects, members, team_memberships,                    │
│      project_memberships, project_teams, identities,                │
│      member_api_tokens, invites, sessions(+member_id,team_id,       │
│      project_id)                                                    │
└──────────────────────────────────────────────────────────────────┘
         ▲                    ▲
         │ Identity           │ Bearer + X-Intellect-Team + X-Intellect-Project
    Telegram/Discord      API Server / CLI / Dashboard
```

**Core invariant:** `get_intellect_home()` and `_apply_profile_override()` are unchanged. Team/project/member resolution happens **after** profile selection, before `AIAgent` construction.

**Resolution order:** Member → Team → Project. Project resolution can depend on team context (e.g., "in team `kitchen`, my active project is `web-app`").

---

## 5. Filesystem layout

```
{INTELLECT_HOME}/
├── config.yaml                 # members.*, team_bindings, project_bindings, defaults
├── .env                        # profile-level API key fallback
├── SOUL.md                     # optional profile-wide preamble
├── skills/                     # optional profile-wide skills
├── state.db
├── teams/
│   ├── _template/              # copied on teams create
│   └── <team_id>/
│       ├── team.yaml
│       ├── SOUL.md             # manual or last synthesized
│       ├── SOUL.generated.md   # LLM output (gitignore optional)
│       ├── SOUL.override.md    # admin override (wins if present)
│       ├── skills/
│       ├── .env                # optional team keys
│       └── workspace/          # optional team cwd
├── projects/                   # NEW
│   ├── _template/              # copied on projects create
│   └── <project_id>/
│       ├── project.yaml        # project config (soul mode, team links, repo info)
│       ├── SOUL.md             # project identity/context (manual)
│       ├── SOUL.generated.md   # optional LLM-generated from README/docs
│       ├── CONVENTIONS.md      # project conventions (like CLAUDE.md)
│       ├── skills/
│       ├── .env                # project-level secrets/API keys
│       └── workspace/          # project working directory (or symlink to git repo)
├── members/
│   ├── _template/
│   └── <member_id>/
│       ├── member.yaml
│       ├── SOUL.md
│       ├── memories/
│       │   ├── MEMORY.md
│       │   └── USER.md
│       ├── skills/
│       ├── workspace/          # default personal cwd
│       ├── home/               # subprocess HOME
│       └── .env                # optional personal API key overrides
└── registry/                   # optional JSON cache
```

**Permissions:** `projects/*/.env`, token files `chmod 0600` on write (mirror `gateway/pairing.py`).

---

## 6. Database schema (state.db migration → v15)

Add to `intellect_state.py` migration chain. Bump `SCHEMA_VERSION` to **15** (v12 = teams/members; v13 = projects; v14–v15 = incremental additions).

### 6.0 New tables (projects)

```sql
-- Projects (work contexts)
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    description TEXT,                          -- short description
    status TEXT NOT NULL DEFAULT 'active',     -- active | archived
    repo_url TEXT,                             -- optional git remote
    default_team_id TEXT REFERENCES teams(id), -- optional default team
    created_at REAL NOT NULL,
    updated_at REAL
);

-- Many-to-many: members ↔ projects
CREATE TABLE IF NOT EXISTS project_memberships (
    project_id TEXT NOT NULL REFERENCES projects(id),
    member_id TEXT NOT NULL REFERENCES members(id),
    role TEXT NOT NULL DEFAULT 'member',       -- member | project_admin
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | active | rejected
    requested_at REAL NOT NULL,
    approved_at REAL,
    approved_by TEXT,                          -- member_id of approver
    PRIMARY KEY (project_id, member_id)
);

CREATE INDEX IF NOT EXISTS idx_project_memberships_member
    ON project_memberships(member_id, status);

-- Optional many-to-many: projects ↔ teams
CREATE TABLE IF NOT EXISTS project_teams (
    project_id TEXT NOT NULL REFERENCES projects(id),
    team_id TEXT NOT NULL REFERENCES teams(id),
    created_at REAL NOT NULL,
    PRIMARY KEY (project_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_project_teams_team
    ON project_teams(team_id);
```

### 6.1 Sessions extension (new columns)

```sql
-- Applied via ALTER if columns missing:
--   sessions.project_id TEXT
--   sessions.project_workspace TEXT  -- snapshot of resolved workspace path

CREATE INDEX IF NOT EXISTS idx_sessions_project
    ON sessions(project_id, started_at DESC);
```

### 6.2 Migration from pre-project installs

On first run with `members.projects.enabled: true`:

1. Create `projects/_template/` from built-in defaults.
2. If `members.projects.default_project` is set, create `projects/<default>/` and insert row.
3. Backfill `sessions.project_id` as NULL (no project was set).
4. No automatic member enrollment in projects (unlike teams which auto-enroll `default`).

---

## 7. Roles & permissions (extended)

### 7.1 Organizational role matrix

| Action | profile_admin | team_admin | project_admin | member |
|--------|:-------------:|:----------:|:-------------:|:------:|
| Create/archive team | ✓ | — | — | — |
| **Create/archive project** | ✓ | — | — | — |
| Assign team_admin | ✓ | — | — | — |
| **Assign project_admin** | ✓ | — | — | — |
| Invite register member | ✓ | — | — | — |
| Create/revoke own API token | ✓ | ✓ | ✓ | ✓ |
| Approve team join | — | ✓ (that team) | — | — |
| **Approve project join** | — | — | ✓ (that project) | — |
| Request team join | — | — | — | ✓ |
| **Request project join** | — | — | — | ✓ |
| Edit team SOUL override | — | ✓ | — | — |
| **Edit project SOUL** | — | — | ✓ | — |
| Trigger team SOUL refresh | ✓ | ✓ | — | — |
| **Trigger project SOUL refresh** | ✓ | — | ✓ | — |
| **Link project to team** | ✓ | ✓ | ✓ | — |
| Bind Identity (self) | ✓ | — | — | ✓ (with code) |
| Bind Identity (others) | ✓ | — | — | — |
| Disable member | ✓ | — | — | — |
| List all sessions | ✓ | team-scoped | project-scoped | own only |
| **Manage project .env** | ✓ | — | ✓ | — |

### 7.2 System RBAC matrix (Action enum)

Implemented in `agent/membership.py`. Maps system roles (`owner`/`admin`/`member`/`guest`) to `Action` enum values for programmatic authorization via `authorize(role, action)`.

| Action | owner | admin | member | guest |
|--------|:-----:|:-----:|:------:|:-----:|
| **Core** | | | | |
| `chat` | ✓ | ✓ | ✓ | |
| `read` | ✓ | ✓ | ✓ | ✓ |
| **Project** | | | | |
| `project:create` | ✓ | ✓ | | |
| `project:manage` | ✓ | ✓ | | |
| `project:archive` | ✓ | | | |
| `project:delete` | ✓ | | | |
| **Team** (added 2026-06-01) | | | | |
| `team:create` | ✓ | ✓ | | |
| `team:manage` | ✓ | ✓ | | |
| `team:archive` | ✓ | | | |
| `team:delete` | ✓ | | | |
| `team:member:add` | ✓ | ✓ | | |
| `team:member:remove` | ✓ | ✓ | | |
| `team:member:list` | ✓ | ✓ | ✓ | ✓ |
| **Member** | | | | |
| `member:invite` | ✓ | ✓ | | |
| `member:kick` | ✓ | | | |
| **API** | | | | |
| `api_token:manage` | ✓ | ✓ | | |
| **Admin** | | | | |
| `admin` | ✓ | | | |

Team and project member add/remove additionally support **dual-gating**: when the global role lacks permission, a team/project-internal admin can still manage members, provided the actor has at least `member` global role (guests excluded). All `actor_role` parameters default to `None`, which bypasses authorization for backward compatibility.

### 7.3 Enforcement status (2026-06-01)

| Layer | Status | Notes |
|-------|--------|-------|
| Action + ROLE_PERMISSIONS | ✅ | 17 actions (4 project + 7 team + 2 member + 4 core), 4 roles |
| `authorize()` function | ✅ | O(1) set lookup, guest floor for dual-gating |
| `resolve_member_id()` → (id, role) | ✅ | 4-step resolution returns role from members table |
| TeamDB (5 methods) | ✅ | create/archive/add/remove/list — all gated |
| ProjectDB (5 methods) | ✅ | create/archive/add/remove — all gated + dual-gating |
| MembershipDB (15 methods, all gated) | ✅ | create/disable/token_create/token_revoke + activate/deactivate/delete/grant_owner/create_invite/set_member_role — all with actor_role gates |
| Password authentication | ✅ | hash+salt login, lockout, reset code flow, passwd, strength warnings |
| Invite → Register flow | ✅ | owner/admin invite (`MEMBER_INVITE`); optional `reserved_member_id`; register sets password + marks invite used |
| Local registration approval | ✅ | `members.registration.local_requires_approval`; `registration_pending=1` queue; WebUI profile admin approve/reject |
| CLI: privileged member commands | ✅ | add/activate/deactivate/delete/grant-owner/reset → `ADMIN` (owner); invite → `MEMBER_INVITE` (owner + admin) |
| WebUI lifecycle on owners | ✅ | activate/deactivate/reset-password/delete: DB 层事务内 `actor_may_lifecycle_manage_target`；`actor_role` 必填；admin 不可作用于 owner。`grant_owner` 仍为 owner-only（`Action.ADMIN`）。见 [hardening §20](./2026-06-02-members-webui-hardening-design.md#20-第三方评审修复2026-06-03-review--已落地)。 |
| CLI: bootstrap | ✅ | first member auto-owner, uses resolved role for team/project creation |
| CLI: member read commands | ✅ | list/show/whoami/workspace — no owner gate needed |
| Skills scanning (3 layers) | ✅ | member/team/project skills directories scanned by _find_all_skills() |
| SOUL assembly (3 layers) | ✅ | assemble_soul() loads team/project/member SOUL into system prompt |
| ID validation | ✅ | validate_member_id() / validate_team_id() — slug regex, reserved words, path traversal rejection |
| create_member(member_id=) | ✅ | optional external member_id, auto-hex when None, CLI --id flag |
| member_session.py | ✅ | server-side session persistence (create/resolve/delete) with atomic JSON writes |
| members_http.py | ✅ | canonical cookie names shared with WebUI |
| Workspace cwd wiring | ✅ | resolve_terminal_cwd() wired into Gateway RuntimeContext |
| Memory per-member scoping | ✅ | members.memory_scope: member isolates memories per member |
| Online presence tracking | ✅ | member_sessions table, CLI login/logout hooks, Gateway per-message heartbeat |
| API Server | ⚳ | Token verify exists; role resolution deferred |
| Gateway session | ⚳ | Platform allowlist only; RBAC not integrated |

### 7.4 RBAC extensions for projects (future)

v1 actions added for projects:

```python
class Action(str, Enum):
    # ... existing ...
    PROJECT_CREATE = "project.create"
    PROJECT_APPROVE_JOIN = "project.approve_join"
    PROJECT_EDIT_SOUL = "project.edit_soul"
    PROJECT_LINK_TEAM = "project.link_team"
    PROJECT_MANAGE_ENV = "project.manage_env"
```

v2 RBAC tables (already reserved in original spec §7.4.3) accommodate project-scoped roles without schema changes:

```sql
-- project_admin at project scope
INSERT INTO member_role_bindings (member_id, role_id, scope_type, scope_id)
VALUES ('alice', 'project_admin', 'project', 'web-app');
```

---

## 8. Configuration (`config.yaml`) — extended

```yaml
members:
  enabled: false
  memory_scope: member

  teams:
    enabled: false
    auto_bootstrap: false
    default_team: default
    group_sessions_per_user: true
    thread_sessions_per_user: true

  projects:                                    # NEW
    enabled: false                             # DEFAULT: no projects
    auto_bootstrap: false
    default_project: null                      # e.g. "default"
    require_project_header: auto               # auto | always | never
    workspace_mode: git                         # git | local | none
    # git: clone repo to projects/<id>/workspace/
    # local: use existing directory
    # none: no workspace

  bootstrap:
    default_admin_login: null   # first bootstrap member login; was profile_admins (removed)

  registration:
    invite_ttl_hours: 168
    local_requires_approval: true   # WebUI local self-register → registration_pending queue

  # OAuth integration — see 2026-05-31-members-oauth-plan-v2.md
  oauth:
    enabled: false
    session_ttl_hours: 168
    auto_provision: false
    providers: []

  soul:
    team_merge: hybrid
    project_merge: manual                      # NEW: manual | generated | hybrid
    synthesize_max_chars: 4000

  cwd:
    default: personal                          # personal | team | project

  api:
    require_team_header: auto
    require_project_header: auto               # NEW

  team_bindings:
    telegram:
      "-1001234567890": kitchen

  project_bindings:                            # NEW
    telegram:
      "-1001234567890": web-app                # map chat to project (optional)
```

---

## 9. Identity

Identity resolution is unchanged from original spec §9. Project context does not affect **who** you are, only **what** you're working on.

**Additions implemented:**
- Member API tokens (`imt_*`) with `scope_type`/`scope_id` for project-scoped access
- CLI device identity via `.cli-session.json` + `identities` table
- Invite/redeem flow with `member_invites` table
- OAuth identity type (`oauth:<provider>`) planned — see §45 and [2026-05-31-members-oauth-plan-v2.md](./2026-05-31-members-oauth-plan-v2.md)

---

## 10. Team + Project resolution

### 10.1 Team resolution (unchanged from original §10)

See original spec §10.

### 10.2 Project resolution (NEW)

After `member_id` and `team_id` are known:

```python
def resolve_project_id(
    *,
    member_id: str,
    team_id: str | None,
    source: SessionSource | None,
    headers: dict,
    session_key: str | None,
    config: dict,
    db: SessionDB,
) -> str | None:
    """
    Returns project_id or None (project context is optional).
    Unlike team, project is NOT required even when projects.enabled.
    """
    # 1. Explicit header (API / Dashboard)
    if p := headers.get("X-Intellect-Project", "").strip():
        require_active_project_membership(member_id, p)
        return p

    # 2. Chat binding (groups/channels → project)
    if source and (p := lookup_project_binding(config, source)):
        require_active_project_membership(member_id, p)
        return p

    # 3. Sticky meta for this session_key
    if session_key and (p := db.get_session_project(session_key)):
        require_active_project_membership(member_id, p)
        return p

    # 4. CLI --project or .active_project file
    if p := read_cli_active_project():
        require_active_project_membership(member_id, p)
        return p

    # 5. Team's default project (if team has one)
    if team_id and (p := db.get_team_default_project(team_id)):
        if db.project_membership_active(member_id, p):
            return p

    # 6. Single active project → use it
    projects = db.list_active_projects(member_id)
    if len(projects) == 1:
        return projects[0].id

    # 7. default_project from config
    if default := config["members"]["projects"].get("default_project"):
        if db.project_membership_active(member_id, default):
            return default

    # 8. No project context — this is valid
    return None
```

**Key design decision:** Project is **optional** context. An agent session can run without a project. Team context may be required (per existing spec) but project context is additive — it enriches the agent's workspace, skills, and SOUL but is never mandatory.

**Gateway slash commands:** `/project <id>`, `/projects` (list active + pending), store sticky project on session meta.

---

## 11. SOUL assembly (extended)

### 11.1 Team SOUL (unchanged from original §11)

See original spec §11.

### 11.2 Project SOUL (NEW)

Project SOUL describes the project's **technical identity** — what it does, its tech stack, conventions, and key design decisions. Unlike team SOUL (which synthesizes member personalities), project SOUL is **manually maintained** by default.

#### Modes (`projects/<id>/project.yaml`)

```yaml
soul:
  mode: manual       # manual | generated | hybrid
  generate:
    sources:          # files to read for auto-generation
      - README.md
      - CLAUDE.md
      - docs/ARCHITECTURE.md
    trigger: file_change   # file_change | manual | cron
```

| mode | Injected project SOUL |
|------|----------------------|
| `manual` | `SOUL.md` only (admin-maintained) |
| `generated` | `SOUL.generated.md` (from project docs/files) |
| `hybrid` | `SOUL.md` then `SOUL.generated.md` appended |

#### System prompt assembly order (updated)

1. `profile/SOUL.md` (optional short preamble)
2. Team SOUL (per §11.1)
3. **Project SOUL** (§11.2) — project identity, tech stack, conventions
4. `members/<member_id>/SOUL.md`

---

## 12. Resource merge rules (extended)

### 12.1 Memory (unchanged — member only)

Project context does **not** create a separate memory store. Memory follows the member.

**Project-level curated memory:** `projects/<id>/CONVENTIONS.md` functions as project-level "CLAUDE.md" — loaded into system prompt, not into the memory store. This is analogous to how Claude Code reads `CLAUDE.md` from a repo root.

### 12.2 Skills (extended)

Scan order (later wins on name conflict):

1. `{INTELLECT_HOME}/skills/`
2. `teams/<team_id>/skills/`
3. **`projects/<project_id>/skills/`** (NEW)
4. `members/<member_id>/skills/`

`skill_manage` writes default to `members/<member_id>/skills/` unless `--team <id>` or **`--project <id>`** (project_admin only).

### 12.3 API keys & auth (extended)

Merge for agent env snapshot (first hit wins unless `members.api.merge: prefer_personal`):

1. `members/<member_id>/.env`
2. `teams/<team_id>/.env`
3. **`projects/<project_id>/.env`** (NEW)
4. Profile `.env`
5. `auth.json` / credential pool

**Project secrets security:** Project `.env` files are especially sensitive — they may contain deployment keys, database credentials, or third-party API tokens. See §39–§42 for Bitwarden-aligned secrets management patterns.

### 12.5 member_id / team_id 校验（new — WebUI Collab Phase 1）

**函数：** `validate_member_id()` / `validate_team_id()`（`agent/membership.py`）

校验规则：
- 正则：`^[a-z0-9][a-z0-9_-]{0,63}$`（最大 64 字符）
- 显式拒绝路径遍历：`/`、`\`、`..`
- 成员 ID 保留字：`admin`、`default`、`root`、`system`、`webui`、`api`、`template`、`none`、`null`、`true`、`false`

**`create_member(member_id=None)`：** 新增可选参数。`None` 时自动生成 12 位 hex（向后兼容），非 `None` 时经 `validate_member_id()` 校验后直接使用。

**CLI `members add --id <slug>`：** 允许 owner 显式指定 member_id（通过 `create_member(member_id=)` 传递）。

**新增模块：**
- `intellect_cli/members_http.py`：`member_cookie_name()` / `team_cookie_name()`
- `agent/member_session.py`：`create_member_session()` / `resolve_member_session()` / `delete_member_session()`（JSON 文件 + 原子写入）

### 12.6 Online presence (v18)

Tracks member online status via `member_sessions` table:

- **Schema**: `member_sessions` (id, member_id, platform, session_type, external_id, login_at, last_active_at, expires_at, status, metadata)
- **Members aggregates**: `last_active_at`, `last_active_platform`, `online_status` (online/offline)
- **TTL**: CLI 24h, Gateway 1h (renewed on each message)
- **Lifecycle**: login → record_session → active → end_session or TTL expiry → offline
- **CLI**: `list` shows STATUS + LAST SEEN, `show` shows active sessions
- **Gateway**: per-message `update_activity`, `/whoami` shows online status + platforms

### 12.7 Session isolation (v0.4.1+)

> **Implemented 2026-06-03** (`54a6a5499`)

**Scope:** Applies only when `members.enabled: true`. **There is no “single-user mode” inside multi-user** — once members are enabled, every session list/read path must resolve an actor `member_id` and apply filtering. The legacy profile (`members.enabled: false`) is a separate operating mode (§2); it does not use member-scoped sessions.

| Layer | Mechanism |
|-------|-----------|
| **Schema** | `sessions.member_id TEXT` column + `idx_sessions_member` index (v19) |
| **Listing** | `list_sessions_rich(member_id=?, config=?)` → strict: `member_id = ?` only; optional `legacy_shared_null`; owner may see all |
| **Creation** | `_insert_session_row(member_id=?)` populates the column from CLI session / gateway source |
| **API** | `GET /api/sessions` scopes to the authenticated member token |
| **Gateway resume** | `/resume` command resolves member_id from platform identity |
| **Legacy NULL rows** | Pre-stamp sessions may have `member_id IS NULL`. Default `members.session_isolation.legacy_null_visibility: strict` hides them from normal members; use `intellect members sessions migrate-ownership --member-id <id>` to assign ownership. Optional `legacy_shared_null` for migration windows only. |
| **Legacy profile** (`members.enabled: false`) | No member session isolation; `resolve_session_list_scope` → `unrestricted` |

### 12.4 Terminal cwd (extended)

| `members.cwd.default` | Resolved cwd |
|----------------------|--------------|
| `personal` | `members/<id>/workspace` → fallback profile `terminal.cwd` |
| `team` | `teams/<team_id>/workspace` → fallback personal |
| **`project`** (NEW) | **`projects/<project_id>/workspace`** → fallback team → fallback personal |

When `members.projects.workspace_mode: git` and a `repo_url` is set on the project, the workspace directory is a git clone. When `workspace_mode: local`, it's a plain directory (or symlink to an existing path).

### 12.5 Subprocess HOME (unchanged)

---

## 13. Session keys (extended)

Extend `build_session_key()` in `gateway/session.py`:

```python
def build_session_key(source, ..., member_id=None, team_id=None, project_id=None) -> str:
    base = _existing_logic(source, ...)
    parts = [base]
    if member_id:
        parts.append(f"member:{member_id}")
    if team_id:
        parts.append(f"team:{team_id}")
    if project_id:                         # NEW
        parts.append(f"project:{project_id}")
    return ":".join(parts)
```

**Cache invariant:** Project change mid-session requires `/new` or documented cache break. Project is fixed for the session lifetime (same as team).

---

## 14. RuntimeContext & agent creation (extended)

### 14.1 Dataclass

```python
@dataclass(frozen=True)
class RuntimeContext:
    member_id: str
    team_id: str
    project_id: str | None                  # NEW — optional
    platform: str
    external_id: str | None
    session_key: str
    env_snapshot: dict[str, str]
    terminal_cwd: str
    subprocess_home: str | None
    project_workspace: str | None           # NEW — resolved project workspace path
```

### 14.2 Resolution entrypoint

```python
def resolve_runtime_context(
    *,
    source: SessionSource | None,
    headers: Mapping[str, str],
    config: dict,
    db: SessionDB,
    cli_overrides: dict | None = None,
) -> RuntimeContext:
    member_id = resolve_member_id(source, headers, config, db, cli_overrides)
    team_id = resolve_team_id(member_id=member_id, source=source, headers=headers, ...)
    project_id = resolve_project_id(member_id=member_id, team_id=team_id, ...)  # NEW
    session_key = build_session_key(source, ..., member_id=member_id,
                                    team_id=team_id, project_id=project_id)
    env_snapshot = build_env_snapshot(member_id, team_id, project_id, config)  # extended
    terminal_cwd = resolve_terminal_cwd(member_id, team_id, project_id, config)  # extended
    subprocess_home = resolve_subprocess_home(member_id)
    project_workspace = resolve_project_workspace(project_id) if project_id else None
    return RuntimeContext(...)
```

### 14.3 AIAgent wiring (extended)

Additional parameter passed through:

- `project_id: str | None = None`
- `project_workspace: str | None = None`

Pass through to:
- `build_system_prompt_parts()` for project SOUL merge
- Skills discovery helper (project skills layer)
- Environment snapshot (project .env)
- `AIAgent.cwd` (project workspace)

---

## 15. API Server changes (extended)

### 15.1 Authentication headers

| Header | Required | Purpose |
|--------|----------|---------|
| `Authorization: Bearer <token>` | Yes | Member token |
| `X-Intellect-Team` | If multi-team | Team context |
| **`X-Intellect-Project`** | Optional | **Project context (NEW)** |
| `X-Intellect-Session-Id` | No | Transcript continuation |
| `X-Intellect-Session-Key` | No | Long-term plugin session scope |

### 15.2 Capabilities document (extended)

```json
{
  "auth": { "type": "member_bearer", "team_header": "X-Intellect-Team", "project_header": "X-Intellect-Project" },
  "members": { "teams": true, "projects": true, "memory_scope": "member" }
}
```

---

## 16. CLI commands (implemented)

All commands are under `intellect members`:

### 16.1 Member commands (18)

| Command | Permission | Description |
|---------|-----------|-------------|
| `members bootstrap [--admin-login] [--team] [--project]` | — | One-shot: create default member(owner) + team + project + session |
| `members add <login> [--name] [--email] [--id <slug>]` | owner | Add a member directly (no invite needed). `--id` sets a custom member_id. |
| `members invite [login] [--email] [--ttl] [--id]` | owner, admin | Create invite code; optional reserved member id |
| `members register <code>` | anyone | Register with invite code → member id + password×2 → auto-login |
| `members activate <login>` | owner | Re-enable a deactivated member (`registration_pending` cleared) |
| `members deactivate <login>` | owner | Soft-disable (`enabled=0`, `registration_pending=0`) |
| `members delete <login>` | owner | Permanently delete member + cascaded records (confirmation required) |
| `members grant-owner <login>` | owner | Promote a member to owner role (confirmation required) |
| `members list` | any | List all members (with online status + last seen) |
| `members show <login>` | any | Member details (with online status + active sessions) |
| `members login <login>` | anyone | Login with password (or first-time password set) |
| `members logout` | logged-in | Clear active session |
| `members passwd` | logged-in | Change own password (old + new×2) |
| `members reset <login>` | owner | Generate reset code; member uses on next login |
| `members bind <login> --provider <p>` | admin+ | Link OAuth identity |
| `members identities <login>` | admin+ | List OAuth bindings |
| `members workspace <login>` | admin+ | Show workspace path |
| `members whoami` | logged-in | Show current member identity |

### 16.2 Team commands (8)

| Command | Description |
|---------|-------------|
| `members teams create <slug> [--name]` | Create a team (auto-adds creator as admin) |
| `members teams list [--member]` | List teams |
| `members teams show <slug>` | Team details + members |
| `members teams archive <slug>` | Archive team |
| `members teams join <slug>` | Join team |
| `members teams leave <slug>` | Leave team |
| `members teams approve <slug> <member>` | Approve member |
| `members teams admin add/remove <slug> <member>` | Manage team admins |

### 16.3 Project commands (23)

| Command | Description |
|---------|-------------|
| `members projects bootstrap` | Create default project + directories |
| `members projects create <slug> [--name] [--team]` | Create project |
| `members projects show <slug>` | Project details + members + linked teams |
| `members projects list [--member] [--all]` | List projects |
| `members projects archive <slug>` | Archive project |
| `members projects join <slug>` | Join project |
| `members projects leave <slug>` | Leave project |
| `members projects approve <slug> <member>` | Approve member |
| `members projects reject <slug> <member>` | Remove member |
| `members projects admin add/remove <slug> <member>` | Manage project admins |
| `members projects link-team <slug> <team>` | Link team to project |
| `members projects unlink-team <slug> <team>` | Unlink team |
| `members projects env set <slug> <key> <value>` | Set env var (chmod 0600) |
| `members projects env unset <slug> <key>` | Remove env var |
| `members projects env list <slug>` | List env keys (values redacted) |
| `members projects soul show <slug>` | Show SOUL.md |
| `members projects soul edit <slug>` | Edit SOUL.md in $EDITOR |
| `members projects clone <slug> [--url] [--branch]` | Git clone/pull into workspace |
| `members projects workspace <slug>` | Print workspace path |
| `members projects token create <slug> [--name]` | Create project-scoped token (`imt_p_*`) |
| `members projects token list <slug>` | List project tokens |
| `members projects token revoke <slug> <id>` | Revoke project token |

### 16.4 Global flags

- `intellect chat --member alice --team kitchen --project web-app`
- Sticky session: `~/.intellect/.cli-session.json`

---

## 17. Gateway slash / IM commands (implemented — P6c)

| Command | Who | Action |
|---------|-----|--------|
| `/team <id>` | Member | Set sticky team for session_key (stored in `state_meta`) |
| `/teams` | Member | List active + pending teams |
| `/project <id>` | Member | Set sticky project for session_key (stored in `state_meta`) |
| `/projects` | Member | List active + pending projects |
| `/join <team_id>` | Member | Request team membership |
| `/members` | Team admin | Pending approvals |

**Implementation:** Handlers in `gateway/run.py`. Sticky context loaded in `_run_agent()` → `agent.runtime_context` → `system_prompt.py` SOUL assembly.

---

## 18. Dashboard — REMOVED

> **Dashboard support has been removed from the project.** The `intellect_cli/dashboard_auth/`,
> `intellect_cli/web_server.py`, `intellect_cli/pty_bridge.py`, and related frontend code
> have been deleted. All project management is done via CLI.
>
> Original Dashboard requirements (§18 in v1 spec) are superseded by CLI commands in §16.

---

## 19. Implementation phases & PRs

| PR | Scope | Est. | Status |
|----|--------|------|--------|
| **P1a–P5** | Teams & Members foundation (original spec) | — | ✅ Done |
| **P6a** | Schema v15, `agent/membership.py`, `agent/projects.py` CRUD, `is_projects_enabled`, `authorize()`, migration, CLI bootstrap, 63 tests | M | ✅ Done |
| **P6b** | `RuntimeContext.project_id`, `resolve_project_id()` 8-step, `build_session_key()` extension, 39 tests | M | ✅ Done |
| **P6c** | Gateway RuntimeContext injection, `/team` `/project` slash command handlers | M | ✅ Done |
| **P7a** | Projects CRUD CLI (11 commands) + TeamDB + 8 team CLI, 16 tests | M | ✅ Done |
| **P7b** | Project SOUL + team/member SOUL I/O, skills quadruple scan, cwd/env snapshot, 13 tests | M | ✅ Done |
| **P8a** | Project env management (set/unset/list, chmod 0600) + SOUL management (show/edit), 9 tests | S | ✅ Done |
| **P8b** | Git workspace (`projects clone/pull`, repo_url tracking) | M | ✅ Done |
| **P9** | ~~Dashboard~~ | — | ❌ Removed |
| **P10** | Secret access audit log + project-scoped API tokens (create/list/revoke) | S | ✅ Done |
| **P11** | Doctor checks (7 project health checks) + AGENTS.md docs | S | ✅ Done |
| **P12** | Member management CLI (list/show/create/login) | S | ✅ Done |
| **P13** | Teams CRUD (TeamDB + 8 CLI commands) | M | ✅ Done |
| **P14** | E2E integration tests (spec §26 full scenario), 4 tests | S | ✅ Done |
| **OAuth P0** | `members.oauth` config + `agent/members_oauth.py` engine (PKCE, state, resolution) + doctor + token storage, 30 tests | S | ✅ Done |
| **OAuth P1** | CLI `members login --oauth <provider>` (loopback + device code) + OAuth-Git integration + invite-in-OAuth | M | ✅ Done |
| **API Server** | Member Bearer token auth, `X-Intellect-Team`/`X-Intellect-Project` headers, capabilities extension | M | ✅ Done |
| **LLM SOUL** | Team SOUL synthesis from member SOULs, `teams soul refresh` CLI | S | ✅ Done |
| **RBAC Team** | 7 new Action values, ROLE_PERMISSIONS extension, TeamDB enforcement, 14 tests | S | ✅ Done |
| **RBAC Project** | ProjectDB enforcement (5 methods) + dual-gating, 7 tests | S | ✅ Done |
| **RBAC Membership** | MembershipDB enforcement (4→9 methods), 4 tests | S | ✅ Done |
| **RBAC Owner** | `members.role` column, bootstrap first-member-owner, resolve_member_id returns role, CLI uses resolved role | S | ✅ Done |
| **Schema v16** | `member_role_bindings` table placeholder for v2 database-driven RBAC | S | ✅ Done |
| **Schema v17** | Password auth columns (hash, salt, reset, lockout, role) on members | S | ✅ Done |
| **Schema v18** | `member_sessions` table + members.online_status/last_active_at/last_active_platform | S | ✅ Done |
| **Password Auth** | Login/passwd/reset CLI + lockout + reset code flow, 12 tests | M | ✅ Done |
| **Member Lifecycle** | add/activate/deactivate/delete/grant-owner CLI + DB methods, 9 tests | M | ✅ Done |
| **Invite/Register** | owner-only invite → interactive register with password, 4 tests | M | ✅ Done |
| **Workspace Wiring** | `resolve_terminal_cwd()` wired into Gateway RuntimeContext (project→team→member→profile priority) | S | ✅ Done |
| **Memory Scoping** | `members.memory_scope: member` — per-member memory isolation under `members/<id>/memories/` | S | ✅ Done |
| **Online Presence** | Schema v18: `member_sessions` table + CLI/Gateway session tracking + `list`/`show`/`/whoami` output | M | ✅ Done |
| **WebUI Collab** | ID validation functions + `create_member(member_id=)` + `members_http.py` + `member_session.py` | S | ✅ Done |

### Remaining phases

| Phase | Scope | Est. |
|-------|-------|------|
| **OAuth P2** | `members bind --oauth`, `members identities`, API Server OAuth endpoint | M |
| **OAuth P3** | Enterprise SSO (trusted_header), WeCom/DingTalk adapters, docs | S |
| **Gateway RBAC** | Gateway session member resolution + RBAC integration | M |
| **v2 DB-driven RBAC** | Load roles from `member_role_bindings` table, custom role CRUD | L |

See [2026-05-31-members-oauth-plan-v2.md](./2026-05-31-members-oauth-plan-v2.md) for full OAuth plan.

---

## 20. Code touch list (actual)

| Area | Files |
|------|-------|
| Schema | `intellect_state.py` (v15: 12 tables, 4 session cols, `secret_access_log`) |
| Membership | `agent/membership.py` (flags, RBAC, MembershipDB), `agent/teams.py` (TeamDB) |
| Projects | `agent/projects.py` (ProjectDB, tokens), `agent/project_env.py` (.env + SOUL I/O, audit), `agent/project_workspace.py` (git clone/pull) |
| Runtime | `agent/runtime_context.py` (RuntimeContext, resolution, SOUL assembly, cwd/env) |
| Agent init | `agent/system_prompt.py` (SOUL assembly hook), `tools/skills_tool.py` (quad scan) |
| Session key | `gateway/session.py` (`build_session_key` + member/team/project) |
| Gateway | `gateway/run.py` (slash handlers + RuntimeContext injection), `intellect_cli/commands.py` (CommandDef) |
| CLI | `intellect_cli/main.py` (38 subcommands), `intellect_cli/doctor.py` (7 checks) |
| Config | `intellect_cli/config.py` (`members.*` block), `intellect_cli/profiles.py` (_PROFILE_DIRS) |
| Constants | `intellect_constants.py` (`get_teams_dir`, `get_projects_dir`) |
| OAuth (planned) | `agent/members_oauth.py` (new) |
| Tests | `tests/agent/test_p6a_comprehensive.py` (88), `tests/agent/test_runtime_context.py` (22), `tests/agent/test_e2e_members_teams_projects.py` (4), `tests/agent/test_p2b_p2c_skills_soul.py` (13), `tests/gateway/test_session_key_project.py` (17) |
| Dashboard | ~~Removed~~ |

---

## 21. Testing strategy (extended)

Additional test requirements beyond original spec §21:

- **Unit:** `resolve_project_id` (all 8 resolution steps), project SOUL merge order, project env merge order, `authorize()` for project actions.
- **Integration:** gateway message → correct `session_key` with project suffix; api_server bearer + team header + project header; project workspace resolution with git clone.
- **Migration:** temp `INTELLECT_HOME` with teams enabled → add `projects.enabled: true` → `projects/` created.
- **Invariants:** With `members.projects.enabled: false`, no project code paths execute; existing session keys unchanged.
- **No change-detector tests** on project counts.

---

## 22. Security notes (extended)

Additional security considerations for projects:

- **Project .env files** may contain deployment keys, database credentials, or third-party API tokens. These are the most sensitive secrets in the system.
- Project membership does **not** automatically grant access to all team secrets; the merge order (§12.3) controls visibility.
- Project `.env` should never be logged, exported, or included in error messages.
- `X-Intellect-Project` without active project membership → 403.
- Git clone workspaces: credentials for private repos stored in project `.env`, never in `project.yaml`.
- See §39–§42 for comprehensive secrets management design aligned with Bitwarden patterns.

---

## 23–25. Docs, Deferred, Resolved Decisions

See original spec §23–§25. Additions:

### 25.1 New resolved decisions (v2)

| Question | Decision |
|----------|----------|
| Project memory scope? | **Member only** — projects use CONVENTIONS.md for project context, not separate memory |
| Project required? | **No** — project context is optional; agent can run without a project |
| Project ↔ Team relationship | **Optional many-to-many** via `project_teams` table |
| Project SOUL mode | **Manual by default** (not synthesized from members) |
| Project workspace git clone | **Yes** — `workspace_mode: git` with `repo_url` |
| Can project exist without team? | **Yes** — solo projects without team association are valid |

---

## 26. Example end-to-end (extended)

1. Profile admin: `intellect members invite` → code `FAM-2026`.
2. Bob redeems → `members/bob/`, Identity `cli:device-1`.
3. Admin: `intellect teams create kitchen --admin alice`.
4. Admin: `intellect projects create web-app --team kitchen --repo https://github.com/acme/web-app.git`.
5. Admin: `intellect projects admin add web-app alice`.
6. Bob: `intellect projects join web-app` → pending.
7. Alice: `intellect projects approve web-app bob`.
8. Alice: `intellect projects clone web-app` → `projects/web-app/workspace/` populated from git.
9. Alice writes `projects/web-app/SOUL.md` describing the tech stack.
10. Synthesis runs → `teams/kitchen/SOUL.generated.md` (from member SOULs, not project).
11. Bob opens CLI: `intellect chat --member bob --team kitchen --project web-app`.
12. Gateway: Identity → `bob`, team → `kitchen`, project → `web-app`, session_key `...:member:bob:team:kitchen:project:web-app`.
13. Agent loads kitchen SOUL + web-app SOUL + bob SOUL; memory from `members/bob/memories/`; cwd = `projects/web-app/workspace/`.
14. Bob's API call: `curl -H "Authorization: Bearer imt_xxx" -H "X-Intellect-Team: kitchen" -H "X-Intellect-Project: web-app" ...`

---

# NEW SECTIONS (v2)

---

## 27. Project membership lifecycle

### 27.1 States

```
invited → pending → active → (removed)
                 ↘ rejected
```

### 27.2 Join flows

| Flow | Steps |
|------|--------|
| **Profile admin creates** | `intellect projects create web-app --admin alice` → auto-activates alice as project_admin |
| **Self-join (open project)** | If project has `membership: open` in `project.yaml`, `intellect projects join web-app` → auto-active |
| **Request-approve** | `intellect projects join web-app` → pending → project_admin `intellect projects approve web-app bob` |
| **Admin add** | `intellect projects admin add web-app bob` → auto-active as member (or project_admin if `--role project_admin`) |

### 27.3 Leave and removal

- Member leaves: `intellect projects leave web-app` → membership row deleted (or marked `removed`).
- Admin removes: `intellect projects reject web-app bob` (pending) or profile admin removes active member.
- Archived projects: memberships preserved but inactive; no new sessions with this project.

---

## 28. Project workspace & git integration

### 28.1 Workspace modes

| Mode | Behavior |
|------|----------|
| `git` | Clone `repo_url` into `projects/<id>/workspace/`; `intellect projects clone` to update |
| `local` | Plain directory; user manages contents manually |
| `none` | No workspace directory created; cwd falls back to team/personal |

### 28.2 Git operations

```bash
intellect projects clone web-app     # git clone (or git pull if exists)
intellect projects workspace <id>    # print workspace path
```

- Credentials: read from project `.env` (`GIT_USERNAME`, `GIT_TOKEN` or `GIT_SSH_KEY`).
- No force-push; no automatic commit; agent can use `bash` tool within workspace.
- `project.yaml` records: `repo_url`, `default_branch`, `last_cloned_at`.

### 28.3 CONVENTIONS.md auto-discovery

When a project workspace is a git repo, the agent should also read `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.github/copilot-instructions.md` from the repo root (first found wins) and merge into project SOUL context. This bridges Intellect's project SOUL with existing repo-level AI instructions.

---

## 29. Project SOUL generation

### 29.1 Input sources

When `soul.mode: generated` or `hybrid`:

1. `projects/<id>/SOUL.md` (manual, if hybrid)
2. `projects/<id>/CONVENTIONS.md` (if exists)
3. Repo-level AI instruction files (§28.3) — `CLAUDE.md`, `AGENTS.md`, etc.
4. Package files: `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`
5. Directory structure overview (top 2 levels)
6. Recent git log (last 20 commits, first line only)

### 29.2 Worker

`agent/auxiliary_client.py` task `projects.soul_generate` (new auxiliary task key).

### 29.3 Trigger

- `intellect projects soul refresh <project_id>`
- On `projects clone` completion (if mode is `generated` or `hybrid` with trigger `file_change`)
- Cron: `members.projects.soul.cron` (optional, e.g., `0 3 * * 1`)

---

## 30. API endpoints — REMOVED

> REST API endpoints (§30 in v2 draft) were part of the Dashboard implementation.
> Since Dashboard has been removed from the project, these endpoints are not
> implemented. All project management is done via CLI (§16.3).

---

## 31. Doctor checks (implemented)

`intellect doctor` additions:

| Check | Severity | Condition |
|-------|----------|-----------|
| `PROJECTS_ENABLED_NO_MEMBERS` | Error | `projects.enabled: true` but `members.enabled: false` |
| `PROJECT_DEFAULT_NOT_FOUND` | Warning | `default_project` set but project doesn't exist |
| `PROJECT_NO_ADMIN` | Warning | Active project with zero `project_admin` members |
| `PROJECT_WORKSPACE_MISSING` | Info | `workspace_mode: git` but workspace dir empty (need `clone`) |
| `PROJECT_ENV_PERMISSIONS` | Warning | Project `.env` not `0600` |
| `PROJECT_GIT_AUTH_MISSING` | Warning | `repo_url` set but no `GIT_TOKEN` in project `.env` |
| `PROJECT_ORPHANED` | Info | Project with no active members |

---

## 32. `project.yaml` reference

```yaml
# projects/<project_id>/project.yaml
id: web-app
display_name: "Web Application"
description: "Main customer-facing web application"
status: active                   # active | archived

repo_url: "https://github.com/acme/web-app.git"
default_branch: main
workspace_mode: git              # git | local | none

soul:
  mode: manual                   # manual | generated | hybrid
  generate:
    sources:
      - README.md
      - CLAUDE.md
      - docs/ARCHITECTURE.md
    include_git_log: true
    git_log_commits: 20
    trigger: manual              # manual | file_change | cron

membership: request              # open | request (require approval)

default_team: kitchen            # optional, for team→project default resolution

skills:
  enabled: true                  # whether to scan project skills/

env:
  merge_order: project_first     # project_first | personal_first (for this project's .env)

created_at: "2026-05-31T10:00:00Z"
updated_at: "2026-05-31T10:00:00Z"
```

---

## 33. Cross-cutting: Teams + Projects interaction

### 33.1 Default project per team

A team can have a default project set (`projects.<id>.default_team` reversed). When a member is in team `kitchen` and project context is not specified, the team's first linked project is suggested (but not auto-selected — user must opt in via `/project` or sticky setting).

### 33.2 Project visibility via team membership

By default, project membership is independent of team membership. However, a project can be configured (`visibility: team_linked`) so that all members of linked teams automatically have read access to the project (but not write/approve).

```yaml
# project.yaml
visibility: private              # private | team_linked | public
# private: only explicit project members
# team_linked: explicit members + all members of linked teams (read-only)
# public: any profile member can join without approval
```

### 33.3 Team→Project routing

When both team and project are specified, team context provides the collaboration/SOUL layer, and project context provides the workspace/tools layer. They are complementary:

- Team `kitchen` + Project `web-app`: "I'm working on web-app with my kitchen team"
- Team `kitchen` + no project: "I'm collaborating with kitchen team" (general chat)
- No team + Project `web-app`: "I'm working on web-app solo"

---

## 34. Migration path for existing teams

For profiles that already have teams enabled (P1a–P5 complete):

1. Upgrade to v13 schema (add projects tables).
2. Set `members.projects.enabled: true` in config.
3. Run `intellect projects bootstrap` — creates `projects/` directory, `_template/`.
4. Existing team workspaces are NOT automatically converted to projects. Teams and projects are distinct concepts.
5. If a team was previously used as a "project" (with workspace, code, etc.), the operator should:
   - Create a project: `intellect projects create <id> --team <team_id>`
   - Move workspace contents: `mv teams/<id>/workspace/ projects/<id>/workspace/`
   - Move skills if project-specific: `mv teams/<id>/skills/ projects/<id>/skills/`

---

## 35. Cross-profile project references (future)

**Deferred to post-v2.** Projects are profile-scoped (like teams). Cross-profile project federation (e.g., sharing a project across two profiles) is a future concern. The `repo_url` field lays groundwork: two profiles could theoretically reference the same git repo as separate project instances.

---

## 36. Performance considerations

- Project resolution adds exactly **one** DB query (lookup `project_memberships` for active membership).
- Project SOUL assembly reuses the same code path as team SOUL assembly.
- Skills scan adds one more directory traversal (`projects/<id>/skills/`).
- Session key extension adds at most 50 bytes (`:project:<id>`).
- No new subprocess; no new file watchers.

**Caching:** `resolve_project_id()` result cached per session (same as team_id).

---

## 37. Backward compatibility summary

| Scenario | Behavior |
|----------|----------|
| `members.projects.enabled: false` | Zero impact. No project code paths execute. |
| `members.teams.enabled: false`, `members.projects.enabled: true` | Solo projects without teams. Valid config (members + projects, no teams). |
| `members.enabled: false`, `members.projects.enabled: true` | Config error caught by doctor. |
| Existing team-only install upgraded | Projects tables added but unread. No automatic project creation. |
| Session keys before projects | Unchanged. New sessions get `:project:<id>` suffix only when project context active. |
| API clients not sending `X-Intellect-Project` | Project resolves to None (valid). No error. |

---

## 38. Documentation deliverables (extended)

- User guide: `website/docs/user-guide/features/projects.md` (**new**)
- User guide update: `website/docs/user-guide/features/teams-and-members.md` (add project cross-references)
- Developer: `website/docs/developer-guide/projects-internals.md` (**new**)
- CHANGELOG: project feature announcement
- `AGENTS.md` update: add projects to architecture diagram

---

## 39. Bitwarden / Secrets Manager alignment analysis

### 39.1 Why this matters

Intellect Agent projects will hold API keys, database credentials, deployment tokens, and third-party service credentials in `projects/<id>/.env`. As the platform scales to multiple teams and projects, ad-hoc `.env` file management becomes a security risk:

- No audit trail for who accessed which secret
- No secret rotation mechanism
- No fine-grained access control (any project member can read all project secrets)
- No version history for secrets
- No machine identity concept for CI/CD agent access

Bitwarden Secrets Manager (and similar tools) provide a proven model for addressing these concerns.

### 39.2 Conceptual mapping

| Bitwarden Concept | Intellect Equivalent | Status |
|-------------------|---------------------|--------|
| **Organization** | Profile (`INTELLECT_HOME`) | ✓ Exists |
| **Project** | Project (`projects/<id>/`) | ✓ This spec |
| **Secret** | Key-value in `projects/<id>/.env` | ✓ Exists |
| **Member** | Member (`members/<id>/`) | ✓ Exists |
| **Group** | Team (`teams/<id>/`) | ✓ Exists |
| **Machine Account** | Project service account (API token scoped to project) | ✗ **Gap** |
| **Access Policy (User→Project)** | `project_memberships.role` | ✓ Partial |
| **Access Policy (User→Secret)** | Per-secret access within project `.env` | ✗ **Gap** |
| **Access Policy (Machine→Project)** | Service account scoped to project | ✗ **Gap** |
| **Secret Version History** | `.env` file git history (accidental) | ✗ **Gap** |
| **Audit Trail** | `errors.log` (not secret-specific) | ✗ **Gap** |
| **Token Exchange** | Member Bearer token (not project-scoped) | ✗ **Gap** |

### 39.3 Key gaps identified

1. **Machine Accounts (Service Accounts):** The current spec only has member API tokens. There is no concept of a non-human identity that can access project secrets for CI/CD pipelines or automated agent runners. Bitwarden's "Machine Accounts" model maps directly: a project-scoped API token with Read or Read+Write permission on project secrets.

2. **Secret-level access policies:** Within a project, all members can read all `.env` entries. Bitwarden allows per-secret access policies (e.g., "database password" is Read+Write for project_admin only, "AWS key" is Read-only for members). This fine-grained control prevents junior members from accessing production credentials.

3. **Secret version history:** Bitwarden maintains immutable secret versions. Intellect's `.env` files have no version history beyond filesystem backups. This is critical for audit and rollback.

4. **Audit trail:** No record of which member accessed which secret and when. Bitwarden provides event logs for every secret access.

5. **Dynamic secrets:** HashiCorp Vault's dynamic secrets (ephemeral database credentials generated on-demand) are absent from the current model. This is a deferred concern but should be designed for.

### 39.4 What to adopt now (v2)

| Feature | Implementation | Priority |
|---------|---------------|----------|
| **Project-scoped API tokens** | Extend `member_api_tokens` with `scope_type: 'project'` and `scope_id`; tokens can be scoped to a project instead of a member | P10 (this spec) |
| **Secret metadata** | `project_env` table in DB tracking key name, added_by, added_at, last_accessed_at (hash values, never plaintext) | P10 |
| **Basic audit log** | `secret_access_log` table: who accessed which project's env, when, via which token | P10 |
| **Per-secret write protection** | `project_env.protected` flag — only project_admin can modify protected keys | P10 |

### 39.5 What to defer (post-v2)

| Feature | Rationale |
|---------|-----------|
| Full secret versioning | Requires schema for immutable secret versions; can start with `.env` git tracking |
| Machine accounts | Requires new auth flow; can approximate with project-scoped member tokens for now |
| Dynamic secrets | Requires Vault integration or similar; significant infrastructure dependency |
| Full RBAC on secrets | Requires per-secret access policies (Bitwarden's 9-policy matrix); v2 RBAC tables support it but implementation deferred |
| External Vault integration | Vault/Bitwarden/Infisical as external secret backend; plugin opportunity |

---

## 40. Project-scoped API tokens (design)

### 40.1 Token types

| Type | Scope | Use case |
|------|-------|----------|
| **Member token** (existing) | Member-level | Personal CLI, API access |
| **Project token** (new) | Project-level | CI/CD pipeline, deployment script, project-specific automation |

### 40.2 Schema extension

```sql
-- Extend member_api_tokens (or create project_api_tokens)
ALTER TABLE member_api_tokens ADD COLUMN scope_type TEXT DEFAULT 'member';
-- 'member' | 'project'

ALTER TABLE member_api_tokens ADD COLUMN scope_id TEXT;
-- NULL for member scope, project_id for project scope

ALTER TABLE member_api_tokens ADD COLUMN permissions TEXT DEFAULT 'read';
-- 'read' | 'read_write' — JSON array for future: '["read:env", "write:env", "read:soul"]'
```

### 40.3 Token creation

```bash
intellect projects token create web-app --label "CI/CD pipeline" --permissions read
# → imt_p_<random> (prefix distinguishes project tokens)
# Token shown once; SHA-256 stored in DB
```

### 40.4 Authentication with project tokens

```
Authorization: Bearer imt_p_xxx
X-Intellect-Project: web-app   # Must match token's scope_id
```

Project tokens skip identity resolution (no member_id) — they authenticate directly as the project's service identity. The `RuntimeContext` for project tokens:

```python
RuntimeContext(
    member_id=None,           # no human member
    team_id=None,             # optional if project linked to team
    project_id="web-app",     # from token scope
    platform="api_server",
    external_id="token:<token_id>",
    ...
)
```

### 40.5 Security constraints

- Project tokens cannot: invite members, create teams, approve memberships, or access member memory.
- Project tokens can: read project SOUL, read project skills, read/write project env (per permissions), use agent within project workspace.
- Project token revocation: `intellect projects token revoke web-app <token_id>`.
- Project tokens expire: configurable TTL (default 90 days, max 365 days).

---

## 41. Secret access audit log

### 41.1 Schema

```sql
CREATE TABLE IF NOT EXISTS secret_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    actor_type TEXT NOT NULL,          -- 'member' | 'project_token'
    actor_id TEXT NOT NULL,            -- member_id or token_id
    project_id TEXT NOT NULL,
    action TEXT NOT NULL,              -- 'read_env' | 'write_env' | 'delete_env' | 'list_env'
    key_name TEXT,                     -- NULL for list, key name for read/write/delete
    source TEXT                        -- 'cli' | 'api' | 'dashboard' | 'gateway'
);

CREATE INDEX IF NOT EXISTS idx_secret_access_log_project
    ON secret_access_log(project_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_secret_access_log_actor
    ON secret_access_log(actor_id, timestamp DESC);
```

### 41.2 Retention

Default: 90 days. Configurable: `members.projects.secrets.audit_retention_days`.

### 41.3 Privacy

- `key_name` is logged (for audit), but `key_value` is **never** logged, stored, or cached in plaintext.
- Env values are only held in memory during agent env snapshot construction; not persisted to any log.

---

## 42. Future: external secrets backend integration

### 42.1 Plugin architecture

The project env system should be designed as a **pluggable backend** from day one:

```python
class ProjectSecretsBackend(Protocol):
    """Backend for project-level secrets storage."""
    def get(self, project_id: str, key: str) -> str | None: ...
    def set(self, project_id: str, key: str, value: str) -> None: ...
    def delete(self, project_id: str, key: str) -> None: ...
    def list(self, project_id: str) -> list[str]: ...
    def audit_log(self, project_id: str, since: float) -> list[dict]: ...

class DotEnvBackend(ProjectSecretsBackend):
    """v1 default: .env files on disk."""

class BitwardenBackend(ProjectSecretsBackend):
    """Future: Bitwarden Secrets Manager API."""

class VaultBackend(ProjectSecretsBackend):
    """Future: HashiCorp Vault KV v2."""

class InfisicalBackend(ProjectSecretsBackend):
    """Future: Infisical API."""
```

### 42.2 Configuration

```yaml
members:
  projects:
    secrets:
      backend: dotenv                    # dotenv | bitwarden | vault | infisical
      bitwarden:
        api_url: "https://api.bitwarden.com"
        organization_id: "..."
        access_token: "${BITWARDEN_ACCESS_TOKEN}"   # from profile .env only
      vault:
        url: "https://vault.example.com:8200"
        mount_path: "intellect"
        auth_method: approle              # approle | token | kubernetes
```

### 42.3 Migration path

1. Start with `dotenv` backend (built-in, no external dependency).
2. Operators can switch to `bitwarden`/`vault`/`infisical` by changing config and restarting.
3. Migration tool: `intellect projects secrets migrate --from dotenv --to bitwarden web-app`.
4. Dual-read window: config supports `backend: dotenv` + `fallback_read: [bitwarden]` during migration.

---

## 43. Comparison: Teams vs Projects (quick reference)

| Operation | Team | Project |
|-----------|------|---------|
| **Create** | Profile admin: `intellect teams create <id>` | Profile admin: `intellect projects create <id>` |
| **Join** | `intellect teams join <id>` (approval required) | `intellect projects join <id>` (may be open) |
| **Set active** | `/team <id>` or `--team` | `/project <id>` or `--project` |
| **SOUL** | Synthesized from member SOULs | Manually authored (project context) |
| **Skills scan** | Yes | Yes |
| **.env file** | Yes | Yes |
| **Workspace** | Optional shared dir | Primary workspace (often git repo) |
| **Memory** | No (member only) | No (member only) |
| **API header** | `X-Intellect-Team` | `X-Intellect-Project` |
| **Required?** | Yes (when teams enabled) | No (optional context) |
| **Admin role** | `team_admin` in `team_memberships` | `project_admin` in `project_memberships` |
| **Archive** | `intellect teams archive <id>` | `intellect projects archive <id>` |

---

## 44. Implementation recommendations

### 44.1 Phase ordering (completed)

1. **P6a** (schema + CRUD) — ✅ foundation
2. **P6b** (RuntimeContext + resolution) — ✅
3. **P7a** (CLI CRUD + TeamDB) — ✅
4. **P8a + P8b** (env + SOUL + git workspace) — ✅
5. **P10** (audit + project tokens) — ✅
6. **P12–P14** (members CLI + teams + E2E tests) — ✅
7. **P6c** (gateway integration) — ✅
8. **P2b+P2c** (SOUL assembly + skills scan + cwd/env) — ✅
9. **P11** (doctor + docs) — ✅
10. **OAuth P0–P3** — planned (§45)

### 44.2 Key design principles

1. **Project is optional** — project context is always optional. This prevents breaking existing workflows.
2. **Reuse team patterns** — project memberships mirror team memberships exactly.
3. **Filesystem-first** — directories are the source of truth for SOUL, skills, and env; DB mirrors for queryability.
4. **Pluggable secrets** — `ProjectSecretsBackend` protocol enables future Vault/Bitwarden.
5. **Zero impact when off** — `members.*.enabled: false` → zero code paths execute.

---

## 45. OAuth Integration Plan

> Full plan: [2026-05-31-members-oauth-plan-v2.md](./2026-05-31-members-oauth-plan-v2.md) (v2.1 — CLI-only + Git Integration)  
> Original design: [2026-05-21-members-oauth-design.md](./2026-05-21-members-oauth-design.md)

### 45.1 Summary

OAuth enables members to prove their identity via external providers (GitHub, Google, Gitee, Azure AD). GitHub and Gitee OAuth tokens **double as git credentials**, automatically authenticating `git clone`/`pull` for matching project repos — no manual `GIT_TOKEN` setup needed.

### 45.2 Architecture (CLI-only + Git)

```
User → intellect members login --oauth github
  ├─ Loopback mode: opens browser on localhost
  └─ Device code mode: URL + code, polls endpoint
  → OAuth callback → token exchange → claims + access_token
  → resolve_oauth_member(provider, claims)
  → Write .cli-session.json + bind identity
  → Store access_token → {HOME}/.oauth-tokens/<member>/github.json (0600)
  → Resolve project context (auto-select if single)
  → Git: auto-authenticated for github.com repos
```

### 45.3 Phases

| Phase | Scope | Key deliverables |
|-------|-------|-----------------|
| **OAuth P0** | Config + engine | `members.oauth` config block, `agent/members_oauth.py` (PKCE, state, token exchange, resolution), doctor checks, OAuth token storage dir |
| **OAuth P1** | CLI login + Git | `intellect members login --oauth <provider>` (loopback + device code), invite-in-OAuth-state, provider presets, **OAuth-Git integration for GitHub + Gitee** |
| **OAuth P2** | Identity linking | `intellect members bind --oauth`, `members identities`, API Server OAuth endpoint with project-scoped token option |
| **OAuth P2.5** | More git hosts | GitLab + Azure DevOps OAuth-Git integration |
| **OAuth P3** | Enterprise SSO | Trusted header, WeCom/DingTalk, Gitea/Forgejo |

### 45.4 OAuth-Git credential resolution

When `clone_project_repo()` is called:

1. **OAuth token** — check `{HOME}/.oauth-tokens/<member>/<provider>.json`; match host → use
2. **Project .env** — fallback to `GIT_USERNAME` + `GIT_TOKEN` or `GIT_SSH_KEY`
3. **System git credential helpers** — default chain

| Provider | Git host | OAuth scope | Phase |
|----------|----------|-------------|-------|
| GitHub | `github.com` | `repo` (private) or none (public) | P1 |
| Gitee | `gitee.com` | `projects` (private) or none (public) | P1 |
| GitLab | `gitlab.com`, self-hosted | `read_repository` | P2.5 |
| Azure DevOps | `dev.azure.com` | via Azure AD OAuth | P2.5 |

### 45.5 Leverages existing

- `identities` table → OAuth `sub` → `(oauth:<provider>, <sub>)` → `member_id`
- `member_invites` table → invite code embedded in OAuth `state`
- `.cli-session.json` → written on OAuth success (same as `members login`)
- `agent/project_workspace.py` → `clone_project_repo()` credential resolution chain
- `agent/project_env.py` → `.env` fallback for git credentials
- `MembershipDB` → member CRUD during `resolve_oauth_member`
- `intellect_cli/auth.py` → PKCE helpers reused

---

## 46. DB-Driven RBAC (Plan C) — Detailed Design

> **Status:** Planned. Extends §7 (roles & permissions) with full database-driven RBAC.
> **Based on:** Original spec §7.4 (v2 schema extension reserved) + current implementation.

### 46.1 Motivation

Current authorization is a hard-coded 4-role matrix in `agent/membership.py`:

```python
ROLE_PERMISSIONS = {
    "owner":  {CHAT, READ, PROJECT_CREATE, ...},
    "admin":  {CHAT, READ, PROJECT_CREATE, MEMBER_INVITE, ...},
    "member": {CHAT, READ},
    "guest":  {READ},
}
```

Limitations:
- Cannot create "can read SOUL but cannot see secrets" role
- Cannot scope roles per resource (e.g., "admin of project A, viewer of project B")
- Cannot add/remove roles at runtime (requires code change)
- No audit trail for role changes

### 46.2 Schema (3 new tables + 1 migration)

```sql
-- Custom role definitions
CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,              -- e.g. 'viewer', 'deployer', 'auditor'
    label TEXT NOT NULL,              -- human-readable: "Read-Only Viewer"
    description TEXT,                 -- optional description
    scope_type TEXT NOT NULL,         -- 'profile' | 'team' | 'project'
    is_builtin INTEGER DEFAULT 0,    -- 1 = owner/admin/member/guest (immutable)
    created_at REAL NOT NULL,
    updated_at REAL
);

-- Permission set per role
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    action TEXT NOT NULL,             -- matches Action enum: "project.create", "chat", etc.
    PRIMARY KEY (role_id, action)
);

-- Member → role binding (scoped)
CREATE TABLE IF NOT EXISTS member_role_bindings (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id),
    role_id TEXT NOT NULL REFERENCES roles(id),
    scope_type TEXT NOT NULL,         -- 'profile' | 'team' | 'project'
    scope_id TEXT,                    -- NULL for profile scope, team_id/project_id otherwise
    granted_by TEXT,                  -- member_id of granter
    granted_at REAL NOT NULL,
    UNIQUE(member_id, role_id, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_role_bindings_member
    ON member_role_bindings(member_id);
CREATE INDEX IF NOT EXISTS idx_role_bindings_scope
    ON member_role_bindings(scope_type, scope_id);
```

**Migration:** Add `roles`, `role_permissions`, `member_role_bindings` to SCHEMA_SQL. Bump SCHEMA_VERSION to 16. Seed 4 built-in roles (`owner`, `admin`, `member`, `guest`) on first migration.

### 46.3 Built-in roles (immutable seed)

| Role | Scope | Permissions |
|------|-------|-------------|
| `owner` | `profile` | All actions (CHAT, READ, PROJECT_*, MEMBER_*, API_TOKEN_*, ADMIN) |
| `admin` | `profile` | CHAT, READ, PROJECT_CREATE, PROJECT_MANAGE, MEMBER_INVITE, API_TOKEN_MANAGE |
| `member` | `profile` | CHAT, READ |
| `guest` | `profile` | READ |

Existing `team_memberships.role` and `project_memberships.role` columns remain for backward compatibility. During authorization, the member's DB role bindings are checked first; if none found, fall back to the membership table's `role` field, then to built-in defaults.

### 46.4 Authorization flow (updated)

```python
def authorize(
    *,
    actor_member_id: str,
    action: Action,
    resource: Resource | None = None,
    db: SessionDB,
) -> bool:
    # 1. Profile-level role bindings (always checked)
    profile_roles = db.get_member_roles(actor_member_id, scope_type="profile")
    for role in profile_roles:
        if action in role.permissions:
            return True

    # 2. Resource-scoped role bindings (if resource specified)
    if resource:
        scoped_roles = db.get_member_roles(
            actor_member_id,
            scope_type=resource.type,
            scope_id=resource.id,
        )
        for role in scoped_roles:
            if action in role.permissions:
                return True

    # 3. Fallback: membership table role
    if resource and resource.type == "project":
        m_role = db.get_project_member_role(actor_member_id, resource.id)
        if m_role:
            builtin = BUILTIN_ROLE_PERMISSIONS.get(m_role, set())
            if action in builtin:
                return True

    return False
```

### 46.5 Custom role examples

```yaml
# Config-driven role seeding (loaded on startup, synced to DB if not exists)
members:
  rbac:
    roles:
      - id: viewer
        label: "Read-Only Viewer"
        scope: project
        permissions: [read]
      - id: deployer
        label: "CI/CD Deployer"
        scope: project
        permissions: [read, read_env, read_soul]
      - id: contributor
        label: "Contributor"
        scope: project
        permissions: [chat, read, read_soul]
      - id: maintainer
        label: "Maintainer"
        scope: project
        permissions: [chat, read, read_env, read_soul, write_env, project_manage]
```

### 46.6 CLI commands

| Command | Description |
|---------|-------------|
| `members role list [--scope project\|team\|profile]` | List all roles |
| `members role show <role_id>` | Show role details + permissions |
| `members role create <id> <label> --scope <type> [--permissions p1,p2]` | Create custom role (profile_admin) |
| `members role delete <role_id>` | Delete custom role (profile_admin) |
| `members role grant <member_login> <role_id> --scope <type> [--id <scope_id>]` | Grant role to member |
| `members role revoke <member_login> <role_id> [--scope <type>] [--id <scope_id>]` | Revoke role |
| `members role whoami` | Show current member's effective permissions |

### 46.7 Doctor checks

| Check | Severity | Condition |
|-------|----------|-----------|
| `RBAC_ROLE_NO_PERMISSIONS` | Warning | Custom role with zero permissions |
| `RBAC_BUILTIN_MODIFIED` | Warning | Built-in role permissions changed in config |
| `RBAC_ORPHANED_ROLE` | Info | Role not assigned to any member |

### 46.8 Implementation phases

| Phase | Scope | Est. | Deliverables |
|-------|-------|------|-------------|
| **RBAC P1** | Schema v16 + `agent/rbac.py` + `authorize()` rewrite + built-in seed | M | DB-driven RBAC core, backward compat with existing roles |
| **RBAC P2** | CLI: `role list/show/create/delete/grant/revoke` + config seed sync | M | Full role management via CLI + config YAML |
| **RBAC P3** | Per-resource ACLs, doctor checks, audit log for role changes | S | Granular per-project/per-team role assignment |

### 46.9 Backward compatibility

- Existing `team_memberships.role` and `project_memberships.role` continue to work
- Built-in roles seeded on migration match current hard-coded matrix
- When `members.enabled: false`, RBAC code paths never execute
- `authorize()` signature unchanged — internal resolution changes only

### 46.10 Code touch list

| Area | Files |
|------|-------|
| Schema | `intellect_state.py` (v16 migration, 3 new tables) |
| RBAC engine | `agent/rbac.py` (new) — `authorize()`, role CRUD, binding management, seed logic |
| CLI | `intellect_cli/main.py` — `members role` subcommand group |
| Config | `intellect_cli/config.py` — `members.rbac.roles` block |
| Doctor | `intellect_cli/doctor.py` — 3 RBAC checks |
| Tests | `tests/agent/test_rbac.py` (new) — ~25 tests |
| Existing | `agent/membership.py` — `authorize()` deprecated in favor of `agent/rbac.py` |

### 46.11 Testing strategy

| Test | Count |
|------|-------|
| Built-in roles seeded on migration | 2 |
| Custom role CRUD (create, read, update, delete) | 5 |
| Role-permission assignment | 3 |
| Member-role binding (grant, check, revoke) | 5 |
| Scope isolation (profile vs team vs project) | 4 |
| Fallback to membership table role | 2 |
| Config seed sync | 2 |
| Legacy parity (rbac disabled) | 2 |
| **Total** | **~25 tests** |

---

*End of spec v2.*
