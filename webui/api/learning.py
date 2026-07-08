"""WebUI REST handlers for Journey / learning graph (HP-401e)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from urllib.parse import parse_qs

logger = logging.getLogger(__name__)


def _profile_from_query(parsed) -> Optional[str]:
    qs = parse_qs(parsed.query or "")
    raw = (qs.get("profile") or [None])[0]
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _profile_context(profile: Optional[str]):
    from api.profiles import cron_profile_context_for_home, get_active_intellect_home, get_intellect_home_for_profile

    if profile:
        return cron_profile_context_for_home(get_intellect_home_for_profile(profile))
    return cron_profile_context_for_home(get_active_intellect_home())


def handle_learning_graph_get(handler, parsed) -> bool:
    from api.helpers import j

    profile = _profile_from_query(parsed)
    try:
        from agent.learning_graph import build_learning_graph

        with _profile_context(profile):
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
    profile = _profile_from_query(parsed)

    from agent.learning_mutations import node_detail

    with _profile_context(profile):
        res = node_detail(node_id)
    if not res.get("ok"):
        return bad(handler, res.get("message", "not found"), status=404)
    j(handler, res)
    return True


def handle_learning_node_delete(handler, body: dict[str, Any]) -> bool:
    from api.helpers import bad, j

    node_id = str((body or {}).get("id") or "").strip()
    if not node_id:
        return bad(handler, "missing id")
    profile = (body or {}).get("profile")

    from agent.learning_mutations import delete_node

    with _profile_context(profile):
        res = delete_node(node_id)
    if not res.get("ok"):
        return bad(handler, res.get("message", "delete failed"), status=400)
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
    profile = (body or {}).get("profile")

    from agent.learning_mutations import edit_node

    with _profile_context(profile):
        res = edit_node(node_id, str(content))
    if not res.get("ok"):
        return bad(handler, res.get("message", "edit failed"), status=400)
    j(handler, res)
    return True


def handle_learning_frames_get(handler, parsed) -> bool:
    """Optional pre-rendered timeline frames (HP-401o)."""
    from api.helpers import j

    qs = parse_qs(parsed.query or "")
    profile = _profile_from_query(parsed)
    try:
        cols = int((qs.get("cols") or ["72"])[0])
        rows = int((qs.get("rows") or ["18"])[0])
        frames = int((qs.get("frames") or ["24"])[0])
    except ValueError:
        cols, rows, frames = 72, 18, 24

    from agent import learning_graph_render as render
    from agent.learning_graph import build_learning_graph

    with _profile_context(profile):
        payload = build_learning_graph()
        out = render.render_frames(payload, cols=cols, rows=rows, frames=frames)
    j(handler, out)
    return True
