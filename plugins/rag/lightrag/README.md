# LightRAG RAG Provider

Document knowledge graph RAG via an external [LightRAG API server](https://github.com/HKUDS/LightRAG). Intellect connects over HTTP only (`rag.provider: lightrag`); EXTRACT/QUERY/embedding run on the server.

## Requirements

- A running `lightrag-server` (local Docker, remote host, or intellect-webui stack)
- Intellect `rag` toolset enabled (via `intellect tools` or `tools.<platform>.enabled`)

## Quick start

```bash
# 1. Start server (dev)
cd deploy/lightrag
intellect lightrag sync-server-env --docker   # optional: align .env with your Intellect model
docker compose up -d

# 2. Configure Intellect plugin (client)
intellect lightrag setup

# 3. Activate provider
intellect config set rag.provider lightrag
```

Smoke test (no LLM required for health-only):

```bash
scripts/smoke_lightrag_compose.sh
scripts/smoke_lightrag_compose.sh --full   # needs working LLM+embedding in server .env
```

## Two model planes (important)

| Workload | Runs where | Config source |
|----------|------------|---------------|
| Conversation **summary** ingest (`ingest.auto_mode: summary`) | Intellect process | `auxiliary.lightrag` in `config.yaml` (default `auto` → main model chain) |
| Document EXTRACT / QUERY / **embedding** | LightRAG server | `deploy/lightrag/.env` or server host env |

Use `intellect lightrag sync-server-env` to generate server `.env` from your current Intellect `model.*` settings.

## Setup

```bash
intellect lightrag setup
```

Prompts for server URL, health check, and optional summary ingest (`ingest.auto_mode: summary`).

### Sync server environment from Intellect model

```bash
intellect lightrag sync-server-env              # → deploy/lightrag/.env (in-repo) or ~/.intellect/lightrag/server.env
intellect lightrag sync-server-env --docker     # rewrite localhost → host.docker.internal for compose
intellect lightrag sync-server-env --dry-run    # preview only
intellect lightrag sync-server-env -o /path/to/.env
intellect lightrag sync-server-env --embedding-model bge-m3:latest
```

**Mapping (automatic):**

| Intellect runtime | LightRAG server |
|-------------------|-----------------|
| OpenRouter / OpenAI-compatible | `LLM_BINDING=openai` + `LLM_BINDING_HOST` + API key |
| Ollama / `127.0.0.1:11434` | `LLM_BINDING=ollama`; embedding defaults to Ollama + `bge-m3:latest` |
| OAuth-only (Codex, etc.) | Warning printed — set `OPENAI_API_KEY` or Ollama manually |

OAuth inference providers are not copied into the server file; add an API-key backend by hand.

## Config

### `~/.intellect/config.yaml`

```yaml
rag:
  provider: lightrag
  prefetch_policy: hybrid
  max_prefetch_tokens: 2000

auxiliary:
  lightrag:
    provider: auto          # or openrouter, custom, ...
    model: ""               # e.g. google/gemini-2.5-flash when pinning a cheap summarizer
    base_url: ""
    timeout: 60
```

`auxiliary.lightrag` is used only for **conversation summary** ingest. It does not affect context compression (`auxiliary.compression`).

### `~/.intellect/lightrag/config.json`

| Key | Default | Description |
|-----|---------|-------------|
| `server.base_url` | `http://127.0.0.1:9621` | LightRAG API base URL |
| `ingest.auto_mode` | `off` | `off`, `summary`, or `full` |
| `ingest.summary_max_tokens` | `256` | Summary length cap |
| `query.default_mode` | `mix` | Default retrieval mode for tools/prefetch |
| `query.prefetch_mode` | `hybrid` | Used when `rag.prefetch_policy` is `hybrid` |
| `upload.default_parse_engine` | `""` | Optional default for multimodal uploads |
| `upload.analyze_images` | `false` | Default VLM image analysis (`i` flag) |

Secrets: `LIGHTRAG_API_KEY` / `LIGHTRAG_BASE_URL` env vars override `config.json` (profile-aware via `INTELLECT_HOME`).

## CLI

| Command | Description |
|---------|-------------|
| `intellect lightrag setup` | Interactive plugin config + health check |
| `intellect lightrag status` | Config path, server URL, health |
| `intellect lightrag health` | `GET /health` summary (`--json`) |
| `intellect lightrag workspaces` | Document counts per workspace (`--scope`, `--json`) |
| `intellect lightrag doctor` | Plugin health subset |
| `intellect lightrag sync-server-env` | Generate server `.env` from Intellect model |
| `intellect lightrag mcp start` | MCP stdio bridge for external clients |
| `intellect lightrag mcp config` | Copy-paste MCP client snippets (Claude/Cursor/VS Code) |

Also: `intellect doctor` includes a **RAG Provider** section when `rag.provider: lightrag`.

## Agent tools

| Tool | RBAC | Notes |
|------|------|-------|
| `lightrag_search` | read | Context chunks only |
| `lightrag_query` | read | Full answer + references |
| `lightrag_insert_text` | chat | `file_path` → API `file_source` |
| `lightrag_upload_document` | chat | Optional `parse_engine`, `analyze_*` for multimodal |
| `lightrag_list_documents` | read | |
| `lightrag_delete_document` | admin | Requires `reason` |
| `lightrag_clear_workspace` | admin | Requires `reason` |

## Coexistence with Graphiti / built-in memory

```yaml
memory:
  provider: graphiti    # conversation graph
rag:
  provider: lightrag    # document corpus
```

Built-in `memory` tool is unchanged. Prefetch order per turn: `<memory-context>` then `<rag-context>`.

## Deployment

See [`deploy/lightrag/README.md`](../../../deploy/lightrag/README.md) for Docker compose, Postgres overlay, and smoke scripts.

## Local embedding (server side)

Intellect does **not** run document embeddings locally. On the LightRAG server:

```bash
# Ollama (typical local setup)
EMBEDDING_BINDING=ollama
EMBEDDING_BINDING_HOST=http://host.docker.internal:11434
EMBEDDING_MODEL=bge-m3:latest
```

Pin embedding model before the first upload; changing dimension requires clearing the workspace and re-indexing.

## References

- Design: [`docs/plans/lightrag-memory-plugin-design.md`](../../../docs/plans/lightrag-memory-plugin-design.md)
- Implementation plan: [`docs/plans/2026-06-06-lightrag-r1-p0-implementation-plan.md`](../../../docs/plans/2026-06-06-lightrag-r1-p0-implementation-plan.md)
- Upstream API: [LightRAG-API-Server.md](https://github.com/HKUDS/LightRAG/blob/main/docs/LightRAG-API-Server.md)
