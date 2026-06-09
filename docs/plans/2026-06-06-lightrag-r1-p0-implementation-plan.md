# LightRAG R1 + P0 Implementation Plan

> **Status (2026-06):** R1+P0–P3+ delivered on `feat/lightrag-r1-p0`. User docs: [`plugins/rag/lightrag/README.md`](../../plugins/rag/lightrag/README.md). Checkboxes below are the original task list; see **Delivery log** at the end for what shipped.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the RAG plugin infrastructure (R1) and a remote-only LightRAG provider (P0) so users can set `rag.provider: lightrag`, prefetch document context on qualifying turns, and call `lightrag_search` / `lightrag_insert_text` against an external LightRAG API server — while `memory.provider: graphiti` continues to work in parallel.

**Architecture:** Mirror the proven `MemoryProvider` / `MemoryManager` split with `RAGProvider` / `RAGManager`. Discovery lives in `plugins/rag/__init__.py`. The LightRAG plugin is a thin httpx REST client + workspace router (Graphiti `client.py` pattern) with **no** embedded `lightrag-hku` SDK. Core changes are minimal one-line delegations in `agent_init.py`, `conversation_loop.py`, `system_prompt.py`, and `tool_executor.py`.

**Tech Stack:** Python 3.11+, httpx, pytest + unittest.mock, existing plugin discovery, Graphiti client patterns (async thread + CircuitBreaker).

**Confirmed decisions (§14.3):**

| # | Decision | P0 impact |
|---|----------|-----------|
| 2 | `ingest.auto_mode: off` + setup wizard opt-in for `summary` | No auto-ingest in P0; `sync_turn` is no-op |
| 4 | `prefetch_policy: hybrid` | Keyword OR len≥40 OR `?` triggers prefetch |
| 5 | RBAC **scheme A** | **P1 only** — P0 ships tools without `member_rbac.py` changes |

**Reference docs:** [`lightrag-memory-plugin-design.md`](lightrag-memory-plugin-design.md), [`2026-06-02-multi-database-cache-mq-design.md` §15.8](2026-06-02-multi-database-cache-mq-design.md).

**Reference code:** `plugins/memory/graphiti/`, `agent/memory_manager.py`, `agent/memory_provider.py`, `plugins/memory/__init__.py`.

---

## File map

| Path | Responsibility |
|------|----------------|
| `agent/rag_provider.py` | `RAGProvider` ABC |
| `agent/rag_manager.py` | Orchestration: prefetch fence, tools, lifecycle |
| `plugins/rag/__init__.py` | Discovery + `load_rag_provider()` |
| `plugins/rag/lightrag/__init__.py` | `LightRAGRAGProvider` lifecycle glue |
| `plugins/rag/lightrag/client.py` | httpx REST + workspace router + CircuitBreaker |
| `plugins/rag/lightrag/config.py` | `$INTELLECT_HOME/lightrag/config.json` |
| `plugins/rag/lightrag/tools.py` | P0: `lightrag_search`, `lightrag_insert_text` schemas |
| `plugins/rag/lightrag/plugin.yaml` | Manifest (`kind: rag`) |
| `plugins/rag/lightrag/cli.py` | `setup|status|health|workspaces|doctor|sync-server-env|mcp` |
| `plugins/rag/lightrag/sync_env.py` | `sync-server-env` — Intellect model → server `.env` |
| `plugins/rag/lightrag/mcp_server.py` | MCP stdio bridge |
| `plugins/rag/lightrag/upload.py` | Multimodal filename hints |
| `deploy/lightrag/docker-compose.yml` | Dev single-service compose |
| `deploy/lightrag/docker-compose.webui.yml` | Three-container overlay |
| `scripts/smoke_lightrag_compose.sh` | Compose health / full round-trip smoke |
| `intellect_cli/config.py` | `DEFAULT_CONFIG["rag"]` block |
| `toolsets.py` | New `"rag"` toolset |
| `tests/agent/test_rag_provider.py` | R1 unit tests |
| `tests/plugins/rag/test_lightrag_plugin.py` | P0 mocked-httpx tests |

**Core touch points (minimal):**

| File | Change |
|------|--------|
| `agent/agent_init.py` | Init `RAGManager`, load provider, inject tool schemas (gated by `rag` toolset) |
| `agent/conversation_loop.py` | `_rag_prefetch_cache` parallel to `_ext_prefetch_cache` |
| `agent/system_prompt.py` | `build_system_prompt()` delegation |
| `agent/tool_executor.py` | Route `lightrag_*` through `RAGManager` |

