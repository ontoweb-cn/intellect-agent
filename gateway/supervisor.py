"""Gateway supervisor — multi-profile child-process management (MP-00a / B1-2).

Per the multiplex ADR, supervisor mode runs ZERO gateways in this process.
Instead it:

1. resolves the serve set (default profile + secondaries) via
   ``profiles.profiles_to_serve(multiplex=True, allowlist)``;
2. pre-checks each secondary's config for PORT-BINDING platforms — a
   secondary that wants its own HTTP listener is rejected at startup
   (the supervisor front end owns the only listener, B1-4);
3. spawns one zero-config-change gateway child per profile
   (``INTELLECT_HOME=<profile home> python -m gateway.run``);
4. waits for each child's control socket to answer ``identify``
   (wait-for-ready probe — the spike showed fixed delays are unreliable);
5. monitors children forever: a dead child restarts with exponential
   backoff, and one child's death NEVER touches the others;
6. on SIGTERM/SIGINT stops everything in reverse.

This process hosts no platforms itself. HTTP/WS front-end routing arrives
in B1-4/B1-5; until then clients talk to each child directly.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Platforms that bind a host port / listener. In multiplex mode the front
# end owns the ONLY listener — a secondary configuring one of these is a
# startup error (fail-closed, per plan B1-4).
PORT_BINDING_PLATFORMS = {"api_server", "webhook"}

READY_TIMEOUT_S = 90.0
READY_POLL_S = 1.0
RESTART_BACKOFF_MIN_S = 1.0
RESTART_BACKOFF_MAX_S = 30.0
MONITOR_POLL_S = 2.0


class PortConflictError(RuntimeError):
    """A secondary profile configured a port-binding platform."""


@dataclass
class ProfileChild:
    """One managed gateway child."""

    name: str
    home: Path
    proc: Optional[subprocess.Popen] = None
    ready: bool = False
    restarts: int = 0
    backoff: float = RESTART_BACKOFF_MIN_S
    last_exit_at: float = 0.0
    desired: bool = True  # False while we are shutting it down
    port_rejected: bool = False
    control_sock: Path = field(default_factory=Path)

    def env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["INTELLECT_HOME"] = str(self.home)
        return env


def resolve_serve_set(allowlist: Optional[List[str]] = None) -> List[tuple]:
    """Serve set for supervisor mode: [(name, home)] incl. default profile."""
    from intellect_cli.profiles import profiles_to_serve

    return profiles_to_serve(multiplex=True, profile_allowlist=allowlist)


def child_platforms(home: Path) -> set:
    """Enabled platform names configured in a profile home (best-effort)."""
    try:
        import yaml

        cfg = home / "config.yaml"
        if not cfg.exists():
            return set()
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        platforms = data.get("platforms") or {}
        if not isinstance(platforms, dict):
            return set()
        enabled = set()
        for name, pcfg in platforms.items():
            if not isinstance(pcfg, dict):
                continue
            if pcfg.get("enabled", True):
                enabled.add(str(name).strip().lower())
        return enabled
    except Exception as exc:
        logger.debug("child_platforms(%s) failed: %s", home, exc)
        return set()


def precheck_port_conflicts(home: Path, name: str) -> None:
    """Raise :class:`PortConflictError` when a SECONDARY wants a listener."""
    enabled = child_platforms(home)
    conflicts = enabled & PORT_BINDING_PLATFORMS
    if conflicts:
        raise PortConflictError(
            f"profile {name!r} enables port-binding platform(s) "
            f"{', '.join(sorted(conflicts))} — in multiplex mode only the "
            "supervisor owns listeners; serve this profile standalone or "
            "disable those platforms in its config.yaml"
        )


class Supervisor:
    """Spawns, probes, monitors and restarts per-profile gateway children."""

    def __init__(
        self,
        serve_set: List[tuple],
        *,
        python: Optional[str] = None,
        ready_timeout_s: float = READY_TIMEOUT_S,
        spawn_factory: Optional[Callable] = None,
        probe: Optional[Callable] = None,
    ) -> None:
        self.children: Dict[str, ProfileChild] = {}
        self._stop = False
        self._python = python or sys.executable
        self._ready_timeout_s = ready_timeout_s
        self._spawn_factory = spawn_factory  # test seam: (child) -> Popen-like
        self._probe = probe  # test seam: (child) -> bool
        for name, home in serve_set:
            sock = Path(home) / "gateway.control.sock"
            self.children[name] = ProfileChild(name=name, home=Path(home),
                                               control_sock=sock)

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        for child in self.children.values():
            if child.name == "default":
                # The default profile IS this supervisor's own profile in the
                # common single-home deployment; when the supervisor runs
                # under the default home it must not spawn itself.
                if Path(child.home).resolve() == _own_home().resolve():
                    logger.info(
                        "Skipping default profile: supervisor runs inside it"
                    )
                    child.desired = False
                    child.port_rejected = True  # mark as not-managed
                    continue
            try:
                precheck_port_conflicts(child.home, child.name)
            except PortConflictError as exc:
                logger.error("Profile rejected (port conflict): %s", exc)
                child.port_rejected = True
                child.desired = False
                continue
            self._spawn(child)
        self._monitor_loop()

    def stop(self) -> None:
        """Initiate shutdown. Non-blocking: safe to call from a signal
        handler (review P2-2) — only sets the flag and sends SIGTERM to
        alive children; the wait/SIGKILL escalation happens in the monitor
        loop's finalization after it observes ``_stop``."""
        logger.info("Supervisor stopping: terminating %d child(ren)",
                    len(self.children))
        self._stop = True
        for child in self.children.values():
            child.desired = False
            if self._child_alive(child):
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(os.getpgid(child.proc.pid), signal.SIGTERM)
                    else:
                        child.proc.terminate()
                except (OSError, ProcessLookupError):
                    pass

    # ── spawn / probe ──────────────────────────────────────────────────

    def _spawn(self, child: ProfileChild) -> None:
        cmd = [self._python, "-m", "gateway.run"]
        if self._spawn_factory is not None:
            # Test seam: a factory returning None means "stub — skip real
            # process handling (pid logging / ready probe)".
            child.proc = self._spawn_factory(child)
            if child.proc is None:
                child.ready = True
                return
        else:
            child.proc = subprocess.Popen(
                cmd,
                cwd=str(_repo_root()),
                env=child.env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # kill B must not touch A
            )
        child.ready = False
        child.last_exit_at = time.time()
        logger.info(
            "Spawned gateway child %r (pid=%s, home=%s)",
            child.name, child.proc.pid, child.home,
        )
        if self._wait_ready(child):
            child.ready = True
            child.backoff = RESTART_BACKOFF_MIN_S
            logger.info("Child %r ready (control socket answering)", child.name)
        else:
            logger.warning(
                "Child %r not ready within %.0fs — monitor will handle it",
                child.name, self._ready_timeout_s,
            )

    def _wait_ready(self, child: ProfileChild) -> bool:
        """Wait-for-ready probe (spike acceptance clause): poll the child's
        control socket `identify` until it answers or the timeout expires.
        NEVER a fixed delay."""
        deadline = time.time() + self._ready_timeout_s
        while time.time() < deadline:
            if self._stop or not self._child_alive(child):
                return False
            if self._probe is not None:
                if self._probe(child):
                    return True
            else:
                try:
                    from gateway.control_socket import query_control_socket

                    ident = query_control_socket(
                        "identify", timeout=2.0, path=child.control_sock
                    )
                    if ident and ident.get("ok"):
                        return True
                except Exception:
                    pass  # not ready yet — keep polling
            time.sleep(READY_POLL_S)
        return False

    def _child_alive(self, child: ProfileChild) -> bool:
        if child.proc is None:
            return False
        return child.proc.poll() is None

    # ── monitor ────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop:
            # Small-step sleep so signal-handler stop() is honored promptly
            # even during long backoff waits.
            self._sleep_interruptible(MONITOR_POLL_S)
            for child in self.children.values():
                if self._stop or not child.desired:
                    continue
                if self._child_alive(child):
                    continue
                # Dead child: restart with backoff. Other children are
                # untouched by design (process-boundary isolation).
                logger.warning(
                    "Child %r exited (restart #%d) — restarting in %.1fs",
                    child.name, child.restarts + 1, child.backoff,
                )
                self._sleep_interruptible(child.backoff)
                # P1 race guard: stop() may have arrived DURING the backoff
                # wait. Spawning past it would leak an orphaned gateway
                # (stop already ran its terminate pass and the supervisor
                # is about to exit). Break instead of spawning.
                if self._stop or not child.desired:
                    break
                child.restarts += 1
                child.backoff = min(child.backoff * 2, RESTART_BACKOFF_MAX_S)
                self._spawn(child)
        # Shutdown finalization (runs after the loop observes _stop):
        # blocking wait + SIGKILL escalation belongs HERE, not in the
        # signal handler (review P2-2).
        for child in self.children.values():
            if child.desired:
                continue
            self._finalize_terminate(child)

    def _finalize_terminate(self, child: ProfileChild, grace_s: float = 10.0) -> None:
        """Post-loop wait/SIGKILL escalation for a SIGTERM'd child."""
        if child.proc is None or not self._child_alive(child):
            return
        try:
            child.proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            logger.warning("Child %r ignored SIGTERM — SIGKILL", child.name)
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(child.proc.pid), signal.SIGKILL)
                else:
                    child.proc.kill()
            except (OSError, ProcessLookupError):
                pass

    # ── termination ────────────────────────────────────────────────────

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep in small steps so stop() (signal handler) is honored fast."""
        deadline = time.time() + seconds
        while not self._stop and time.time() < deadline:
            time.sleep(min(0.25, max(0.05, deadline - time.time())))

def _own_home() -> Path:
    from intellect_constants import get_intellect_home

    return get_intellect_home()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_supervisor(allowlist: Optional[List[str]] = None) -> int:
    """CLI entry: run the supervisor until signalled. Returns exit code."""
    serve_set = resolve_serve_set(allowlist)
    logger.info(
        "Supervisor serving %d profile(s): %s",
        len(serve_set), ", ".join(name for name, _ in serve_set),
    )
    sup = Supervisor(serve_set)

    def _handle_signal(signum, frame):
        logger.info("Supervisor received signal %s", signum)
        sup.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Monitor loop runs inline; start() returns when sup.stop() flips the
    # flag (start() internally runs _monitor_loop — so stop() must be called
    # from a signal handler thread; signal handlers run in the main thread
    # between bytecodes, and _monitor_loop sleeps in chunks, so this works).
    sup.start()
    return 0
