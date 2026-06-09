# Spec Change List: Multi-Project Extension (v2)

> **Base spec:** `docs/plans/2026-05-19-profile-teams-members-spec.md` (v1, P1a–P5 complete)
> **Target spec:** `docs/plans/2026-05-31-profile-teams-members-projects-spec-v2.md` (v2, this change list)
> **Date:** 2026-05-31
> **Purpose:** Enable parallel workstreams by clearly identifying every changed, added, and deleted section.

---

## Change Summary

| Category | Count |
|----------|-------|
| Modified sections (§1–§26) | 17 |
| New sections (§27–§44) | 18 |
| Deleted sections | 0 |
| New database tables | 3 (`projects`, `project_memberships`, `project_teams`) |
| New database columns | 3 (`sessions.project_id`, `sessions.project_workspace`, `secret_access_log.*`) |
| New CLI command group | 1 (`intellect projects`, 18 subcommands) |
| New API endpoints | 16 (under `/api/projects/*`) |
| New Python modules | 5 (`agent/projects.py`, `intellect_cli/projects.py`, `intellect_cli/project_env.py`, `agent/project_workspace.py`, `agent/project_secrets.py`) |
| New Dashboard pages | 2 (ProjectsPage, ProjectSettingsPage) |
| New config keys | ~15 (under `members.projects.*`) |
| Schema version bump | v12 → v13 |

---

## Section-by-Section Changes

### §1 Summary

| Change | Detail |
|--------|--------|
| **MODIFY** | Add "Project" row to Layer/Scope/Physical home table |
| **MODIFY** | Add bold markers for v2 product decisions (items 5–6) |
| **ADD** | New §1.1 "Team vs Project: Conceptual Distinction" comparison table |

**Files affected:** Spec only (no code).
**Breaking:** No.

---

### §2 Operating Modes

| Change | Detail |
|--------|--------|
| **MODIFY** | Configuration gate table: add `members.projects.enabled` column, two new rows (multi-team+multi-project, solo projects) |
| **MODIFY** | Extended guard condition: `is_projects_enabled(config)` = `members.enabled and members.projects.enabled` |
| **ADD** | §2.3 enabling multi-project mode (new `intellect projects bootstrap` step) |
| **MODIFY** | Config error detection: `projects.enabled: true` + `members.enabled: false` → doctor error |

**Files affected:** `intellect_cli/config.py` (DEFAULT_CONFIG), `agent/membership.py` (add `is_projects_enabled`).
**Breaking:** No. New flag defaults to `false`.

---

### §3 Glossary

| Change | Detail |
|--------|--------|
| **ADD** | `project_id` term definition |
| **MODIFY** | `session_key` definition: add "**project**" to extension list |
| **MODIFY** | `RuntimeContext` definition: add `project_id` |

**Files affected:** Spec only (no code).
**Breaking:** No.

---

### §4 Architecture

| Change | Detail |
|--------|--------|
| **MODIFY** | Architecture diagram: add `projects/web-app/` and `projects/mobile-app/` blocks |
| **MODIFY** | DB list: add `project_memberships`, `project_teams` |
| **MODIFY** | Annotation: add `X-Intellect-Project` to API Server arrow |
| **ADD** | Resolution order note: Member → Team → Project |

**Files affected:** Spec only (documentation diagram).
**Breaking:** No.

---

### §5 Filesystem Layout

| Change | Detail |
|--------|--------|
| **ADD** | `projects/` directory tree with `_template/`, `<project_id>/`, `project.yaml`, `SOUL.md`, `SOUL.generated.md`, `CONVENTIONS.md`, `skills/`, `.env`, `workspace/` |
| **MODIFY** | Permissions note: add `projects/*/.env` to chmod 0600 requirement |

**Files affected:** `intellect_cli/profiles.py` (_PROFILE_DIRS, if project dirs are auto-created), `agent/projects.py` (new — directory bootstrap).
**Breaking:** No. Directories only created when `projects.enabled: true`.

---

### §6 Database Schema

| Change | Detail |
|--------|--------|
| **MODIFY** | Schema version: v12 → **v13** |
| **ADD** | `projects` table (id, display_name, description, status, repo_url, default_team_id, timestamps) |
| **ADD** | `project_memberships` table (project_id, member_id, role, status, timestamps, approver) |
| **ADD** | `project_teams` table (project_id, team_id, created_at) |
| **ADD** | Indexes: `idx_project_memberships_member`, `idx_project_teams_team` |
| **ADD** | §6.1 Sessions extension: `sessions.project_id TEXT`, `sessions.project_workspace TEXT`, index `idx_sessions_project` |
| **ADD** | §6.2 Migration from pre-project installs (3-step migration) |

