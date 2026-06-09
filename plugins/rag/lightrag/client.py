"""LightRAG REST client + per-scope workspace manager."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class LightRAGUnavailable(Exception):
    """Server unreachable or circuit open."""


@dataclass
class CircuitBreaker:
    threshold: int = 3
    cooldown: float = 30.0
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at == 0.0:
                return True
            if time.monotonic() - self._opened_at >= self.cooldown:
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
                    "lightrag: circuit breaker OPEN after %d failures",
                    self._failures,
                )


SCOPE_CHOICES = ("auto", "member", "team", "project", "all", "session")


@dataclass
class _Scope:
    member_id: Optional[str] = None
    team_id: Optional[str] = None
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    ws_cfg: Dict[str, Any] = field(default_factory=dict)

    def _name(self, kind: str, val: Optional[str]) -> Optional[str]:
        if not val:
            return None
        prefix = self.ws_cfg.get(f"{kind}_prefix", f"{kind}_")
        return f"{prefix}{val}"

    def workspaces_for(self, scope: str) -> List[str]:
        scope = (scope or "auto").strip().lower()
        default = self.ws_cfg.get("default", "global")
        session_ws = (
            f"{self.ws_cfg.get('session_prefix', 'session_')}{self.session_id}"
            if self.session_id
            else None
        )
        member_ws = self._name("member", self.member_id)
        team_ws = self._name("team", self.team_id)
        project_ws = self._name("project", self.project_id)

        if scope == "session":
            return [session_ws or default]
        if scope == "member":
            return [member_ws or default]
        if scope == "team":
            return [team_ws] if team_ws else [default]
        if scope == "project":
            return [project_ws] if project_ws else [default]
        if scope == "all":
            out = []
            for ws in (member_ws, team_ws, project_ws, session_ws, default):
                if ws and ws not in out:
                    out.append(ws)
            return out or [default]
        # auto: member + team when present
        out = []
        for ws in (member_ws, team_ws):
            if ws and ws not in out:
                out.append(ws)
        if not out:
            out = [session_ws or default]
        return out

    def write_workspace(self, scope: str = "auto") -> str:
        workspaces = self.workspaces_for(scope)
        return workspaces[0] if workspaces else self.ws_cfg.get("default", "global")


class LightRAGClient:
    """Sync httpx client for one LightRAG server."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        server = config.get("server") or {}
        self._base_url = (server.get("base_url") or "").rstrip("/")
        prefix = (server.get("api_prefix") or "").strip("/")
        if prefix:
            self._base_url = f"{self._base_url}/{prefix}"
        self._api_key = server.get("api_key") or ""
        self._timeout = float(server.get("timeout_seconds") or 120)
        cb_cfg = config.get("circuit_breaker") or {}
        self._breaker = CircuitBreaker(
            threshold=int(cb_cfg.get("threshold") or 3),
            cooldown=float(cb_cfg.get("cooldown_seconds") or 30),
        )
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=self._timeout)

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> Dict[str, Any]:
        if not self._base_url:
            raise LightRAGUnavailable("LightRAG base_url not configured")
        if not self._breaker.allow():
            raise LightRAGUnavailable("LightRAG circuit breaker open")
        try:
            r = self._client.get(self._url("/health"), headers=self._headers())
            r.raise_for_status()
            self._breaker.record_success()
            return r.json() if r.content else {"status": "ok"}
        except Exception as exc:
            self._breaker.record_failure()
            raise LightRAGUnavailable(str(exc)) from exc

    def query(
        self,
        query: str,
        *,
        workspace: str,
        mode: str = "mix",
        only_need_context: bool = True,
        enable_rerank: bool = False,
    ) -> Dict[str, Any]:
        if not self._breaker.allow():
            raise LightRAGUnavailable("LightRAG circuit breaker open")
        body: Dict[str, Any] = {
            "query": query,
            "mode": mode,
            "only_need_context": only_need_context,
            "enable_rerank": enable_rerank,
            "include_references": True,
        }
        if workspace:
            body["workspace"] = workspace
        try:
            r = self._client.post(
                self._url("/query"),
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            self._breaker.record_success()
            data = r.json()
            return data if isinstance(data, dict) else {"response": str(data)}
        except Exception as exc:
            self._breaker.record_failure()
            raise LightRAGUnavailable(str(exc)) from exc

    def insert_text(
        self,
        text: str,
        *,
        workspace: str,
        file_path: str = "",
    ) -> Dict[str, Any]:
        return self._post_json(
            "/documents/text",
            self._doc_body(text=text, file_path=file_path, workspace=workspace),
        )

    def insert_texts(
        self,
        items: List[Dict[str, str]],
        *,
        workspace: str,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"texts": items}
        if workspace:
            body["workspace"] = workspace
        return self._post_json("/documents/texts", body)

    def upload_file(
        self,
        path: Path,
        *,
        workspace: str,
        upload_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._breaker.allow():
            raise LightRAGUnavailable("LightRAG circuit breaker open")
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data: Dict[str, str] = {}
        if workspace:
            data["workspace"] = workspace
        fname = upload_name or path.name
        try:
            with path.open("rb") as fh:
                r = self._client.post(
                    self._url("/documents/upload"),
                    headers=headers,
                    data=data,
                    files={"file": (fname, fh)},
                )
            r.raise_for_status()
            self._breaker.record_success()
            payload = r.json()
            return payload if isinstance(payload, dict) else {"status": "ok"}
        except Exception as exc:
            self._breaker.record_failure()
            raise LightRAGUnavailable(str(exc)) from exc

    def list_documents(self, *, workspace: str) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if workspace:
            params["workspace"] = workspace
        if not self._breaker.allow():
            raise LightRAGUnavailable("LightRAG circuit breaker open")
        try:
            r = self._client.get(
                self._url("/documents"),
                headers=self._headers(),
                params=params or None,
            )
            r.raise_for_status()
            self._breaker.record_success()
            data = r.json()
            return data if isinstance(data, dict) else {"documents": data}
        except Exception as exc:
            self._breaker.record_failure()
            raise LightRAGUnavailable(str(exc)) from exc

    def delete_documents(
        self,
        doc_ids: List[str],
        *,
        workspace: str = "",
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"doc_ids": doc_ids}
        if workspace:
            body["workspace"] = workspace
        return self._delete_json("/documents/delete_document", body)

    def clear_documents(self, *, workspace: str) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if workspace:
            params["workspace"] = workspace
        return self._delete_json("/documents", None, params=params or None)

    def _doc_body(
        self, *, text: str, file_path: str, workspace: str
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"text": text}
        # LightRAG REST API uses ``file_source`` (``file_path`` is agent UX only).
        if file_path:
            body["file_source"] = file_path
        if workspace:
            body["workspace"] = workspace
        return body

    def _post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self._breaker.allow():
            raise LightRAGUnavailable("LightRAG circuit breaker open")
        try:
            r = self._client.post(
                self._url(path),
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            self._breaker.record_success()
            data = r.json()
            return data if isinstance(data, dict) else {"status": "ok"}
        except Exception as exc:
            self._breaker.record_failure()
            raise LightRAGUnavailable(str(exc)) from exc

    def _delete_json(
        self,
        path: str,
        body: Optional[Dict[str, Any]],
        *,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not self._breaker.allow():
            raise LightRAGUnavailable("LightRAG circuit breaker open")
        try:
            r = self._client.request(
                "DELETE",
                self._url(path),
                headers=self._headers(),
                json=body,
                params=params,
            )
            r.raise_for_status()
            self._breaker.record_success()
            if r.content:
                data = r.json()
                return data if isinstance(data, dict) else {"status": "ok"}
            return {"status": "ok"}
        except Exception as exc:
            self._breaker.record_failure()
            raise LightRAGUnavailable(str(exc)) from exc


def merge_query_results(results: List[Dict[str, Any]]) -> str:
    """Merge multi-workspace query payloads into plain context text."""
    parts: List[str] = []
    seen_refs: set = set()
    for data in results:
        response = data.get("response") or data.get("context") or ""
        if isinstance(response, str) and response.strip():
            parts.append(response.strip())
        refs = data.get("references") or []
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                key = (ref.get("file_path"), ref.get("reference_id"))
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                fp = ref.get("file_path") or ""
                content = ref.get("content")
                if isinstance(content, list):
                    for chunk in content:
                        if chunk:
                            parts.append(f"[{fp}] {chunk}")
                elif content:
                    parts.append(f"[{fp}] {content}")
    return "\n\n".join(parts)


class LightRAGClientManager:
    """Scope-aware facade over LightRAGClient."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._client = LightRAGClient(config)
        self._scope = _Scope(ws_cfg=config.get("workspace") or {})

    def bind_scope(
        self,
        *,
        member_id: Optional[str] = None,
        team_id: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._scope = _Scope(
            member_id=member_id,
            team_id=team_id,
            project_id=project_id,
            session_id=session_id,
            ws_cfg=self._config.get("workspace") or {},
        )

    def health(self) -> Dict[str, Any]:
        return self._client.health()

    def search(
        self,
        query: str,
        *,
        scope: str = "auto",
        mode: Optional[str] = None,
        only_need_context: bool = True,
        enable_rerank: Optional[bool] = None,
    ) -> str:
        qcfg = self._config.get("query") or {}
        mode = mode or qcfg.get("default_mode", "mix")
        if enable_rerank is None:
            enable_rerank = bool(qcfg.get("enable_rerank", False))
        workspaces = self._scope.workspaces_for(scope)

        shared = self._client._client

        def _query_ws(ws: str) -> Optional[Dict[str, Any]]:
            # Per-thread client sharing transport config — httpx.Client is
            # not thread-safe, so each worker gets its own instance.
            thread_client = LightRAGClient(
                self._config,
                http_client=httpx.Client(
                    transport=shared._transport,
                    timeout=shared.timeout,
                )
                if getattr(shared, "_transport", None)
                else None,
            )
            try:
                return thread_client.query(
                    query,
                    workspace=ws,
                    mode=mode,
                    only_need_context=only_need_context,
                    enable_rerank=enable_rerank,
                )
            except LightRAGUnavailable as exc:
                logger.warning(
                    "lightrag: workspace %s query skipped: %s", ws, exc
                )
                return None
            finally:
                thread_client.close()

        payloads = self._query_workspaces_parallel(workspaces, _query_ws)
        return merge_query_results(payloads)

    @staticmethod
    def _query_workspaces_parallel(
        workspaces: List[str],
        query_fn,
    ) -> List[Dict[str, Any]]:
        if not workspaces:
            return []
        if len(workspaces) == 1:
            one = query_fn(workspaces[0])
            return [one] if one else []

        max_workers = min(len(workspaces), 4)
        payloads: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(query_fn, ws): ws for ws in workspaces}
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    if result:
                        payloads.append(result)
                except Exception as exc:
                    ws = futures[fut]
                    logger.warning(
                        "lightrag: parallel query %s failed: %s", ws, exc
                    )
        return payloads

    def insert_text(
        self,
        text: str,
        *,
        scope: str = "auto",
        file_path: str = "",
    ) -> Dict[str, Any]:
        ws = self._scope.write_workspace(scope)
        return self._client.insert_text(text, workspace=ws, file_path=file_path)

    def insert_texts(
        self,
        items: List[Dict[str, str]],
        *,
        scope: str = "auto",
    ) -> Dict[str, Any]:
        ws = self._scope.write_workspace(scope)
        return self._client.insert_texts(items, workspace=ws)

    def query_answer(
        self,
        query: str,
        *,
        scope: str = "auto",
        mode: Optional[str] = None,
        enable_rerank: Optional[bool] = None,
    ) -> Dict[str, Any]:
        qcfg = self._config.get("query") or {}
        mode = mode or qcfg.get("default_mode", "mix")
        if enable_rerank is None:
            enable_rerank = bool(qcfg.get("enable_rerank", False))
        ws = self._scope.write_workspace(scope)
        return self._client.query(
            query,
            workspace=ws,
            mode=mode,
            only_need_context=False,
            enable_rerank=enable_rerank,
        )

    def upload_document(
        self,
        file_path: str,
        *,
        scope: str = "auto",
        parse_engine: Optional[str] = None,
        process_options: Optional[str] = None,
        analyze_images: Optional[bool] = None,
        analyze_tables: Optional[bool] = None,
        analyze_equations: Optional[bool] = None,
        chunking: Optional[str] = None,
        skip_kg: Optional[bool] = None,
    ) -> Dict[str, Any]:
        from .upload import build_process_options, build_upload_filename

        path = _resolve_upload_path(file_path)
        ws = self._scope.write_workspace(scope)
        ucfg = self._config.get("upload") or {}

        if process_options is None:
            process_options = build_process_options(
                analyze_images=(
                    analyze_images
                    if analyze_images is not None
                    else ucfg.get("analyze_images")
                ),
                analyze_tables=(
                    analyze_tables
                    if analyze_tables is not None
                    else ucfg.get("analyze_tables")
                ),
                analyze_equations=(
                    analyze_equations
                    if analyze_equations is not None
                    else ucfg.get("analyze_equations")
                ),
                chunking=chunking or ucfg.get("chunking"),
                skip_kg=skip_kg if skip_kg is not None else ucfg.get("skip_kg"),
                extra=ucfg.get("multimodal_default_options", ""),
            )
        if parse_engine is None:
            parse_engine = ucfg.get("default_parse_engine") or None

        upload_name = build_upload_filename(
            path,
            parse_engine=parse_engine,
            process_options=process_options or None,
        )
        return self._client.upload_file(
            path,
            workspace=ws,
            upload_name=upload_name,
        )

    def list_documents(self, *, scope: str = "auto") -> Dict[str, Any]:
        ws = self._scope.write_workspace(scope)
        return self._client.list_documents(workspace=ws)

    def delete_document(
        self,
        doc_id: str,
        *,
        scope: str = "auto",
    ) -> Dict[str, Any]:
        ws = self._scope.write_workspace(scope)
        return self._client.delete_documents([doc_id], workspace=ws)

    def clear_workspace(self, *, scope: str = "auto") -> Dict[str, Any]:
        ws = self._scope.write_workspace(scope)
        return self._client.clear_documents(workspace=ws)

    def workspace_stats(self, *, scope: str = "all") -> List[Dict[str, Any]]:
        """Document counts per workspace visible under *scope*."""
        workspaces = self._scope.workspaces_for(scope)
        stats: List[Dict[str, Any]] = []
        for ws in workspaces:
            count: Optional[int] = None
            error: Optional[str] = None
            try:
                data = self._client.list_documents(workspace=ws)
                docs = data.get("documents") or data.get("statuses") or []
                if isinstance(docs, list):
                    count = len(docs)
                elif isinstance(docs, dict):
                    count = len(docs)
            except LightRAGUnavailable as exc:
                error = str(exc)
            stats.append(
                {"workspace": ws, "document_count": count, "error": error}
            )
        return stats

    def shutdown(self) -> None:
        self._client.close()


def _resolve_upload_path(file_path: str) -> Path:
    """Resolve and validate a local file path for upload."""
    if not file_path or not str(file_path).strip():
        raise ValueError("file_path is required")
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    p = p.resolve()
    if not p.is_file():
        raise ValueError(f"file not found or not a regular file: {p}")
    return p
