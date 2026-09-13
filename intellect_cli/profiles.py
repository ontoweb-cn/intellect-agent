"""Deprecated compatibility shim for ``intellect_cli.profiles``.

The implementation lives in :mod:`intellect_cli.agents_home`. This module
replaces itself in ``sys.modules`` (and on the parent package) so that:

* ``from intellect_cli.profiles import X`` keeps working
* ``patch("intellect_cli.profiles._get_profiles_root", …)`` patches the
  real implementation globals (same module object as agents_home)
"""

from __future__ import annotations

import sys

from intellect_cli import agents_home as _agents_home

sys.modules[__name__] = _agents_home

# importlib may have already bound this package attribute to the transient
# shim module object; force it to the real implementation.
try:
    import intellect_cli as _pkg

    setattr(_pkg, "profiles", _agents_home)
except Exception:
    pass
