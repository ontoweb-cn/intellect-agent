"""Thread-safe stdout/stderr redirection guard.

History (2026-10-12 WebUI outage): ``tools/code_execution_tool.py`` and
``contextlib.redirect_stdout`` call sites swapped the process-global
``sys.stdout`` to devnull around tool dispatch. When two threads redirected
concurrently (WebUI background-review thread + an execute_code RPC thread),
the second thread's restore put a *closed* devnull back into ``sys.stdout``,
and every subsequent ``print()`` in the process raised
``ValueError: I/O operation on closed file`` — killing all WebUI responses
for as long as the process stayed up.

This module provides the fix:

- ``GuardedStream`` — a stable proxy installed once as ``sys.stdout`` /
  ``sys.stderr``. ``print()`` and libraries keep working through it; the
  underlying target is never closed or replaced by the guard itself.
- ``redirect_thread_output()`` — per-thread redirection. Only the calling
  thread's writes go to the override stream; every other thread is unaffected,
  so concurrent tool dispatch can no longer corrupt global state.

The proxy also tolerates a dead underlying target: if the real stdout was
closed by some other bug, writes become no-ops instead of raising, so a
long-lived server keeps serving (logging paths should use files anyway).
"""

import contextlib
import io
import sys
import threading

__all__ = [
    "NULL_STREAM",
    "GuardedStream",
    "install_stream_guard",
    "stream_guard_installed",
    "redirect_thread_output",
]


class _NullStream:
    """Discards everything written to it. Class-level, so no fd and no
    closeable state — unlike ``open(os.devnull)`` there is nothing a racing
    thread can close out from under us."""

    encoding = "utf-8"
    errors = "strict"

    def write(self, s: str) -> int:
        return len(s) if isinstance(s, str) else 0

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise io.UnsupportedOperation("fileno() on null stream")

    @property
    def closed(self) -> bool:
        return False

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)


NULL_STREAM = _NullStream()


class GuardedStream:
    """Process-wide proxy for one stdio stream.

    ``write``/``flush``/``writelines`` go to the calling thread's override
    (``redirect_thread_output``) when one is active, otherwise to the
    underlying target captured at install time. All other attributes are
    forwarded to the same resolution, so ``isatty()``, ``fileno()``,
    ``encoding`` etc. keep working for libraries that probe them.

    The proxy is deliberately immortal: ``close()`` only closes the calling
    thread's override, never the underlying target, and writes to a closed
    or broken target are swallowed. A stdio replacement that can raise is how
    the 2026-10-12 outage happened; this class exists so that can't recur.
    """

    def __init__(self, target, name: str = "stdout"):
        self._target = target
        self._name = name
        self._tls = threading.local()
        self._target_lock = threading.Lock()

    # ── thread-local override plumbing ────────────────────────────────
    def _current(self):
        override = getattr(self._tls, "stream", None)
        if override is not None:
            return override
        return self._target

    @property
    def target(self):
        return self._target

    @property
    def name(self) -> str:
        return self._name

    # ── write path (never raises) ─────────────────────────────────────
    def write(self, s: str) -> int:
        try:
            stream = self._current()
            result = stream.write(s)
            return result if isinstance(result, int) else len(s)
        except (ValueError, OSError, RuntimeError):
            # Closed/broken underlying stream — drop instead of killing the
            # calling thread. Logging to files is the durable path.
            return len(s) if isinstance(s, str) else 0

    def flush(self) -> None:
        try:
            self._current().flush()
        except (ValueError, OSError, RuntimeError):
            pass

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def fileno(self) -> int:
        """Descriptor of the *underlying target*, regardless of override.

        fd 1/2 belong to the process; the thread override only diverts
        Python-level writes. Libraries probing fileno() inside a suppressed
        window (tqdm, rich, ...) therefore keep getting the real descriptor,
        matching the pre-guard behaviour where the override was an open
        devnull file. Explicitly defined (not via __getattr__) so the
        resolution never follows the override stream.
        """
        return self._target.fileno()

    # ── close semantics ───────────────────────────────────────────────
    def close(self) -> None:
        # Only the calling thread's override is closeable through the proxy.
        # The underlying real stream belongs to the process — closing it from
        # here is exactly the bug class this guard exists to prevent.
        override = getattr(self._tls, "stream", None)
        if override is not None:
            try:
                override.close()
            except Exception:
                pass
            self._tls.stream = None

    @property
    def closed(self) -> bool:
        target = self._target
        try:
            return bool(getattr(target, "closed", False))
        except Exception:
            return False

    # ── passthrough ───────────────────────────────────────────────────
    def __getattr__(self, attr):
        # Only called for attributes not found normally (i.e. everything we
        # don't define above). Resolve against the thread's current stream.
        target = self._current()
        try:
            return getattr(target, attr)
        except (ValueError, OSError, RuntimeError):
            raise AttributeError(attr)

    def __repr__(self) -> str:
        return f"<GuardedStream {self._name} target={self._target!r}>"


_guard_lock = threading.Lock()
_guard_installed = {"stdout": False, "stderr": False}


def stream_guard_installed() -> bool:
    return _guard_installed["stdout"] and _guard_installed["stderr"]


def install_stream_guard() -> None:
    """Replace sys.stdout/sys.stderr with GuardedStream proxies (idempotent).

    Safe to call from any thread at any time; later callers are no-ops. The
    current objects are kept as the underlying targets, so whatever the
    process was writing to before keeps receiving output.
    """
    with _guard_lock:
        if not _guard_installed["stdout"] and not isinstance(sys.stdout, GuardedStream):
            sys.stdout = GuardedStream(sys.stdout, "stdout")
            _guard_installed["stdout"] = True
        if not _guard_installed["stderr"] and not isinstance(sys.stderr, GuardedStream):
            sys.stderr = GuardedStream(sys.stderr, "stderr")
            _guard_installed["stderr"] = True


@contextlib.contextmanager
def redirect_thread_output(stream):
    """Redirect only the calling thread's stdout/stderr to *stream*.

    Pass ``NULL_STREAM`` to silence output (the execute_code use case) or a
    file-like object to capture it. On exit the thread's streams revert to the
    process-wide target. Other threads are never affected, which is what makes
    this safe under concurrent tool dispatch.
    """
    stdout_guard = sys.stdout if isinstance(sys.stdout, GuardedStream) else None
    stderr_guard = sys.stderr if isinstance(sys.stderr, GuardedStream) else None
    if stdout_guard is None or stderr_guard is None:
        install_stream_guard()
        # Re-verify what install actually left in place: the installed-flag
        # may be stale relative to reality (e.g. a test or embedder swapped
        # sys.stdout after install), and touching a non-guard stream's
        # thread-local slot would raise AttributeError. Only ever mutate
        # objects we can prove are GuardedStreams.
        if stdout_guard is None and isinstance(sys.stdout, GuardedStream):
            stdout_guard = sys.stdout
        if stderr_guard is None and isinstance(sys.stderr, GuardedStream):
            stderr_guard = sys.stderr
    try:
        if stdout_guard is not None:
            stdout_guard._tls.stream = stream
        if stderr_guard is not None:
            stderr_guard._tls.stream = stream
        yield
    finally:
        if stdout_guard is not None:
            stdout_guard._tls.stream = None
        if stderr_guard is not None:
            stderr_guard._tls.stream = None
