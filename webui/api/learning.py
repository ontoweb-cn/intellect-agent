"""WebUI REST handlers for Journey / learning graph (HP-401e).

Profile scope follows ``/api/memory``: always the request cookie's active
profile via ``get_active_intellect_home()`` — no client-supplied profile override.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)


def _active_profile_context():
    from api.profiles import cron_profile_context_for_home, get_active_intellect_home

    return cron_profile_context_for_home(get_active_intellect_home())


def handle_learning_graph_get(handler, parsed) -> bool:
    from api.helpers import j

    try:
        from agent.learning_graph import build_learning_graph

        with _active_profile_context():
            payload = build_learning_graph()
        j(handler, payload)
        return True
    except Exception:
        logger.exception("GET /api/learning/graph failed")
        j(handler, {"error": "Failed to build learning graph"}, status=500)
        return True


def handle_learning_node_get(handler, parsed) -> bool:
    from api.helpers import bad, j

    qs = parse_qs(parsed.query or "")
    node_id = (qs.get("id") or [""])[0].strip()
    if not node_id:
        return bad(handler, "missing id query parameter")

    from agent.learning_mutations import node_detail

    with _active_profile_context():
        res = node_detail(node_id)
    if not res.get("ok"):
        code = res.get("code")
        status = 409 if code in ("stale", "ambiguous") else 404
        j(handler, {"error": res.get("message", "not found"), "code": code}, status=status)
        return True
    j(handler, res)
    return True


def handle_learning_node_delete(handler, body: dict[str, Any]) -> bool:
    from api.helpers import bad, j

    node_id = str((body or {}).get("id") or "").strip()
    if not node_id:
        return bad(handler, "missing id")

    from agent.learning_mutations import delete_node

    with _active_profile_context():
        res = delete_node(node_id)
    if not res.get("ok"):
        code = res.get("code")
        status = 409 if code in ("stale", "ambiguous") else 400
        j(handler, {"error": res.get("message", "delete failed"), "code": code}, status=status)
        return True
    j(handler, res)
    return True


def handle_learning_node_put(handler, body: dict[str, Any]) -> bool:
    from api.helpers import bad, j

    node_id = str((body or {}).get("id") or "").strip()
    content = (body or {}).get("content")
    if not node_id:
        return bad(handler, "missing id")
    if content is None:
        return bad(handler, "missing content")

    from agent.learning_mutations import edit_node

    with _active_profile_context():
        res = edit_node(node_id, str(content))
    if not res.get("ok"):
        code = res.get("code")
        status = 409 if code in ("stale", "ambiguous") else 400
        j(handler, {"error": res.get("message", "edit failed"), "code": code}, status=status)
        return True
    j(handler, res)
    return True


def handle_learning_frames_get(handler, parsed) -> bool:
    """Optional pre-rendered timeline frames (HP-401o)."""
    from api.helpers import j

    qs = parse_qs(parsed.query or "")
    try:
        cols = int((qs.get("cols") or ["72"])[0])
        rows = int((qs.get("rows") or ["18"])[0])
        frames = int((qs.get("frames") or ["24"])[0])
    except ValueError:
        cols, rows, frames = 72, 18, 24

    from agent import learning_graph_render as render
    from agent.learning_graph import build_learning_graph

    with _active_profile_context():
        payload = build_learning_graph()
        out = render.render_frames(payload, cols=cols, rows=rows, frames=frames)
    j(handler, out)
    return True
