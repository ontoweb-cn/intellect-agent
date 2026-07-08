"""Verification evidence storage — thin Python wrapper over Rust backend (HP-303).

All operations are fail-open: a write failure logs a debug message and returns,
never blocks the agent turn.  The config gate ``agent.verification.enabled``
(default False) controls whether evidence is collected at all.
"""

from __future__ import annotations

import logging
import uuid
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _rust_backend():
    """Lazy import so the module is importable even when the Rust extension
    is not built (e.g. during development / CI bootstrap)."""
    try:
        from intellect_rust import (
            rust_insert_verification_evidence as _insert,
            rust_query_verification_evidence as _query,
            rust_classify_verification_command as _classify,
        )
        return _insert, _query, _classify
    except ImportError:
        return None, None, None


def record_evidence(
    db_path: str,
    session_id: str,
    kind: str,
    command: str,
    exit_code: Optional[int] = None,
    output: str = "",
    passed: Optional[bool] = None,
    task_id: Optional[str] = None,
) -> bool:
    """Record a verification event.  Returns True on success, False on failure.

    Fail-open: this function NEVER raises.  Failures are logged and swallowed.
    """
    _insert, _, _ = _rust_backend()
    if _insert is None:
        return False

    ev_id = uuid.uuid4().hex[:12]
    summary = (output or "")[:500]
    created_at = int(time.time())

    try:
        _insert(
            db_path, ev_id, session_id, kind, command,
            summary, created_at, task_id, exit_code, passed,
        )
        return True
    except Exception:
        logger.debug("verification_evidence: write failed (fail-open)", exc_info=True)
        return False


def query_evidence(
    db_path: str,
    session_id: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query verification evidence.  Returns empty list on error."""
    _, _query, _ = _rust_backend()
    if _query is None:
        return []
    try:
        import json
        return json.loads(_query(db_path, session_id, kind, limit))
    except Exception:
        logger.debug("verification_evidence: query failed", exc_info=True)
        return []


def classify_command(command: str) -> Optional[str]:
    """Classify a shell command. Returns kind string or None."""
    _, _, _classify = _rust_backend()
    if _classify is None:
        return None
    try:
        return _classify(command)
    except Exception:
        return None


def is_verification_enabled(config: Optional[dict] = None) -> bool:
    """Check the config gate.  Defaults to False (opt-in)."""
    if config is None:
        try:
            from intellect_cli.config import load_config
            config = load_config() or {}
        except Exception:
            return False
    return bool(config.get("agent", {}).get("verification", {}).get("enabled", False))
