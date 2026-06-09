# Session isolation rollout (agent)

> **Canonical user-facing checklist:** intellect-webui [`docs/session-isolation-rollout.md`](../../intellect-webui/docs/session-isolation-rollout.md)

CLI surface used during rollout:

| Command | Purpose |
|---------|---------|
| `intellect members sessions audit-null` | Count JSON + session-store rows with NULL `member_id` |
| `intellect members sessions migrate-ownership --member-id <hex>` | Stamp legacy sessions (JSON + SQLite/PG via `SessionDB`) |
| `intellect doctor` | Includes session isolation when `members.enabled` |
| `intellect doctor --storage` | PG/Redis HA gates + session isolation |

Implementation: `intellect_cli/members_sessions.py` (`_count_null_member_sessions_in_db`, `_migrate_null_member_sessions_in_db`).
