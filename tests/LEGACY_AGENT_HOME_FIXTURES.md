# Legacy `profiles/` test fixtures (intentional)

During the profile→agent rename, on-disk homes are canonical under
`~/.intellect/agents/<id>/`, with dual-read + migrate from
`~/.intellect/profiles/<id>/`.

Some tests **intentionally** create or assert against `profiles/` to cover
legacy dual-read / migration paths. Do **not** blindly rewrite these to
`agents/` — that would drop regression coverage.

## Keep under `profiles/` (legacy dual-read / migrate)

| Area | Why |
|------|-----|
| `tests/intellect_cli/test_agents_home_migration.py` | Migrates `profiles/` → `agents/` |
| `tests/intellect_cli/test_profiles.py` (`TestLegacyProfilesDualRead`) | Prefer agents, resolve legacy |
| `tests/intellect_cli/test_gateway_service.py` (`TestProfileArg` legacy dir cases) | `_profile_arg` accepts legacy path |
| `tests/gateway/test_multiplex_e2e.py` (legacy serve-set layouts) | `/p/` + legacy homes if present |
| `tests/intellect_cli/test_apply_profile_override.py` (docstrings / path shapes) | Override when home is under profiles |
| Fixtures that only assert **display** strings containing `profiles/` for backward UX | Display / prompt copy |

## Prefer `agents/` (canonical)

New tests and updated fixtures should mkdir `agents/<id>` unless the case is
explicitly about legacy layout.

## Do not touch

- `~/.hindsight/profiles/` (Hindsight memory plugin — unrelated product path)
- `ProviderProfile` / WebUI `user_profile` keys
