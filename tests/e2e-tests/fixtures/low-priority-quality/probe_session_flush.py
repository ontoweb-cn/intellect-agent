"""Diagnostic probe for SessionStore batch resume marking (R6: no browser UI)."""
import sys
from pathlib import Path as _RepoPath
sys.path.insert(0, str(_RepoPath(__file__).resolve().parents[4]))
import tempfile
from pathlib import Path
from unittest.mock import patch

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="e2e-low-priority-session-") as td:
        tmp = Path(td)
        store = SessionStore(sessions_dir=tmp, config=GatewayConfig())
        sources = [
            SessionSource(platform=Platform.TELEGRAM, chat_id="e2e-low-priority-a", user_id="u1"),
            SessionSource(platform=Platform.TELEGRAM, chat_id="e2e-low-priority-b", user_id="u2"),
        ]
        keys = [store.get_or_create_session(s).session_key for s in sources]
        with patch.object(store, "_save_impl", wraps=store._save_impl) as save_mock:
            for key in keys:
                assert store.mark_resume_pending(key, flush=False) is True
            print("save_calls_after_marks", save_mock.call_count)
            assert save_mock.call_count == 0
            store.flush()
            print("save_calls_after_flush", save_mock.call_count)
            assert save_mock.call_count == 1

        after = SessionStore(sessions_dir=tmp, config=GatewayConfig())
        after._ensure_loaded()
        after_values = [after._entries[k].resume_pending for k in keys]
        print("persisted_markers", after_values)
        assert all(after_values), "batch flush did not persist both markers"


if __name__ == "__main__":
    main()
