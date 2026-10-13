"""Tests for agent/stream_guard.py — thread-safe stdio redirection.

Regression context: the 2026-10-12 WebUI outage. Process-global
``sys.stdout = devnull`` swaps raced between concurrent agent threads; the
losing restore left a *closed* devnull installed process-wide, so every
``print()`` — including the WebUI access log — raised
``ValueError: I/O operation on closed file`` until the process was killed.

Note on technique: pytest's global capture re-assigns ``sys.stdout`` between
fixture setup and the call phase, so installing the guard from a *fixture*
silently loses to ``EncodedFile``/``CaptureIO``. The ``guarded_stdio`` fixture
therefore hands back an ``install()`` helper that the test body invokes.
"""

import io
import sys
import threading

import pytest

from agent.stream_guard import (
    NULL_STREAM,
    GuardedStream,
    install_stream_guard,
    redirect_thread_output,
    stream_guard_installed,
)


class _GuardedStdio:
    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.installed = False

    def install(self):
        """Swap sys.stdout/sys.stderr to fresh guards. Call from the test
        body — not from a fixture — or pytest capture wins the race."""
        self._monkeypatch.setattr(
            sys, "stdout", GuardedStream(self.out, "stdout")
        )
        self._monkeypatch.setattr(
            sys, "stderr", GuardedStream(self.err, "stderr")
        )
        self._monkeypatch.setattr(
            "agent.stream_guard._guard_installed",
            {"stdout": True, "stderr": True},
        )
        self.installed = True
        return self.out, self.err


@pytest.fixture
def guarded_stdio(monkeypatch):
    return _GuardedStdio(monkeypatch)


class TestGuardedStream:
    def test_writes_reach_target(self, guarded_stdio):
        out, _ = guarded_stdio.install()
        sys.stdout.write("hello\n")
        assert "hello" in out.getvalue()

    def test_thread_override_silences_only_that_thread(self, guarded_stdio):
        out, _ = guarded_stdio.install()
        seen = io.StringIO()

        def worker():
            with redirect_thread_output(seen):
                sys.stdout.write("from worker\n")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        sys.stdout.write("from main\n")
        assert "from worker" in seen.getvalue()
        assert "from worker" not in out.getvalue()
        assert "from main" in out.getvalue()

    def test_override_cleaned_up_after_context(self, guarded_stdio):
        out, _ = guarded_stdio.install()

        def worker():
            with redirect_thread_output(NULL_STREAM):
                pass
            sys.stdout.write("after restore\n")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert "after restore" in out.getvalue()

    def test_close_does_not_kill_target(self, guarded_stdio):
        out, _ = guarded_stdio.install()
        guard = sys.stdout
        guard.close()
        assert not out.closed
        sys.stdout.write("still alive\n")
        assert "still alive" in out.getvalue()

    def test_write_to_dead_target_is_swallowed(self):
        dead = io.StringIO()
        dead.close()
        guard = GuardedStream(dead, "stdout")
        assert guard.write("no raise") == len("no raise")
        guard.flush()

    def test_passthrough_attributes(self, monkeypatch):
        class Target(io.StringIO):
            encoding = "utf-8"

            def isatty(self):
                return True

        import agent.stream_guard as sg

        guard = GuardedStream(Target(), "stdout")
        assert guard.encoding == "utf-8"
        assert guard.isatty() is True
        # attribute resolution follows the thread's current stream
        monkeypatch.setattr(sys, "stdout", guard)
        monkeypatch.setattr(sys, "stderr", guard)
        monkeypatch.setattr(
            sg, "_guard_installed", {"stdout": True, "stderr": True}
        )
        with redirect_thread_output(NULL_STREAM):
            assert guard.writable() is True

    def test_fileno_resolves_to_target_even_under_override(self, monkeypatch):
        """Library compat: fileno() must return the process-level descriptor
        even while this thread's writes are suppressed (the old devnull
        override exposed a valid fd; the guard keeps that contract)."""
        import agent.stream_guard as sg

        class Target(io.StringIO):
            def fileno(self):
                return 42

        guard = GuardedStream(Target(), "stdout")
        monkeypatch.setattr(sys, "stdout", guard)
        monkeypatch.setattr(sys, "stderr", guard)
        monkeypatch.setattr(
            sg, "_guard_installed", {"stdout": True, "stderr": True}
        )
        with redirect_thread_output(NULL_STREAM):
            assert guard.fileno() == 42

    def test_null_stream_discards(self):
        assert NULL_STREAM.write("x") == 1
        NULL_STREAM.flush()
        NULL_STREAM.close()
        assert NULL_STREAM.closed is False


