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

try:
    import fcntl
except ImportError:  # Windows — the turn-lock wrapper degrades to unlocked
    fcntl = None
from typing import List, Optional
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


# ── message_agent tool (BT-02 / B2-2) ──────────────────────────────────

import json as _json
import shlex as _shlex
import sys as _sys

from tools.bot_mode_roster import build_roster
from tools.bot_relay import peers_config, resolve_peer

MESSAGE_AGENT_TOOL_NAME = "message_agent"
MAX_DM_CHARS = 16000
DEFAULT_MAX_DM_DEPTH = 3
DM_DEPTH_ENV = "INTELLECT_BOT_DM_DEPTH"

ATTRIBUTION_PREFIX = "Message from 🤖 {handle} (@{handle}): "

FORGED_CALL_ERROR = {
    "error": "message_agent is not available in this session",
    "code": "not_bot_chat_session",
}


def message_agent_schema() -> dict:
    """One fresh schema copy per injection — agents must never share the
    same dict object (copy-on-write injection)."""
    return {
        "type": "function",
        "function": {
            "name": MESSAGE_AGENT_TOOL_NAME,
            "description": (
                "Send a fire-and-forget DM to another local agent's Bot Chat "
                "session. Their reply (if any) arrives as a later turn — "
                "never wait synchronously and never claim the reply arrived."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target agent name from the Bot Mode roster.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message body (max 16000 characters).",
                    },
                },
                "required": ["target", "message"],
            },
        },
    }


def bot_mode_enabled() -> bool:
    """Config gate: ``bot_mode.enabled`` (default False — opt-in feature:
    spawns background processes and writes cross-profile sessions)."""
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
        return bool((cfg.get("bot_mode") or {}).get("enabled"))
    except Exception:
        return False


def max_dm_depth() -> int:
    """``bot_mode.max_dm_depth`` (default 3) — caps chained bot→bot DMs so
    two agents can never ping-pong forever."""
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
        return max(1, int((cfg.get("bot_mode") or {}).get("max_dm_depth") or DEFAULT_MAX_DM_DEPTH))
    except Exception:
        return DEFAULT_MAX_DM_DEPTH


def current_dm_depth() -> int:
    """How many bot DM hops the current process is away from a human turn."""
    try:
        return max(0, int(os.environ.get(DM_DEPTH_ENV, "0")))
    except ValueError:
        return 0


def _session_home(agent) -> Optional[Path]:
    """Home of the session's owning profile, derived from the session DB
    path — NEVER from environment variables (multi-profile distrust,
    deep-dive §11)."""
    db = getattr(agent, "_session_db", None)
    db_path = getattr(db, "db_path", None)
    return Path(db_path).parent if db_path else None


def _read_session_title(agent) -> str:
    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", None)
    if db is None or not session_id:
        return ""
    try:
        return db.get_session_title(session_id) or ""
    except Exception:
        return ""


def ensure_message_agent_tool(agent) -> bool:
    """Injection gate chain: config enabled → session title == "Bot Chat"
    → append the schema to THIS agent's tool list (idempotent, per-agent
    copy — never the memoized global schema cache, never a core toolset).

    Returns True when the schema is present after the call.
    """
    if not bot_mode_enabled():
        return False
    home = _session_home(agent)
    if home is None:
        return False
    if _read_session_title(agent) != BOT_CHAT_SESSION_TITLE:
        return False
    tools = getattr(agent, "tools", None)
    if tools is None:
        return False
    if any(
        (t.get("function") or {}).get("name") == MESSAGE_AGENT_TOOL_NAME
        for t in tools
        if isinstance(t, dict)
    ):
        return True
    # Copy-on-write: agent.tools may be the memoized shared list object.
    agent.tools = [*tools, message_agent_schema()]
    valid = getattr(agent, "valid_tool_names", None)
    if valid is not None:
        valid.add(MESSAGE_AGENT_TOOL_NAME)
    return True


def turn_lock_path(target_home: Path) -> Path:
    """Per-profile turn lock — serialized bot turns, no interleaving."""
    return Path(target_home) / "bot_chat.lock"