**Files affected:** `intellect_state.py` (SCHEMA_VERSION bump, migration v13, new table creation).
**Breaking:** No. New tables are additive; existing queries unchanged.

---

### §7 Roles & Permissions

| Change | Detail |
|--------|--------|
| **MODIFY** | Permissions matrix: add `project_admin` column, 7 new action rows (Create/archive project, Assign project_admin, Approve project join, Request project join, Edit project SOUL, Trigger project SOUL refresh, Link project to team, Manage project .env) |
| **ADD** | §7.4 RBAC extensions: new `Action` enum values (PROJECT_CREATE, PROJECT_APPROVE_JOIN, PROJECT_EDIT_SOUL, PROJECT_LINK_TEAM, PROJECT_MANAGE_ENV) |
| **MODIFY** | v2 RBAC schema example: add project-scoped role binding example |

**Files affected:** `agent/membership.py` (new Action enum values, authorize() project cases).
**Breaking:** No. New actions, existing actions unchanged.

---

### §8 Configuration

| Change | Detail |
|--------|--------|
| **ADD** | `members.projects` config block (enabled, auto_bootstrap, default_project, require_project_header, workspace_mode) |
| **ADD** | `members.soul.project_merge` key (manual | generated | hybrid) |
| **MODIFY** | `members.cwd.default` options: add `project` |
| **ADD** | `members.api.require_project_header` key (auto | always | never) |
| **ADD** | `members.project_bindings` block (chat → project mapping, same pattern as team_bindings) |

**Files affected:** `intellect_cli/config.py` (DEFAULT_CONFIG additions, ~15 new keys with defaults).
**Breaking:** No. All new keys default to off/disabled.

---

### §9 Identity

| Change | Detail |
|--------|--------|
| **ADD** | Note about project-scoped API tokens (future). Identity resolution unchanged. |

**Files affected:** None for v1. Project tokens deferred to P10.
**Breaking:** No.

---

### §10 Team + Project Resolution

| Change | Detail |
|--------|--------|
| **MODIFY** | Section title: "Team resolution" → "Team + Project resolution" |
| **ADD** | §10.2 Project resolution function (`resolve_project_id`) with 8-step resolution order |
| **ADD** | Key design decision callout: project context is optional |

**Files affected:** `agent/runtime_context.py` (add `resolve_project_id` function), `gateway/session.py` (project resolution integration).
**Breaking:** No. `resolve_team_id` unchanged.

---

### §11 SOUL Assembly

| Change | Detail |
|--------|--------|
| **MODIFY** | Section title: "Team SOUL" → "SOUL assembly (extended)" |
| **ADD** | §11.2 Project SOUL concept, modes table (manual/generated/hybrid), generate sources config |
| **MODIFY** | System prompt assembly order: add Project SOUL at position 3 (after team, before member) |

**Files affected:** `agent/system_prompt.py` (add project SOUL to assembly order), `agent/auxiliary_client.py` (new task `projects.soul_generate`).
**Breaking:** No. Project SOUL only injected when project context active.

---

### §12 Resource Merge Rules

| Change | Detail |
|--------|--------|
| **ADD** | §12.1 Project-level curated memory note (CONVENTIONS.md, not separate memory store) |
| **MODIFY** | §12.2 Skills scan order: add `projects/<project_id>/skills/` at position 3 (after team, before member) |
| **MODIFY** | §12.3 API keys merge order: add `projects/<project_id>/.env` at position 3 |
| **MODIFY** | §12.4 Terminal cwd table: add `project` row → `projects/<project_id>/workspace` |

**Files affected:** `tools/skills_tool.py` (quadruple scan), `agent/agent_init.py` (env merge), `run_agent.py` (cwd resolution).
**Breaking:** No. Extra scan directories only added when project context active.

---

### §13 Session Keys

| Change | Detail |
|--------|--------|
| **MODIFY** | `build_session_key()` signature: add `project_id: str | None = None` |
| **MODIFY** | Key format: add `:project:<id>` suffix when project_id present |
| **ADD** | Cache invariant note: project change requires `/new` |

**Files affected:** `gateway/session.py` (`build_session_key` extension).
**Breaking:** **Conditional.** Session keys with project suffix differ from keys without. This only affects sessions where a project is active. Legacy sessions (no project) have identical keys.

---

