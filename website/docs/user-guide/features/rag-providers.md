---
sidebar_position: 5
title: "RAG Providers"
description: "Document knowledge-base plugins — LightRAG and third-party RAG backends via rag.provider"
---

# RAG Providers

Intellect Agent ships RAG (retrieval-augmented generation) provider plugins for **document corpora** — specs, manuals, policies, uploaded PDFs, and ingested notes. RAG is separate from [Persistent Memory](/user-guide/features/memory) and [Memory Providers](/user-guide/features/memory-providers):

| Layer | What it stores | Typical source |
|-------|----------------|----------------|
| **Built-in memory** | Curated facts in MEMORY.md / USER.md | Agent `memory` tool |
| **Memory provider** | Conversation graph, user model, long-term recall | [Graphiti](/user-guide/features/memory-providers#graphiti), Honcho, Hindsight, … |
| **RAG provider** | Indexed documents and knowledge graphs | Uploads, inserts, optional turn summaries |

Only **one** RAG provider can be active at a time (`rag.provider` in config). Memory and RAG can run together — for example [Graphiti](/user-guide/features/memory-providers#graphiti) for conversation memory + LightRAG for documents.

## Quick Start

```bash
intellect lightrag setup          # configure the active RAG plugin
intellect lightrag status         # server URL + health
intellect config set rag.provider lightrag
```

Enable the **`rag` toolset** so agent tools are exposed (`intellect tools`, or add `rag` under `tools.cli.enabled` / your platform's tool list).

```yaml
# ~/.intellect/config.yaml
rag:
  provider: lightrag
  prefetch_policy: hybrid
  max_prefetch_tokens: 2000
```

## How It Works

When a RAG provider is active, Intellect:

1. **Prefetches document context** before qualifying turns (injected as `<rag-context>` after `<memory-context>`)
2. **Registers provider tools** (`lightrag_search`, `lightrag_query`, upload/insert, …) when the `rag` toolset is enabled
3. **Optionally ingests conversation summaries** after each turn (provider-specific; LightRAG uses `ingest.auto_mode`)
4. **Scopes workspaces** per member, team, project, or session when multi-user features are enabled

Prefetch does **not** break prompt caching — it runs at turn start only, never mid-conversation.

### Prefetch policy (`rag.prefetch_policy`)

| Policy | Behavior |
|--------|----------|
| `off` | Never prefetch |
| `always` | Prefetch every turn |
| `intent` | Prefetch when message contains `rag.prefetch_keywords` |
| `hybrid` (default) | Keywords **or** message length ≥ `prefetch_min_chars` (40) **or** contains `?` / `？` |

Retrieved text is capped by `max_prefetch_tokens` (default 2000).

## Available Providers

### LightRAG

[LightRAG](https://github.com/HKUDS/LightRAG) document knowledge graph via a **remote API server**. Intellect is a thin HTTP client — EXTRACT, QUERY, and embedding run on the server, not inside the agent process.

| | |
|---|---|
| **Best for** | Team document libraries, specs/policies, multimodal uploads (PDF, Office) |
| **Requires** | Running `lightrag-server` (Docker compose, remote host, or intellect-webui stack) |
| **Data storage** | Server-side (file, Postgres+pgvector, or upstream backends) |
| **Cost** | Your LLM/embedding API keys on the **server**; optional cheap model for summary ingest via `auxiliary.lightrag` |

**Tools (7):** `lightrag_search` (chunks), `lightrag_query` (answer + refs), `lightrag_insert_text`, `lightrag_upload_document`, `lightrag_list_documents`, `lightrag_delete_document` (admin), `lightrag_clear_workspace` (admin)

**Two model planes:**

| Workload | Runs where | Config |
|----------|------------|--------|
| Conversation summary ingest | Intellect | `auxiliary.lightrag` in `config.yaml` |
| Document EXTRACT / QUERY / embedding | LightRAG server | `deploy/lightrag/.env` or host env |

**Setup:**

```bash
# Generate server .env from your Intellect model (optional)
intellect lightrag sync-server-env --docker

# Start dev server
cd deploy/lightrag && docker compose up -d

# Configure plugin + activate
intellect lightrag setup
intellect config set rag.provider lightrag

# Smoke test (health only — no LLM required)
scripts/smoke_lightrag_compose.sh
```

**CLI:**

```bash
intellect lightrag setup | status | health | workspaces | doctor
intellect lightrag sync-server-env [--docker] [--dry-run]
intellect lightrag mcp start | mcp config
```

`intellect doctor` includes a **RAG Provider** section when LightRAG is active.

<details>
<summary>LightRAG config reference</summary>

**`~/.intellect/config.yaml`**

```yaml
rag:
  provider: lightrag
  prefetch_policy: hybrid
  max_prefetch_tokens: 2000

auxiliary:
  lightrag:
    provider: auto          # or pin openrouter, custom, …
    model: ""               # e.g. google/gemini-2.5-flash for cheap summaries
```

**`~/.intellect/lightrag/config.json`**

| Key | Default | Description |
|-----|---------|-------------|
| `server.base_url` | `http://127.0.0.1:9621` | LightRAG API base URL |
| `ingest.auto_mode` | `off` | `off`, `summary`, or `full` — opt in via setup wizard |
| `ingest.summary_max_tokens` | `256` | Max tokens for turn-summary ingest |
| `query.default_mode` | `mix` | Default retrieval mode for tools |
| `query.prefetch_mode` | `hybrid` | Mode used when `rag.prefetch_policy` is `hybrid` |
| `upload.default_parse_engine` | `""` | Default parser for multimodal uploads |
| `upload.analyze_images` | `false` | Default VLM image analysis |

Env overrides (profile-aware): `LIGHTRAG_BASE_URL`, `LIGHTRAG_API_KEY`.

</details>

<details>
<summary>Deployment & coexistence</summary>

**Deploy templates:** `deploy/lightrag/docker-compose.yml` (dev) and `docker-compose.webui.yml` (Postgres overlay). See the [deploy README](https://gitee.com/ontoweb/intellect-agent/blob/main/deploy/lightrag/README.md).

**With [Graphiti](/user-guide/features/memory-providers#graphiti) memory:**

```yaml
memory:
  provider: graphiti   # conversation knowledge graph — see Memory Providers
rag:
  provider: lightrag   # document corpus
```

Built-in `memory` tool is unchanged. Same turn: memory prefetch first, then RAG prefetch.

**Plugin README:** [plugins/rag/lightrag/README.md](https://gitee.com/ontoweb/intellect-agent/blob/main/plugins/rag/lightrag/README.md)

</details>

---

## Provider Comparison

| Provider | Runtime | Storage | Tools | Server required | Unique feature |
|----------|---------|---------|-------|-----------------|----------------|
| **LightRAG** | Remote HTTP | Server-side graph + vectors | 7 | `lightrag-server` | Knowledge graph RAG, multimodal upload, MCP bridge, `sync-server-env` |

## Profile & Workspace Isolation

- Plugin config lives under `$INTELLECT_HOME/lightrag/` — each [profile](/user-guide/profiles) has its own `config.json` and credentials.
- Document **workspaces** are derived from runtime context (`member_*`, `team_*`, `project_*`, `session_*`) and sent per request to the server. Leave `WORKSPACE` empty in server compose; Intellect sets scope automatically.

## Third-Party RAG Plugins

Install additional providers under:

- `~/.intellect/plugins/rag/<name>/`
- or `~/.intellect/plugins/<name>/` with `kind: rag` in `plugin.yaml`

Set `rag.provider: <name>` and run that plugin's CLI (`intellect <name> setup` when wired). Bundled providers are discovered from `plugins/rag/` in the repo.

## Building a RAG Provider

RAG plugins implement the `RAGProvider` ABC in `agent/rag_provider.py` and register via `plugins/rag/`. Design reference: [lightrag-memory-plugin-design.md](https://gitee.com/ontoweb/intellect-agent/blob/main/docs/plans/lightrag-memory-plugin-design.md) (§3–4). A dedicated developer-guide page may be added later; until then, use LightRAG as the reference implementation.
