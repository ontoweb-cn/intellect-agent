"""Diagnostic probe for single-session immediate resume marker."""
import sys
from pathlib import Path as _RepoPath
sys.path.insert(0, str(_RepoPath(__file__).resolve().parents[4]))
import tempfile
from pathlib import Path
from unittest.mock import patch

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="e2e-low-priority-session-single-") as td:
        tmp = Path(td)
        store = SessionStore(sessions_dir=tmp, config=GatewayConfig())
        source = SessionSource(platform=Platform.TELEGRAM, chat_id="e2e-low-priority-single", user_id="u1")
        key = store.get_or_create_session(source).session_key
        with patch.object(store, "_save_impl", wraps=store._save_impl) as save_mock:
            assert store.mark_resume_pending(key) is True
            print("save_calls", save_mock.call_count)
            assert save_mock.call_count == 1
        after = SessionStore(sessions_dir=tmp, config=GatewayConfig())
        after._ensure_loaded()
        print("persisted", after._entries[key].resume_pending)
        assert after._entries[key].resume_pending is True


if __name__ == "__main__":
    main()
