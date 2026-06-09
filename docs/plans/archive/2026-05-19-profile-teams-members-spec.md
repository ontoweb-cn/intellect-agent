# Profile Teams & Members — Implementation Spec

> **Status:** Implementation (May 2026). **P1a–P5** complete on branch `v0.3`. User guide, developer internals, `intellect doctor` checks, and breaking-changes notes shipped in P5.  
> **Goal:** Multi-team, multi-member collaboration inside a single Intellect Profile without duplicating `INTELLECT_HOME`.  
> **For implementers:** Use TDD; land in phased PRs (§19). Read `AGENTS.md` invariants (prompt cache, profile-safe paths, no change-detector tests).

---

## 1. Summary

| Layer | Scope | Physical home |
|-------|--------|----------------|
| **Profile** | One agent instance home (`INTELLECT_HOME`) | `~/.intellect` or `~/.intellect/profiles/<name>/` |
| **Team** | Shared context within a profile (SOUL, skills, env, workspace) | `teams/<team_id>/` + `teams` / `team_memberships` tables |
| **Member** | A person (memory, personal SOUL/skills, API tokens) | `members/<member_id>/` + `members` table |
| **Identity** | External account → Member mapping | `identities` table |

**Fixed product decisions:**

1. Profile admin creates teams and appoints team admins; members join multiple teams with team-admin approval; profile admin invites new members to register.
2. Team SOUL is synthesized from active members’ personal SOUL files; team admins may override via `SOUL.override.md`.
3. **Memory follows the member** (`members.memory_scope: member`), not the team.
4. API auth: **one Bearer token per member** (hashed at rest); team context via `X-Intellect-Team`.
5. Group chats: per-participant agent memory via existing `group_sessions_per_user` + `thread_sessions_per_user` (default on when teams enabled).

**Non-goals (v1):** Cross-profile federation; billing/quota; untrusted multi-tenant SaaS; per-team memory stores.

**Mode switch:** Single-user vs multi-team/multi-member is **only** controlled by `config.yaml` (§2). No second code path compiled differently; guards at runtime.

---

## 2. Operating modes (single-user vs multi-user)

### 2.1 Configuration gate (authoritative)

Two independent flags; **both** must be understood:

| `members.enabled` | `members.teams.enabled` | Mode | User-visible behavior |
|-------------------|-------------------------|------|------------------------|
| `false` (default) | ignored | **Legacy single-user** | Identical to today’s Intellect. No `members/` or `teams/` dirs required. |
| `true` | `false` | **Single-member** (optional) | One logical member (`default`); no teams, no invites, no team headers. Useful stepping stone; still uses `members/default/` paths if migrated. |
| `true` | `true` | **Multi-user + multi-team** | Full spec: Identity, teams, approvals, member API tokens, extended `session_key`. |

**Default in `DEFAULT_CONFIG`:** `members.enabled: false`, `members.teams.enabled: false`.

**Explicit opt-in:** Operator sets `members.teams.enabled: true` (and typically `members.enabled: true`) in `config.yaml` or via `intellect config set`. No automatic enablement on upgrade.

### 2.2 Zero-impact guarantee (legacy single-user)

When `members.enabled` is `false`:

| Area | Required behavior |
|------|-------------------|
| `get_intellect_home()` | Unchanged |
| `get_memory_dir()` | Returns `{INTELLECT_HOME}/memories` (today’s path), **not** `members/*/memories` |
| `build_session_key()` | **Byte-identical** output for all existing test fixtures |
| `AIAgent` / `init_agent` | `member_id` / `team_id` omitted; no extra DB lookups |
| Gateway / API / CLI | No `resolve_runtime_context()` call; no `/team`, no member Bearer requirement |
| `state.db` | New tables may exist after migration but are **unread** |
| `API_SERVER_KEY` | Unchanged shared-key semantics |
| Performance | No added latency on hot path (guard is a single config read, optionally cached) |

**Implementation rule:** Every new branch MUST begin with:

```python
if not is_members_enabled(config):  # members.enabled
    return legacy_path(...)
```

For teams-specific behavior, use `is_teams_enabled(config)` (`members.enabled and members.teams.enabled`).

**Tests (mandatory):** CI job or test module `tests/agent/test_membership_legacy_parity.py` asserts fixture parity with flag off (session keys, memory paths, prompt assembly without member dirs).

### 2.3 Enabling multi-user mode (operator)

Recommended flow:

```bash
intellect config set members.enabled true
intellect config set members.teams.enabled true
intellect members bootstrap   # idempotent; see §7.3
```

`intellect members bootstrap` creates DB rows + `members/default` + `teams/default` if missing, runs first-admin logic (§7.3), and prints next steps. Does **not** run on ordinary `intellect chat` unless explicitly invoked or `members.teams.auto_bootstrap: true` (default **false**).

### 2.4 Disabling multi-user mode

Setting `members.teams.enabled: false` (or `members.enabled: false`) immediately restores legacy paths on **next** process start. Data under `members/` and `teams/` is preserved but ignored. Re-enabling picks up existing rows and directories without data loss.

### 2.5 `members.mode` (optional alias, config ergonomics)

Optional read-only derived label for docs/UI (not a second source of truth):

