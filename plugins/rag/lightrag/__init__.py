"""LightRAG RAG plugin — remote API server client."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent.rag_provider import RAGProvider

from .client import LightRAGClientManager, LightRAGUnavailable
from .config import load_config
from .ingest import serialize_messages, summarize_exchange, summarize_text
from .prefetch import should_prefetch
from .tools import ALL_SCHEMAS

logger = logging.getLogger(__name__)


def _ok(**payload: Any) -> str:
    return json.dumps({"success": True, **payload})


def _err(msg: str, **payload: Any) -> str:
    return json.dumps({"success": False, "error": msg, **payload})


class LightRAGRAGProvider(RAGProvider):
    """Document RAG via external lightrag-server REST API."""

    def __init__(self) -> None:
        self._mgr: Optional[LightRAGClientManager] = None
        self._session_id: str = ""
        self._member_id: Optional[str] = None
        self._intellect_home: str = ""
        self._cfg: Dict[str, Any] = {}
        self._agent_config: Dict[str, Any] = {}
        self._agent_context: str = "primary"

    @property
    def name(self) -> str:
        return "lightrag"

    def is_available(self) -> bool:
        try:
            cfg = load_config()
            base = (cfg.get("server") or {}).get("base_url", "")
            return bool(base and str(base).strip())
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._member_id = kwargs.get("member_id")
        self._intellect_home = kwargs.get("intellect_home", "")
        self._agent_config = kwargs.get("config") or {}
        self._agent_context = kwargs.get("agent_context", "primary")
        self._cfg = load_config(self._intellect_home)

        self._mgr = LightRAGClientManager(self._cfg)
        self._mgr.bind_scope(
            member_id=self._member_id,
            team_id=kwargs.get("team_id"),
            project_id=kwargs.get("project_id"),
            session_id=session_id,
        )
        try:
            self._mgr.health()
            logger.info(
                "lightrag: server OK at %s",
                self._cfg.get("server", {}).get("base_url"),
            )
        except LightRAGUnavailable as exc:
            logger.warning("lightrag: health check failed: %s", exc)

    def system_prompt_block(self) -> str:
        ingest = (self._cfg.get("ingest") or {}).get("auto_mode", "off")
        return (
            "LightRAG document knowledge base is active. Tools: "
            "`lightrag_search` (context only), `lightrag_query` (answer+citations), "
            "`lightrag_insert_text`, `lightrag_upload_document`, "
            "`lightrag_list_documents`. Conversation auto-ingest is "
            f"{ingest!r}."
        )

    def _rag_config(self) -> Dict[str, Any]:
        return self._agent_config.get("rag") or {}

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._mgr:
            return ""
        rag_cfg = self._rag_config()
        if not should_prefetch(
            query,
            policy=rag_cfg.get("prefetch_policy", "hybrid"),
            min_chars=int(rag_cfg.get("prefetch_min_chars") or 40),
            keywords=rag_cfg.get("prefetch_keywords") or [],
        ):
            return ""
        qcfg = self._cfg.get("query") or {}
        mode = rag_cfg.get("prefetch_mode") or qcfg.get("prefetch_mode", "hybrid")
        try:
            return self._mgr.search(
                query,
                scope="auto",
                mode=mode,
                only_need_context=True,
                enable_rerank=bool(qcfg.get("enable_rerank", False)),
            )
        except LightRAGUnavailable:
            return ""
        except Exception as exc:
            logger.debug("lightrag prefetch failed: %s", exc)
            return ""

    def _ingest_text(self, text: str, *, label: str, scope: str = "auto") -> None:
        if not self._mgr or not text.strip():
            return
        try:
            self._mgr.insert_text(
                text,
                scope=scope,
                file_path=label,
            )
        except Exception as exc:
            logger.debug("lightrag: ingest skipped: %s", exc)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if self._agent_context != "primary" or not self._mgr:
            return
        ingest_cfg = self._cfg.get("ingest") or {}
        mode = ingest_cfg.get("auto_mode", "off")
        if mode == "off":
            return
        sid = session_id or self._session_id
        label = f"chat-turn-{sid}"
        if mode == "full":
            parts = []
            if user_content.strip():
                parts.append(f"User: {user_content.strip()}")
            if assistant_content.strip():
                parts.append(f"Assistant: {assistant_content.strip()}")
            self._ingest_text("\n".join(parts), label=label)
            return
        if mode == "summary":
            summary = summarize_exchange(
                user_content,
                assistant_content,
                max_tokens=int(ingest_cfg.get("summary_max_tokens") or 256),
            )
            if summary:
                self._ingest_text(summary, label=f"summary-{label}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        ingest_cfg = self._cfg.get("ingest") or {}
        min_turns = int(ingest_cfg.get("session_end_min_turns") or 3)
        if not self._mgr or not messages or len(messages) < min_turns:
            return
        body = serialize_messages(messages)
        if not body.strip():
            return
        label = f"session-end-{self._session_id}"
        mode = ingest_cfg.get("auto_mode", "off")
        if mode == "summary":
            summary = summarize_text(body, max_tokens=512)
            if summary:
                self._ingest_text(summary, label=label)
        elif mode == "full":
            self._ingest_text(body, label=label)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        ingest_cfg = self._cfg.get("ingest") or {}
        if not ingest_cfg.get("pre_compress", True):
            return ""
        if not self._mgr or not messages:
            return ""
        body = serialize_messages(messages)
        if not body.strip():
            return ""
        label = f"pre-compress-{self._session_id}"
        mode = ingest_cfg.get("auto_mode", "off")
        if mode == "off":
            return ""
        if mode == "summary":
            summary = summarize_text(body, max_tokens=512)
            if summary:
                self._ingest_text(summary, label=label)
        else:
            self._ingest_text(body, label=label)
        return (
            "Older turns were persisted to the LightRAG document index; "
            "use `lightrag_search` to recall them."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return list(ALL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._mgr:
            return _err("LightRAG not initialized")
        scope = args.get("scope", "auto")
        try:
            if tool_name == "lightrag_search":
                context = self._mgr.search(
                    args.get("query", ""),
                    scope=scope,
                    mode=args.get("mode"),
                )
                return _ok(context=context)
            if tool_name == "lightrag_query":
                result = self._mgr.query_answer(
                    args.get("query", ""),
                    scope=scope,
                    mode=args.get("mode"),
                    enable_rerank=args.get("enable_rerank"),
                )
                return _ok(result=result)
            if tool_name == "lightrag_insert_text":
                if self._agent_context != "primary":
                    return _err("insert not allowed in non-primary agent context")
                result = self._mgr.insert_text(
                    args.get("text", ""),
                    scope=scope,
                    file_path=args.get("file_path", ""),
                )
                return _ok(result=result)
            if tool_name == "lightrag_upload_document":
                if self._agent_context != "primary":
                    return _err("upload not allowed in non-primary agent context")
                result = self._mgr.upload_document(
                    args.get("file_path", ""),
                    scope=scope,
                    parse_engine=args.get("parse_engine"),
                    process_options=args.get("process_options"),
                    analyze_images=args.get("analyze_images"),
                    analyze_tables=args.get("analyze_tables"),
                    analyze_equations=args.get("analyze_equations"),
                    chunking=args.get("chunking"),
                )
                return _ok(result=result)
            if tool_name == "lightrag_list_documents":
                result = self._mgr.list_documents(scope=scope)
                return _ok(result=result)
            if tool_name == "lightrag_delete_document":
                err = self._check_admin_rbac(tool_name, args.get("reason", ""))
                if err:
                    return _err(err)
                result = self._mgr.delete_document(
                    args.get("doc_id", ""),
                    scope=scope,
                )
                return _ok(result=result)
            if tool_name == "lightrag_clear_workspace":
                err = self._check_admin_rbac(tool_name, args.get("reason", ""))
                if err:
                    return _err(err)
                result = self._mgr.clear_workspace(scope=scope)
                return _ok(result=result)
            return _err(f"unknown tool: {tool_name}")
        except LightRAGUnavailable as exc:
            return _err(str(exc))
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.error("lightrag tool %s failed: %s", tool_name, exc)
            return _err(str(exc))

    def _check_admin_rbac(self, tool_name: str, reason: str) -> Optional[str]:
        if not reason.strip():
            return "requires a non-empty `reason` for the audit trail"
        if not self._member_id:
            return None
        # (single-user: no tool RBAC)
        return None

    def shutdown(self) -> None:
        if self._mgr:
            try:
                self._mgr.shutdown()
            except Exception:
                pass
            self._mgr = None


def register(ctx) -> None:
    ctx.register_rag_provider(LightRAGRAGProvider())
