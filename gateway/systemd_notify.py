"""Best-effort sd_notify(3) client for the gateway.

Talks to the service manager over the ``NOTIFY_SOCKET`` datagram socket when
the gateway runs under systemd (Type=notify / WatchdogSec configured). Every
function here is best-effort: failures are swallowed and return ``False`` so
callers never need to guard.

State strings we send:

- ``READY=1``      — startup finished; systemd unblocks ``ExecStart``.
- ``WATCHDOG=1``   — heartbeat within the WatchdogSec budget. The watchdog
                     only feeds this while the event loop is provably alive
                     (see ``gateway.shutdown_watchdog``).
- ``STOPPING=1``   — graceful shutdown started; systemd starts TimeoutStopSec.
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger(__name__)


def notify_socket_path() -> str | None:
    """Return the NOTIFY_SOCKET path, or None outside a service manager."""
    path = os.environ.get("NOTIFY_SOCKET", "").strip()
    return path or None


def is_managed() -> bool:
    """True when a service manager handed us a NOTIFY_SOCKET."""
    return notify_socket_path() is not None


def watchdog_usec() -> int:
    """Configured watchdog budget in microseconds (0 when unset/invalid)."""
    try:
        return max(0, int(os.environ.get("WATCHDOG_USEC", "0")))
    except ValueError:
        return 0


def sd_notify(state: str) -> bool:
    """Send one sd_notify state line. Best-effort; never raises."""
    path = notify_socket_path()
    if not path:
        return False

    # systemd uses "@"/ for abstract namespace sockets.
    if path.startswith("@"):
        sock_path = "\0" + path[1:]
        family = socket.AF_UNIX
        addr = sock_path
    elif path.startswith("/"):
        family = socket.AF_UNIX
        addr = path
    else:
        # Unexpected form (e.g. tcp:) — out of scope, degrade quietly.
        logger.debug("Unsupported NOTIFY_SOCKET form: %r", path)
        return False

    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            # Bound send so a wedged receiver queue can never park the
            # caller — in particular the watchdog monitor thread, which
            # must never block on its own notification path.
            sock.settimeout(0.5)
            sock.connect(addr)
            sock.sendall(state.encode("utf-8"))
        return True
    except OSError as exc:
        logger.debug("sd_notify(%r) failed: %s", state, exc)
        return False


def notify_ready() -> bool:
    return sd_notify("READY=1")


def notify_stopping() -> bool:
    return sd_notify("STOPPING=1")


def notify_watchdog() -> bool:
    return sd_notify("WATCHDOG=1")
