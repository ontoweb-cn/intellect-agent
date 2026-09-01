"""Bot Mode DM transport (BT-01 / B2-1) and the message_agent tool
(BT-02 / B2-2) — tools/bot_mode_dm.py per the plan.

Transport (B2-1): a DM to profile X is delivered by (1) ensuring X's
"Bot Chat" session exists — deterministic id ``bot_chat`` in X's own
state.db, titled ``Bot Chat`` (Hermes ``-c "Bot Chat" --create-if-missing``
equivalent) — and (2) spawning ``intellect -p X chat -Q --continue
"Bot Chat" --query-file <tmp>`` as a background process
(``notify_on_complete``), so the reply lands in the sender's next turn.
The message body NEVER enters shell arguments: it is written to a 0o600
file inside a 0o700 directory, and deleted by the spawned process after
read (opt-in via ``INTELLECT_QUERY_FILE_DELETE=1``, set by the deliverer).

Tool (B2-2): schema is injected ONLY into Bot Chat sessions
(``ensure_message_agent_tool`` gate chain: config → session title), and
dispatch re-validates session origin (double title-gate) — a forged call
from any other session returns a structured error, never a delivery.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from tools.bot_mode_roster import BOT_CHAT_SESSION_TITLE

logger = logging.getLogger(__name__)

# Deterministic session id for a profile's Bot Chat session. Two concurrent
# deliverers race on INSERT OR IGNORE + title set; the loser's title write
# raises ValueError (title already taken by the row it would have targeted)
# and is absorbed.
BOT_CHAT_SESSION_ID = "bot_chat"

BOT_CHAT_SOURCE = "bot_chat"

# When set to "1" in the spawned chat's environment, chat deletes the
# --query-file after reading it. The deliverer sets this; user-passed
# --query-file runs never delete.
QUERY_FILE_DELETE_ENV = "INTELLECT_QUERY_FILE_DELETE"


def bot_chat_db_path(target_home: Path) -> Path:
    return Path(target_home) / "state.db"


def ensure_bot_chat_session(target_home: Path) -> str:
    """Create (once) the target profile's Bot Chat session; return its id.

    Opens the target's state.db DIRECTLY (same user, same machine — SQLite
    WAL handles concurrency). Deterministic id keeps concurrent deliverers
    idempotent without locks. The connection is closed before returning:
    holding it open makes the NEXT opener's WAL pragma fail (two concurrent
    journal_mode negotiations on one db).
    """
    from intellect_state import SessionDB

    db = SessionDB(db_path=bot_chat_db_path(target_home))
    try:
        existing = db.resolve_session_by_title(BOT_CHAT_SESSION_TITLE)
        if existing:
            return existing
        db.ensure_session(BOT_CHAT_SESSION_ID, source=BOT_CHAT_SOURCE)
        try:
            db.set_session_title(BOT_CHAT_SESSION_ID, BOT_CHAT_SESSION_TITLE)
        except ValueError:
            # Lost the title race — the deterministic row already carries
            # the title from the winning deliverer.
            pass
        return BOT_CHAT_SESSION_ID
    finally:
        try:
            db.close()
        except Exception:
            pass


def dm_temp_dir() -> Path:
    """0o700 scratch directory for DM query files (sender's own home)."""
    from intellect_constants import get_intellect_home

    directory = get_intellect_home() / "cache" / "bot_mode" / "dm"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def write_dm_query_file(message: str) -> Path:
    """Write the DM body to a 0o600 file in a 0o700 directory.

    The body never travels through argv (shell-injection / leak defense,
    Hermes lineage). The spawned chat deletes the file after reading when
    the deliverer set ``QUERY_FILE_DELETE_ENV``.
    """
    directory = dm_temp_dir()
    fd, raw = tempfile.mkstemp(prefix="dm-", suffix=".txt", dir=directory)
    try:
        os.write(fd, message.encode("utf-8"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return Path(raw)


def read_query_file(path: Path, *, delete_after: bool = False) -> str:
    """Read a --query-file; optionally delete it after a successful read."""
    text = Path(path).read_text(encoding="utf-8")
    if delete_after:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    return text
