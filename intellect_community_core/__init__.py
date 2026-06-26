"""Intellect Community Core — Rust extension bindings.

When running from the project root directory, Python's import system may
resolve this source-directory package before the maturin-installed one in
site-packages.  The maturin-installed package contains the compiled .so
extension, while the source directory does not, causing a nested
``ModuleNotFoundError``.  The fallback below detects this situation and
re-orders sys.path so the installed version is found first.
"""

import os
import sys

# ---- Detect whether we are the source-directory copy -------------------
# The source directory is a flat package (__init__.py inside
# intellect_community_core/).  The maturin-installed copy lives under
# site-packages and contains a compiled .so alongside __init__.py.
# We distinguish them by checking whether a .so file exists in the
# *same* directory as this __init__.py — only the installed copy has one.
_source_dir = os.path.dirname(os.path.abspath(__file__))
_has_extension = any(
    f.endswith(".so") or f.endswith(".pyd")
    for f in os.listdir(_source_dir)
)

if not _has_extension:
    # We are the source-directory copy.  Find the maturin-installed
    # version in site-packages and prioritise it.
    _installed_dir = None
    for _p in sys.path:
        _candidate = os.path.join(_p, "intellect_community_core")
        if os.path.isdir(_candidate) and _candidate != _source_dir:
            # Verify the candidate actually contains a compiled extension
            try:
                _has_ext = any(
                    f.endswith(".so") or f.endswith(".pyd")
                    for f in os.listdir(_candidate)
                )
                if _has_ext:
                    _installed_dir = _candidate
                    break
            except OSError:
                continue

    if _installed_dir is not None:
        # Insert the parent of the installed package at the front of
        # sys.path so `import intellect_community_core` finds it first.
        _installed_parent = os.path.dirname(_installed_dir)
        sys.path.insert(0, _installed_parent)
    else:
        # No installed copy found.  Let the original import error surface
        # so the user gets a clear error message.
        pass

# ---- Import the compiled extension ------------------------------------
from .intellect_community_core import *  # noqa: E402, F403

__doc__ = intellect_community_core.__doc__  # noqa: F405
if hasattr(intellect_community_core, "__all__"):  # noqa: F405
    __all__ = intellect_community_core.__all__  # noqa: F405
