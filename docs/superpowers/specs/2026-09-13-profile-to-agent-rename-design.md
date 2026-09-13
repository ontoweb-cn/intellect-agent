# Design: Profile → Agent rename (option B)

**Date:** 2026-09-13  
**Branch:** `rename/profile-to-agent`  
**Status:** approved for implementation (user chose B)

## Goal

Rename the **isolation unit** formerly called *profile* to *agent* across disk layout, CLI, config, gateway routing, docs, and tests — matching the product positioning: single-owner multi-agent identity/state isolation.

## Non-goals (do not rename)

- `ProviderProfile` / model-provider plugins
- `agent/` package, `AIAgent`, `run_agent.py`, subagents / `delegate_task`
- WebUI `user_profile` (personal account UI)
- Kanban DB column named `profile` *may* stay as storage column with API/docs saying “agent”, or be migrated in a follow-up — prefer dual-name in APIs first

## Canonical names

| Surface | Old | New | Compat |
|---------|-----|-----|--------|
| Disk dir | `~/.intellect/profiles/<id>/` | `~/.intellect/agents/<id>/` | Dual-read; auto-migrate on access when safe |
| Sticky file | `active_profile` | `active_agent` | Read old if new missing; write new |
| CLI command | `intellect profile` | `intellect agent` | Old command remains as deprecated alias |
| Flags | `-p` / `--profile` | `-a` / `--agent` | Old flags still accepted |
| Config | `profiles.*` | `agents.*` | Migrate on load; write new |
| HTTP prefix | `/p/<name>/` | `/a/<name>/` | Accept both |
| Status field | `served_profiles` | `served_agents` | Emit both during transition |
| Module | `intellect_cli/profiles.py` | `intellect_cli/agents_home.py` | Shim re-exports from old path |

## Isolation rules unchanged

- One process ↔ one `INTELLECT_HOME` (agent home)
- Multiplex = supervisor + per-agent children
- Not multi-user; not a filesystem sandbox

## Migration behavior

1. If `agents/<id>` exists → use it.
2. Else if `profiles/<id>` exists → treat as that agent home (and optionally rename/move into `agents/` when `agents/` is empty of conflicts).
3. Prefer **move** (same filesystem) when creating the new tree and legacy-only dirs exist; never delete without move success.
4. Config: if `agents` missing and `profiles` present → copy into `agents` section and bump `_config_version` as needed.

## Risks

- Name collision with runtime “agent” in docs — mitigate with “agent home” / “agent instance” in developer docs where ambiguous.
- `-a` may collide with other CLIs; strip only when used as Intellect global flag (same pattern as `-p`).
- Large test surface (~370 files) — fix by updating helpers first, then bulk path strings.
