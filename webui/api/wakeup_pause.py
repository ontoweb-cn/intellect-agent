"""Wakeup / credential-exhaustion pause gate (W2 B4).

Profile-home SoT: ``{INTELLECT_HOME}/webui/wakeup_pause.json``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_PAUSE_FILENAME = "wakeup_pause.json"


def _webui_state_dir() -> Path:
    try:
        from api.profiles import get_active_intellect_home

        home = Path(get_active_intellect_home())
    except Exception:
        try:
            from intellect_constants import get_intellect_home

            home = Path(get_intellect_home())
        except Exception:
            home = Path(os.environ.get("INTELLECT_HOME") or (Path.home() / ".intellect"))
    path = home / "webui"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pause_path() -> Path:
    return _webui_state_dir() / _PAUSE_FILENAME


def _norm(value: str | None) -> str:
    return str(value or "").strip().lower()


def fingerprint(provider: str | None, model: str | None) -> str:
    return f"{_norm(provider)}|{_norm(model)}"


def read_pause() -> dict[str, Any] | None:
    path = _pause_path()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("wakeup pause read failed: %s", exc)
        return None
    if not isinstance(data, dict) or not data.get("paused"):
        return None
    return data


def set_pause(
    *,
    reason: str,
    provider: str | None,
    model: str | None,
    detail: str | None = None,
) -> dict[str, Any]:
    payload = {
        "v": 1,
        "paused": True,
        "reason": str(reason or "credential_exhausted"),
        "provider": _norm(provider),
        "model": _norm(model),
        "fingerprint": fingerprint(provider, model),
        "paused_at": time.time(),
        "detail": (detail or "")[:300] or None,
    }
    path = _pause_path()
    with _LOCK:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return payload


def clear_pause() -> None:
    path = _pause_path()
    with _LOCK:
        try:
            path.unlink(missing_ok=True)
        except TypeError:
            # py<3.8 missing_ok — not expected, but keep safe
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.debug("wakeup pause clear failed: %s", exc)


def clear_pause_for_fingerprint(provider: str | None, model: str | None) -> bool:
    """Clear pause only when it matches the given fingerprint."""
    paused = read_pause()
    if not paused:
        return False
    if paused.get("fingerprint") != fingerprint(provider, model):
        return False
    clear_pause()
    return True


def clear_if_fingerprint_changed(provider: str | None, model: str | None) -> bool:
    """Clear pause when the user switches model/provider fingerprint."""
    paused = read_pause()
    if not paused:
        return False
    if paused.get("fingerprint") != fingerprint(provider, model):
        clear_pause()
        return True
    return False


def is_blocked(provider: str | None, model: str | None) -> dict[str, Any] | None:
    """Return pause payload when the same fingerprint is still paused."""
    clear_if_fingerprint_changed(provider, model)
    paused = read_pause()
    if not paused:
        return None
    if paused.get("fingerprint") != fingerprint(provider, model):
        return None
    return paused


def process_wakeup_paused_event(paused: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": 1,
        "reason": paused.get("reason") or "credential_exhausted",
        "provider": paused.get("provider") or "",
        "model": paused.get("model") or "",
        "paused_at": paused.get("paused_at"),
        "detail": paused.get("detail"),
    }
