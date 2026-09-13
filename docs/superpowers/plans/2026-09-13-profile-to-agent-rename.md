# Profile → Agent Rename (B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Full-stack rename of isolation *profile* → *agent* with dual-read compatibility.  
**Architecture:** Canonical on-disk `agents/`, sticky `active_agent`, CLI `intellect agent`, flags `--agent`/`-a`, config `agents.*`, HTTP `/a/`. Legacy `profiles/`, `active_profile`, `intellect profile`, `--profile`/`-p`, `/p/` remain accepted.  
**Tech Stack:** Python CLI (`intellect_cli`), gateway multiplex, WebUI APIs, pytest via `scripts/run_tests.sh`.  
**Branch:** `rename/profile-to-agent`  
**Status:** Tasks 1–8 complete. Strategy A: canonical names are written; legacy
paths/columns/keys stay readable long-term (no DB column drop, no unmounting of
old APIs).

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
- [x] Broader bot_mode / gateway e2e path audit — covered by Task 8

### Out of scope (design follow-ups — do not block B)

- DROP-ing the legacy `profile` column (deliberately kept; dual-read forever)
- `ProviderProfile` / `user_profile` / `.hindsight/profiles/`

---

### Task 8: Kanban DB column + WebUI REST + bot_mode/gateway dispatch — DONE

Compatibility strategy **A**: canonical names are what new code writes; legacy
names stay readable long-term and the legacy DB column is never dropped.

**Kanban `task_runs.profile` → `agent`** (`intellect_cli/kanban_db.py`)

- [x] `SCHEMA_SQL` `task_runs` gains `agent TEXT` (and `_REBUILD_SPECS` stays in sync — `test_rebuilt_schema_matches_fresh` guards this)
- [x] `_migrate_add_optional_columns`: ADD `agent` + one-shot `UPDATE … SET agent = profile`; only copies when the column was just added, so an existing `agent` is never clobbered
- [x] All four INSERT sites (backfill, `_synthesize_ended_run`, `claim_task`, `claim_review_task`) write **both** columns
- [x] `Run` dataclass + `Run.from_row` dual-read either column
- [x] `build_worker_context` reads `run.agent or run.profile`; role-history SQL uses `COALESCE(r.agent, r.profile) = ?`
- [x] Consumers emit both keys: `intellect_cli/kanban.py` (`show`/`runs` JSON + text), `tools/kanban_tools.py`; `webui/api/kanban_bridge.py` picks up `agent` via `asdict`
- [x] Tests: backfill, no-clobber, `from_row` dual-read, dual-write on claim

**WebUI REST** (`webui/api/routes.py`) — canonical `/api/agents` + `/api/agent/*`

- [x] GET `/api/agents` (aliases `/api/profiles`), payload emits `agents` + `profiles`
- [x] GET `/api/agent/active` (aliases `/api/profile/active`)
- [x] POST `/api/agent/{switch,create,delete}` (alias `/api/profile/*`); create returns `agent` + `profile`
- [x] User-visible strings say “agent” (both spellings routed; CSRF gate unchanged)
- [x] Tests: `tests/webui/test_agents_api_alias.py`

**bot_mode / gateway dispatch**

- [x] `tools/bot_mode_dm.py` spawns `-a <target>` (canonical) instead of `-p`
- [x] `webui/api/gateway_lifecycle.py` passes `--agent` and recognises the `agents/` parent dir (was `profiles/`-only)
- [x] `tests/gateway/test_bot_mode_e2e.py` fixture uses `agents/`
- [x] `/a/` e2e coverage: `tests/gateway/test_multiplex_front.py` (parse + HTTP + 404) and `tests/gateway/test_multiplex_e2e.py` (real supervisor, key isolation both prefixes)
- [x] `tests/tools/test_bot_relay.py`: stale `/p/`-prefix assertion corrected (relay targets the peer URL verbatim) + canonical `/a/` prefix pass-through test

**Verification**

```bash
scripts/run_tests.sh tests/intellect_cli/test_kanban_db_init.py \
  tests/intellect_cli/test_kanban_core_functionality.py \
  tests/gateway/test_multiplex_front.py tests/gateway/test_multiplex_e2e.py \
  tests/gateway/test_bot_mode_e2e.py tests/webui/test_agents_api_alias.py \
  tests/webui/test_gateway_lifecycle_and_wakeup.py tests/tools/test_bot_relay.py

# Opt-in real-process e2e (runner blanks env):
INTELLECT_BOT_MODE_E2E=1 ./venv/bin/python -m pytest tests/gateway/test_bot_mode_e2e.py -q
INTELLECT_MULTIPLEX_E2E=1 ./venv/bin/python -m pytest tests/gateway/test_multiplex_e2e.py -q
```