### §14 RuntimeContext

| Change | Detail |
|--------|--------|
| **MODIFY** | Dataclass: add `project_id: str \| None` and `project_workspace: str \| None` fields |
| **MODIFY** | Resolution entrypoint: add `project_id` resolution call, extend `build_env_snapshot` and `resolve_terminal_cwd` signatures |
| **MODIFY** | AIAgent wiring: add `project_id` and `project_workspace` parameters |

**Files affected:** `agent/runtime_context.py` (dataclass + resolution), `run_agent.py` (AIAgent.__init__ signature), `gateway/run.py` (gateway resolve call).
**Breaking:** **Conditional.** AIAgent signature gains 2 optional parameters. All existing callers pass `None` (backward compatible).

---

### §15 API Server

| Change | Detail |
|--------|--------|
| **ADD** | `X-Intellect-Project` header to auth table (optional) |
| **MODIFY** | Capabilities document: add `project_header` and `projects: true` |

**Files affected:** `gateway/platforms/api_server.py` (header parsing, capabilities response).
**Breaking:** No. New header is optional.

---

### §16 CLI Commands

| Change | Detail |
|--------|--------|
| **ADD** | §16.3 Projects command group (18 commands: bootstrap, create, list, show, archive, join, leave, approve, reject, admin add/remove, link-team, unlink-team, soul refresh, soul edit, env set/unset/list, clone) |
| **MODIFY** | §16.4 Global flags: add `--project` / `-P` flag, `.active_project` sticky file |

**Files affected:** `intellect_cli/main.py` (new subcommand group registration), `intellect_cli/projects.py` (new module, ~800 lines), `intellect_cli/project_env.py` (new module).
**Breaking:** No. New commands only.

---

### §17 Gateway Slash Commands

| Change | Detail |
|--------|--------|
| **ADD** | 3 new slash commands: `/project <id>`, `/projects`, `/join-project <project_id>` |

**Files affected:** `intellect_cli/commands.py` (register new commands), `gateway/run.py` (command handlers).
**Breaking:** No.

---

### §18 Dashboard

| Change | Detail |
|--------|--------|
| **ADD** | 5 new requirements (items 7–11): project switcher, project context in chat, project admin page, env vars UI, SOUL editor |
| **ADD** | Cookie: `intellect_dashboard_project` |

**Files affected:** `web/src/components/MemberTeamBar.tsx` → rename/refactor, `web/src/pages/ProjectsPage.tsx` (new), `web/src/lib/api.ts` (new endpoints), `intellect_cli/dashboard_members_api.py` (new project endpoints), `agent/dashboard_session.py` (new cookie).
**Breaking:** UI only. Backend APIs additive.

---

### §19 Implementation Phases

| Change | Detail |
|--------|--------|
| **ADD** | 6 new PRs (P6a–P10) with scope, estimates, and status |
| **MODIFY** | Phase table: add rows for project PRs |

**Files affected:** Spec only (planning).
**Breaking:** No.

---

### §20 Code Touch List

| Change | Detail |
|--------|--------|
| **MODIFY** | Table: add **bold** entries for 7 new/renamed files, 5 new Python modules, 6 new test files |

**Files affected:** Spec only (planning reference).
**Breaking:** No.

---

### §21 Testing Strategy

| Change | Detail |
|--------|--------|
| **ADD** | 4 new test categories (unit, integration, migration, invariants for projects) |

**Files affected:** New test files.
**Breaking:** No.

---

### §22 Security Notes

| Change | Detail |
|--------|--------|
| **ADD** | 4 project-specific security notes (.env sensitivity, membership controls, logging prohibition, git credentials) |
| **ADD** | Cross-reference to §39–§42 (secrets management) |

**Files affected:** Spec only (guidance). Implementation: `agent/project_env.py` (chmod 0600 enforcement).
**Breaking:** No.

---

### §23–§25 Docs, Deferred, Resolved Decisions

| Change | Detail |
|--------|--------|
| **ADD** | §25.1 "New resolved decisions (v2)" with 6 project decisions |
| **MODIFY** | §23 Documentation: add 2 new docs, 2 updates |
| **MODIFY** | §24 Deferred: add project-related deferred items |

**Files affected:** Spec only.
**Breaking:** No.

---

### §26 Example End-to-End

| Change | Detail |
|--------|--------|
| **MODIFY** | Extend from 10 steps to 14 steps, adding project creation, join, approval, clone, and API usage with project header |

**Files affected:** Spec only.
**Breaking:** No.

---

