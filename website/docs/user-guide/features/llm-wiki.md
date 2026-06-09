---
sidebar_position: 6
title: "LLM Wiki & Vault"
description: "Scoped markdown knowledge bases, Quartz Vault browsing in Intellect WebUI, and Global wiki contribution review"
---

# LLM Wiki & Vault

Intellect ships a bundled **[llm-wiki](/docs/user-guide/skills/bundled/research/research-llm-wiki)** skill that builds and maintains a **Karpathy-style LLM Wiki** — a directory of interlinked markdown files the agent compiles over time. Unlike one-shot RAG retrieval, the wiki **compounds**: cross-links, contradictions, and synthesis persist across sessions.

In **Intellect WebUI**, the dedicated **Wiki** rail tab lets you browse every scoped wiki as a **Quartz Vault** site, trigger builds, initialize missing wikis, and (for admins) review member contributions to the organization-wide Global wiki.

:::tip Skill vs feature page
This page covers **product behavior** (paths, WebUI, permissions, Vault). The full agent instructions live in the bundled skill reference: [Llm Wiki](/docs/user-guide/skills/bundled/research/research-llm-wiki).
:::

## How it differs from Memory and RAG

| System | What it stores | Best for |
|--------|----------------|----------|
| **[Persistent Memory](memory.md)** | Short curated notes in `MEMORY.md` / `USER.md` | Preferences, environment facts, user model |
| **[RAG Providers](rag-providers.md)** | External document corpora with vector retrieval | PDFs, manuals, large static libraries |
| **LLM Wiki** | Agent-maintained markdown pages + immutable `raw/` sources | Research notes, entity pages, evolving synthesis |

All three can run together. The wiki is plain markdown on disk — open it in Obsidian, VS Code, or any editor.

## Scoped wiki paths

When [Teams, Projects & Members](teams-and-members.md) is enabled, each chat session resolves a **single active wiki** from context. The agent runtime injects these variables before tool calls:

| Variable | Meaning |
|----------|---------|
| `WIKI_PATH` | Intended wiki directory (may not exist yet) |
| `WIKI_SCOPE` | `project` \| `team` \| `member` \| `global` |
| `WIKI_SCOPE_ID` | Team/project slug or member id; empty for global |
| `WIKI_WRITE_MODE` | `read_write` or `read_only` |

**Resolution order** (default session — not an explicit global target):

1. **Project** — `$INTELLECT_HOME/projects/{slug}/wiki/`
2. **Team** — `$INTELLECT_HOME/teams/{slug}/wiki/` (no active project)
3. **Member** — `$INTELLECT_HOME/members/{id}/wiki/` (logged in, no team/project)
4. **Legacy global** — `~/wiki` or `skills.config.wiki.path` (single-user mode only)

**Organization (Global) wiki** — `$INTELLECT_HOME/wiki/global/`:

- **Everyone can read** (Vault + agent reads).
- **Only `owner` and `admin` can write** directly. Ordinary members get `WIKI_WRITE_MODE=read_only` when the resolved scope is global.
- **Team and project wikis** are read/write for all members who can access that team or project (v1).

Directories use **intended paths** — missing folders are normal until the first init or write.

:::info Multi-tenant `WIKI_PATH` in `.env`
In multi-member profiles, profile `.env` values for `WIKI_PATH` are ignored when scoping is automatic. The runtime always injects the scoped path above. Single-user installs may still set `skills.config.wiki.path` or `WIKI_PATH` for a legacy global location.
:::

## Wiki directory layout

```
wiki/
├── SCHEMA.md           # Conventions and tag taxonomy
├── index.md            # Sectioned catalog with one-line summaries
├── log.md              # Append-only action log
├── raw/                # Immutable sources (articles, papers, transcripts)
├── entities/           # Entity pages
├── concepts/           # Topic pages
├── comparisons/        # Side-by-side analyses
└── queries/            # Filed query results worth keeping
```

The agent reads `SCHEMA.md`, `index.md`, and recent `log.md` at the start of every wiki session before ingesting or editing.

## Intellect WebUI — Wiki panel

Open the **Wiki** icon in the left rail (between Chat and Tasks). The layout mirrors Skills/Memory:

| Area | Purpose |
|------|---------|
| **Rail** | Wiki entry point |
| **Sidebar catalog** (`panelWiki`) | Grouped scopes: Personal, Teams, Projects, and **Organization (Global)** |
| **Main viewer** (`mainWiki`) | Quartz Vault iframe, build controls, init when missing |

### Sidebar catalog

`GET /api/wiki/catalog` returns every scope you can access, with display names (not just slugs), wiki status badges (`Ready`, `Building`, `Empty`, `Missing`, …), and vault build state.

Select a row to load the matching Vault URL:

| Scope | Vault path pattern |
|-------|-------------------|
| Personal (member) | `/vault/m/{member_id}/` |
| Team | `/vault/t/{slug}/` |
| Project | `/vault/p/{slug}/` |
| Global | `/vault/global/` |