```yaml
members:
  enabled: false
  teams:
    enabled: false
  # Derived: mode = legacy | single_member | multi_team
```

Implement `def members_mode(config) -> Literal["legacy", "single_member", "multi_team"]` in `agent/membership.py` for logging and `intellect doctor`.

---

## 3. Glossary

| Term | Meaning |
|------|---------|
| `member_id` | Stable slug (`alice`), `[a-z0-9][a-z0-9_-]{0,63}` |
| `team_id` | Stable slug (`kitchen`), same pattern |
| `platform` | `telegram`, `discord`, `cli`, `dashboard`, `api_server`, … |
| `external_id` | Platform-native user id or `token:<token_id>` for API |
| `session_key` | Existing gateway session lane id; extended with member + team |
| `RuntimeContext` | Resolved `(member_id, team_id, identities…)` for one agent run |

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Profile (INTELLECT_HOME) — single gateway process typical      │
│  config.yaml · state.db · profile admins · invites             │
│                                                                │
│  teams/kitchen/          teams/dev/                            │
│    SOUL·skills·env       …                                     │
│                                                                │
│  members/alice/          members/bob/                          │
│    memories·SOUL·skills  …                                     │
│    tokens/ (optional)                                          │
│                                                                │
│  DB: teams, members, team_memberships, identities,             │
│      member_api_tokens, invites, sessions(+member_id,team_id)  │
└─────────────────────────────────────────────────────────────┘
         ▲                    ▲
         │ Identity           │ Bearer + X-Intellect-Team
    Telegram/Discord      API Server / CLI / Dashboard
```

**Core invariant:** `get_intellect_home()` and `_apply_profile_override()` are unchanged. Team/member resolution happens **after** profile selection, before `AIAgent` construction.

**Concurrency:** Do not rely on process-global `os.environ["TERMINAL_CWD"]` for multi-member/team gateway concurrency. Use per-agent **env snapshot** (same pattern as cron job scoping in `cron/scheduler.py`).

---

## 5. Filesystem layout

```
{INTELLECT_HOME}/
├── config.yaml                 # members.*, team_bindings, defaults
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
│       ├── home/               # subprocess HOME (see get_subprocess_home)
│       └── .env                # optional personal API key overrides
└── registry/                   # optional JSON cache; source of truth is state.db
```

**Permissions:** `members/*/.env`, `teams/*/.env`, token files `chmod 0600` on write (mirror `gateway/pairing.py`).

---

## 6. Database schema (state.db migration → v12)

Add to `intellect_state.py` migration chain. Bump `SCHEMA_VERSION` to **12**.

```sql
-- Members (people)
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'invited',  -- invited | active | disabled
    profile_role TEXT,                       -- NULL | 'profile_admin'
    created_at REAL NOT NULL,
    updated_at REAL
);

-- Teams
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',   -- active | archived
    created_at REAL NOT NULL,
    updated_at REAL
);

-- Many-to-many with approval
CREATE TABLE IF NOT EXISTS team_memberships (
    team_id TEXT NOT NULL REFERENCES teams(id),
    member_id TEXT NOT NULL REFERENCES members(id),
    role TEXT NOT NULL DEFAULT 'member',     -- member | team_admin
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | active | rejected
    requested_at REAL NOT NULL,
    approved_at REAL,
    approved_by TEXT,                        -- member_id of approver
    PRIMARY KEY (team_id, member_id)
);

CREATE INDEX IF NOT EXISTS idx_team_memberships_member
    ON team_memberships(member_id, status);