def handle_message_agent_call(agent, function_args) -> str:
    """Dispatch for message_agent with the DOUBLE title-gate (execution-time
    re-read of session title + home). A forged call from any non-Bot-Chat
    session returns a structured error — never a delivery (gate-4)."""
    function_args = function_args or {}
    home = _session_home(agent)
    if not bot_mode_enabled() or home is None:
        return _json.dumps(dict(FORGED_CALL_ERROR))
    if _read_session_title(agent) != BOT_CHAT_SESSION_TITLE:
        return _json.dumps(dict(FORGED_CALL_ERROR))

    target = str(function_args.get("target") or "").strip().lower()
    message = str(function_args.get("message") or "")
    if not target or not message.strip():
        return _json.dumps(
            {"error": "target and message are required", "code": "invalid_args"}
        )
    if len(message) > MAX_DM_CHARS:
        return _json.dumps(
            {"error": f"message exceeds {MAX_DM_CHARS} characters",
             "code": "message_too_long"}
        )

    roster = {e["name"]: e for e in build_roster()}
    # B2-4 resolution order: LOCAL roster shadows peers — a name that is
    # both a local profile and a configured peer always delivers locally
    # (same-machine is canonical; documented in the website guide).
    peer = None if target in roster else resolve_peer(target)
    peer_names = set(peers_config())
    if target not in roster and peer is None:
        return _json.dumps(
            {"error": f"Unknown target agent: {target!r}",
             "code": "unknown_target",
             "roster": sorted(set(roster) | peer_names)}
        )
    # Depth budget (BT-04 budget, DM-only form): no infinite bot↔bot loops.
    depth = current_dm_depth()
    if depth >= max_dm_depth():
        return _json.dumps(
            {"error": f"DM depth budget exhausted ({max_dm_depth()})",
             "code": "depth_exhausted"}
        )
    # Sending identity: the home-matched roster entry. Local delivery
    # requires it (error below); relayed delivery falls back to the active
    # profile name (a peer target has no local roster entry).
    sender = next(
        (e["name"] for e in roster.values()
         if _same_path(e["home"], home)),
        None,
    )

    if peer is not None:
        # ── B2-4: cross-gateway relayed delivery (fire-and-forget) ──
        peer_name, peer_cfg = peer
        try:
            from tools.bot_relay import peer_profile_name

            peer_profile_name(peer_name, peer_cfg)
        except ValueError as exc:
            return _json.dumps(
                {"error": str(exc), "code": "invalid_peer_profile"}
            )
        import threading as _threading
        from tools.bot_relay import relay_delivery

        _threading.Thread(
            target=relay_delivery,
            args=(peer_name, peer_cfg, sender or "default", message, home),
            kwargs={"timeout": 600.0},
            name=f"bot-relay-{peer_name}", daemon=True,
        ).start()
        return _json.dumps(
            {"delivered": True, "relayed": True, "target": peer_name,
             "note": ("queued for cross-gateway delivery to the peer's "
                      "Bot Chat session; the peer's reply arrives as a "
                      "later turn — do not claim it already arrived.")}
        )

    if sender is None:
        return _json.dumps(
            {"error": "sending profile is not in the roster",
             "code": "sender_not_in_roster"}
        )

    target_home = Path(roster[target]["home"])
    ensure_bot_chat_session(target_home)
    attributed = ATTRIBUTION_PREFIX.format(handle=sender) + message
    query_file = write_dm_query_file(attributed)

    from tools.approval import get_current_session_key
    from tools.process_registry import process_registry

    env_vars = dict(os.environ)
    env_vars[DM_DEPTH_ENV] = str(depth + 1)
    env_vars[QUERY_FILE_DELETE_ENV] = "1"
    # The child runs from the TARGET's home (neutral context — never the
    # sender's cwd) and needs the repo importable from there.
    repo_root = str(Path(__file__).resolve().parents[1])
    existing_pp = env_vars.get("PYTHONPATH")
    env_vars["PYTHONPATH"] = (
        repo_root + (os.pathsep + existing_pp if existing_pp else "")
    )
    turn_lock = turn_lock_path(target_home)
    command = (
        f"{_shlex.quote(_sys.executable)} -m tools.bot_mode_dm "
        f"--turn-lock {_shlex.quote(str(turn_lock))} -- "
        f"-p {target} chat -Q "
        f"--continue {_shlex.quote(BOT_CHAT_SESSION_TITLE)} "
        f"--query-file {_shlex.quote(str(query_file))}"
    )
    try:
        proc_session = process_registry.spawn_local(
            command=command,
            cwd=str(target_home),
            task_id="",
            session_key=get_current_session_key(default=""),
            env_vars=env_vars,
            use_pty=False,
        )
        proc_session.notify_on_complete = True
    except Exception as exc:
        try:
            query_file.unlink(missing_ok=True)
        except OSError:
            pass
        return _json.dumps(
            {"error": f"delivery failed: {exc}", "code": "delivery_failed"}
        )
    return _json.dumps(
        {
            "delivered": True,
            "target": target,
            "note": (
                "Message queued for the target agent's Bot Chat session. "
                "Their reply arrives as a later turn — do not claim it "
                "already arrived."
            ),
        }
    )


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except Exception:
        return False


# ── turn-lock wrapper entry (P2-1) ──────────────────────────────────────

def _turn_lock_entry(argv: List[str]) -> int:
    """``python -m tools.bot_mode_dm --turn-lock PATH -- <chat argv…>``

    Holds an exclusive flock on PATH for the child chat's whole run, so
    concurrent DMs to one profile SERIALIZE instead of interleaving turns
    (WAL protects the database; this protects the turn semantics). The lock
    releases automatically when the process exits.
    """
    import runpy

    if "--turn-lock" not in argv or "--" not in argv:
        print("usage: -m tools.bot_mode_dm --turn-lock PATH -- <chat argv...>")
        return 2
    lock_path = Path(argv[argv.index("--turn-lock") + 1])
    chat_argv = argv[argv.index("--") + 1:]

    if fcntl is not None:
        handle = open(lock_path, "a+", encoding="utf-8")
        # Blocking acquire: queued deliveries process one at a time, in order.
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            _sys.argv = ["intellect"] + chat_argv
            runpy.run_module("intellect_cli.main", run_name="__main__")
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except Exception:
                pass
        return 0

    # No fcntl (Windows): degrade to unlocked delivery — single-owner V1
    # accepted limitation; database integrity stays WAL-guaranteed.
    _sys.argv = ["intellect"] + chat_argv
    runpy.run_module("intellect_cli.main", run_name="__main__")
    return 0


if __name__ == "__main__":
    _sys.exit(_turn_lock_entry(_sys.argv[1:]))