## New Sections (§27–§44)

| Section | Title | Description | Est. implementation impact |
|---------|-------|-------------|---------------------------|
| §27 | Project membership lifecycle | State machine, join flows, leave/remove | Medium — mirrors team membership code |
| §28 | Project workspace & git integration | Workspace modes, git clone, CONVENTIONS.md auto-discovery | Medium — new git operations module |
| §29 | Project SOUL generation | Input sources, worker task, triggers | Small — reuses team SOUL generation patterns |
| §30 | API endpoints | 16 REST endpoints + extended member context | Large — new API surface |
| §31 | Doctor checks | 7 new `intellect doctor` checks | Small — additive checks |
| §32 | `project.yaml` reference | Complete config file specification | Spec only |
| §33 | Teams + Projects interaction | Default project per team, visibility modes, routing | Medium — cross-entity logic |
| §34 | Migration path for existing teams | Upgrade steps for teams→teams+projects | Documentation + bootstrap logic |
| §35 | Cross-profile project references | Deferred future work | None (deferred) |
| §36 | Performance considerations | Query count, caching strategy | Spec only (implementation guidance) |
| §37 | Backward compatibility summary | 6 scenarios with behavior | Spec only (testing guidance) |
| §38 | Documentation deliverables | 4 doc items | Documentation work |
| §39 | Bitwarden alignment analysis | Conceptual mapping, 5 gaps identified, adoption priorities | Spec/RFC — minimal code (P10) |
| §40 | Project-scoped API tokens | Token types, schema, auth flow, security constraints | Medium — extends existing token system |
| §41 | Secret access audit log | Schema, retention, privacy | Small — new table + logging calls |
| §42 | Future external secrets backend | Pluggable backend protocol, config, migration path | Spec only (architecture guidance) |
| §43 | Teams vs Projects quick reference | Comparison table | Spec only |
| §44 | Implementation recommendations | Phase ordering, design principles | Spec only |

---

## Workstream Assignments (Suggested)

### Workstream A: Core Data Layer (P6a) ✅ **DONE (2026-05-31)**

**Delivered:**
- `intellect_state.py`: SCHEMA_VERSION 14→15, 4 sessions columns, 9 new tables, 15 indexes, v15 migration block
- `agent/membership.py` (new): `is_members_enabled/is_teams_enabled/is_projects_enabled`, `Action` enum (10 values), `ROLE_PERMISSIONS` (4 roles), `authorize()`, `Resource` dataclass, `MembershipDB` class
- `agent/projects.py` (new): `get_projects_home/get_project_dir/ensure_project_dirs`, `ProjectDB(MembershipDB)` with full CRUD
- `intellect_cli/config.py`: `members.*` config block, `_KNOWN_ROOT_KEYS` updated
- `intellect_cli/main.py`: `_BUILTIN_SUBCOMMANDS` + "members", `intellect members projects bootstrap|list` subcommands
- `intellect_cli/profiles.py`: `_PROFILE_DIRS` + "teams", + "projects"
- `intellect_constants.py`: `get_teams_dir()`, `get_projects_dir()`
- **Tests:** 63 tests across 11 test classes, zero regressions on existing suite

**Files:** `intellect_state.py`, `agent/membership.py`, `agent/projects.py`, `intellect_cli/config.py`, `intellect_cli/main.py`, `intellect_cli/profiles.py`, `intellect_constants.py`
**Verified:** 63/63 new tests pass, 229/229 existing state tests pass (0 regressions)

### Workstream B: Runtime & Resolution (P6b)
**Files:** `agent/runtime_context.py`, `gateway/session.py`, `gateway/run.py`
**Changes:** RuntimeContext extension, resolve_project_id, session key extension, gateway integration
**Dependencies:** Workstream A (schema)
**Estimated:** 2–4 days

### Workstream C: CLI & Commands (P7a)
**Files:** `intellect_cli/projects.py` (new), `intellect_cli/main.py`, `intellect_cli/commands.py`
**Changes:** 18 CLI subcommands, slash commands, global flags
**Dependencies:** Workstream A (CRUD)
**Estimated:** 4–6 days

### Workstream D: SOUL, Skills & Env (P7b + P8a)
**Files:** `agent/system_prompt.py`, `tools/skills_tool.py`, `agent/agent_init.py`, `intellect_cli/project_env.py` (new)
**Changes:** Project SOUL assembly, quadruple skills scan, env merge, env management CLI
**Dependencies:** Workstream A (schema), Workstream B (resolution)
**Estimated:** 4–6 days

