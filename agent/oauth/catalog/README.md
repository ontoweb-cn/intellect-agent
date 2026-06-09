# OAuth built-in provider catalog

Human-editable source of truth for providers seeded into `oauth_providers` on first
`intellect setup` / `SessionDB` v19 migration (see
`docs/plans/oauth-db-only-migration-pr-plan.md`).

## Files

| File | Purpose |
|------|---------|
| `builtin_providers.json` | Provider definitions (`schema_version`, `providers[]`) |
| `icons/*.svg` | Brand icons referenced by `icon.path` |

## Review checklist

### Login — core (in current `_seed_oauth_providers` + enterprise)

| `id` | In Python seed today | In this catalog |
|------|---------------------|-----------------|
| `github` | yes | yes |
| `google` | yes | yes |
| `gitee` | yes | yes |
| `azure_ad` | yes | yes |
| `wecom` | **no** | yes (new) |
| `dingtalk` | **no** | yes (new) |
| `feishu` | **no** | yes (new) |

### Login — extended (in `OAUTH_PROVIDER_PRESETS` only)

| `id` | Notes |
|------|-------|
| `gitlab` | Self-hosted URL override via Settings |
| `gitea` | Self-hosted URL override via Settings |
| `azure_devops` | Azure DevOps OAuth |

### Model — core (in current seed)

| `id` | Runtime alias / notes |
|------|----------------------|
| `ontoweb` | ONTOWEB Portal device-code flow |
| `openai_codex` | OpenAI Codex / ChatGPT OAuth |
| `xai` | Auth store id `xai-oauth` |
| `gemini` | Auth store id `google-gemini-cli` |
| `qwen` | Auth store id `qwen-oauth` |

## `enabled_default` policy

| Group | Default | Rationale |
|-------|---------|-----------|
| Public login (github, google, gitee) | `true` | Match current seed (`enabled=1`) |
| Tenant login (azure_ad, wecom, dingtalk, feishu) | `false` | Require admin credentials first |
| Extended login presets | `false` | Opt-in |
| Model providers | `false` | Opt-in via `intellect auth` |

> **Note:** Current `_seed_oauth_providers()` sets `enabled=1` for all rows.
> The catalog reflects the **target** policy after PR-A0.

## Validate locally

```bash
python3 -m json.tool agent/oauth/catalog/builtin_providers.json > /dev/null
```
