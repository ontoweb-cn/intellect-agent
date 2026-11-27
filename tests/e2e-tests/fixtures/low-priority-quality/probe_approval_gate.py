"""Diagnostic probe for the multi-line approval security gate (R6: no browser UI)."""
import queue
import threading
from unittest.mock import MagicMock, patch

from cli import IntellectCLI


def main() -> None:
    cli = IntellectCLI.__new__(IntellectCLI)
    cli._approval_state = {
        "command": "echo benign\nrm -rf $HOME/.ssh",
        "description": "e2e multi-line",
        "choices": ["once", "session", "always", "deny", "view"],
        "selected": 0,
        "requires_full_review": True,
        "viewed_full": False,
        "response_queue": queue.Queue(),
    }
    cli._invalidate = MagicMock()
    cli._approval_lock = threading.Lock()
    with patch("cli._cprint"):
        cli._handle_approval_selection()
        blocked = cli._approval_state is not None and cli._approval_state["response_queue"].empty()
        cli._approval_state["selected"] = 4
        cli._handle_approval_selection()
        viewed = cli._approval_state["viewed_full"]
    print("blocked_before_view", blocked)
    print("viewed_full", viewed)
    assert blocked and viewed


if __name__ == "__main__":
    main()
