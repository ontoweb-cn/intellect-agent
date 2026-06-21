"""Gateway config/runtime helpers extracted from run.py."""

from __future__ import annotations

import sys
from typing import Optional

from gateway.helpers import _log_non_critical


def _resolve_intellect_bin() -> Optional[list[str]]:
    """Resolve the Intellect update command as argv parts.

    Tries in order:
    1. ``shutil.which("intellect")`` — standard PATH lookup
    2. ``sys.executable -m intellect_cli.main`` — fallback when Intellect is running
       from a venv/module invocation and the ``intellect`` shim is not on PATH

    Returns argv parts ready for quoting/joining, or ``None`` if neither works.
    """
    import shutil

    intellect_bin = shutil.which("intellect")
    if intellect_bin:
        return [intellect_bin]

    try:
        import importlib.util

        if importlib.util.find_spec("intellect_cli") is not None:
            return [sys.executable, "-m", "intellect_cli.main"]
    except Exception:
        _log_non_critical()
    return None


def _home_target_env_var(platform_name: str) -> str:
    """Return the configured home-target env var for a platform.

    Consults built-in ``_HOME_TARGET_ENV_VARS`` first, then the plugin
    registry via ``cron.scheduler._resolve_home_env_var``, then falls back
    to ``<PLATFORM>_HOME_CHANNEL`` for unknown names.
    """
    from cron.scheduler import _resolve_home_env_var

    resolved = _resolve_home_env_var(platform_name)
    if resolved:
        return resolved
    return f"{platform_name.upper()}_HOME_CHANNEL"


def _home_thread_env_var(platform_name: str) -> str:
    """Return the optional thread/topic env var for a platform home target."""
    return f"{_home_target_env_var(platform_name)}_THREAD_ID"