class TestInstallStreamGuard:
    def test_install_wraps_once(self, monkeypatch):
        import agent.stream_guard as sg

        out, err = io.StringIO(), io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        monkeypatch.setattr(
            sg, "_guard_installed", {"stdout": False, "stderr": False}
        )

        sg.install_stream_guard()
        first = sys.stdout
        assert isinstance(first, GuardedStream)
        assert isinstance(sys.stderr, GuardedStream)

        sg.install_stream_guard()  # idempotent
        assert sys.stdout is first
        assert stream_guard_installed() is True

    def test_install_rejected_after_race_restore(self, monkeypatch):
        """Simulates the outage shape: sys.stdout was globally swapped away
        from the guard and a racing restore put a closed object back. A fresh
        install must wrap whatever is current, restoring write safety."""
        import agent.stream_guard as sg

        dead = io.StringIO()
        dead.close()
        monkeypatch.setattr(sys, "stdout", dead)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        monkeypatch.setattr(
            sg, "_guard_installed", {"stdout": False, "stderr": False}
        )

        sg.install_stream_guard()
        sys.stdout.write("safe again\n")
        assert isinstance(sys.stdout, GuardedStream)

    def test_redirect_tolerates_unguarded_stdout(self, monkeypatch):
        """If the installed-flag lies (sys.stdout was swapped to a plain
        stream after install), redirect_thread_output must degrade to a no-op
        for that stream instead of raising AttributeError."""
        import agent.stream_guard as sg

        plain = io.StringIO()
        monkeypatch.setattr(sys, "stdout", plain)
        monkeypatch.setattr(sys, "stderr", plain)
        monkeypatch.setattr(
            sg, "_guard_installed", {"stdout": True, "stderr": True}
        )

        with redirect_thread_output(NULL_STREAM):
            plain.write("inside")
        assert "inside" in plain.getvalue()


class TestRaceRegression:
    def test_concurrent_redirects_never_poison_global_stdout(self, guarded_stdio):
        """THE regression test. With the old process-global swap, two threads
        redirecting concurrently left sys.stdout pointing at a closed stream
        after the interleaving {A.save, B.save, A.restore, B.restore}. The
        thread-local guard keeps sys.stdout writable no matter how the threads
        interleave.
        """
        out, _ = guarded_stdio.install()
        errors = []
        barrier = threading.Barrier(8)

        def worker(n: int) -> None:
            barrier.wait()  # maximize interleaving pressure
            for _ in range(50):
                try:
                    with redirect_thread_output(NULL_STREAM):
                        sys.stdout.write(f"w{n}\n")
                    sys.stdout.write(f"after w{n}\n")  # must land on target
                except Exception as exc:  # pragma: no cover — the old bug
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"writes raised under concurrency: {errors[:3]}"
        assert isinstance(sys.stdout, GuardedStream)
        assert not sys.stdout.closed
        assert "after w0" in out.getvalue()

    def test_restore_never_installs_closed_stream(self, guarded_stdio):
        """Even a maliciously closed override must not leak: exiting the
        context restores *no* stream (thread-local cleanup), leaving the
        process target untouched."""
        out, _ = guarded_stdio.install()
        hostile = io.StringIO()
        hostile.close()

        def worker():
            with redirect_thread_output(hostile):
                pass
            sys.stdout.write("survived\n")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert "survived" in out.getvalue()
        assert sys.stdout is not hostile
