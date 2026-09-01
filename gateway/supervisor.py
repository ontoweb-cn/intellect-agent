"""Gateway supervisor — multi-profile child-process management (MP-00a / B1-2).

Per the multiplex ADR, supervisor mode runs ZERO gateways in this process.
Instead it:

1. resolves the serve set (default profile + secondaries) via
   ``profiles.profiles_to_serve(multiplex=True, allowlist)``;
2. pre-checks each secondary's config: with the front end active (B1-4)
   only a PINNED port/host is a conflict — an unpinned listener platform
   is served under the front end's ``/p/<name>/`` prefix (children rebind
   to an internal loopback ephemeral port via INTELLECT_MULTIPLEX_CHILD);
3. spawns one gateway child per profile (``INTELLECT_HOME=<profile home>
   INTELLECT_MULTIPLEX_CHILD=1 python -m gateway.run``);
4. waits for each child's control socket to answer ``identify``
   (wait-for-ready probe — the spike showed fixed delays are unreliable);
5. monitors children forever: a dead child restarts with exponential
   backoff, one child's death NEVER touches the others, and each ready
   child's bound listener ports are polled from its control socket
   ``status`` for the front end;
6. on SIGTERM/SIGINT stops everything in reverse.

The HTTP/WS front end lives in :mod:`gateway.multiplex_front` (B1-4/B1-5):
it owns the ONLY external listener and routes by profile URL prefix. When
aiohttp is unavailable the supervisor degrades to the pre-B1-4 shape (no
front end, strict listener-platform precheck, children direct).
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
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Set on supervisor-spawned children (and only there). Listener platforms
# in a child rebind to an internal loopback ephemeral port; the front end
# (B1-4) owns the only external listener. Standalone gateways never see
# the flag, so every behavior gated on it is a no-op when multiplex is off.
MULTIPLEX_CHILD_ENV = "INTELLECT_MULTIPLEX_CHILD"

# Hosts that only serve same-machine connections (mirrors the webhook
# adapter's loopback set — listener *pinning* to these is allowed in a
# multiplex secondary because the effective bind is internal anyway).
_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1",
    "localhost",
    "::1",
    "ip6-localhost",
    "ip6-loopback",
})


class PortConflictError(RuntimeError):
    """A secondary profile configured a port-binding platform."""


def is_multiplex_child() -> bool:
    """True when this gateway process is a supervisor-spawned child."""
    return os.environ.get(MULTIPLEX_CHILD_ENV, "") == "1"


def resolved_runner_port(runner: Any) -> Optional[int]:
    """First bound (host, port) of a started aiohttp AppRunner, or None.

    Defensive about aiohttp versions where ``addresses`` is a property or
    a method; used by listener adapters to report their resolved ephemeral
    port to the multiplex front end.
    """
    try:
        addrs = runner.addresses
        if callable(addrs):
            addrs = addrs()
        for addr in addrs or []:
            if isinstance(addr, (tuple, list)) and len(addr) >= 2:
                return int(addr[1])
    except Exception:
        pass
    return None


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
    # True when start() skipped this profile because the supervisor itself
    # runs inside its home (the common default-profile deployment) — NOT a
    # config conflict.
    skipped_own_home: bool = False
    control_sock: Path = field(default_factory=Path)
    # Listener ports the child reported via its control socket `status`
    # (B1-4): {"api_server": 41234, "webhook": 41235}. The front end
    # routes /p/<name>/ traffic to these internal loopback ports.
    listeners: Dict[str, int] = field(default_factory=dict)

    def env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["INTELLECT_HOME"] = str(self.home)
        env[MULTIPLEX_CHILD_ENV] = "1"
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


def listener_binding(home: Path, platform: str) -> Tuple[Optional[str], Optional[int]]:
    """(host, port) a profile's config pins for a listener platform.

    B1-4: merely ENABLING a listener platform is fine in a multiplex
    secondary — the front end serves it under ``/p/<name>/`` and the child
    rebinds to an internal ephemeral port. Only a pinned ``port`` (or a
    non-loopback ``host``) expresses "I want my own external binding".
    """
    try:
        import yaml

        cfg = home / "config.yaml"
        if not cfg.exists():
            return (None, None)
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        pcfg = (data.get("platforms") or {}).get(platform)
        if not isinstance(pcfg, dict):
            return (None, None)
        host = pcfg.get("host")
        port = pcfg.get("port")
        return (
            str(host).strip() if host else None,
            int(port) if port is not None else None,
        )
    except Exception as exc:
        logger.debug("listener_binding(%s, %s) failed: %s", home, platform, exc)
        return (None, None)


def listener_is_pinned(home: Path, platform: str) -> bool:
    """True when a profile's config pins a listener platform to its own
    external binding (explicit port, or a non-loopback host).

    Shared by the B1-4 precheck and ``intellect doctor``'s multiplex check.
    """
    host, port = listener_binding(home, platform)
    return port is not None or (
        host is not None and host.lower() not in _LOOPBACK_HOSTS
    )


def precheck_port_conflicts(home: Path, name: str) -> None:
    """Raise :class:`PortConflictError` when a SECONDARY pins its own binding.

    B1-4 rule (front end active): a listener platform without a pinned
    port/host is served under the front end's ``/p/<name>/`` prefix; a
    pinned port or a non-loopback host means the profile wants its own
    external listener, which multiplex forbids (fail-closed with a
    readable error, per MP-04 acceptance).
    """
    enabled = child_platforms(home)
    for platform in sorted(enabled & PORT_BINDING_PLATFORMS):
        if listener_is_pinned(home, platform):
            host, port = listener_binding(home, platform)
            detail = f"port {port}" if port is not None else f"host {host!r}"
            raise PortConflictError(
                f"profile {name!r} pins platform {platform!r} to {detail} — "
                "in multiplex mode the supervisor front end owns the only "
                "external listener; remove the explicit host/port from its "
                f"config.yaml to serve it under /p/{name}/"
            )


def _strict_precheck_port_conflicts(home: Path, name: str) -> None:
    """Pre-B1-4 rule: ANY listener-platform secondary is rejected.

    Only used when the front end is unavailable (aiohttp missing), where
    an enabled listener platform would be unreachable by construction.
    """
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
        # The HTTP/WS front end (gateway.multiplex_front, B1-4), when the
        # supervisor is running with one. Its presence decides which
        # port-conflict precheck rule applies (see start()).
        self.front_end: Optional[Any] = None
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
                    child.skipped_own_home = True
                    continue
            try:
                if self.front_end is not None:
                    # B1-4 rule: only a PINNED port/host is a conflict.
                    precheck_port_conflicts(child.home, child.name)
                else:
                    # No front end (aiohttp missing): an enabled listener
                    # platform would be unreachable — keep the strict
                    # pre-B1-4 rule.
                    _strict_precheck_port_conflicts(child.home, child.name)
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

    def _refresh_listeners(self, child: ProfileChild) -> None:
        """Poll a ready child's control socket `status` for bound ports (B1-4).

        Under the supervisor, children rebind listener platforms to internal
        loopback ephemeral ports; the front end routes by the ports the child
        reports in its runtime status. Absent/unusable payloads leave the
        previous snapshot untouched — the front end answers 503 until a real
        port shows up.
        """
        if not child.ready or not self._child_alive(child):
            return
        try:
            from gateway.control_socket import query_control_socket

            snap = query_control_socket(
                "status", timeout=2.0, path=child.control_sock
            )
        except Exception:
            return
        if not isinstance(snap, dict) or not snap.get("ok"):
            return
        platforms = (snap.get("runtime_status") or {}).get("platforms") or {}
        listeners: Dict[str, int] = {}
        for platform in PORT_BINDING_PLATFORMS:
            info = platforms.get(platform)
            if not isinstance(info, dict):
                continue
            port = info.get("port")
            if isinstance(port, int) and port > 0:
                listeners[platform] = port
        if listeners != child.listeners:
            child.listeners = listeners
            logger.info("Child %r listeners: %s", child.name, listeners or "{}")

    # ── monitor ────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop:
            # Small-step sleep so signal-handler stop() is honored promptly
            # even during long backoff waits.
            self._sleep_interruptible(MONITOR_POLL_S)
            self._write_observability()
            for child in self.children.values():
                if self._stop or not child.desired:
                    continue
                if self._child_alive(child):
                    self._refresh_listeners(child)
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

    # ── observability (MP-06) ──────────────────────────────────────────

    @staticmethod
    def _profile_state(child: "ProfileChild") -> str:
        if child.skipped_own_home:
            return "own-home"
        if child.port_rejected:
            return "rejected"
        if not child.desired:
            return "stopping"
        if child.proc is not None and not child.ready:
            return "starting"
        if child.proc is None:
            return "backoff" if child.restarts else "pending"
        if not child.ready:
            return "starting"
        return "ready"

    def observability_snapshot(self) -> List[dict]:
        """Per-profile summary for runtime status / control socket / CLI."""
        return [
            {
                "name": child.name,
                "pid": child.proc.pid if child.proc is not None else None,
                "state": self._profile_state(child),
                "restarts": child.restarts,
                "port_rejected": child.port_rejected,
                "listeners": dict(child.listeners),
            }
            for child in self.children.values()
        ]

    def _write_observability(self, *, force: bool = False) -> None:
        """Persist served_profiles into this home's runtime status.

        Written on change only (monitor-loop cadence is 2s — writing every
        cycle would churn the status file for no reader benefit).
        """
        snapshot = self.observability_snapshot()
        try:
            import json as _json

            payload = _json.dumps(snapshot, sort_keys=True)
        except Exception:
            payload = ""
        if not force and payload == getattr(self, "_last_observe_payload", None):
            return
        self._last_observe_payload = payload
        try:
            from gateway.status import write_runtime_status

            write_runtime_status(
                gateway_state="multiplex", served_profiles=snapshot
            )
        except Exception as exc:
            logger.debug("served_profiles status write failed: %s", exc)

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


def _write_supervisor_pid() -> bool:
    """Claim this home's gateway.pid for the supervisor (MP-06).

    Returns False (with a logged, readable error) when a LIVE gateway or
    supervisor already owns the home; stale records are cleaned by
    ``get_running_pid`` and the write retried.
    """
    try:
        from gateway.status import get_running_pid, write_pid_file

        try:
            write_pid_file()
            return True
        except FileExistsError:
            live = get_running_pid()
            if live:
                logger.error(
                    "PID %s already runs a gateway under this home — "
                    "refusing to start a second multiplex supervisor "
                    "(stop it first or use a different profile)",
                    live,
                )
                return False
            write_pid_file()  # stale record — get_running_pid cleaned it
            return True
    except OSError as exc:
        logger.warning("Supervisor pid-file write failed: %s", exc)
        return True  # observability loss must not block supervision


def run_supervisor(allowlist: Optional[List[str]] = None) -> int:
    """CLI entry: run the supervisor (and front end) until signalled.

    Returns exit code. Signal safety: the handler only sets flags
    (review P2-2) — the monitor thread observes ``_stop`` and the front
    end's stop event unwinds the asyncio loop on the main thread.
    """
    serve_set = resolve_serve_set(allowlist)
    logger.info(
        "Supervisor serving %d profile(s): %s",
        len(serve_set), ", ".join(name for name, _ in serve_set),
    )
    if not _write_supervisor_pid():
        return 1
    sup = Supervisor(serve_set)

    # Front end (B1-4) is optional: without aiohttp the supervisor keeps
    # the pre-B1-4 shape (strict listener precheck, children direct).
    front = None
    try:
        from gateway.multiplex_front import MultiplexFront
    except ImportError as exc:
        logger.warning(
            "aiohttp unavailable (%s) — multiplex front end disabled; "
            "secondaries with listener platforms stay rejected and each "
            "child is reachable only on its own configured ports",
            exc,
        )
    else:
        front = MultiplexFront(sup)
        sup.front_end = front

    # Supervisor control socket (MP-06): identifies with role=supervisor
    # and a live served_profiles snapshot. The own-home child never runs,
    # so this home's socket path is free by construction.
    control = None
    try:
        from gateway.control_socket import ControlSocketServer

        control = ControlSocketServer(
            extra_provider=lambda: {
                "role": "supervisor",
                "served_profiles": sup.observability_snapshot(),
            }
        )
        if not control.start():
            control = None
    except Exception as exc:
        logger.debug("Supervisor control socket unavailable: %s", exc)
        control = None

    sup._write_observability(force=True)

    thread_error: List[BaseException] = []

    def _monitor_body() -> None:
        try:
            sup.start()
        except BaseException as exc:  # noqa: BLE001 — must not exit silently
            logger.exception("Supervisor monitor crashed")
            thread_error.append(exc)
            if front is not None:
                front.request_stop()

    def _handle_signal(signum, frame):
        logger.info("Supervisor received signal %s", signum)
        sup.stop()
        if front is not None:
            front.request_stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    exit_code = 0
    try:
        if front is None:
            # Degraded mode: monitor loop runs inline exactly as in B1-2.
            sup.start()
        else:
            import asyncio
            import threading

            monitor = threading.Thread(
                target=_monitor_body,
                name="gateway-supervisor-monitor",
                daemon=True,
            )
            monitor.start()
            try:
                exit_code = asyncio.run(front.run())
            finally:
                sup.stop()
                monitor.join(timeout=15)
            if thread_error:
                exit_code = exit_code or 1
        return exit_code
    finally:
        if control is not None:
            try:
                control.stop()
            except Exception:
                pass
        # Post-mortem hygiene: the topology snapshot describes dead children
        # once this process exits — clear it so status readers don't infer
        # a live multiplex from a stale file.
        try:
            from gateway.status import remove_pid_file, write_runtime_status

            sup.stop()
            write_runtime_status(
                gateway_state="stopped",
                exit_reason="supervisor shutdown",
                served_profiles=[],
            )
            remove_pid_file()
        except Exception:
            pass
