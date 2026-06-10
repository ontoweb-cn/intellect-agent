"""
Intellect Web UI -- Optional state.db sync bridge.

Mirrors WebUI session metadata (token usage, title, model) into the
intellect-agent state.db so that /insights, session lists, and cost
tracking include WebUI activity.

This is opt-in via the 'sync_to_insights' setting (default: off).
All operations are wrapped in try/except -- if state.db is unavailable,
locked, or the schema doesn't match, the WebUI continues normally.

The bridge uses absolute token counts (not deltas) because the WebUI
Session object already accumulates totals across turns. This avoids
any double-counting risk.
"""
import logging

logger = logging.getLogger(__name__)


def _get_state_db(profile: str = None):
    """Get a SessionDB instance for the active or named profile's state.db.

    When *profile* is set, resolves that profile's home directly (fixes #2762
    TLS mismatch in streaming worker threads). Uses :mod:`api.storage_bridge`
    (W1) so the path matches agent ``storage.*`` config.
    """
    from api.storage_bridge import get_session_db

    return get_session_db(profile=profile)


def sync_session_start(
    session_id: str,
    model=None,
    profile: str = None,
    *,
    member_id: str = None,
    team_id: str = None,
) -> None:
    """Register a WebUI session in state.db (idempotent).

    Called when a session's first message is sent.  *member_id* / *team_id*
    stamp multi-user ownership in state.db (Phase B session isolation).
    """
    db = _get_state_db(profile=profile)
    if not db:
        return
    try:
        db.ensure_session(
            session_id=session_id,
            source='webui',
            model=model,
            member_id=member_id,
            team_id=team_id,
        )
    except Exception:
        logger.debug("Failed to sync session start to state.db")
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")


def sync_session_usage(session_id: str, input_tokens: int=0, output_tokens: int=0,
                       estimated_cost=None, model=None, title: str = None,
                       message_count: int = None, profile: str = None,
                       *, member_id: str = None, team_id: str = None) -> None:
    """Update token usage and title for a WebUI session in state.db.
    Called after each turn completes. Uses absolute=True to set totals
    (the WebUI Session already accumulates across turns).

    ``profile`` lets the caller name the target state.db explicitly,
    which is what fixes #2762: this function is invoked from the
    agent streaming worker thread, where the request-thread's TLS
    profile context has not been propagated. Without an explicit
    profile, the TLS lookup falls back to the process-global active
    profile and writes the session's usage to the wrong state.db
    (e.g. ``hiyuki``'s instead of the cookie-switched ``maiko``'s).
    """
    db = _get_state_db(profile=profile)
    if not db:
        return
    try:
        # Ensure session exists first (idempotent)
        db.ensure_session(
            session_id=session_id,
            source='webui',
            model=model,
            member_id=member_id,
            team_id=team_id,
        )
        # Set absolute token counts
        db.update_token_counts(
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            model=model,
            absolute=True,
        )
        # Update title if we have one, using the public API
        if title:
            try:
                db.set_session_title(session_id, title)
            except Exception:
                logger.debug("Failed to sync session title to state.db")
        # Update message count
        if message_count is not None:
            try:
                def _set_msg_count(conn):
                    conn.execute(
                        "UPDATE sessions SET message_count = ? WHERE id = ?",
                        (message_count, session_id),
                    )
                db._execute_write(_set_msg_count)
            except Exception:
                logger.debug("Failed to sync message count to state.db")
    except Exception:
        logger.debug("Failed to sync session usage to state.db")
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")