### Build and initialize

- **Rebuild** — `POST /api/wiki/build` for the selected scope; poll `GET /api/wiki/build/status`.
- **Initialize Wiki** — when the scoped directory is missing, `POST /api/wiki/init` scaffolds `SCHEMA.md`, `index.md`, `log.md`, and layer folders (same logic as `intellect_cli.wiki_scaffold`).
- **Open in tab** — opens the Vault URL in a new browser tab.

Scheduled rebuilds use gateway cron + `intellect vault tick` (see [Vault scheduling](#vault-scheduling) below).

### Insights card

The **Insights** tab also shows a compact **LLM Wiki** status card (`GET /api/wiki/status`): entry counts, last writer, traffic-light availability, enable/disable toggle (`POST /api/wiki/toggle`), and quick rebuild — useful when you are not on the Wiki panel.

## Global wiki — member workflow

When a member asks the agent to **add content to the organization / global wiki**:

1. The agent **cannot** write under `wiki/global/` (`read_only` guard).
2. Content is saved to the member's **personal wiki** instead.
3. The agent explains that Global is admin-only and offers **submission for review**.

To promote personal pages to Global:

1. Confirm which relative paths to submit (e.g. `entities/topic.md`). Do not submit `SCHEMA.md`, `index.md`, or `log.md`.
2. Submit via `POST /api/wiki/contributions` with `page_paths`, `title`, `summary`, and optional `note`.
3. Track status in the Wiki panel (**Organization (Global)** row shows pending contribution count for admins; members see their own submissions).

**Admins (`owner` / `admin`):**

- List queue: `GET /api/wiki/contributions`
- Preview diff: `GET /api/wiki/contributions/{id}/diff`
- Approve (merge into Global): `POST /api/wiki/contributions/{id}/review` with `action: approve`
- Reject or request changes: same endpoint with `rejected` / `changes_requested`
- Withdraw (member): `POST /api/wiki/contributions/{id}/withdraw`

Merged pages land in `wiki/global/` with provenance recorded in Global `log.md`. A Global Vault rebuild runs after merge.

## Configuration

```yaml
# ~/.intellect/config.yaml (illustrative)
skills:
  config:
    wiki:
      enabled: true
      path: ~/wiki          # legacy single-user default; overridden when members scoping is active

vault:
  routing:
    enabled: true           # serve /vault/* static sites from WebUI
  build_trigger: scheduled  # or manual
  build_cron: "0 3 * * *"   # when scheduled
```

**Environment variables** forwarded to agent runs (Docker and WebUI):

- `WIKI_PATH`, `WIKI_SCOPE`, `WIKI_SCOPE_ID`, `WIKI_WRITE_MODE`, `WIKI_SKILL_VERSION`

**CLI scheduling:**

```bash
intellect vault tick              # one scheduled build pass
intellect vault tick --force      # rebuild all eligible vaults
intellect vault tick --json       # machine-readable output
```

External schedulers can also call WebUI `POST /api/vault/tick`.

## Using the skill in chat

Trigger the bundled skill by asking the agent to:

- Create or initialize a wiki for a domain
- Ingest a paper, article, or meeting transcript into `raw/`
- Answer a question using existing wiki pages
- Lint or audit wiki health (broken links, stale `index.md`, orphan pages)

Attach `/llm-wiki` or enable the skill in session settings when you want explicit skill loading.

With [Teams, Projects & Members](teams-and-members.md), pin team/project context in WebUI (or gateway headers) so writes land in the shared wiki you intend.

## Related docs

- [Llm Wiki skill reference](/docs/user-guide/skills/bundled/research/research-llm-wiki) — full SKILL.md the agent sees
- [Teams, Projects & Members](teams-and-members.md) — multi-user scope and RBAC
- [RAG Providers](rag-providers.md) — document corpora retrieval (complementary)
- [Obsidian skill](/docs/user-guide/skills/bundled/note-taking/note-taking-obsidian) — optional vault sync patterns

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Wiki panel shows **Missing** | Scoped directory never initialized — use **Initialize Wiki** or ask the agent to create the wiki |
| Vault iframe blank after build | Build failed — check build status badge; run Rebuild; confirm `vault.routing.enabled` |
| Agent writes to personal wiki instead of Global | Expected for non-admin members — use contribution flow or ask an admin |
| Agent cannot write to team/project wiki | Not a member of that team/project, or session context not pinned to that scope |
| Stale Vault content | Trigger Rebuild or wait for `intellect vault tick` / scheduled cron |
| `WIKI_PATH` in `.env` ignored | Multi-member auto-scoping overrides profile `.env`; check injected scope in agent logs |

Run `intellect doctor` after config changes. Wiki and vault health integrate with WebUI status APIs when the dashboard is running.