-- External identity → member
CREATE TABLE IF NOT EXISTS identities (
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    member_id TEXT NOT NULL REFERENCES members(id),
    created_at REAL NOT NULL,
    metadata TEXT,                           -- JSON: username, label, …
    PRIMARY KEY (platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_identities_member
    ON identities(member_id);

-- Per-member API bearer tokens (store hash only)
CREATE TABLE IF NOT EXISTS member_api_tokens (
    id TEXT PRIMARY KEY,                     -- uuid
    member_id TEXT NOT NULL REFERENCES members(id),
    token_hash TEXT NOT NULL,                -- SHA-256 hex of raw token
    label TEXT,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);

CREATE INDEX IF NOT EXISTS idx_member_api_tokens_hash
    ON member_api_tokens(token_hash) WHERE revoked_at IS NULL;

-- Profile-admin invites
CREATE TABLE IF NOT EXISTS member_invites (
    code TEXT PRIMARY KEY,                   -- or id + code hash
    created_by TEXT NOT NULL,
    max_uses INTEGER,
    use_count INTEGER DEFAULT 0,
    expires_at REAL,
    created_at REAL NOT NULL,
    metadata TEXT                            -- JSON: preassigned member_id, email hint
);

-- Sessions extension (nullable for backwards compat)
-- Applied via ALTER in migration if columns missing:
--   sessions.member_id TEXT
--   sessions.team_id TEXT
--   sessions.gateway_session_key TEXT  -- if not already present elsewhere

CREATE INDEX IF NOT EXISTS idx_sessions_member_team
    ON sessions(member_id, team_id, started_at DESC);
```

**Session meta (optional):** `state_meta` keys `session:<session_key>:team_id` for sticky team per lane (if not fully encoded in `session_key`).

### 6.1 Migration from pre-teams installs

On first run with `members.teams.enabled: true`:

1. Create `members/default` from template; copy existing `memories/` → `members/default/memories/`.
2. Insert `members(id='default', status='active')`.
3. Create `teams/default`; insert membership `(default, default, team_admin, active)`.
4. Backfill `sessions.member_id='default'`, `sessions.team_id='default'` where NULL.
5. First profile admin: follow §7.3 (not automatic implicit admin without explicit rule).

---

## 7. Roles & permissions

| Action | profile_admin | team_admin | member |
|--------|:-------------:|:----------:|:------:|
| Create/archive team | ✓ | — | — |
| Assign team_admin | ✓ | — | — |
| Invite register member | ✓ | — | — |
| Create/revoke own API token | ✓ | ✓ | ✓ |
| Approve team join | — | ✓ (that team) | — |
| Request team join | — | — | ✓ |
| Edit team SOUL override | — | ✓ | — |
| Trigger team SOUL refresh | ✓ | ✓ | — |
| Bind Identity (self) | ✓ | — | ✓ (with code) |
| Bind Identity (others) | ✓ | — | — |
| Disable member | ✓ | — | — |
| List all sessions | ✓ | team-scoped | own only |

Enforcement: central `agent/membership.py` (new module) used by CLI, gateway, API, Dashboard — not duplicated per adapter. All checks go through `authorize(actor, action, resource)` (§7.4) even when v1 only implements three roles.

### 7.3 First profile administrator (bootstrap)

The **first profile admin** must be defined unambiguously to avoid lockout or silent self-promotion.

#### 7.3.1 Precedence (first match wins at bootstrap time)

| Priority | Source | Behavior |
|----------|--------|----------|
| 1 | `members.profile_admins` in `config.yaml` (non-empty list) | Those `member_id`s receive `profile_role='profile_admin'` on bootstrap. Members must exist or are created as `active` stubs. |
| 2 | `members.bootstrap.profile_admin_member_id` | Single explicit id (e.g. `default` or `alice`) promoted to profile admin. |
| 3 | `members.bootstrap.profile_admin_strategy` | See table below (only if no row in `members` has `profile_role='profile_admin'` yet). |
| 4 | **None matched** | Bootstrap **succeeds** for dirs/DB/migration but **no** profile admin exists until one is set manually — CLI prints warning and `intellect doctor` reports `NO_PROFILE_ADMIN`. |

**Never** auto-enable `members.teams.enabled` without operator action.

#### 7.3.2 `profile_admin_strategy` values (v1)

```yaml
members:
  bootstrap:
    profile_admin_strategy: config_only   # recommended default
    # profile_admin_member_id: default    # optional tie-breaker with strategy
```

| Strategy | When used | Who becomes first profile admin |
|----------|-----------|----------------------------------|
| `config_only` | Default | **Only** ids listed in `members.profile_admins` or `profile_admin_member_id`. No implicit promotion. Safest for upgrades. |
| `first_local_operator` | Fresh `intellect members bootstrap` on interactive TTY | OS user running the command → member id `default` (or `members.bootstrap.default_member_id`) + `profile_admin`, and Identity `(cli, <device>)` bound. |
| `first_invite_creator` | First `members invite` on empty admin set | Inviter’s member_id (must already exist as profile_admin **or** inviter becomes admin when creating first invite — **only if** `members.bootstrap.invite_creator_becomes_admin: true`, default **false**). |
| `first_invite_redeemer` | First successful `members redeem` | Redeemer becomes profile admin **only if** no admin exists and strategy is explicitly set (discouraged for production; useful for homelab). |

**Recommended production default:** `config_only` + explicit:

```yaml
members:
  profile_admins:
    - alice
  bootstrap:
    profile_admin_strategy: config_only
```

#### 7.3.3 Scenarios

**A. Legacy install → enable teams later**

1. Operator sets `members.teams.enabled: true`.
2. Runs `intellect members bootstrap`.
3. Migration copies `memories/` → `members/default/memories/`.
4. If `profile_admins: [default]` or `profile_admin_member_id: default` → `default` is admin.
5. Otherwise operator must run:  
   `intellect members promote-profile-admin default`  
   (profile_admin-only command; **break-glass**: documented env `INTELLECT_BREAK_GLASS_PROFILE_ADMIN=default` for one-shot TTY only, logged to `errors.log`).

**B. Greenfield multi-user Profile**

1. `intellect profile create family`.
2. Edit `config.yaml`: `members.teams.enabled: true`, `profile_admins: [parent]`.
3. `intellect members bootstrap` → creates `members/parent/`, admin role.
4. `intellect members invite` for other family members (parent is admin).

**C. First human touches CLI before config lists admins**

1. `profile_admin_strategy: first_local_operator` on homelab only.
2. `intellect members bootstrap` on TTY → `default` + admin + CLI identity.

#### 7.3.4 Invariants

- At most one bootstrap path may assign admins per run; idempotent re-run does not demote existing admins.
- `intellect doctor` checks: if `teams.enabled` and zero `profile_role='profile_admin'` → **warn**.
- Team admins are **not** profile admins unless also listed in `profile_admins` or promoted.

### 7.4 RBAC forward compatibility (v1 → v2)

v1 ships a **fixed role matrix** (profile_admin, team_admin, member). The implementation MUST remain extensible to full RBAC without schema breakage.

#### 7.4.1 Design principles

1. **No scattered role string checks** in gateway/CLI — only `agent/membership.authorize(actor, action, resource)`.
2. **Actions are stable string constants** (e.g. `team.approve_join`, `member.invite`, `session.list_all`).
3. **Resources are typed** (`team:<id>`, `member:<id>`, `session:<id>`).
4. v1 roles map to **permission sets** in code; v2 can move sets to DB without changing call sites.

#### 7.4.2 v1 API (implement now)

```python
# agent/membership.py

class Action(str, Enum):
    TEAM_CREATE = "team.create"
    TEAM_APPROVE_JOIN = "team.approve_join"
    MEMBER_INVITE = "member.invite"
    MEMBER_DISABLE = "member.disable"
    SESSION_LIST_ALL = "session.list_all"
    # ...

def authorize(
    *,
    actor_member_id: str,
    action: Action,
    resource: Resource | None = None,
    db: MembershipDB,
) -> bool:
    """v1: hardcoded role matrix. v2: load grants from DB."""
```

#### 7.4.3 v2 schema extension (reserved, do not implement in v1)

```sql
-- Future RBAC (document only)
CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,           -- e.g. 'profile_admin', 'team_editor', custom
    scope TEXT NOT NULL,           -- 'profile' | 'team'
    description TEXT
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL,
    action TEXT NOT NULL,          -- matches Action enum / registry
    PRIMARY KEY (role_id, action)
);

CREATE TABLE IF NOT EXISTS member_role_bindings (
    member_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,      -- 'profile' | 'team'
    scope_id TEXT,                 -- NULL for profile scope, team_id for team scope
    PRIMARY KEY (member_id, role_id, scope_type, scope_id)
);
```

v1 columns **`members.profile_role`** and **`team_memberships.role`** remain; v2 migration can map:

- `profile_role='profile_admin'` → bind `roles.profile_admin` at profile scope
- `team_memberships.role='team_admin'` → bind `roles.team_admin` at team scope

#### 7.4.4 Custom roles (post-v1)

Feasible without breaking installs:

- Add roles via YAML `members.rbac.roles[]` or DB seed.
- `authorize()` checks bindings first, then falls back to v1 matrix for built-in roles.
- Tool/slash gating (`gateway/slash_access.py`) can later call same `authorize()` with `action=slash.run`.

#### 7.4.5 Explicit non-goals for v1 RBAC

- Per-resource ACLs (single session visible to user X only).
- Attribute-based conditions (time-of-day, IP).
- External IdP groups sync.

These fit the same `authorize()` hook later.

---

## 8. Configuration (`config.yaml`)

```yaml
members:
  enabled: false                 # DEFAULT: legacy single-user (see §2)
  memory_scope: member           # only supported value in v1

  teams:
    enabled: false               # DEFAULT: no teams until explicit opt-in
    auto_bootstrap: false        # if true, run bootstrap on gateway start (not recommended)
    default_team: default
    group_sessions_per_user: true  # only applied when teams.enabled
    thread_sessions_per_user: true

  profile_admins: []             # explicit list; preferred for production
  bootstrap:
    profile_admin_strategy: config_only   # §7.3.2
    profile_admin_member_id: null         # optional single id
    default_member_id: default            # legacy migration target
    invite_creator_becomes_admin: false

  registration:
  # Profile admin only — creates Member row + invite
    invite_ttl_hours: 168

  soul:
    team_merge: hybrid          # synthesized | manual | hybrid (see §10)
    synthesize_max_chars: 4000

  cwd:
    default: personal           # personal | team — which workspace wins if both exist

  api:
    require_team_header: auto   # auto | always | never
    # auto: require X-Intellect-Team when member has >1 active team

  # Optional: map messaging chats to teams
  team_bindings:
    telegram:
      "-1001234567890": kitchen   # group chat_id → team_id
    discord:
      "guild:channel": dev
```

**Feature off:** See §2.2 — `members.enabled: false` is the primary off switch; `teams.enabled: false` keeps legacy session/memory paths even if `members.enabled: true` (single-member mode).

---

## 9. Identity

### 9.1 Purpose

Map `(platform, external_id)` → `member_id` so Telegram `12345`, Discord snowflake, CLI device id, and API token id resolve to the same person.

### 9.2 Resolution order

1. **API Server:** `Authorization: Bearer` → hash → `member_api_tokens` → `member_id`; identity row `(api_server, token:<id>)` for audit.
2. **CLI / Dashboard:** session file `{INTELLECT_HOME}/.cli-session.json` → `{ member_id, device_id }` → identity `(cli|dashboard, device_id)`.
3. **Messaging:** `SessionSource.user_id` (+ `user_id_alt` if needed) → lookup `identities`.
4. If unknown and invite/pairing flow active → register or reject.
5. Else if `members.default_member` bootstrap for single-user legacy — **only when `members.teams.enabled` false**.

### 9.3 Binding flows

| Flow | Steps |
|------|--------|
| Profile invite | Admin `intellect members invite` → code → user redeems → create `members/<id>/` + optional first Identity |
| IM pairing | Extend `gateway/pairing.py` or parallel `members/pairing/` → on approve, `INSERT identities` |
| Link existing | `intellect members bind --member alice --platform telegram --external-id 12345` (profile_admin or verified code) |
| API token | `intellect members token create` → show once → store hash |

**Never** auto-merge two external ids without an admin binding action.

---

## 10. Team resolution

After `member_id` is known:

```python
def resolve_team_id(
    *,
    member_id: str,
    source: SessionSource | None,
    headers: dict,
    session_key: str | None,
    config: dict,
    db: SessionDB,
) -> str:
    # 1. Explicit header (API / Dashboard)
    if t := headers.get("X-Intellect-Team", "").strip():
        require_active_membership(member_id, t)
        return t

    # 2. Chat binding (groups/channels)
    if source and (t := lookup_team_binding(config, source)):
        require_active_membership(member_id, t)
        return t

    # 3. Sticky meta for this session_key
    if session_key and (t := db.get_session_team(session_key)):
        require_active_membership(member_id, t)
        return t

    # 4. CLI --team or .active_team file
    if t := read_cli_active_team():
        ...

    # 5. Single active team → use it
    teams = db.list_active_teams(member_id)
    if len(teams) == 1:
        return teams[0].id

    # 6. default_team if member is member of it
    if default := config["members"]["teams"].get("default_team"):
        if db.membership_active(member_id, default):
            return default

    raise TeamRequiredError("Specify team: X-Intellect-Team header or /team <id>")
```

**Gateway slash commands:** `/team <id>`, `/teams` (list active + pending), store sticky team on session meta.

---

## 11. Team SOUL

### 11.1 Modes (`teams/<id>/team.yaml`)

```yaml
soul:
  mode: hybrid    # synthesized | manual | hybrid
  synthesize:
    trigger: membership_change   # membership_change | manual | cron
    include: active_members_only
```

| mode | Injected team SOUL |
|------|-------------------|
| `manual` | `SOUL.md` only (admin-maintained) |
| `synthesized` | `SOUL.generated.md` (from member SOULs) |
| `hybrid` | `SOUL.override.md` if exists, else generated, else `SOUL.md` |

### 11.2 Synthesis job

- **Input:** For each `team_memberships.status=active`, read `members/<id>/SOUL.md` (skip missing).
- **Worker:** `agent/auxiliary_client.py` task `teams.soul_synthesize` (new auxiliary task key).
- **Output:** `teams/<id>/SOUL.generated.md` + update `SOUL.md` symlink or copy for convenience.
- **Triggers:** membership approved/removed; `intellect teams soul refresh <team_id>`.

### 11.3 System prompt assembly (stable tier)

Order (concatenate with `\n\n`):

1. `profile/SOUL.md` (optional short preamble)
2. Team SOUL (per §11.1)
3. `members/<member_id>/SOUL.md`

**Cache invariant:** Still one frozen system prompt per **session**; team/member are fixed for the session lifetime. Changing `/team` mid-session requires `/new` or documented cache break (same as toolset changes).

---

## 12. Resource merge rules

### 12.1 Memory (member only)

```python
def get_memory_dir(member_id: str) -> Path:
    return get_intellect_home() / "members" / member_id / "memories"
```

- Built-in `MemoryStore`: pass `member_id` into `load_from_disk()`.
- Plugins: `MemoryManager.initialize_all(..., user_id=f"member:{member_id}")` via `memory_provider_user_id()` in `agent/agent_init.py` — **do not** include `team_id` in memory scope.

**External provider parity (v1, May 2026):** Core passes `user_id` to every plugin on init. Plugins must use it for all reads/writes when `members.enabled` is true. Current status:

| Provider | Member-scoped when `members.enabled`? | v1 storage / identity key |
|----------|--------------------------------------|---------------------------|
| Honcho | Yes | `kwargs["user_id"]` → `runtime_user_peer_name` (`member:<id>`) |
| Mem0 | Yes | `kwargs["user_id"]` |
| Hindsight | Yes | `kwargs["user_id"]` |
| RetainDB | Yes | `kwargs["user_id"]` |
| Supermemory | **No** (gap) | `container_tag` + `agent_identity` (profile name only) |
| ByteRover | **No** (gap) | `{INTELLECT_HOME}/byterover/` (profile-wide tree) |
| Holographic | **No** (gap) | `{INTELLECT_HOME}/memory_store.db` (or config path; profile-wide) |
| OpenViking | **No** (gap) | `OPENVIKING_USER` / env defaults (`default`); `initialize()` ignores `user_id` |

**Operator impact:** With multiple members on one profile, the four gap providers may **share** external storage until fixed. Builtin `MEMORY.md` / `USER.md` and Honcho/Mem0/Hindsight/RetainDB remain member-isolated.

**Follow-up (post-v1):** See §24 — wire `user_id` / `member:{id}` (or member-scoped on-disk paths under `members/<id>/`) for Supermemory, ByteRover, Holographic, OpenViking; add `intellect doctor` warnings when `members.enabled` + gap provider active; Honcho file migration should use `get_memory_dir(member_id)` not legacy `memories/`.

### 12.2 Skills

Scan order (later wins on name conflict):

1. `{INTELLECT_HOME}/skills/`
2. `teams/<team_id>/skills/`
3. `members/<member_id>/skills/`

`skill_manage` writes default to `members/<member_id>/skills/` unless `--team <id>` (team_admin only).

### 12.3 API keys & auth

Merge for agent env snapshot (first hit wins unless `members.api.merge: prefer_personal`):

1. `members/<member_id>/.env`
2. `teams/<team_id>/.env`
3. Profile `.env`
4. `auth.json` / credential pool (future: per-member pool id in `member.yaml`)

### 12.4 Terminal cwd

| `members.cwd.default` | Resolved cwd |
|----------------------|--------------|
| `personal` | `members/<id>/workspace` (mkdir) → fallback profile `terminal.cwd` |
| `team` | `teams/<team_id>/workspace` → fallback personal |

Set on **agent subprocess env** only for that run.

### 12.5 Subprocess HOME

Prefer `members/<member_id>/home/` if directory exists; else profile `home/`; else `get_subprocess_home()` today.

---

## 13. Session keys

Extend `build_session_key()` in `gateway/session.py`:

```python
def build_session_key(source, ..., member_id: str | None = None, team_id: str | None = None) -> str:
    base = _existing_logic(source, ...)
    parts = [base]
    if member_id:
        parts.append(f"member:{member_id}")
    if team_id:
        parts.append(f"team:{team_id}")
    return ":".join(parts)
```

When `members.teams.enabled`, force `group_sessions_per_user=True` and `thread_sessions_per_user=True` regardless of legacy config (document breaking change behind flag).

Persist `member_id`, `team_id`, `gateway_session_key` on `sessions` row at creation.

---

## 14. RuntimeContext & agent creation

### 14.1 Dataclass

```python
@dataclass(frozen=True)
class RuntimeContext:
    member_id: str
    team_id: str
    platform: str
    external_id: str | None
    session_key: str
    env_snapshot: dict[str, str]   # merged env for this run
    terminal_cwd: str
    subprocess_home: str | None
```

### 14.2 Resolution entrypoint

New: `agent/runtime_context.py`

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
    session_key = build_session_key(source, ..., member_id=member_id, team_id=team_id)
    env_snapshot = build_env_snapshot(member_id, team_id, config)
    terminal_cwd = resolve_terminal_cwd(member_id, team_id, config)
    subprocess_home = resolve_subprocess_home(member_id)
    return RuntimeContext(...)
```

### 14.3 AIAgent wiring

Extend `AIAgent.__init__` / `init_agent()` with:

- `member_id: str | None = None`
- `team_id: str | None = None`

Pass through to:

- `MemoryStore(member_id=...)`
- `MemoryManager.initialize_all(..., user_id=f"member:{member_id}")`
- `build_system_prompt_parts()` for SOUL merge
- Skills discovery helper

**Gateway** (`gateway/run.py`): replace ad-hoc user_id threading with `resolve_runtime_context()` before agent spawn.

**API Server** (`gateway/platforms/api_server.py`): replace single `API_SERVER_KEY` auth with member token table; deprecate anonymous multi-user on shared key (keep profile key only for migration window → log warning).

---

## 15. API Server changes

### 15.1 Authentication

| Header | Required | Purpose |
|--------|----------|---------|
| `Authorization: Bearer <token>` | Yes | Member token |
| `X-Intellect-Team` | If multi-team | Team context |
| `X-Intellect-Session-Id` | No | Transcript continuation |
| `X-Intellect-Session-Key` | No | Long-term plugin session scope (existing) |

Token format: `imt_<random>` (prefix for grep-ability). Store `SHA-256(token)` in `member_api_tokens`.

### 15.2 Capabilities document

Extend `GET /v1/capabilities`:

```json
{
  "auth": { "type": "member_bearer", "team_header": "X-Intellect-Team" },
  "members": { "teams": true, "memory_scope": "member" }
}
```

### 15.3 Admin HTTP (Dashboard only, localhost or authenticated)

Under `/api/members/*`, `/api/teams/*` — see §16. Not on OpenAI `/v1/*` surface.

---

## 16. CLI commands

New group: `intellect members` and `intellect teams` (in `intellect_cli/main.py` + handlers module `intellect_cli/members.py`).

### 16.1 Members

| Command | Description |
|---------|-------------|
| `members bootstrap` | Idempotent: dirs, default member/team, migration; first-admin per §7.3 |
| `members promote-profile-admin <id>` | Profile admin only; break-glass env documented in §7.3.3 |
| `members invite [--member-id ID] [--ttl 168h]` | Profile admin: create invite code |
| `members redeem <code> [--member-id]` | Complete registration |
| `members list` | List members (role, status) |
| `members show <id>` | Details + identities (redacted) |
| `members disable <id>` | Profile admin |
| `members bind --member M --platform P --external-id E` | Admin bind identity |
| `members login <member_id>` | CLI session + identity |
| `members token create [--label L]` | Print bearer once |
| `members token list` | List token ids (not secrets) |
| `members token revoke <id>` | Revoke |

### 16.2 Teams

| Command | Description |
|---------|-------------|
| `teams create <team_id> [--name] [--admin MEMBER ...]` | Profile admin |
| `teams list` | All teams |
| `teams show <team_id>` | Members + pending |
| `teams join <team_id>` | Request membership |
| `teams leave <team_id>` | Active member |
| `teams approve <team_id> <member_id>` | Team admin |
| `teams reject <team_id> <member_id>` | Team admin |
| `teams admin add/remove <team_id> <member_id>` | Profile admin |
| `teams soul refresh <team_id>` | Run synthesis |
| `teams soul edit <team_id>` | Edit override in `$EDITOR` |

### 16.3 Global flags

- `intellect chat --member alice --team kitchen` (also `-M`/`-T` if short flags available)
- Sticky: `{INTELLECT_HOME}/.active_member`, `.active_team` (optional)

---

## 17. Gateway slash / IM commands

| Command | Who | Action |
|---------|-----|--------|
| `/team <id>` | Member | Set sticky team for session_key |
| `/teams` | Member | List active + pending teams |
| `/join <team_id>` | Member | Request membership |
| `/members` | Team admin | Pending approvals (MVP text list) |

Register in `intellect_cli/commands.py` with `gateway_only` as appropriate.

---

## 18. Dashboard

**Implemented (P4 + dashboard teams UI, May 2026).** Requirements vs shipped behavior:

| # | Requirement | Shipped |
|---|-------------|---------|
| 1 | Map browser session → `member_id` | Member picker in header; cookies `intellect_dashboard_member` / `intellect_dashboard_team`. Full password/OAuth login deferred. |
| 2 | Team switcher → API/PTY context | Header team dropdown from `GET /api/members/me/teams` (active memberships only); `X-Intellect-Team` on REST; PTY `member`/`team` query params → `INTELLECT_MEMBER` / `INTELLECT_TEAM`. |
| 3 | Admin: invite / approve | **`/members`**: invite + redeem + API tokens. **`/teams`**: join, create team, pending approvals, set active team. |
| 4 | API tokens UI | **`/members` page**: create/list/revoke via `/api/members/tokens`. |
| 5 | Multi-team gate | Chat overlay when `requires_team_selection` and no active team cookie. |
| 6 | Dashboard team resolution | `attach_runtime_context` uses `resolve_member_team_id(for_dashboard=True)` — no CLI `.active_team` fallback. |

**Backend:** `intellect_cli/dashboard_members_api.py` registers `/api/members/*` and `/api/teams/*`. `intellect_cli/web_server.py` auth middleware accepts member cookie **or** member bearer (`imt_*`); attaches `RuntimeContext` to request state. `agent/dashboard_session.py` owns cookie names.

**Tests:** `tests/intellect_cli/test_dashboard_members_api.py`, PTY member env in `tests/intellect_cli/test_web_server.py::TestPtyWebSocket`.

---

## 19. Implementation phases & PRs

| PR | Scope | Est. | Status |
|----|--------|------|--------|
| **P1a** | Schema v12, `agent/membership.py` CRUD, `is_members_enabled` / `is_teams_enabled`, `authorize()` stub, migration, `members bootstrap` | M | Done (`v0.3`) |
| **P1b** | `RuntimeContext`, `get_memory_dir(member)`, MemoryStore/Manager | M | Done |
| **P1c** | `build_session_key` + sessions columns + gateway resolve (read-only team default) | M | Done |
| **P2a** | Teams CRUD CLI, memberships approve/join, directories | M | Done |
| **P2b** | SOUL merge + synthesis job | M | Done |
| **P2c** | Skills triple-scan, cwd/env snapshot | M | Done |
| **P3a** | Member API tokens + api_server auth breaking change (changelog) | L | Done |
| **P3b** | Identity bind + pairing integration + `/team` commands | M | Done |
| **P4** | Dashboard team switcher + invite/approve UI | L | Done (`0072b5582`+) |
| **P5** | Docs, doctor checks, website user guide (§23) | S | Done |

**M/L/S = relative size.** Each PR must include tests and remain green with `members.teams.enabled: false`.

---

## 20. Code touch list (primary)

| Area | Files |
|------|--------|
| Schema | `intellect_state.py` |
| Membership API | `agent/membership.py` (new), `agent/runtime_context.py` (new) |
| Agent init | `agent/agent_init.py`, `agent/system_prompt.py`, `run_agent.py` |
| Memory | `tools/memory_tool.py`, `agent/memory_manager.py` |
| Skills | `tools/skills_tool.py`, `agent/skill_commands.py` |
| Session key | `gateway/session.py` |
| Gateway | `gateway/run.py`, `gateway/config.py` |
| API | `gateway/platforms/api_server.py` |
| Pairing | `gateway/pairing.py` or `gateway/members_pairing.py` |
| CLI | `intellect_cli/main.py`, `intellect_cli/members.py` (new), `intellect_cli/commands.py` |
| Config | `intellect_cli/config.py` (`DEFAULT_CONFIG` bump — no version bump unless migration needed) |
| Dashboard API | `intellect_cli/dashboard_members_api.py`, `agent/dashboard_session.py` |
| Dashboard UI | `intellect_cli/web_server.py`, `web/src/contexts/MemberContext.tsx`, `web/src/components/MemberTeamBar.tsx`, `web/src/pages/MembersPage.tsx`, `web/src/lib/api.ts` |
| Tests | `tests/agent/test_membership*.py`, `tests/agent/test_members_*.py`, `tests/gateway/test_members_runtime.py`, `tests/gateway/test_api_server_members_auth.py`, `tests/intellect_cli/test_dashboard_members_api.py`, `tests/test_intellect_state.py::TestSchemaV12Members`, … |

---

## 21. Testing strategy

- **Unit:** `resolve_member_id`, `resolve_team_id`, SOUL merge order, env merge order, token hash auth.
- **Integration:** gateway message → correct `session_key` + DB rows; api_server bearer + team header.
- **Migration:** temp `INTELLECT_HOME` with legacy `memories/` only → upgrade → `members/default`.
- **Invariants:** With `members.enabled: false`, byte-identical session keys and memory paths (§2.2).
- **Bootstrap:** First-admin precedence and lockout warnings (§7.3).
- **RBAC:** `authorize()` unit tests per action; v1 matrix only.
- **No change-detector tests** on team/member counts.

Run: `scripts/run_tests.sh tests/agent/test_membership.py -q` (per PR).

---

## 22. Security notes (family/small-team model)

- Shared gateway process: members are mutually trusted for host filesystem; isolation is logical (memory/SOUL/cwd), not kernel-level.
- Member tokens are secrets equal to API keys; chmod 0600, never log raw token.
- `X-Intellect-Team` without active membership → 403.
- Deprecate shared `API_SERVER_KEY` for multi-user; document single-user local dev exception with warning.
- Dashboard on `0.0.0.0` requires member auth (not only ephemeral SPA token).

---

## 23. Documentation deliverables

- User guide: `website/docs/user-guide/features/teams-and-members.md`
- Developer: `website/docs/developer-guide/teams-members-internals.md`
- CHANGELOG breaking: API server auth, session_key format when flag enabled
- `AGENTS.md` § Profiles: link to teams spec

---

## 24. Deferred (post-v1)

- Full RBAC UI and custom roles (schema reserved in §7.4.3)
- Per-member credential pool slices in UI
- Team-archived automatic session migration
- OAuth/login providers for Dashboard
- `memory` tool tags / team-scoped **views** without separate files
- Federation across profiles
- Rate limits per member
- **Memory provider member scoping (§12.1 parity table):** Supermemory, ByteRover, Holographic, and OpenViking do not yet honor `MemoryManager.initialize_all(..., user_id=member:<id>)`. Post-v1 work:
  - **Supermemory:** derive `container_tag` (or per-member container) from `kwargs["user_id"]` when present; keep `{identity}` template for profile-only installs.
  - **ByteRover:** scope `_get_brv_cwd()` to `members/<member_id>/byterover/` (or pass member into brv CLI env) when members enabled.
  - **Holographic:** default `db_path` to `members/<member_id>/memory_store.db` when members enabled; document override in config.
  - **OpenViking:** map `kwargs["user_id"]` → Viking `user` (and optionally `account`) on init and every write tool; document `OPENVIKING_USER` override for single-user.
  - **Honcho:** one-time migration path should call `get_memory_dir(member_id)` instead of `{INTELLECT_HOME}/memories`.
  - **Doctor:** warn when `memory.provider` is a gap provider and `members.enabled` is true with more than one active member.
  - **Tests:** extend `tests/agent/test_memory_user_id.py` or provider-specific tests asserting `member:alice` ≠ `member:bob` storage for each fixed plugin.

---

## 25. Resolved decisions (formerly open)

| Question | Decision |
|----------|----------|
| Single-user compatibility | **§2** — `members.enabled: false` default; legacy parity tests |
| First profile admin | **§7.3** — `config_only` + explicit `profile_admins`; break-glass documented |
| Memory per team? | **No** — member only |
| `/new` clears team sticky? | **No** |
| RBAC | **§7.4** — `authorize()` hook + v2 tables reserved |
| Archived team sessions | Read-only; reject new messages |

---

## 26. Example end-to-end

1. Profile admin: `intellect members invite` → code `FAM-2026`.
2. Bob redeems → `members/bob/`, Identity `cli:device-1`.
3. Admin: `intellect teams create kitchen --admin alice`.
4. Bob: `intellect teams join kitchen` → pending.
5. Alice: `intellect teams approve kitchen bob`.
6. Synthesis runs → `teams/kitchen/SOUL.generated.md`.
7. Bob Telegram (bound): message in family group (`team_bindings` → `kitchen`).
8. Gateway: Identity → `bob`, team → `kitchen`, session_key `...:member:bob:team:kitchen`.
9. Agent loads kitchen SOUL + bob SOUL; memory from `members/bob/memories/`.
10. Open WebUI: Bob’s bearer + `X-Intellect-Team: kitchen` → same memory, new transcript unless session header set.

---

*End of spec.*