### Verification gate (before calling B complete)

```bash
scripts/run_tests.sh \
  tests/intellect_cli/test_profiles.py \
  tests/intellect_cli/test_apply_profile_override.py \
  tests/intellect_cli/test_profile_gate.py \
  tests/intellect_cli/test_agents_home_migration.py \
  tests/intellect_cli/test_completion.py
```

---

### Post-review reconciliation (strategy A)

A code review of Task 8 raised ten findings. Disposition, so the judgment calls
are not re-litigated:

**Fixed**

- `switch_profile` returned only ``profiles``, so ``POST /api/agent/switch`` was
  the one canonical endpoint with no canonical key while GET/create emitted
  both — a migrated client got ``undefined`` from ``data.agents`` on a
  *successful* switch. Now returns ``agents`` + ``profiles``.
- The ``agent`` backfill is no longer gated on "this call added the column":
  the ``ALTER`` commits on its own, so a crash between ADD and UPDATE (or a row
  written by an older binary afterwards) left ``agent IS NULL`` forever. Now a
  NULL-guarded repair behind a read-only probe.
- ``tools/bot_mode_dm.py`` interpolated the model-supplied ``target`` into a
  shell line unquoted while every sibling argument used ``_shlex.quote``.
- The WebUI **client** still called the legacy routes: 9 fetch sites in
  ``panels.js``/``boot.js`` moved to ``/api/agents`` + ``/api/agent/*``, with
  reads via ``_agentList()`` (canonical key, legacy fallback). The canonical
  surface now has real callers, not just mounted aliases.
- ``tests/webui/test_agents_api_alias.py`` gained a **real-dispatch** test
  (real serializer, real ``j``, JSON body parsed) plus switch coverage. The
  rest of that module mocks ``j``, which is why it asserted ``is True``: the
  real ``j`` returns ``None`` and the server's contract is ``False → 404``.

**Rejected**

- **A canonical error vocabulary on the multiplex wire.** A change was drafted
  that mirrored ``ERROR_UNREADY``-style codes by string surgery
  (``unknown_profile`` → ``unknown_agent``) and emitted ``agent``/``agent_code``
  beside ``profile``/``code``. Reverted: it invents wire vocabulary no consumer
  asked for, and the mapping is synthesized rather than derived from a client.
  ``_error_response`` keeps ``code``/``profile`` only. ``/multiplex/status``
  keeps its ``agents``/``profiles`` dual key — that one is a renamed *field*,
  not an invented name.
- **Reconciling ``task_runs`` physical column order.** On an upgraded board
  ``agent`` is last (``ALTER`` appends) while a fresh board has it 4th. Every
  in-tree reader is name-based, so the divergence is invisible; normalizing it
  means rewriting the table on upgrade for a hypothetical positional reader.
  Accepted and documented at the migration site instead: read ``task_runs`` by
  name, never by ``SELECT *`` ordinal.

**Latent, left alone** — two independent ``Run`` fields can disagree only for an
out-of-tree writer; ``Run``'s ``agent`` field sits mid-dataclass so positional
construction shifts (no in-tree callers); the dual JSON keys double-encode each
payload, which is the deliberate cost of strategy A.

---

### Known remaining legacy naming (deliberately NOT in Task 8)

Scoped out of this round; each is its own follow-up.

- **Kanban CLI assignment vocabulary.** `intellect kanban assign <profile>`,
  `--assignee`/`--new-assignee` help strings (`intellect_cli/kanban.py:440,
  455, 460, 662, 690, 721`), and the swarm spec field `workers[].profile`
  (`intellect_cli/kanban_swarm.py:108,164`). These describe
  ``tasks.assignee`` — the *assignment target* — which Task 8 intentionally
  left alone (`tasks` has no ``profile`` column; only ``task_runs`` did).
- **Dropping the legacy `task_runs.profile` column.** Strategy A keeps it
  forever; there is no scheduled removal.
- `ProviderProfile`, WebUI `user_profile`, `.hindsight/profiles/` — unrelated
  namespaces, never in scope.