---

## R1 — RAG infrastructure

### Task 1: `RAGProvider` ABC

**Files:**
- Create: `agent/rag_provider.py`
- Test: `tests/agent/test_rag_provider.py`

- [ ] **Step 1: Write failing import test**

```python
# tests/agent/test_rag_provider.py
def test_rag_provider_abc_exists():
    from agent.rag_provider import RAGProvider
    assert hasattr(RAGProvider, "name")
    assert hasattr(RAGProvider, "prefetch")
    assert hasattr(RAGProvider, "get_tool_schemas")
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
scripts/run_tests.sh tests/agent/test_rag_provider.py::test_rag_provider_abc_exists -q
```

- [ ] **Step 3: Implement ABC** (adapt from `agent/memory_provider.py`; slimmer surface)

```python
# agent/rag_provider.py — key methods
class RAGProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None: ...

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        pass  # P0 default no-op; LightRAG overrides when ingest != off (P1)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any]) -> str:
        return json.dumps({"success": False, "error": f"unknown tool: {name}"})

    def shutdown(self) -> None:
        pass
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add agent/rag_provider.py tests/agent/test_rag_provider.py
git commit -m "Add RAGProvider ABC for pluggable RAG backends"
```

---

### Task 2: `RAGManager` orchestrator

**Files:**
- Create: `agent/rag_manager.py`
- Modify: `tests/agent/test_rag_provider.py`

- [ ] **Step 1: Write failing fence test**

```python
def test_build_rag_context_block_wraps_content():
    from agent.rag_manager import build_rag_context_block
    out = build_rag_context_block("chunk one")
    assert out.startswith("<rag-context>")
    assert out.rstrip().endswith("</rag-context>")
    assert "chunk one" in out
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement `RAGManager`** (mirror `MemoryManager`; **one** external provider limit)

Key functions:
- `build_rag_context_block(raw)` — parallel to `build_memory_context_block()`; sanitize fence-break attempts (reuse `sanitize_context` from `memory_manager` or duplicate minimal strip)
- `prefetch_all(query)` → single provider `prefetch()` → wrap in `<rag-context>`
- `build_system_prompt()` → provider `system_prompt_block()`
- `get_all_tool_schemas()` / `handle_tool_call()` / `has_tool()`
- `initialize_all(**kwargs)` / `shutdown_all()`
- `add_provider()` — reject second external provider (same log pattern as memory)

- [ ] **Step 4: Add stub provider test**

```python
class _StubRAG(RAGProvider):
    name = "stub"
    def is_available(self): return True
    def initialize(self, session_id, **kwargs): pass
    def prefetch(self, query, *, session_id=""): return "hit"
```

- [ ] **Step 5: Run tests — expect PASS**

- [ ] **Step 6: Commit**

---

### Task 3: `plugins/rag/` discovery

**Files:**
- Create: `plugins/rag/__init__.py`
- Create: `plugins/rag/lightrag/__init__.py` (stub `LightRAGRAGProvider` returning `is_available() == False` until Task 9)
- Test: `tests/plugins/rag/test_rag_discovery.py`

- [ ] **Step 1: Write discovery test**

```python
def test_discover_rag_providers_lists_lightrag():
    from plugins.rag import discover_rag_providers
    names = [n for n, _, _ in discover_rag_providers()]
    assert "lightrag" in names
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement discovery** (copy `plugins/memory/__init__.py` structure)

Scan order:
1. Bundled `plugins/rag/<name>/`
2. User `$INTELLECT_HOME/plugins/rag/<name>/`
3. User `$INTELLECT_HOME/plugins/<name>/` with `RAGProvider` heuristic

Exports: `discover_rag_providers()`, `load_rag_provider(name)`, `find_provider_dir(name)`.

Registration helper in each plugin:

```python
def register_rag_provider(cls):
    _REGISTRY[name] = cls
```

- [ ] **Step 4: Stub provider registers on import**

- [ ] **Step 5: Run tests — expect PASS**

- [ ] **Step 6: Commit**

---

### Task 4: `DEFAULT_CONFIG["rag"]` block

**Files:**
- Modify: `intellect_cli/config.py` (after `"memory"` section ~L1406)

