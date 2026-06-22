"""
Intellect CLI - Unified command-line interface for Intellect Agent.

Provides subcommands for:
- intellect chat          - Interactive chat (same as ./intellect)
- intellect gateway       - Run gateway in foreground
- intellect gateway start - Start gateway service
- intellect gateway stop  - Stop gateway service
- intellect setup         - Interactive setup wizard
- intellect status        - Show status of all components
- intellect cron          - Manage cron jobs
"""

import os
import subprocess
import sys
from pathlib import Path


def _resolve_version() -> str:
    """Resolve the version string from the closest available source.

    1. pyproject.toml — editable/dev installs (most accurate)
    2. importlib.metadata — wheel/pip installs (no pyproject.toml on disk)
    3. git describe --tags — dev clones without either
    4. Hardcoded fallback
    """
    _candidate = Path(__file__).resolve().parent.parent

    # 1. pyproject.toml — canonical for editable/develop installs
    _pyproject = _candidate / "pyproject.toml"
    if _pyproject.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # Python < 3.11
        try:
            _cfg = tomllib.loads(_pyproject.read_text(encoding="utf-8"))
            _ver = _cfg.get("project", {}).get("version")
            if _ver:
                return _ver
        except Exception:
            pass

    # 2. importlib.metadata — standard for wheel/pip installs
    try:
        from importlib.metadata import version as _pkg_version  # Python >= 3.8
        return _pkg_version("intellect-agent")
    except Exception:
        pass

    # 3. git describe — dev clones without accessible pyproject.toml
    try:
        _result = subprocess.run(
            ["git", "-C", str(_candidate), "describe", "--tags", "--dirty"],
            capture_output=True, text=True, timeout=5,
        )
        _desc = _result.stdout.strip()
        if _desc:
            # Transform v0.6.6-3-gabcdef → 0.6.6.dev3+gabcdef
            _desc = _desc.lstrip("v")
            if "-" in _desc:
                _parts = _desc.split("-", 2)
                _tag = _parts[0]
                _dist = _parts[1] if len(_parts) > 1 else "0"
                _hash = _parts[2] if len(_parts) > 2 else ""
                return f"{_tag}.dev{_dist}+{_hash}"
            return _desc
    except Exception:
        pass

    # 4. Last resort
    return "0.0.0"


def _resolve_release_date() -> str:
    """Resolve the release date from the latest git tag."""
    try:
        _candidate = Path(__file__).resolve().parent.parent
        _result = subprocess.run(
            ["git", "-C", str(_candidate), "log", "-1", "--format=%ad",
             "--date=format:%Y.%m.%d", "--", "pyproject.toml"],
            capture_output=True, text=True, timeout=5,
        )
        _date = _result.stdout.strip()
        if _date:
            return _date
    except Exception:
        pass
    return "unknown"


__version__ = _resolve_version()
__release_date__ = _resolve_release_date()


def _ensure_utf8():
    """Force UTF-8 stdout/stderr on Windows to prevent UnicodeEncodeError.

    Windows services and terminals default to cp1252, which cannot encode
    box-drawing characters used in CLI output. This causes unhandled
    UnicodeEncodeError crashes on gateway startup.
    """
    if sys.platform != "win32":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if getattr(stream, "encoding", "").lower().replace("-", "") != "utf8":
                new_stream = open(
                    stream.fileno(), "w", encoding="utf-8",
                    buffering=1, closefd=False,
                )
                setattr(sys, stream_name, new_stream)
        except (AttributeError, OSError):
            pass


_ensure_utf8()
