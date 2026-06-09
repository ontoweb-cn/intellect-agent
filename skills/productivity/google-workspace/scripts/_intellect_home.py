"""Resolve INTELLECT_HOME for standalone skill scripts.

Skill scripts may run outside the Intellect process (e.g. system Python,
nix env, CI) where ``intellect_constants`` is not importable.  This module
provides the same ``get_intellect_home()`` and ``display_intellect_home()``
contracts as ``intellect_constants`` without requiring it on ``sys.path``.

When ``intellect_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``intellect_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``INTELLECT_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from intellect_constants import display_intellect_home as display_intellect_home
    from intellect_constants import get_intellect_home as get_intellect_home
except (ModuleNotFoundError, ImportError):

    def get_intellect_home() -> Path:
        """Return the Intellect home directory (default: ~/.intellect).

        Mirrors ``intellect_constants.get_intellect_home()``."""
        val = os.environ.get("INTELLECT_HOME", "").strip()
        return Path(val) if val else Path.home() / ".intellect"

    def display_intellect_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``intellect_constants.display_intellect_home()``."""
        home = get_intellect_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