- [ ] **Step 1: Add config block** (no `_config_version` bump — new section deep-merges)

```python
"rag": {
    "enabled": True,
    "provider": "",              # "lightrag" to activate
    "prefetch_policy": "hybrid", # off | always | intent | hybrid
    "prefetch_min_chars": 40,
    "prefetch_keywords": [
        "文档", "规范", "政策", "手册", "合同", "知识库",
        "spec", "README", "according to", "lightrag", "document",
    ],
    "max_prefetch_tokens": 2000,  # char budget ~5500; truncate in manager
    "prefetch_mode": "hybrid",      # LightRAG query mode for prefetch-only calls
},
```

- [ ] **Step 2: Smoke test config load**

```bash
python -c "from intellect_cli.config import load_config; c=load_config(); assert 'rag' in c"
```

- [ ] **Step 3: Commit**

---

### Task 5: `toolsets.py` — `"rag"` toolset

**Files:**
- Modify: `toolsets.py`

- [ ] **Step 1: Add toolset entry**

```python
"rag": {
    "description": "Document knowledge base search and ingestion (RAG)",
    "tools": [],  # populated dynamically by RAGManager at agent init
    "includes": [],
},
```

Note: Unlike `"memory"` (fixed `["memory"]`), RAG tool names are provider-specific (`lightrag_*`). Injection happens in `agent_init` like memory provider tools — toolset gate checks `"rag" in enabled_toolsets`.

- [ ] **Step 2: Commit**

---

### Task 6: Wire `agent_init.py`

**Files:**
- Modify: `agent/agent_init.py` (~L1097 memory block — add parallel RAG block after it)

- [ ] **Step 1: Add `agent._rag_manager = None` init path**

Pattern (mirror memory block):
- Read `rag_config = _agent_cfg.get("rag", {})`
- Skip if `skip_memory` is True? **No** — RAG is independent; only skip if `rag.enabled` is False or `rag.provider` empty
- `RAGManager()` → `load_rag_provider(name)` → `add_provider()` if `is_available()`
- `initialize_all(**_init_kwargs)` — **same kwargs dict** as memory (member_id, team_id, project_id, agent_context, etc.)
- Inject tool schemas when `enabled_toolsets is None or "rag" in enabled_toolsets`

- [ ] **Step 2: Manual smoke** (no provider configured → no crash)

```bash
python -c "
from agent.agent_init import init_agent_defaults
# minimal import check — full AIAgent init is heavy; defer to integration test
from agent.rag_manager import RAGManager
assert RAGManager is not None
"
```

- [ ] **Step 3: Commit**

---

### Task 7: Wire `conversation_loop.py` prefetch

**Files:**
- Modify: `agent/conversation_loop.py` (~L774)

- [ ] **Step 1: Add RAG prefetch cache** (after memory prefetch)

```python
_rag_prefetch_cache = ""
if agent._rag_manager:
    try:
        _query = original_user_message if isinstance(original_user_message, str) else ""
        _rag_prefetch_cache = agent._rag_manager.prefetch_all(_query) or ""
    except Exception:
        pass
```

- [ ] **Step 2: Inject into user message** — find where `_ext_prefetch_cache` is appended to the turn input; append `_rag_prefetch_cache` **after** memory block (memory first, RAG second — matches multi-DB design §15.8)

- [ ] **Step 3: Commit**

---

### Task 8: Wire `system_prompt.py` + `tool_executor.py`

**Files:**
- Modify: `agent/system_prompt.py` (~L342)
- Modify: `agent/tool_executor.py` (~L815)

- [ ] **Step 1: system_prompt** — after memory block:

```python
if agent._rag_manager:
    _rag_block = agent._rag_manager.build_system_prompt()
    if _rag_block:
        parts.append(_rag_block)
```

- [ ] **Step 2: tool_executor** — parallel branch:

```python
elif agent._rag_manager and agent._rag_manager.has_tool(function_name):
    function_result = agent._rag_manager.handle_tool_call(function_name, function_args)
```

- [ ] **Step 3: Commit**

---

### Task 9: R1 integration tests

**Files:**
- Create: `tests/agent/test_rag_manager.py`
- Modify: `tests/agent/test_rag_provider.py`

- [ ] **Step 1: Test one-provider limit, prefetch fence, tool routing**

- [ ] **Step 2: Run R1 suite**

