"""Peer-gateway DM relay (BT-03 / B2-4) — cross-machine bot DMs.

Extends Bot Mode beyond one machine: a ``message_agent`` target that is
not in the local roster may name a PEER — a remote intellect gateway the
owner has explicitly configured (``bot_mode.peers``). Delivery = HTTP to
the peer's own MP-04 routing surface:

    POST {peer}/p/{profile}/api/sessions          (ensure the Bot Chat session)
    POST {peer}/p/{profile}/api/sessions/{id}/chat {"message": attributed}

The peer runs a full agent turn synchronously; the captured reply text is
written back into the SENDER's Bot Chat session (``Reply from 🤖 …``) so
the sender model sees it next turn. Failures land in the sender session
as visible error entries — never silent.

Auth = the PEER profile's API key as a shared secret (owner-managed on
both machines). Fire-and-forget contract unchanged: the tool result only
confirms delivery; replies arrive as later turns.

Trust boundary: peers are owner-declared cross-machine pairings — this
extends Bot Mode across machines, it does NOT make it multi-user. The
``peers`` config defaults to empty (nothing is cross-machine unless the
owner says so).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RELAY_TIMEOUT_S = 600.0


_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def peer_profile_name(peer_name: str, peer_cfg: Dict[str, Any]) -> str:
    """Validated remote profile name for URL-path use.

    Raises ValueError on a malformed config value — a typo'd profile would
    otherwise become a malformed URL path.
    """
    profile = str(peer_cfg.get("profile") or peer_name).strip().lower()
    if not _PROFILE_RE.match(profile):
        raise ValueError(f"invalid peer profile name: {profile!r}")
    return profile


def peers_config() -> Dict[str, Dict[str, Any]]:
    """``bot_mode.peers`` — {name: {url, api_key|api_key_env, profile?}}."""
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
        raw = (cfg.get("bot_mode") or {}).get("peers") or {}
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, pcfg in raw.items():
        if isinstance(pcfg, dict) and str(pcfg.get("url") or "").strip():
            out[str(name).strip().lower()] = pcfg
    return out


def resolve_peer(target: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Peer-config lookup for one target name (None = not a peer)."""
    peers = peers_config()
    name = target.strip().lower()
    if name in peers:
        return name, peers[name]
    return None


def _peer_api_key(peer_cfg: Dict[str, Any]) -> str:
    """Peer profile's API key: direct ``api_key`` or ``api_key_env`` indirection
    (recommended — the secret then lives in each machine's env/.env)."""
    key = str(peer_cfg.get("api_key") or "").strip()
    if key:
        return key
    env_name = str(peer_cfg.get("api_key_env") or "").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def relay_delivery(
    peer_name: str,
    peer_cfg: Dict[str, Any],
    sender: str,
    message: str,
    sender_home: Path,
    *,
    timeout: float = DEFAULT_RELAY_TIMEOUT_S,
) -> Dict[str, Any]:
    """Synchronous cross-gateway delivery — run in a background thread.

    Ensures the peer's Bot Chat session, posts the attributed message to
    the peer's chat endpoint (the peer runs a full agent turn), then
    writes the peer's reply — or a visible failure entry — back into the
    sender's Bot Chat session.
    """
    import httpx

    from tools.bot_mode_dm import BOT_CHAT_SESSION_ID, BOT_CHAT_SESSION_TITLE

    url = str(peer_cfg.get("url") or "").rstrip("/")
    if not url.lower().startswith("https://"):
        # The relay carries the peer profile's API key as a Bearer secret —
        # an http:// peer URL puts it on the wire in cleartext. LAN
        # deployments may accept this; the owner should see it either way.
        logger.warning(
            "Relay to peer %r uses a non-HTTPS url (%s) — the peer API key "
            "travels in cleartext", peer_name, url,
        )
    profile = peer_profile_name(peer_name, peer_cfg)
    api_key = _peer_api_key(peer_cfg)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    attributed = f"Message from 🤖 {sender} (@{sender}): {message}"
    base = f"{url}/p/{profile}"

    outcome = "delivered (no reply text)"
    reply_text = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            # Ensure the peer's Bot Chat session exists (create only when
            # absent — duplicate ids are never created).
            probe = client.get(
                f"{base}/api/sessions/{BOT_CHAT_SESSION_ID}", headers=headers
            )
            if probe.status_code == 404:
                created = client.post(
                    f"{base}/api/sessions",
                    json={"id": BOT_CHAT_SESSION_ID,
                          "title": BOT_CHAT_SESSION_TITLE},
                    headers=headers,
                )
                created.raise_for_status()

            resp = client.post(
                f"{base}/api/sessions/{BOT_CHAT_SESSION_ID}/chat",
                json={"message": attributed},
                headers=headers,
            )
            if resp.status_code == 401:
                outcome = "peer_auth_failed: the peer profile rejected the relay key"
            elif resp.status_code == 404:
                outcome = "peer has no Bot Chat session and creation failed"
            else:
                resp.raise_for_status()
                body = resp.json()
                reply_text = str(
                    (body.get("message") or {}).get("content")
                    or body.get("reply")
                    or ""
                )
                outcome = f"reply: {reply_text[:400]}" if reply_text else "delivered"
    except httpx.TimeoutException:
        outcome = f"peer turn exceeded {timeout:.0f}s timeout"
    except httpx.HTTPError as exc:
        outcome = f"peer unreachable: {exc}"
    except Exception as exc:
        outcome = f"relay error: {exc}"

    _write_back(sender_home, peer_name, reply_text, outcome)
    return {"delivered": reply_text != "", "outcome": outcome,
            "reply": reply_text, "peer": peer_name}


def _write_back(sender_home: Path, peer_name: str,
                reply_text: str, outcome: str) -> None:
    """Append the reply (or a visible failure entry) to the sender's Bot
    Chat session so the sender model sees the outcome next turn."""
    from intellect_state import SessionDB
    from tools.bot_mode_dm import BOT_CHAT_SESSION_ID, ensure_bot_chat_session

    try:
        ensure_bot_chat_session(sender_home)
        db = SessionDB(db_path=Path(sender_home) / "state.db")
        try:
            if reply_text:
                db.append_message(
                    BOT_CHAT_SESSION_ID, "user",
                    f"Reply from 🤖 {peer_name}: {reply_text}",
                )
            else:
                db.append_message(
                    BOT_CHAT_SESSION_ID, "user",
                    f"[relay] delivery to {peer_name}: {outcome}",
                )
        finally:
            db.close()
    except Exception as exc:
        logger.debug("relay write-back failed: %s", exc)
