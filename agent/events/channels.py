"""Frozen Redis pub/sub channel names (§16.3)."""

WEBUI_SESSIONS = "webui.sessions"


def webui_approval_channel(session_id: str) -> str:
    return f"webui.approval.{session_id}"


def webui_clarify_channel(session_id: str) -> str:
    return f"webui.clarify.{session_id}"


def webui_kanban_channel(board_id: str) -> str:
    return f"webui.kanban.{board_id}"


def gateway_run_events_channel(run_id: str) -> str:
    return f"runs.{run_id}"