```bash
scripts/run_tests.sh tests/agent/test_rag_provider.py tests/agent/test_rag_manager.py tests/plugins/rag/ -q
```

- [ ] **Step 3: Commit**

**R1 done when:** Agent starts with `rag.provider: ""` unchanged; with stub provider, prefetch fence and tool routing work.

---

## P0 — LightRAG remote provider

### Task 10: Plugin scaffold + manifest

**Files:**
- Create: `plugins/rag/lightrag/plugin.yaml`
- Modify: `plugins/rag/lightrag/__init__.py`

- [ ] **Step 1: Create manifest**

```yaml
name: lightrag
version: 0.1.0
description: LightRAG document knowledge graph (remote API server)
kind: rag
requires_env: []
```

- [ ] **Step 2: Register `LightRAGRAGProvider` + `register_rag_provider()`**

- [ ] **Step 3: Commit**

---

### Task 11: `config.py` — plugin-local config

**Files:**
- Create: `plugins/rag/lightrag/config.py`

- [ ] **Step 1: Implement load/save/schema** (mirror `graphiti/config.py`)

`$INTELLECT_HOME/lightrag/config.json` defaults:

```python
DEFAULT_CONFIG = {
    "mode": "remote",
    "server": {
        "base_url": "http://127.0.0.1:9621",
        "api_key": "",
        "timeout_seconds": 120,
        "api_prefix": "",
    },
    "workspace": {
        "default": "global",
        "session_prefix": "session_",
        "member_prefix": "member_",
        "team_prefix": "team_",
        "project_prefix": "project_",
    },
    "query": {
        "default_mode": "mix",
        "prefetch_mode": "hybrid",
        "enable_rerank": False,
        "only_need_context": True,  # prefetch path
    },
    "ingest": {
        "auto_mode": "off",  # off | summary | full — P0: off only
    },
    "circuit_breaker": {
        "threshold": 3,
        "cooldown_seconds": 30,
    },
}
```

Env overrides: `LIGHTRAG_BASE_URL`, `LIGHTRAG_API_KEY`, `LIGHTRAG_TIMEOUT`.

- [ ] **Step 2: Unit test load merge + env override**

- [ ] **Step 3: Commit**

---

### Task 12: `client.py` — httpx REST client

**Files:**
- Create: `plugins/rag/lightrag/client.py`
- Test: `tests/plugins/rag/test_lightrag_client.py`

- [ ] **Step 1: Write failing health-check test** (mock httpx)

- [ ] **Step 2: Implement layers** (Graphiti pattern, **sync httpx** — LightRAG server is sync REST; no asyncio loop needed unless we add background prefetch later)

```
LightRAGClient          — one base_url; methods: health(), query(), insert_text()
LightRAGClientManager   — scope → workspace names; parallel multi-workspace query + merge
CircuitBreaker          — copy Graphiti defaults from config
```

**Workspace routing** (`bind_scope(member_id, team_id, project_id)`):
- `auto` → `[member_*?, team_*?, project_*?, session_* or global]`
- Implement `resolve_workspaces(scope: str) -> list[str]`

**API calls (verify against target server `/docs` OpenAPI during implementation):**

| Method | Endpoint | Notes |
|--------|----------|-------|
| Health | `GET /health` | Init gate |
| Query | `POST /query` | `only_need_context: true` for search/prefetch |
| Insert | `POST /documents/text` | `{text, file_path?}` |

**Workspace per request:** Check LightRAG server version for `workspace` field in JSON body or `X-Workspace` header. If absent in pinned server version, P0 documents single-workspace dev mode (`workspace.default`) and files issue for upstream; do **not** embed SDK.

- [ ] **Step 3: Implement `merge_query_results()`** — dedupe by `(file_path, reference_id)`

- [ ] **Step 4: Run client tests — expect PASS**

- [ ] **Step 5: Commit**

---

### Task 13: Hybrid prefetch policy

**Files:**
- Create: `plugins/rag/lightrag/prefetch.py`
- Test: `tests/plugins/rag/test_lightrag_prefetch.py`

- [ ] **Step 1: Write policy tests**

