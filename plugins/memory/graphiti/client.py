"""Graphiti async client + per-scope graph manager.

Graphiti-core is async-only.  Bridging async↔sync at every call site
would multiply event-loop boilerplate across the provider; instead we
run ONE dedicated event-loop thread per process and submit coroutines
to it via ``run_coroutine_threadsafe``.  Same pattern as e.g.
asyncio-bridge libraries; cheap, safe, no per-call thread spin-up.

Three layers:

  GraphitiClient        — one Graphiti instance bound to one FalkorDB
                          graph (= one scope).  Wraps async API in sync
                          methods that block on the bg loop.
  GraphitiClientManager — owns the loop thread; resolves member/team/
                          project → graph name → client cache; merges
                          search results across scopes.
  CircuitBreaker        — trips after N consecutive failures; auto-
                          resets after a cooldown.  Keeps a FalkorDB
                          outage from spamming the agent's prompt path.

Phase 1 scope: episode write, fact search, node search, timeline,
delete.  Hybrid search defaults; tuning lives in Phase 5.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Outage handling defaults.  Tunable via config in Phase 5.
_BREAKER_THRESHOLD = 3              # consecutive failures before tripping
_BREAKER_COOLDOWN_SEC = 30.0        # how long to stay open before retry
_CALL_TIMEOUT_SEC = 10.0            # per-call deadline for sync wrappers


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreaker:
    """Trip-on-N-failures, half-open after cooldown."""

    threshold: int = _BREAKER_THRESHOLD
    cooldown: float = _BREAKER_COOLDOWN_SEC
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        """Return True if the next call should proceed."""
        with self._lock:
            if self._opened_at == 0.0:
                return True
            if time.monotonic() - self._opened_at >= self.cooldown:
                # half-open: let one call through to probe
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold and self._opened_at == 0.0:
                self._opened_at = time.monotonic()
                logger.warning(
                    "graphiti: circuit breaker OPEN after %d failures "
                    "(cooldown %.1fs)",
                    self._failures,
                    self.cooldown,
                )

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "failures": self._failures,
                "open": self._opened_at != 0.0,
                "opens_until": (
                    self._opened_at + self.cooldown
                    if self._opened_at
                    else 0.0
                ),
            }


# ---------------------------------------------------------------------------
# Async loop runner (one per process)
# ---------------------------------------------------------------------------

class _LoopThread:
    """Background thread running an asyncio event loop forever."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="graphiti-loop", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def submit(self, coro, *, timeout: float = _CALL_TIMEOUT_SEC):
        """Submit a coroutine; block (in caller) until result or timeout.

        Auto-starts the loop thread if not already running, so callers
        that build a bare ``GraphitiClient`` (e.g. tests, CLI) don't have
        to remember to ``start()`` first.  ``GraphitiClientManager`` also
        starts it eagerly in ``__init__``; this is the safety net.
        """
        if self._loop is None or not self._loop.is_running():
            self.start()
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("graphiti loop thread failed to start")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)


# Module-level singleton — one loop thread services all clients.
_LOOP = _LoopThread()


# ---------------------------------------------------------------------------
# Per-scope client
# ---------------------------------------------------------------------------

