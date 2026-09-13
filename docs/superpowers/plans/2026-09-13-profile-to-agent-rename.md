# Profile → Agent Rename (B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Full-stack rename of isolation *profile* → *agent* with dual-read compatibility.  
**Architecture:** Canonical on-disk `agents/`, sticky `active_agent`, CLI `intellect agent`, flags `--agent`/`-a`, config `agents.*`, HTTP `/a/`. Legacy `profiles/`, `active_profile`, `intellect profile`, `--profile`/`-p`, `/p/` remain accepted.  
**Tech Stack:** Python CLI (`intellect_cli`), gateway multiplex, WebUI APIs, pytest via `scripts/run_tests.sh`.

---

### Task 1: Path root dual-read + sticky file

**Files:**
- Modify: `intellect_cli/profiles.py` (later shim to `agents_home.py`)
- Modify: `intellect_constants.py` (warnings mentioning profile)
- Test: `tests/intellect_cli/test_profiles.py`

- [ ] Add `_get_agents_root()` returning `<root>/agents`
- [ ] `_get_profiles_root()` becomes alias that prefers `agents/`, falls back to `profiles/`
- [ ] Resolve agent dir: `agents/<id>` else `profiles/<id>`
- [ ] Sticky: read `active_agent` then `active_profile`; write `active_agent`
- [ ] Run focused profile path tests

### Task 2: CLI `intellect agent` + flags

**Files:**
- Modify: `intellect_cli/main.py` (`_apply_profile_override`, parser, `cmd_profile`)
- Modify: `intellect_cli/profile_gate.py` → also read `agents.management_enabled`
- Modify: `intellect_cli/commands/registry.py`, `completion.py`

- [ ] Accept `--agent`/`-a` in `_apply_profile_override` (keep `--profile`/`-p`)
- [ ] Register `agent` subcommand (same handlers as profile)
- [ ] Keep `profile` as deprecated alias
- [ ] Gate checks `agents.management_enabled` with fallback to `profiles.management_enabled`

### Task 3: Config migration

**Files:**
- Modify: `intellect_cli/config.py` (`DEFAULT_CONFIG`, migrate)

- [ ] Add `agents: { management_enabled: false }`
- [ ] On migrate/load: promote `profiles` → `agents` if needed
- [ ] Bump `_config_version` only if transform required

### Task 4: Module rename + shims

**Files:**
- Create: `intellect_cli/agents_home.py` (moved from profiles.py)
- Modify: `intellect_cli/profiles.py` → rebind shim (`sys.modules[__name__] = agents_home`)
- Helpers: `get_agent_dir`, `list_agents`, etc. already aliased

- [x] `git mv` profiles.py → agents_home.py
- [x] Shim re-exports / module identity for patches
- [x] Disk migration `migrate_legacy_agent_homes` + once-per-process latch
- [x] Tests: `test_agents_home_migration.py` + dual-read updates

### Task 5: Gateway `/a/` + status fields

**Files:**
- Modify: `gateway/multiplex_front.py` (or equivalent)
- Modify: `gateway/status.py`, bot_mode roster scanners

- [ ] Route `/a/<name>/` and keep `/p/<name>/`
- [ ] Emit `served_agents` (+ `served_profiles` alias)

### Task 6: WebUI + docs + AGENTS.md

**Files:**
- Modify: `webui/api/profiles.py`, routes
- Modify: `website/docs/user-guide/profiles.md` (redirect/rename)
- Modify: `AGENTS.md` Profiles section

### Task 7: Test sweep

- [ ] Update fixtures using `profiles/` paths
- [ ] `scripts/run_tests.sh tests/intellect_cli/test_profiles.py tests/intellect_cli/test_apply_profile_override.py tests/intellect_cli/test_profile_gate.py -q`
- [ ] Expand to gateway/bot_mode as needed
