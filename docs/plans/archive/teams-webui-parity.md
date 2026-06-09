# Teams WebUI parity (Phase 0–3)

> **Status:** Implemented Jun 2026 — agent `MembershipStore` slug resolution, `authorize_scoped`, WebUI `/api/teams/*`, CLI/gateway join flow.

## Agent

- `Action.TEAM_APPROVE_JOIN` + `authorize_scoped()` / extended `authorize(store=...)`
- `MembershipStore`: `resolve_team_internal_id`, `create_team_for_webui`, `list_teams_brief`, pending join on slug
- `agent/members_teams_webui.py`: `seed_team_tree`, `team_row_for_api`

## WebUI

- `/api/teams` create/list/detail/join/approve/reject/leave/archive/admin/soul/refresh
- Teams panel: leave, archive, SOUL refresh

## CLI / Gateway

- `intellect members teams` uses logged-in member + pending join
- `/join <team>` gateway slash command

## Tests

- `tests/agent/test_team_webui_adapter.py`
- `tests/agent/test_authorize_scoped.py`
- `intellect-webui/tests/test_teams_api.py`
