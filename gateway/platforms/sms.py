"""Shim — canonical implementation moved to plugins/platforms/sms/adapter.py (P3-10).

This module is a *true alias* for the implementing module, not a re-export.
Registering the implementation in ``sys.modules`` under this name means
``gateway.platforms.sms`` and ``plugins.platforms.sms.adapter`` are the
same module object, so they share one global namespace.

That matters because callers depend on more than the names a re-export would
carry:

* the gateway's built-in adapter chain imports module-level helpers
  (``check_sms_requirements``, private regexes and constants),
* the test-suite ``monkeypatch.setattr``s module globals through *this*
  path (e.g. a ``*_AVAILABLE`` flag) and expects the implementation to
  observe the change.

A class-only re-export breaks the first, and any re-export breaks the second
(patching the alias's namespace would not reach the implementation's).  An
alias makes both work unchanged and cannot drift out of sync.
"""

import sys

from plugins.platforms.sms import adapter as _impl

sys.modules[__name__] = _impl
