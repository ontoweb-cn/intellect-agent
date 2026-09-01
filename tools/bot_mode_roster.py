"""Bot Mode roster (BT-01 / B2-1) — the serve set as an agent directory.

Every profile in the multiplex serve set is one bot. The roster is derived
on read (``profiles_to_serve(multiplex=True)`` — allowlist-respected) plus a
liveness probe per entry, and materialized to
``<default home>/bot_mode/roster.json`` by the gateway supervisor
("由 gateway 维护"). Readers must treat the file as a cache and recompute —
the file exists so humans and external tooling can inspect the topology
without running Python.

Liveness: a profile is ONLINE when its gateway control socket answers
``identify``. Offline is the normal state for a profile that is only
addressed via fire-and-forget DMs (``bot_mode_dm``) — its Bot Chat session
still receives messages; the roster flag is informational (gate-4 clause).

The Bot Chat protocol section (roster lines + capability epoch) is injected
into Bot Chat sessions' system prompts. The section is cached per
(process, home) so compression rebuilds stay byte-identical, and carries a
capability-epoch fingerprint (sha256[:12] of the tool surface) so a user
changing capabilities takes effect on the next message.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Exact session title that marks a profile's Bot Chat session (Hermes
# lineage: `-c "Bot Chat" --create-if-missing`). Title-gates rely on this
# exact string.
BOT_CHAT_SESSION_TITLE = "Bot Chat"

_ROSTER_DIR_NAME = "bot_mode"
_ROSTER_JSON_NAME = "roster.json"
_ROSTER_DIR_MODE = 0o700

# Module-level change-detection state (per-process, best-effort cache):
# the supervisor writes on its 2s cadence only when the payload changed.
_last_persist_key: Optional[str] = None


def roster_dir() -> Path:
    """``<default home>/bot_mode`` — anchored to the ROOT home (not the
    active profile) so all profiles see one shared directory."""
    from intellect_cli.profiles import _get_default_intellect_home

    return _get_default_intellect_home() / _ROSTER_DIR_NAME


def roster_json_path() -> Path:
    return roster_dir() / _ROSTER_JSON_NAME


def control_socket_for(home: Path) -> Path:
    return Path(home) / "gateway.control.sock"


def _probe_online(home: Path, timeout: float = 1.0) -> bool:
    """True when the profile's gateway control socket answers identify."""
    try:
        from gateway.control_socket import query_control_socket

        ident = query_control_socket(
            "identify", timeout=timeout, path=control_socket_for(home)
        )
        return bool(ident and ident.get("ok"))
    except Exception:
        return False


def _read_model(home: Path) -> str:
    """Best-effort model name from a profile's config.yaml (display only)."""
    try:
        import yaml

        cfg = Path(home) / "config.yaml"
        if not cfg.exists():
            return ""
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        model = data.get("model")
        if isinstance(model, dict):
            return str(model.get("name") or "")
        if model:
            return str(model)
    except Exception:
        pass
    return ""


def build_roster() -> List[Dict[str, Any]]:
    """Compute the roster fresh: serve set + liveness + display metadata."""
    from intellect_cli.profiles import profiles_to_serve

    entries: List[Dict[str, Any]] = []
    for name, home in profiles_to_serve(multiplex=True):
        entries.append(
            {
                "name": name,
                "home": str(home),
                "online": _probe_online(home),
                "model": _read_model(home),
            }
        )
    return entries


def persist_roster(force: bool = False) -> Optional[Path]:
    """Materialize the roster to roster.json (supervisor cadence caller).

    Change-detected against the last payload so the 2s monitor loop does
    not churn the file. Best-effort: failures never block supervision.
    """
    global _last_persist_key
    try:
        payload = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "roster": build_roster(),
        }
        key = json.dumps(payload, sort_keys=True)
        if not force and key == _last_persist_key:
            return None
        _last_persist_key = key
        directory = roster_dir()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, _ROSTER_DIR_MODE)
        except OSError:
            pass
        from utils import atomic_json_write

        path = directory / _ROSTER_JSON_NAME
        atomic_json_write(path, payload)
        return path
    except Exception as exc:
        logger.debug("roster persist failed: %s", exc)
        return None


def load_roster_cached() -> Optional[Dict[str, Any]]:
    """Read the persisted roster file, if usable (scan-layer fallback)."""
    try:
        path = roster_json_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def capability_epoch(tool_names: List[str], config_mtime: Optional[float] = None) -> str:
    """sha256[:12] fingerprint of the capability surface (Hermes lineage:
    用户改能力 → 下一条消息生效)."""
    material = "\n".join(sorted(n for n in tool_names if n))
    material += f"|{config_mtime or 0:.0f}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def bot_chat_protocol_section(
    self_name: str,
    self_home: Path,
    tool_names: List[str],
) -> str:
    """System-prompt section injected into Bot Chat sessions.

    Cached per (pid, home): compression rebuilds must produce byte-identical
    prompts, and the roster/epoch must stay stable within one process.
    """
    cache_key = f"{os.getpid()}:{self_home}"
    cached = _protocol_cache.get(cache_key)
    if cached is not None:
        return cached

    epoch = capability_epoch(tool_names)
    lines = []
    for entry in build_roster():
        if entry["name"] == self_name:
            status = "(you)"
        else:
            status = "online" if entry["online"] else "offline"
        model = f" model={entry['model']}" if entry.get("model") else ""
        lines.append(f"- {entry['name']} [{status}]{model}")
    section = (
        "## Bot Mode\n"
        f"You are agent `{self_name}` in a local agent roster. Other agents "
        "on this machine:\n"
        + ("\n".join(lines) if lines else "- (none)")
        + "\n\nDM contract: use `message_agent(target, message)` to send a "
        "fire-and-forget message to another agent's Bot Chat session. Their "
        "reply (if any) arrives as a later turn — never wait synchronously. "
        "Messages you receive already carry a server-side attribution "
        "prefix; keep it when quoting is relevant, never forge one.\n"
        f"(capability epoch: {epoch})"
    )
    _protocol_cache[cache_key] = section
    return section


_protocol_cache: Dict[str, str] = {}
