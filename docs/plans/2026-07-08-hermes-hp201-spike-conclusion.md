# Hermes HP-201 — CLI / Gateway completion reflux spike conclusion

> Date: 2026-07-08 | Gates: HP-202 implementation

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | CLI completion transport | **Dedicated drain** via `async_delegation.drain_completion_notifications()` in `cli._process_loop` (idle + post-turn), same cadence as `process_registry.drain_notifications()` |
| 2 | Agent running during completion | **Queue** synthetic text to `_pending_input`; do not interrupt mid-tool |
| 3 | Fan-out merge | **Single merged synthesis turn** per drain batch; cap at `delegation.max_merged_completions` (default 3) |
| 4 | Gateway path | **`MessageEvent(internal=True)`** + `adapter.handle_message()` — never `_pending_messages` alone |
| 5 | Fallback | If gateway metadata missing (plain CLI), auto-drain only; user can always `/delegations list` |

## HP-202 interface constraints

- Rust `DelegationRegistry.drain_completions(parent_session_key)` is the source of truth.
- Python formats `[IMPORTANT: Background delegation …]` prompts (mirrors terminal notify).
- Gateway registers `pending_delegation_watchers` at spawn; `_run_delegation_watcher` polls every 5s.