class GraphitiClient:
    """One Graphiti instance bound to one FalkorDB/Neo4j graph name."""

    def __init__(
        self,
        graph_name: str,
        *,
        falkordb_host: str = "localhost",
        falkordb_port: int = 6380,
        falkordb_password: str = "",
        embedding_provider: str = "local",
        embedding_model: str = "bge-m3",
        ontology_kwargs: Optional[Dict[str, Any]] = None,
        # Backend selection (Phase 5.4).  Default keeps falkordb so
        # existing call sites are unchanged.
        backend: str = "falkordb",
        uri: Optional[str] = None,           # neo4j: bolt://host:port
        user: Optional[str] = None,          # neo4j: username
        multi_db: bool = True,               # neo4j: Enterprise multi-db?
        # LLM endpoint (Phase 5c).  When provider="openai" with no
        # base_url, falls back to graphiti-core's default OpenAIClient
        # (needs OPENAI_API_KEY).  Point base_url at any OpenAI-
        # compatible endpoint (Ollama / vLLM / LiteLLM) to run fully
        # local — the api_key still has to be set (most servers ignore
        # the value but the SDK refuses to construct without one) so
        # we accept any non-empty placeholder.
        llm_provider: str = "openai",
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_small_model: Optional[str] = None,
    ) -> None:
        self.graph_name = graph_name
        # Connection params — interpretation depends on backend.
        self._host = falkordb_host
        self._port = falkordb_port
        self._password = falkordb_password
        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._ontology_kwargs = dict(ontology_kwargs or {})
        self._backend = backend
        self._uri = uri
        self._user = user
        self._multi_db = multi_db
        # LLM endpoint.
        self._llm_provider = llm_provider
        self._llm_base_url = llm_base_url
        self._llm_api_key = llm_api_key
        self._llm_model = llm_model
        self._llm_small_model = llm_small_model
        self._g: Any = None          # graphiti_core.Graphiti instance
        # Lightweight driver cache for query-only ops (ping, dump,
        # timeline).  Built lazily, reused across calls so we don't
        # spawn a fresh Redis connection pool and a fresh background
        # `build_indices_and_constraints` task on every probe.  See
        # _driver_for_queries() for the lifecycle.
        self._query_driver: Any = None
        self._initialized = False
        self.breaker = CircuitBreaker()

    async def _driver_for_queries(self) -> Any:
        """Lazy, reusable driver for query-only ops.

        graphiti-core's FalkorDriver.__init__ schedules a background
        ``build_indices_and_constraints`` task on every construction,
        which leaks "Task exception was never retrieved" warnings when
        the driver is closed before that task finishes.  Caching the
        driver moves that one-time setup cost off the hot path and
        avoids the leak.
        """
        if self._query_driver is not None:
            return self._query_driver
        self._query_driver = _build_driver(
            backend=getattr(self, "_backend", "falkordb"),
            graph_name=self.graph_name,
            host=self._host,
            port=self._port,
            password=self._password,
            uri=getattr(self, "_uri", None),
            user=getattr(self, "_user", None),
            multi_db=getattr(self, "_multi_db", True),
        )
        return self._query_driver

    # -- lifecycle --------------------------------------------------------

    async def _ensure(self) -> None:
        if self._initialized:
            return
        # Imports deferred to call time so the plugin module itself loads
        # without graphiti-core installed (gap §0 — discovery before deps).
        from graphiti_core import Graphiti  # type: ignore

        driver = _build_driver(
            backend=getattr(self, "_backend", "falkordb"),
            graph_name=self.graph_name,
            host=self._host,
            port=self._port,
            password=self._password,
            uri=getattr(self, "_uri", None),
            user=getattr(self, "_user", None),
            multi_db=getattr(self, "_multi_db", True),
        )
        # Build embedder + LLM client based on config so users who
        # picked `embedding_provider: local` actually GET local embeddings
        # (and not graphiti-core's silent fallback to OpenAI on no key).
        embedder = _build_embedder(
            provider=getattr(self, "_embedding_provider", "local"),
            model=getattr(self, "_embedding_model", None),
        )
        llm_client = _build_llm_client(
            provider=getattr(self, "_llm_provider", "openai"),
            base_url=getattr(self, "_llm_base_url", None),
            api_key=getattr(self, "_llm_api_key", None),
            model=getattr(self, "_llm_model", None),
            small_model=getattr(self, "_llm_small_model", None),
        )
        graphiti_kwargs: Dict[str, Any] = {"graph_driver": driver}
        if embedder is not None:
            graphiti_kwargs["embedder"] = embedder
        if llm_client is not None:
            graphiti_kwargs["llm_client"] = llm_client
        self._g = Graphiti(**graphiti_kwargs)
        await self._g.build_indices_and_constraints()
        self._initialized = True
        logger.info(
            "graphiti: client ready (backend=%s graph=%s embedder=%s llm=%s)",
            getattr(self, "_backend", "falkordb"),
            self.graph_name,
            getattr(self, "_embedding_provider", "local"),
            getattr(self, "_llm_provider", "openai"),
        )

    async def _ping(self) -> bool:
        """Cheap reachability probe — bypasses graphiti_core init.

        Reuses the cached query driver so we don't spawn a fresh
        Redis connection pool (and a fresh stray
        ``build_indices_and_constraints`` background task) on every
        probe.  Doctor's per-second ping cadence stays cheap.
        """
        driver = await self._driver_for_queries()
        await driver.execute_query("RETURN 1 AS ok")
        return True

    # -- operations -------------------------------------------------------

    async def _add_episode(
        self,
        content: str,
        source_description: str,
        reference_time: Optional[str],
    ) -> Dict[str, Any]:
        await self._ensure()
        from datetime import datetime, timezone

        ts = (
            datetime.fromisoformat(reference_time)
            if reference_time
            else datetime.now(timezone.utc)
        )
        result = await self._g.add_episode(
            name=f"{self.graph_name}:{ts.isoformat()}",
            episode_body=content,
            source_description=source_description or "agent",
            reference_time=ts,
            # group_id = defense-in-depth tenant tag.  Per-graph FalkorDB
            # databases (driver.database = graph_name) already keep
            # writes physically separate; group_id ensures the data
            # remains tagged even if a future deployment moves to a
            # shared database with multi-tenant filtering at the query
            # layer (Neo4j Community fallback path).
            group_id=self.graph_name,
            # entity_types / edge_types / edge_type_map come from
            # plugins/memory/graphiti/ontology.py when an ontology.yaml
            # is present; otherwise these are empty and graphiti-core
            # falls back to its learned extraction mode.
            **self._ontology_kwargs,
        )
        # AddEpisodeResults.episode.uuid is the episode UUID; older
        # graphiti-core versions returned the episode directly.
        episode_obj = getattr(result, "episode", result)
        episode_id = getattr(episode_obj, "uuid", None) or str(episode_obj)
        return {
            "episode_id": episode_id,
            "graph": self.graph_name,
        }

    async def _search_facts(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        await self._ensure()
        edges = await self._g.search(
            query=query,
            num_results=max_results,
            # group_ids = defense-in-depth tenant filter.  See _add_episode
            # for the rationale; here we restrict reads to the same graph
            # name we'd write to.
            group_ids=[self.graph_name],
        )
        return [
            {
                "fact": getattr(e, "fact", str(e)),
                "valid_at": str(getattr(e, "valid_at", "")) or None,
                "invalid_at": str(getattr(e, "invalid_at", "")) or None,
                "graph": self.graph_name,
                "source": getattr(e, "source_node_uuid", None),
                "target": getattr(e, "target_node_uuid", None),
                "episode_id": (
                    getattr(e, "episodes", None)[0]
                    if getattr(e, "episodes", None)
                    else None
                ),
            }
            for e in edges
        ]

    async def _search_nodes(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        await self._ensure()
        # graphiti-core's search() returns edges (EntityEdges).  Use
        # search_() to get the structured SearchResults object that
        # includes node hits.  Older versions exposed search_nodes
        # directly; we try both for forward compatibility.
        search_nodes = getattr(self._g, "search_nodes", None)
        if search_nodes is not None:
            nodes = await search_nodes(query=query, num_results=max_results)
        else:
            search_ = getattr(self._g, "search_", None)
            if search_ is None:
                return []
            from graphiti_core.search.search_config_recipes import (  # type: ignore
                NODE_HYBRID_SEARCH_RRF,
            )
            cfg = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
            cfg.limit = max_results
            results = await search_(
                query=query,
                config=cfg,
                group_ids=[self.graph_name],
            )
            nodes = getattr(results, "nodes", []) or []
        return [
            {
                "node_id": getattr(n, "uuid", None) or str(n),
                "name": getattr(n, "name", "") or "",
                "summary": getattr(n, "summary", "") or "",
                "graph": self.graph_name,
            }
            for n in nodes
        ]

    async def _get_node_timeline(
        self,
        node_id: str,
        since: Optional[str],
        until: Optional[str],
    ) -> List[Dict[str, Any]]:
        # Bypass _ensure() — like _ping and _dump, timeline read is a
        # query-only operation and must not depend on OPENAI_API_KEY.
        driver = await self._driver_for_queries()
        if driver is None or not hasattr(driver, "execute_query"):
            return []
        cypher = (
            "MATCH (n {uuid: $node_id})-[r:RELATES_TO]-(m) "
            "RETURN r.uuid AS uuid, r.fact AS fact, "
            "r.valid_at AS valid_at, r.invalid_at AS invalid_at, "
            "r.created_at AS created_at, r.episodes AS episodes"
        )
        try:
            rows = await driver.execute_query(cypher, node_id=node_id)
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in _normalize_query_result(rows):
            va = row.get("valid_at")
            ia = row.get("invalid_at")
            ca = row.get("created_at")
            va = str(va) if va else None
            ia = str(ia) if ia else None
            ca = str(ca) if ca else None
            if since and va and va < since:
                continue
            if until and va and va > until:
                continue
            episodes = row.get("episodes")
            out.append(
                {
                    "fact": row.get("fact"),
                    "valid_at": va,
                    "invalid_at": ia,
                    "observed_at": ca,        # when intellect first stored it
                    "episode_id": (episodes or [None])[0],
                }
            )
        out.sort(key=lambda x: x.get("valid_at") or "")
        return out

    async def _delete_episode(self, episode_id: str) -> Dict[str, Any]:
        await self._ensure()
        # graphiti-core 0.29: Graphiti.remove_episode(episode_uuid)
        # Older releases used delete_episode; accept both for forward compat.
        delete = getattr(self._g, "remove_episode", None) or getattr(
            self._g, "delete_episode", None
        )
        if delete is None:
            return {
                "deleted": False,
                "reason": "graphiti-core version lacks remove_episode",
            }
        await delete(episode_id)
        return {"deleted": True, "episode_id": episode_id}

    async def _build_communities(self) -> Dict[str, Any]:
        """Re-cluster nodes into communities for this scope (Phase 5.5).

        Communities are a Graphiti feature that groups closely-related
        entities into clusters and summarises each one.  They make
        downstream search results more navigable but are expensive to
        compute — run via the scheduled CLI, not on every turn.
        """
        await self._ensure()
        build = getattr(self._g, "build_communities", None)
        if build is None:
            return {"built": False, "reason": "build_communities not available"}
        nodes, edges = await build(group_ids=[self.graph_name])
        return {
            "built": True,
            "graph": self.graph_name,
            "community_count": len(nodes),
            "community_edge_count": len(edges),
        }

    async def _dump(self) -> Dict[str, Any]:
        """Cypher-level export of this scope's graph (Phase 5.6).

        Portable across FalkorDB and Neo4j: we don't rely on RDB
        snapshots (which only work for FalkorDB and live in the
        container's filesystem).  Two queries — one for nodes, one
        for edges — both filtered by ``group_id`` so a dump of one
        tenant graph can't accidentally include another's data even
        if the backend uses a shared database (Neo4j Community mode).

        Returns ``{nodes: [...], edges: [...]}`` ready for the CLI
        to write out as JSON-lines or a single JSON document.

        Bypasses ``_ensure()`` (and thus the Graphiti LLM/embedder
        wiring) for the same reason ``_ping`` does — dumping a graph
        is a backup operation that must not depend on an OpenAI key
        being configured at the moment of the dump.
        """
        driver = await self._driver_for_queries()
        if driver is None or not hasattr(driver, "execute_query"):
            return {"nodes": [], "edges": [], "error": "driver lacks execute_query"}

        nodes_cypher = (
            "MATCH (n) WHERE n.group_id = $g "
            "RETURN n.uuid AS uuid, n.name AS name, "
            "n.summary AS summary, n.group_id AS group_id, "
            "labels(n) AS labels"
        )
        edges_cypher = (
            "MATCH (s)-[r]->(t) WHERE r.group_id = $g "
            "RETURN r.uuid AS uuid, type(r) AS type, "
            "s.uuid AS source_uuid, t.uuid AS target_uuid, "
            "r.fact AS fact, r.valid_at AS valid_at, "
            "r.invalid_at AS invalid_at, r.created_at AS created_at, "
            "r.group_id AS group_id, r.episodes AS episodes"
        )
        try:
            nodes_rows = await driver.execute_query(
                nodes_cypher, g=self.graph_name
            )
            edges_rows = await driver.execute_query(
                edges_cypher, g=self.graph_name
            )
        except Exception as exc:
            return {"nodes": [], "edges": [], "error": str(exc)}
        return {
            "graph": self.graph_name,
            "nodes": [
                {k: _to_jsonable(v) for k, v in row.items()}
                for row in _normalize_query_result(nodes_rows)
            ],
            "edges": [
                {k: _to_jsonable(v) for k, v in row.items()}
                for row in _normalize_query_result(edges_rows)
            ],
        }

    async def _stats(self) -> Dict[str, Any]:
        await self._ensure()
        # Best-effort node/edge counts via the underlying driver.
        # graphiti_core 0.29 exposes the driver as either .driver or
        # .graph_driver depending on construction path.
        driver = getattr(self._g, "driver", None) or getattr(
            self._g, "graph_driver", None
        )
        if driver is None or not hasattr(driver, "execute_query"):
            return {"graph": self.graph_name, "available": True}
        try:
            n = await driver.execute_query("MATCH (n) RETURN count(n) AS c")
            e = await driver.execute_query("MATCH ()-[r]->() RETURN count(r) AS c")
            return {
                "graph": self.graph_name,
                "nodes": _first_count(n),
                "edges": _first_count(e),
            }
        except Exception as exc:
            return {"graph": self.graph_name, "error": str(exc)}

    async def _shutdown(self) -> None:
        if self._g is not None:
            close = getattr(self._g, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    logger.debug("graphiti: client close failed: %s", exc)
        if self._query_driver is not None:
            close = getattr(self._query_driver, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    logger.debug(
                        "graphiti: query-driver close failed: %s", exc
                    )
        self._g = None
        self._query_driver = None
        self._initialized = False

    # -- sync façade (used by tool handlers) -----------------------------

    def add_episode(self, **kwargs) -> Dict[str, Any]:
        return self._call(self._add_episode(**kwargs))

    def search_facts(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        return self._call(self._search_facts(query, max_results))

    def search_nodes(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        return self._call(self._search_nodes(query, max_results))

    def get_node_timeline(
        self, node_id: str, since: Optional[str] = None, until: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return self._call(self._get_node_timeline(node_id, since, until))

    def delete_episode(self, episode_id: str) -> Dict[str, Any]:
        return self._call(self._delete_episode(episode_id))

    def build_communities(self) -> Dict[str, Any]:
        # Longer timeout — community detection touches every node in the
        # graph; 10 s is too tight for anything non-trivial.
        return self._call(self._build_communities(), timeout=120.0)

    def dump(self) -> Dict[str, Any]:
        # Same rationale as build_communities — touches every node/edge.
        return self._call(self._dump(), timeout=120.0)

    def ping(self) -> bool:
        try:
            return self._call(self._ping())
        except Exception as exc:
            logger.warning(
                "graphiti: ping failed for %s: %s", self.graph_name, exc
            )
            return False

    def stats(self) -> Dict[str, Any]:
        return self._call(self._stats())

    def shutdown(self) -> None:
        try:
            self._call(self._shutdown(), timeout=3.0)
        except Exception:
            pass

    def _call(self, coro, *, timeout: float = _CALL_TIMEOUT_SEC):
        """Submit ``coro`` to the bg loop, applying the circuit breaker."""
        if not self.breaker.allow():
            raise GraphitiUnavailable(
                f"graphiti circuit breaker OPEN for {self.graph_name}"
            )
        try:
            result = _LOOP.submit(coro, timeout=timeout)
            self.breaker.record_success()
            return result
        except Exception as exc:
            self.breaker.record_failure()
            raise


def _first_count(rows: Any) -> int:
    """Extract the first integer from an execute_query result.

    Tolerates both FalkorDB's ``(rows, headers, summary)`` tuple shape
    and Neo4j's list-of-mappings shape.
    """
    try:
        for row in _normalize_query_result(rows):
            for v in row.values():
                return int(v)
    except Exception:
        pass
    return -1


def _normalize_query_result(result: Any) -> List[Dict[str, Any]]:
    """Normalize ``driver.execute_query`` output into a list of dicts.

    FalkorDB returns ``(rows, headers, summary)`` where ``rows`` is a
    list of column-value lists.  Neo4j returns a list of row objects
    with a ``keys()`` / ``__getitem__`` mapping protocol.  This helper
    accepts either shape and produces ``[{col: val, ...}, ...]``.
    """
    # FalkorDB tuple shape
    if isinstance(result, tuple) and len(result) >= 2 and isinstance(result[1], list):
        rows, headers = result[0], result[1]
        out: List[Dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, (list, tuple)):
                out.append(
                    {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                )
            elif isinstance(row, dict):
                out.append(dict(row))
            else:
                out.append({"_raw": str(row)})
        return out

    # Neo4j-style list-of-mappings (or anything iterable of mapping-likes)
    if result is None:
        return []
    try:
        return [_row_to_dict(r) for r in result]
    except TypeError:
        return []


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Best-effort row → dict.  Drivers return mappings, dicts, or tuples
    depending on version; we accept all three and degrade to a string
    representation when nothing else fits.
    """
    if isinstance(row, dict):
        return {k: _to_jsonable(v) for k, v in row.items()}
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            return {k: _to_jsonable(row[k]) for k in keys()}
        except Exception:
            pass
    return {"_raw": str(row)}


def _to_jsonable(v: Any) -> Any:
    """Convert datetime / set / arbitrary objects to JSON-safe values."""
    from datetime import datetime, date

    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _build_driver(
    *,
    backend: str,
    graph_name: str,
    host: str,
    port: int,
    password: str = "",
    uri: Optional[str] = None,
    user: Optional[str] = None,
    multi_db: bool = True,
) -> Any:
    """Build the appropriate graphiti-core driver for ``backend``.

    ``backend`` is one of:
      - ``"falkordb"`` (default) — FalkorDriver, ``database = graph_name``
      - ``"neo4j"`` with ``multi_db=True`` (Enterprise) — Neo4jDriver,
        ``database = graph_name``.  One database per tenant graph.
      - ``"neo4j"`` with ``multi_db=False`` (Community) — Neo4jDriver,
        ``database = "neo4j"`` (the only database Community allows).
        Tenant isolation relies on ``group_id`` filtering at the query
        layer; the same defense-in-depth tag we already write to every
        episode (see ``_add_episode``).

    Imports are deferred so callers that never use neo4j don't pay the
    import cost.
    """
    if backend == "falkordb":
        from graphiti_core.driver.falkordb_driver import FalkorDriver  # type: ignore

        return FalkorDriver(
            host=host,
            port=port,
            password=password or None,
            database=graph_name,
        )
    if backend == "neo4j":
        from graphiti_core.driver.neo4j_driver import Neo4jDriver  # type: ignore

        effective_db = graph_name if multi_db else "neo4j"
        return Neo4jDriver(
            uri=uri or f"bolt://{host}:{port}",
            user=user,
            password=password or None,
            database=effective_db,
        )
    raise ValueError(
        f"unknown graphiti backend {backend!r}; expected falkordb or neo4j"
    )


def _build_embedder(
    *,
    provider: str,
    model: Optional[str] = None,
) -> Any:
    """Build a graphiti-core ``EmbedderClient`` for ``provider``.

    Returns ``None`` to let graphiti-core fall back to its default
    (OpenAIEmbedder, requires OPENAI_API_KEY).

    Supported providers:
      - ``"local"``  — FastembedEmbedder (CPU/ONNX, bge-m3 default,
                       no API key required).  Recommended default.
      - ``"openai"`` — graphiti-core's built-in OpenAIEmbedder.
                       Requires OPENAI_API_KEY.

    Future providers (gemini / voyage / azure) can be wired the same
    way; for now the plugin only declares the two that matter most.
    """
    p = (provider or "").strip().lower()
    if p == "local":
        from .embedder_local import FastembedEmbedder
        return FastembedEmbedder(model=model)
    if p in ("openai", ""):
        return None      # let graphiti-core use its default
    raise ValueError(
        f"unknown graphiti embedding_provider {provider!r}; "
        f"expected one of: local, openai"
    )


def _build_llm_client(
    *,
    provider: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    small_model: Optional[str] = None,
) -> Any:
    """Build a graphiti-core ``LLMClient``.

    Returns ``None`` to let graphiti-core fall back to its default
    (OpenAIClient, requires OPENAI_API_KEY).

    Supported providers:
      - ``"openai"``        — graphiti-core's default; requires
                              OPENAI_API_KEY unless ``base_url`` is set.
      - ``"openai_compat"`` — Same OpenAIClient but with a custom
                              ``base_url`` (Ollama / vLLM / LiteLLM /
                              local LMStudio etc.).  ``api_key`` may
                              be a placeholder string (most local
                              servers ignore the value but the SDK
                              refuses to construct without one).
    """
    p = (provider or "").strip().lower()
    # Normalize empty provider to "openai" so no input falls into the dead
    # zone between the two guards below.
    if not p:
        p = "openai"
    if p in ("", "openai") and not base_url:
        return None      # let graphiti-core use its default

    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_client import OpenAIClient

    # Providers that always require a custom base_url — local endpoints
    # that can't work against api.openai.com.
    _NEEDS_BASE_URL = frozenset({"ollama", "vllm", "litellm", "openai_compat"})
    if p in _NEEDS_BASE_URL and not base_url:
        raise ValueError(
            f"graphiti llm_provider {p!r} requires llm_base_url to be set "
            f"(e.g. http://localhost:11434/v1 for Ollama)"
        )

    if p in ("openai", "openai_compat", "ollama", "vllm", "litellm"):
        cfg = LLMConfig(
            api_key=api_key or "sk-not-used-by-local-endpoint",
            base_url=base_url,
            model=model,
            small_model=small_model,
        )
        return OpenAIClient(config=cfg)
    raise ValueError(
        f"unknown graphiti llm_provider {provider!r}; "
        f"expected one of: openai, openai_compat, ollama, vllm, litellm"
    )


class GraphitiUnavailable(RuntimeError):
    """Raised when the circuit breaker is open or FalkorDB is unreachable."""


# ---------------------------------------------------------------------------
# Scope routing
# ---------------------------------------------------------------------------

@dataclass
class _Scope:
    member_id: Optional[str] = None
    team_id: Optional[str] = None
    project_id: Optional[str] = None

    def graphs_for(self, scope: str) -> List[str]:
        """Resolve the ``scope`` tool argument to a list of graph names."""
        member_graph = (
            f"member_{self.member_id}" if self.member_id else "global"
        )
        team_graph = f"team_{self.team_id}" if self.team_id else None
        project_graph = (
            f"project_{self.project_id}" if self.project_id else None
        )

        if scope == "auto":
            out = [member_graph]
            if team_graph:
                out.append(team_graph)
            return out
        if scope == "member":
            return [member_graph]
        if scope == "team":
            return [team_graph] if team_graph else []
        if scope == "project":
            return [project_graph] if project_graph else []
        if scope == "all":
            out = [member_graph]
            if team_graph:
                out.append(team_graph)
            if project_graph:
                out.append(project_graph)
            return out
        return [member_graph]

    def write_graph(self) -> str:
        """Writes always go to the member graph (or global if no member)."""
        return f"member_{self.member_id}" if self.member_id else "global"


class GraphitiClientManager:
    """Owns the loop thread + per-graph client cache."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        ontology_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._config = config
        self._ontology_kwargs = dict(ontology_kwargs or {})
        self._clients: Dict[str, GraphitiClient] = {}
        self._lock = threading.Lock()
        self._scope = _Scope()
        _LOOP.start()

    # -- scope binding ---------------------------------------------------

    def bind_scope(
        self,
        *,
        member_id: Optional[str],
        team_id: Optional[str],
        project_id: Optional[str],
    ) -> None:
        self._scope = _Scope(
            member_id=member_id, team_id=team_id, project_id=project_id
        )

    # -- client factory --------------------------------------------------

    def _client(self, graph_name: str) -> GraphitiClient:
        with self._lock:
            c = self._clients.get(graph_name)
            if c is None:
                backend = (self._config.get("backend") or "falkordb").strip().lower()
                c = GraphitiClient(
                    graph_name=graph_name,
                    falkordb_host=self._config.get("falkordb_host", "localhost"),
                    falkordb_port=int(self._config.get("falkordb_port", 6380)),
                    falkordb_password=self._config.get("falkordb_password", ""),
                    embedding_provider=self._config.get(
                        "embedding_provider", "local"
                    ),
                    embedding_model=self._config.get(
                        "embedding_model", "bge-m3"
                    ),
                    ontology_kwargs=self._ontology_kwargs,
                    backend=backend,
                    uri=self._config.get("neo4j_uri"),
                    user=self._config.get("neo4j_user"),
                    multi_db=bool(self._config.get("neo4j_multi_db", True)),
                    llm_provider=self._config.get("llm_provider", "openai"),
                    llm_base_url=self._config.get("llm_base_url"),
                    llm_api_key=self._config.get("llm_api_key"),
                    llm_model=self._config.get("llm_model"),
                    llm_small_model=self._config.get("llm_small_model"),
                )
                self._clients[graph_name] = c
            return c

    # -- operations (dispatched from tool handlers) ----------------------

    def add_episode(
        self,
        content: str,
        *,
        source_description: str = "agent",
        reference_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        graph = self._scope.write_graph()
        return self._client(graph).add_episode(
            content=content,
            source_description=source_description,
            reference_time=reference_time,
        )

    def search_facts(
        self, query: str, *, max_results: int = 10, scope: str = "auto"
    ) -> List[Dict[str, Any]]:
        max_results = max(1, min(int(max_results or 10), 50))
        graphs = self._scope.graphs_for(scope)
        merged: List[Dict[str, Any]] = []
        for g in graphs:
            try:
                merged.extend(self._client(g).search_facts(query, max_results))
            except GraphitiUnavailable as exc:
                logger.warning("graphiti: %s skipped: %s", g, exc)
        return merged[:max_results]

    def search_nodes(
        self, query: str, *, max_results: int = 10, scope: str = "auto"
    ) -> List[Dict[str, Any]]:
        max_results = max(1, min(int(max_results or 10), 50))
        graphs = self._scope.graphs_for(scope)
        merged: List[Dict[str, Any]] = []
        for g in graphs:
            try:
                merged.extend(self._client(g).search_nodes(query, max_results))
            except GraphitiUnavailable as exc:
                logger.warning("graphiti: %s skipped: %s", g, exc)
        return merged[:max_results]

    def get_node_timeline(
        self,
        node_id: str,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Timeline scoped to the write graph by default; agent can pass a
        # node_id from any graph the search returned.
        for g in self._scope.graphs_for("all"):
            try:
                out = self._client(g).get_node_timeline(node_id, since, until)
                if out:
                    return out
            except GraphitiUnavailable:
                continue
        return []

    def delete_episode(self, episode_id: str) -> Dict[str, Any]:
        # Writes go to the member graph; mirror that for deletes.
        graph = self._scope.write_graph()
        return self._client(graph).delete_episode(episode_id)

    def rebuild_communities(self, *, scope: str = "all") -> Dict[str, Any]:
        """Re-cluster nodes for the requested scope graphs.

        Returns ``{<graph_name>: <stats_dict>}``.  Errors per-graph are
        recorded under ``error`` rather than propagated so the operator
        sees partial success when only one tenant's graph misbehaves.
        Use the long-timeout sync wrapper in GraphitiClient.
        """
        graphs = self._scope.graphs_for(scope)
        out: Dict[str, Any] = {}
        for g in graphs:
            try:
                out[g] = self._client(g).build_communities()
            except Exception as exc:
                out[g] = {"built": False, "error": str(exc)}
        return out

    def dump(self, *, scope: str = "all") -> Dict[str, Any]:
        """Cypher-level export of every graph in the requested scope.

        Returns ``{<graph_name>: {nodes: [...], edges: [...]}}``.
        Per-graph errors are isolated (one graph failing does not
        prevent the others from dumping).  Intended for
        ``intellect graphiti dump`` — see CLI for serialization to
        disk.
        """
        graphs = self._scope.graphs_for(scope)
        out: Dict[str, Any] = {}
        for g in graphs:
            try:
                out[g] = self._client(g).dump()
            except Exception as exc:
                out[g] = {"nodes": [], "edges": [], "error": str(exc)}
        return out

    def stats(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"graphs": {}}
        for g in self._scope.graphs_for("all"):
            try:
                out["graphs"][g] = self._client(g).stats()
            except GraphitiUnavailable as exc:
                out["graphs"][g] = {"error": str(exc)}
        out["circuit_breakers"] = {
            g: c.breaker.snapshot() for g, c in self._clients.items()
        }
        return out

    def ping(self) -> Dict[str, bool]:
        return {
            g: self._client(g).ping() for g in self._scope.graphs_for("all")
        }

    def shutdown(self) -> None:
        with self._lock:
            for c in self._clients.values():
                c.shutdown()
            self._clients.clear()
