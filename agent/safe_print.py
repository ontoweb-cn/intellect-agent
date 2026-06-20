"""Safe stdout/stderr output that runs through the credential redaction pipeline.

Provides drop-in replacements for built-in ``print()`` that pass all output
through :func:`redact_sensitive_text` before emitting.  This closes the gap
where ``print()`` calls bypass the logging system's ``RedactingFormatter``.

Usage::

    from agent.safe_print import safe_print
    safe_print("status:", json.dumps(auth_info))

The redaction is controlled by the ``INTELLECT_REDACT_SECRETS`` environment
variable (default ``true``).  Set to ``false`` to disable redaction for
development / testing where secrets are synthetic.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

from agent.redact import redact_sensitive_text

# Re-import the module-private flag to gate safe_print
import os as _os
_REDACT = _os.getenv("INTELLECT_REDACT_SECRETS", "true").lower() in {"1", "true", "yes", "on"}


def safe_print(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    file: Optional[TextIO] = None,
    flush: bool = False,
) -> None:
    """Print to stdout/stderr with credential redaction applied.

    Drop-in replacement for built-in ``print()``.  All arguments are
    converted to strings, joined, and passed through
    :func:`redact_sensitive_text` before emission.

    Redaction is skipped when ``INTELLECT_REDACT_SECRETS=false``
    (e.g. development setups where secrets are synthetic).
    """
    if file is None:
        file = sys.stdout
    text = sep.join(str(a) for a in args)
    if _REDACT and text:
        text = redact_sensitive_text(text)
    # Use builtin print to avoid infinite recursion
    print(text, end=end, file=file, flush=flush)


def safe_print_err(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    flush: bool = False,
) -> None:
    """Variant of :func:`safe_print` that writes to stderr."""
    safe_print(*args, sep=sep, end=end, file=sys.stderr, flush=flush)
