# Profile → Agent Rename (B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Full-stack rename of isolation *profile* → *agent* with dual-read compatibility.  
**Architecture:** Canonical on-disk `agents/`, sticky `active_agent`, CLI `intellect agent`, flags `--agent`/`-a`, config `agents.*`, HTTP `/a/`. Legacy `profiles/`, `active_profile`, `intellect profile`, `--profile`/`-p`, `/p/` remain accepted.  
**Tech Stack:** Python CLI (`intellect_cli`), gateway multiplex, WebUI APIs, pytest via `scripts/run_tests.sh`.  
**Branch:** `rename/profile-to-agent`  
**Status:** Tasks 1–7 core complete; optional polish listed under Task 6/7.

---

### Task 1: Path root dual-read + sticky file — DONE

**Files:** `intellect_cli/agents_home.py`, `intellect_constants.py`, `tests/intellect_cli/test_profiles.py`

- [x] Add `_get_agents_root()` returning `<root>/agents`
- [x] `_get_profiles_root()` returns canonical `agents/` (legacy via `_get_legacy_profiles_root`)
- [x] Resolve agent dir: `agents/<id>` else `profiles/<id>`
- [x] Sticky: read `active_agent` then `active_profile`; write `active_agent`
- [x] Run focused profile path tests

### Task 2: CLI `intellect agent` + flags — DONE

**Files:** `intellect_cli/main.py`, `intellect_cli/agent_gate.py` (+ `profile_gate` shim), completion

- [x] Accept `--agent`/`-a` in `_apply_profile_override` (keep `--profile`/`-p`)
- [x] Register `agent` subcommand (same handlers as profile)
- [x] Keep `profile` as deprecated alias
- [x] Gate checks `agents.management_enabled` OR `profiles.management_enabled`
- [x] `profile_gate` → `agent_gate` + shim

### Task 3: Config migration — DONE

**Files:**
- Modify: `intellect_cli/config.py` (`DEFAULT_CONFIG`, `migrate_config`)
- Test: `tests/intellect_cli/test_config.py` (`TestAgentsConfigPromotion`)

- [x] Add `agents: { management_enabled: false }` to `DEFAULT_CONFIG` (legacy `profiles` kept)
- [x] On migrate (`_config_version` 25 → 26): promote `profiles.*` → `agents.*` when needed
- [x] Bump `_config_version` to 26 for that transform
- [x] Test: migrate promotes; asserts version equals `DEFAULT_CONFIG["_config_version"]` (not a frozen literal alone)

### Task 4: Module rename + shims — DONE

- [x] `git mv` profiles.py → agents_home.py
- [x] Shim re-exports / module identity for patches
- [x] Disk migration `migrate_legacy_agent_homes` + once-per-process latch
- [x] Tests: `test_agents_home_migration.py` + dual-read updates

### Task 5: Gateway `/a/` + status fields — DONE

- [x] Route `/a/<name>/` and keep `/p/<name>/`
- [x] Emit `served_agents` (+ `served_profiles` alias)
- [x] Service units / PID scan accept `--agent` (legacy `--profile` still matched)

### Task 6: WebUI + docs + AGENTS.md — MOSTLY DONE

**Done:**
- [x] WebUI API: agents root, dual-read paths, sticky `active_agent`
- [x] EN + zh-Hans `user-guide/profiles.md` + `reference/profile-commands.md` terminology
- [x] `AGENTS.md` agents/homes section
- [x] Remaining high-traffic docs: faq, cli-commands, profile-distributions, multiplex, open-webui, api-server, teams-and-members (+ zh-Hans mirrors for faq/cli/teams)
- [x] WebUI user-visible “Profiles vs workspaces” → “Agents vs workspaces” (panel ids / API paths unchanged)
- [x] CLI user-facing strings in `cmd_agent`

**Optional later (not blocking B):**
- [x] Rename doc slug `profiles.md` → `agents.md` + redirect stubs
- [x] Exhaustive zh-Hans / skill SKILL.md path sweeps
- [x] WebUI i18n keys `profile_*` → `agent_*` (aliases via `I18N_AGENT_HOME_ALIASES`)

### Task 7: Test sweep — MOSTLY DONE

**Done:** focused suites for profiles, gate, migration, completion, container_boot, ProfileArg, systemd preflight stubs; key fixtures prefer `agents/` (file_safety, file_operations, bot_mode_roster, update_check).

**Optional later:**
- [x] Document intentional legacy `profiles/` fixtures (`tests/LEGACY_AGENT_HOME_FIXTURES.md`)
- [ ] Broader bot_mode / gateway e2e path audit (optional)

**Status:** Core B rename is functionally complete; leftover items are polish / optional.

### Out of scope (design follow-ups — do not block B)

- Kanban DB column rename `profile` → `agent`
- Renaming WebUI REST `/api/profiles` paths
- `ProviderProfile` / `user_profile` / `.hindsight/profiles/`

### Verification gate (before calling B complete)

```bash
scripts/run_tests.sh \
  tests/intellect_cli/test_profiles.py \
  tests/intellect_cli/test_apply_profile_override.py \
  tests/intellect_cli/test_profile_gate.py \
  tests/intellect_cli/test_agents_home_migration.py \
  tests/intellect_cli/test_completion.py
```
