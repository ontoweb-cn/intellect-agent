"""Local control socket for the gateway (identify/status, v1).

The PID file + runtime-status JSON on disk are *scans*: they tell a CLI or
updater what the last writer claimed, but never confirm a live listener.
This module adds a tiny request/response Unix-domain socket owned by the
gateway itself, so local tooling can ask the running process directly and
fall back to the disk scan when the socket is absent.

Protocol (v1): connect, send one line of JSON ``{"op": "identify"|"status"}``,
receive one line of JSON, close.

- ``identify`` → ``{"ok": true, "kind", "pid", "start_time", "version"}``
- ``status``   → identify fields plus the persisted runtime-status payload
  (gateway_state, platforms, exit_reason, …).

Everything is best-effort and fail-closed to the existing scan layer: bind
failures disable the socket with a debug log, request errors return an
error object, and the server thread is a daemon so it can never block
shutdown.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from typing import Any, Optional

from gateway.status import build_pid_record, read_runtime_status
from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

CONTROL_SOCKET_NAME = "gateway.control.sock"
_REQUEST_TIMEOUT_S = 2.0
# Kernel accept backlog. Generous on purpose: idle/held connections occupy
# it, and a full backlog makes the kernel REFUSE new connects — the
# semaphore below is the real concurrency cap.
_BACKLOG = 16
# Cap concurrently-served connections so a runaway local client can't
# exhaust threads. Same-user only (socket is 0600), so this is hygiene.
_MAX_CONCURRENT_CONNECTIONS = 8


def control_socket_path() -> Any:
    return get_intellect_home() / CONTROL_SOCKET_NAME


def _try_version() -> str:
    try:
        from intellect_cli import __version__

        return str(__version__)
    except Exception:
        return ""


def _handle_request(req: Any) -> dict:
    """Build the response payload for one decoded request object."""
    if not isinstance(req, dict):
        return {"ok": False, "error": "invalid request"}
    op = str(req.get("op") or "")
    # argv is deliberately stripped: it can carry config paths and other
    # per-host details a diagnostic endpoint has no business echoing back.
    base = {k: v for k, v in build_pid_record().items() if k != "argv"}
    base["ok"] = True
    base["op"] = op
    base["version"] = _try_version()
    if op == "identify":
        return base
    if op == "status":
        base["runtime_status"] = read_runtime_status()
        return base
    return {"ok": False, "error": f"unknown op: {op!r}"}


def _serve_connection(conn: socket.socket) -> None:
    try:
        with conn:
            conn.settimeout(_REQUEST_TIMEOUT_S)
            chunks = b""
            while b"\n" not in chunks:
                data = conn.recv(4096)
                if not data:
                    break
                chunks += data
                if len(chunks) > 65536:
                    break
            line = chunks.split(b"\n", 1)[0].strip()
            try:
                req = json.loads(line.decode("utf-8")) if line else {}
                resp = _handle_request(req)
            except (ValueError, UnicodeDecodeError):
                resp = {"ok": False, "error": "invalid json"}
            conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
    except OSError:
        pass


class ControlSocketServer:
    """Background thread serving the control socket. Best-effort."""

    def __init__(self, path=None) -> None:
        self._path = path or control_socket_path()
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._created_file = False

    @property
    def path(self):
        return self._path

    def start(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # A stale socket file from a crashed gateway blocks bind; probe
            # it first and reclaim only if nothing answers.
            if self._path.exists():
                if not _socket_is_live(self._path):
                    self._path.unlink(missing_ok=True)
                else:
                    logger.debug(
                        "Control socket %s already served by another process", self._path
                    )
                    return False
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # Bind under a tightened umask so the socket is 0600 from the
            # instant it becomes reachable — bind-then-chmod would leave a
            # TOCTOU window where other local users could connect.
            old_umask = os.umask(0o177)
            try:
                sock.bind(str(self._path))
            finally:
                os.umask(old_umask)
            self._created_file = True
            sock.listen(_BACKLOG)
            sock.settimeout(1.0)
            self._sock = sock
        except OSError as exc:
            logger.debug("Control socket bind failed (%s): %s", self._path, exc)
            return False

        self._thread = threading.Thread(
            target=self._serve, name="gateway-control-socket", daemon=True
        )
        self._thread.start()
        logger.debug("Control socket serving at %s", self._path)
        return True

    def _serve(self) -> None:
        slots = threading.Semaphore(_MAX_CONCURRENT_CONNECTIONS)
        while not self._stop.is_set():
            try:
                assert self._sock is not None
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not slots.acquire(blocking=False):
                # Over the concurrent-connection cap — shed the excess
                # instead of spawning an unbounded thread per connection.
                try:
                    conn.close()
                except OSError:
                    pass
                continue

            def _serve_with_slot(c=conn):
                try:
                    _serve_connection(c)
                finally:
                    slots.release()

            threading.Thread(target=_serve_with_slot, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._created_file:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass


def _socket_is_live(path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect(str(path))
            probe.sendall(b'{"op": "identify"}\n')
            probe.settimeout(1.0)
            return bool(probe.recv(4096))
    except OSError:
        return False


def start_control_socket() -> Optional[ControlSocketServer]:
    """Start the control socket if possible; None when unavailable."""
    server = ControlSocketServer()
    return server if server.start() else None


def query_control_socket(
    op: str, timeout: float = _REQUEST_TIMEOUT_S, path=None
) -> Optional[dict]:
    """Client-side helper for CLI/updater. None → socket absent/unusable.

    Callers should treat None as "no live socket" and fall back to the
    PID-file / runtime-status scan layer.
    """
    sock_path = path if path is not None else control_socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(sock_path))
            sock.sendall(json.dumps({"op": op}).encode("utf-8") + b"\n")
            buf = b""
            while b"\n" not in buf:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data
            line = buf.split(b"\n", 1)[0].strip()
            return json.loads(line.decode("utf-8")) if line else None
    except (OSError, ValueError):
        return None