### Workstream E: Git Workspace (P8b)
**Files:** `agent/project_workspace.py` (new), `run_agent.py`
**Changes:** Git clone/pull, CONVENTIONS.md discovery, workspace resolution
**Dependencies:** Workstream A (schema)
**Estimated:** 3–4 days

### Workstream F: Dashboard (P9) — **REMOVED**
Dashboard has been removed from the project. All project management is via CLI.

### Workstream G: Secrets & Audit (P10) ✅ **DONE (2026-05-31)**
**Files:** `agent/project_secrets.py` (new), `intellect_state.py` (secret_access_log table)
**Changes:** Project-scoped tokens, secret access audit log, pluggable backend protocol
**Dependencies:** Workstream A (schema)
**Estimated:** 3–5 days

### Workstream H: Docs & Tests
**Files:** Test files for all workstreams, website docs
**Changes:** Unit/integration/migration tests, user guide, developer docs
**Dependencies:** Respective workstreams
**Estimated:** Ongoing, parallel with all workstreams

---

## Breaking Changes Audit

| Change | Severity | Mitigation |
|--------|----------|------------|
| Session key format with project suffix | **Low** — only when project context active; legacy sessions unchanged | Feature flag gated; `projects.enabled: false` produces identical keys |
| AIAgent.__init__ new parameters | **None** — optional parameters with None default | All existing callers pass `project_id=None` implicitly |
| Schema v13 new tables | **None** — additive, no existing table modifications | Additive migration; v12 tables untouched |
| Skills scan order change | **Low** — one additional directory in scan order | Only when project context active; name conflicts resolved same way |
| Config keys nesting change | **None** — all new keys under `members.projects.*` with safe defaults | Defaults are `false`/`null`/`[]` |

**Conclusion:** No breaking changes for existing users. All new functionality is feature-flag gated behind `members.projects.enabled: true`.

---

## Files With No Changes Required

These files from the original spec's code touch list (§20) require **no modification** for projects:

| File | Reason |
|------|--------|
| `gateway/pairing.py` | Unchanged — projects don't affect identity binding |
| `intellect_logging.py` | Unchanged — projects use existing logging infrastructure |
| `intellect_constants.py` | Unchanged — `get_intellect_home()` unchanged |
| `tools/memory_tool.py` | Unchanged — memory follows member, not project |
| `agent/memory_manager.py` | Unchanged — memory scope unchanged |
| `tools/environments/*` | Unchanged — terminal backends use cwd from RuntimeContext |

---

## Risk Areas

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Feature creep: projects become "teams v2" | Medium | High | Clear §1.1 distinction table; projects are work contexts, teams are collaboration contexts |
| Session key explosion with all three dimensions | Low | Medium | Session key max length ~300 chars; SQLite TEXT handles this fine |
| Project .env security | Medium | High | chmod 0600, audit log, never log values, pluggable backend for future Vault/Bitwarden |
| Git workspace credential leakage | Medium | High | Credentials in project .env only; never in project.yaml; never in logs |
| Dashboard complexity with 3 context dimensions | Medium | Medium | Member→Team→Project progressive disclosure in header; project optional |
| Overlapping team/project skills causing confusion | Low | Low | Clear scan order documented; `skill_manage` requires explicit `--team` or `--project` flag |

---

*End of change list.*

---

## Post-v2 Changes (2026-06-03)

### Session Isolation (multi-user hardening)

| Change | Detail |
|--------|--------|
| **ADD** | §12.7 Session isolation (spec v2) — `member_id` column + filtering + backward compat |
| **ADD** | `agent/session_visibility.py` — `SessionListScope`, `resolve_session_list_scope()`, `session_row_visible()` |
| **MODIFY** | `intellect_state.py` — `list_sessions_rich(member_id=?)` + `get_session(actor_member_id=?)` |
| **MODIFY** | `gateway/platforms/api_server.py` — `GET /api/sessions` passes `member_id` from token |
| **MODIFY** | `gateway/run.py` — `/resume` resolves member context |
| **MODIFY** | `cli.py`, `run_agent.py`, `tui_gateway/server.py` — member_id from `.cli-session.json` |

**Files affected:** `agent/session_visibility.py` (NEW), `intellect_state.py`, `api_server.py`, `gateway/run.py`, `cli.py`, `run_agent.py`, `tui_gateway/server.py`
**Breaking:** No (NULL member_id = legacy, single-user unchanged)
**Commits:** `54a6a5499`, `27c9bdcde`
