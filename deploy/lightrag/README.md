# LightRAG deployment (Intellect RAG plugin)

Intellect talks to LightRAG via **HTTP only** (`rag.provider: lightrag`). This directory provides compose templates; LLM/embedding/storage settings live in the LightRAG server `.env`.

- **Plugin user guide:** [`plugins/rag/lightrag/README.md`](../../plugins/rag/lightrag/README.md)
- **Upstream API:** [LightRAG-API-Server.md](https://github.com/HKUDS/LightRAG/blob/main/docs/LightRAG-API-Server.md)

## Quick start (dev — file storage)

```bash
cd deploy/lightrag
docker compose up -d
```

The stock image defaults to **Ollama** (`localhost:11434`) for LLM + embedding.
Indexing will fail until Ollama is reachable from the container — either:

- Copy `.env.example` → `.env`, set `LLM_BINDING=openai` + `OPENAI_API_KEY`, and
  add `env_file: [.env]` under `lightrag` in `docker-compose.yml`, or
- Run an Ollama sidecar and point `LLM_BINDING_HOST` / `EMBEDDING_BINDING_HOST`
  at `http://ollama:11434`.

### Compose smoke test

```bash
# Health + plugin client (no LLM required)
scripts/smoke_lightrag_compose.sh

# Start compose then smoke
scripts/smoke_lightrag_compose.sh --up

# Full insert → index → search (requires working LLM+embedding in server .env)
scripts/smoke_lightrag_compose.sh --full
```

## WebUI / production overlay (Postgres + pgvector)

```bash
cd deploy/lightrag
cp .env.example .env   # edit API keys + LIGHTRAG_PG_PASSWORD
docker compose -f docker-compose.webui.yml up -d lightrag postgres-lightrag
```

Merge `docker-compose.webui.yml` into your intellect-webui stack when the agent
runs in Docker; set `LIGHTRAG_BASE_URL=http://lightrag:9621` or
`rag.provider: lightrag` with matching `lightrag/config.json`.

Configure Intellect:

```bash
intellect lightrag setup
# or edit ~/.intellect/lightrag/config.json
```

Generate LightRAG **server** `.env` from your current Intellect model (`config.yaml` + `.env` keys):

```bash
intellect lightrag sync-server-env              # → deploy/lightrag/.env
intellect lightrag sync-server-env --docker     # localhost → host.docker.internal
intellect lightrag sync-server-env --dry-run    # preview only
```

Conversation summary ingest uses `auxiliary.lightrag` in `config.yaml` (defaults to `auto` = main model chain). Pin a cheap model there if needed:

```yaml
auxiliary:
  lightrag:
    provider: openrouter
    model: google/gemini-2.5-flash
```

```yaml
# ~/.intellect/config.yaml
rag:
  provider: lightrag
```

## Workspace

Leave `WORKSPACE` empty in compose — Intellect passes workspace per request (`member_*`, `team_*`, `project_*`, `session_*`) from runtime context.

## Coexistence with Graphiti

```yaml
memory:
  provider: graphiti
rag:
  provider: lightrag
```

Memory (conversation graph) and RAG (document corpus) use separate backends and orchestrators.
