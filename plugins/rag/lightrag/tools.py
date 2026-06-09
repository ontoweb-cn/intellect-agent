"""LightRAG agent tool schemas."""

from __future__ import annotations

from typing import Any, Dict, List

from .client import SCOPE_CHOICES

# Tool name → RBAC sensitivity (see agent/member_rbac.py).
TOOL_RBAC: Dict[str, str] = {
    "lightrag_search": "read",
    "lightrag_query": "read",
    "lightrag_list_documents": "read",
    "lightrag_insert_text": "chat",
    "lightrag_upload_document": "chat",
    "lightrag_delete_document": "admin",
    "lightrag_clear_workspace": "admin",
}

SEARCH_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_search",
    "description": (
        "Retrieve relevant document context from the LightRAG knowledge base "
        "without generating an answer. Use when you need citations or chunks "
        "from uploaded docs, specs, or ingested notes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query."},
            "mode": {
                "type": "string",
                "enum": ["mix", "hybrid", "local", "global", "naive"],
                "description": "LightRAG retrieval mode. Default mix.",
            },
            "scope": {
                "type": "string",
                "enum": list(SCOPE_CHOICES),
                "description": "Workspace scope. Default auto.",
            },
        },
        "required": ["query"],
    },
}

QUERY_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_query",
    "description": (
        "Run a full LightRAG query that returns an answer plus references. "
        "Use when you need a direct RAG response with citations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Question to answer."},
            "mode": {
                "type": "string",
                "enum": ["mix", "hybrid", "local", "global", "naive"],
            },
            "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
            "enable_rerank": {
                "type": "boolean",
                "description": "Enable reranker for this query.",
            },
        },
        "required": ["query"],
    },
}

INSERT_TEXT_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_insert_text",
    "description": (
        "Insert a text fragment into the LightRAG document index. "
        "Triggers server-side entity extraction."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text content to index."},
            "file_path": {
                "type": "string",
                "description": "Optional logical label/path for the chunk.",
            },
            "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
        },
        "required": ["text"],
    },
}

UPLOAD_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_upload_document",
    "description": (
        "Upload a local file into the LightRAG index. Path must exist on the "
        "agent host (respects terminal working directory). For PDFs, Office "
        "docs, or images, set parse_engine and analyze_* flags to enable "
        "server-side multimodal parsing (MinerU/Docling/RagAnything pipeline)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the file.",
            },
            "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
            "parse_engine": {
                "type": "string",
                "enum": ["legacy", "native", "mineru", "docling"],
                "description": (
                    "Optional content extraction engine. Requires matching "
                    "LightRAG server configuration."
                ),
            },
            "process_options": {
                "type": "string",
                "description": (
                    "Optional raw process_options string (e.g. ietP). "
                    "Overrides analyze_* / chunking when set."
                ),
            },
            "analyze_images": {
                "type": "boolean",
                "description": "Enable VLM image analysis (process_options i).",
            },
            "analyze_tables": {
                "type": "boolean",
                "description": "Enable table analysis (process_options t).",
            },
            "analyze_equations": {
                "type": "boolean",
                "description": "Enable equation analysis (process_options e).",
            },
            "chunking": {
                "type": "string",
                "enum": ["F", "R", "V", "P"],
                "description": "Chunking strategy for structured documents.",
            },
        },
        "required": ["file_path"],
    },
}

LIST_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_list_documents",
    "description": "List documents and processing status in a workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
        },
    },
}

DELETE_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_delete_document",
    "description": (
        "Delete a document from the LightRAG index by doc_id. "
        "Requires a non-empty reason for the audit trail."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document identifier."},
            "reason": {
                "type": "string",
                "description": "Audit reason (required).",
            },
            "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
        },
        "required": ["doc_id", "reason"],
    },
}

CLEAR_SCHEMA: Dict[str, Any] = {
    "name": "lightrag_clear_workspace",
    "description": (
        "Clear all documents in a workspace. Destructive — requires reason."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Audit reason (required)."},
            "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
        },
        "required": ["reason"],
    },
}

ALL_SCHEMAS: List[Dict[str, Any]] = [
    SEARCH_SCHEMA,
    QUERY_SCHEMA,
    INSERT_TEXT_SCHEMA,
    UPLOAD_SCHEMA,
    LIST_SCHEMA,
    DELETE_SCHEMA,
    CLEAR_SCHEMA,
]
TOOL_NAMES = tuple(s["name"] for s in ALL_SCHEMAS)