```python
from plugins.rag.lightrag.prefetch import should_prefetch

def test_hybrid_triggers_on_question():
    assert should_prefetch("What is the refund policy?", policy="hybrid", min_chars=40)

def test_hybrid_triggers_on_length():
    assert should_prefetch("x" * 40, policy="hybrid", min_chars=40)

def test_hybrid_triggers_on_keyword():
    assert should_prefetch("see README", policy="hybrid", keywords=["README"])

def test_hybrid_skips_short_chat():
    assert not should_prefetch("hi", policy="hybrid", min_chars=40)

def test_off_never_prefetches():
    assert not should_prefetch("anything?", policy="off")
```

- [ ] **Step 2: Implement `should_prefetch(query, *, policy, min_chars, keywords)`**

- [ ] **Step 3: Commit**

---

### Task 14: `LightRAGRAGProvider` lifecycle

**Files:**
- Modify: `plugins/rag/lightrag/__init__.py`
- Test: `tests/plugins/rag/test_lightrag_plugin.py`

- [ ] **Step 1: `is_available()`** — True when `config.json` has `server.base_url` (no import of lightrag-hku)

- [ ] **Step 2: `initialize()`** — load config, construct `LightRAGClientManager`, `GET /health`, store scope ids from kwargs

- [ ] **Step 3: `prefetch()`** —
  1. Read `rag.prefetch_policy` from agent config (passed via kwargs `config`)
  2. If `should_prefetch()` false → return `""`
  3. `POST /query` with `only_need_context=true`, `mode=prefetch_mode`
  4. Truncate to `max_prefetch_tokens` char budget
  5. Return raw text (RAGManager wraps fence)

- [ ] **Step 4: `sync_turn()`** — **no-op** (ingest.auto_mode off; P1 adds summary branch)

- [ ] **Step 5: `system_prompt_block()`** — short static blurb: LightRAG active, tools available, ingest off by default

- [ ] **Step 6: `shutdown()`** — close httpx client

- [ ] **Step 7: Run provider tests with mocked client**

- [ ] **Step 8: Commit**

---

### Task 15: P0 tools (`lightrag_search`, `lightrag_insert_text`)

**Files:**
- Create: `plugins/rag/lightrag/tools.py`
- Modify: `plugins/rag/lightrag/__init__.py` (`handle_tool_call`)

- [ ] **Step 1: Define schemas** (JSON-string return, Graphiti style)

**`lightrag_search`**
- Params: `query` (required), `mode` (default `mix`), `scope` (default `auto`), `top_k` optional if server supports
- Calls client `query(only_need_context=true)`
- Returns `{success, context, references}`

**`lightrag_insert_text`**
- Params: `text` (required), `file_path` (optional label), `scope` (default `auto`)
- Resolves target workspace from scope
- Calls `POST /documents/text`
- Returns `{success, track_id?}`

- [ ] **Step 2: Write tool dispatch tests** (mock client)

- [ ] **Step 3: Commit**

---

### Task 16: CLI `intellect lightrag`

**Files:**
- Create: `plugins/rag/lightrag/cli.py`
- Modify: plugin discovery for CLI registration (mirror `plugins/memory/graphiti/cli.py` + `discover_plugin_cli_commands`)

- [ ] **Step 1: `register_cli(subparser)`** with:
  - `setup` — prompt base_url, health check, write config.json, **opt-in** prompt: "Enable conversation summary ingest?" → sets `ingest.auto_mode: summary` if yes
  - `status` — print server health, base_url, workspace strategy

- [ ] **Step 2: Wire CLI discovery for active `rag.provider`** (parallel to memory provider CLI — may need small addition to `intellect_cli/plugins.py` or memory CLI scanner to also scan `plugins/rag/<active>/cli.py`)

- [ ] **Step 3: Manual smoke**

```bash
intellect lightrag status  # expect graceful error when not configured
```

- [ ] **Step 4: Commit**

---

### Task 17: `deploy/lightrag/` compose templates

**Files:**
- Create: `deploy/lightrag/docker-compose.yml`
- Create: `deploy/lightrag/README.md`
- Create: `deploy/lightrag/.env.example`

- [ ] **Step 1: Single-service dev compose**

```yaml
# deploy/lightrag/docker-compose.yml
services:
  lightrag:
    image: ghcr.io/hkuds/lightrag:latest  # pin digest in README
    ports:
      - "9621:9621"
    volumes:
      - lightrag_data:/app/data
    environment:
      - WORKSPACE=
volumes:
  lightrag_data:
```

- [ ] **Step 2: Document** — embedding model pin warning, workspace empty for Intellect-side routing, link to upstream docs

