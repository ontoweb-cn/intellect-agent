"""Deprecated compatibility shim for ``intellect_cli.profile_gate``.

Prefer :mod:`intellect_cli.agent_gate`.
"""

from __future__ import annotations

import sys

from intellect_cli import agent_gate as _agent_gate

sys.modules[__name__] = _agent_gate

try:
    import intellect_cli as _pkg

    setattr(_pkg, "profile_gate", _agent_gate)
except Exception:
    pass