- [ ] **Step 3: `docker-compose.webui.yml`** — document three-container layout (lightrag + webui + optional rerank); **merge into main webui compose is P2**

- [ ] **Step 4: Commit**

---

### Task 18: End-to-end test + docs touch-up

**Files:**
- Modify: `tests/plugins/rag/test_lightrag_plugin.py`
- Modify: `docs/plans/lightrag-memory-plugin-design.md` (§14.3 header → 已确认)

- [ ] **Step 1: Integration test** — mock httpx transport:
  1. Provider init + health
  2. `prefetch()` with hybrid policy on `"What does the spec say?"`
  3. `lightrag_insert_text` → `lightrag_search` round-trip

- [ ] **Step 2: Run full new test dir**

```bash
scripts/run_tests.sh tests/agent/test_rag_*.py tests/plugins/rag/ -q
```

- [ ] **Step 3: Update design doc §14.3** — mark decisions 2/4/5 confirmed; link to this plan

- [ ] **Step 4: Commit**

---

## P1 backlog (out of P0 scope)

| Item | Notes |
|------|-------|
| RBAC scheme A | `agent/member_rbac.py` ~15 lines for `lightrag_*` tools |
| Remaining tools | `lightrag_query`, `lightrag_upload_document`, `lightrag_list_documents`, `lightrag_delete_document` |
| `ingest.auto_mode: summary` | auxiliary LLM summary → `/documents/text` in `sync_turn` |
| Hooks | `on_session_end`, `on_pre_compress` |
| Doctor | embedding dimension / server reachability checks |
| Webui compose merge | P2 |

---

## Manual acceptance (P0)

```yaml
# ~/.intellect/config.yaml
rag:
  provider: lightrag
memory:
  provider: graphiti   # optional coexistence smoke
```

```bash
cd deploy/lightrag && docker compose up -d
intellect lightrag setup    # opt-in summary ingest: say No (stay off)
intellect lightrag status
# CLI session:
#   "What is in the uploaded docs about X?"  → hybrid prefetch injects <rag-context>
#   lightrag_insert_text(text="...", file_path="note.md")
#   lightrag_search(query="...")
```

---

## Risk register

| Risk | Mitigation |
|------|------------|
| Per-request `workspace` unsupported on server | Verify OpenAPI day 1; document fallback; don't embed SDK |
| Prefetch latency | hybrid policy + circuit breaker + char budget truncate |
| Tool schema bloat | P0 ships 2 tools only |
| RBAC gap in P0 | Document gateway deploys should wait for P1 or disable `rag` toolset |
| Prompt cache | Prefetch only at turn start; never mid-turn |

---

*Plan version: 2026-06-06. Decisions: ingest off+opt-in, prefetch hybrid, RBAC A @ P1.*

---

## Delivery log (feat/lightrag-r1-p0)

| Phase | Shipped | Notes |
|-------|---------|-------|
| **R1** | ✅ | `RAGProvider` / `RAGManager`, `plugins/rag/` discovery, `rag:` config, agent wiring |
| **P0** | ✅ | Remote httpx client, `lightrag_search` + `lightrag_insert_text`, hybrid prefetch, dev compose |
| **P1** | ✅ | 7 tools + RBAC, summary ingest (`sync_turn`), session/pre_compress hooks |
| **P2** | ✅ | `intellect doctor` RAG section, webui compose, parallel multi-workspace query |
| **P3** | ✅ | `kind: rag`, multimodal upload hints, `intellect lightrag mcp start\|config`, `health` / `workspaces` |
| **P3+** | ✅ | `intellect lightrag sync-server-env`, `auxiliary.lightrag` task, `file_source` API fix |

**Tests:** `scripts/run_tests.sh tests/agent/test_rag_provider.py tests/plugins/rag/ tests/intellect_cli/test_doctor_lightrag.py` — 43 passed (incl. 5× `test_lightrag_sync_env.py`).

**Docs:** [`plugins/rag/lightrag/README.md`](../../plugins/rag/lightrag/README.md), [`deploy/lightrag/README.md`](../../deploy/lightrag/README.md), design doc rev.3.

**Manual acceptance (updated):**

```bash
intellect lightrag sync-server-env --docker
cd deploy/lightrag && docker compose up -d
intellect lightrag setup
scripts/smoke_lightrag_compose.sh --full   # needs server LLM+embedding in .env
```
