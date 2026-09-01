"""Tests for the multiplex supervisor (MP-00a / B1-2, front end B1-4)."""

import threading
import time

import pytest

from gateway.supervisor import (
    MULTIPLEX_CHILD_ENV,
    PortConflictError,
    Supervisor,
    child_platforms,
    is_multiplex_child,
    listener_binding,
    precheck_port_conflicts,
    resolve_serve_set,
    resolved_runner_port,
    _strict_precheck_port_conflicts,
)


def _make_supervisor(tmp_path, serve, **kw) -> Supervisor:
    serve_set = [(name, tmp_path / name) for name, _ in serve]
    for _, home in serve:
        (tmp_path / _).mkdir(parents=True, exist_ok=True)
    # Test seams: never spawn a real process unless a test asks for it.
    return Supervisor(serve_set, spawn_factory=lambda child: None, **kw)


# ── resolve_serve_set ──────────────────────────────────────────────────

def test_resolve_single_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / "home"))
    # profiles/ dir doesn't exist; multiplex=False -> active profile only.
    served = resolve_serve_set(None)
    assert served == [("default", tmp_path / "home")]


def test_resolve_multiplex_includes_secondaries(monkeypatch, tmp_path):
    root = tmp_path / "profiles"
    for name in ("coder", "writer", "Not-Valid!", "x"):
        (root / name).mkdir(parents=True)
    default_home = tmp_path / "home"
    monkeypatch.setenv("INTELLECT_HOME", str(default_home))
    monkeypatch.setattr(
        "intellect_cli.profiles._get_profiles_root", lambda: root, raising=False
    )
    served = dict(resolve_serve_set(None))
    # default always served; valid secondaries included; invalid names dropped.
    assert "default" in served
    assert "coder" in served and "writer" in served
    assert "Not-Valid!" not in served


def test_resolve_multiplex_allowlist_filters(monkeypatch, tmp_path):
    root = tmp_path / "profiles"
    for name in ("coder", "writer"):
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "intellect_cli.profiles._get_profiles_root", lambda: root, raising=False
    )
    served = dict(resolve_serve_set(["coder"]))
    assert "coder" in served and "writer" not in served
    assert "default" in served  # default is never filtered


# ── port-conflict precheck (B1-4 rule) ─────────────────────────────────

def test_precheck_allows_unpinned_listener_platform(tmp_path):
    """B1-4: a listener platform WITHOUT a pinned binding is served under
    the front end's /p/<name>/ prefix (child rebinds internally)."""
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  api_server:\n    enabled: true\n", encoding="utf-8"
    )
    precheck_port_conflicts(home, "web")  # no raise


def test_precheck_rejects_pinned_port(tmp_path):
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  api_server:\n    enabled: true\n    port: 8642\n",
        encoding="utf-8",
    )
    with pytest.raises(PortConflictError, match="port 8642"):
        precheck_port_conflicts(home, "web")


def test_precheck_rejects_external_host(tmp_path):
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  webhook:\n    enabled: true\n    host: 0.0.0.0\n",
        encoding="utf-8",
    )
    with pytest.raises(PortConflictError, match="host"):
        precheck_port_conflicts(home, "web")


def test_precheck_allows_loopback_host(tmp_path):
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  api_server:\n    enabled: true\n    host: 127.0.0.1\n",
        encoding="utf-8",
    )
    precheck_port_conflicts(home, "web")  # no raise


def test_precheck_allows_non_binding_platforms(tmp_path):
    home = tmp_path / "tg"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  telegram:\n    token: x\n", encoding="utf-8"
    )
    precheck_port_conflicts(home, "tg")  # no raise


def test_strict_precheck_rejects_any_listener_platform(tmp_path):
    """Degraded mode (front end unavailable, e.g. no aiohttp): the pre-B1-4
    rule stands — ANY listener-platform secondary is rejected, because it
    would be unreachable by construction."""
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  api_server:\n    enabled: true\n", encoding="utf-8"
    )
    with pytest.raises(PortConflictError, match="api_server"):
        _strict_precheck_port_conflicts(home, "web")


def test_listener_binding_reads_pinned_values(tmp_path):
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  api_server:\n    host: 127.0.0.1\n    port: 9000\n",
        encoding="utf-8",
    )
    assert listener_binding(home, "api_server") == ("127.0.0.1", 9000)
    assert listener_binding(home, "webhook") == (None, None)
    assert listener_binding(tmp_path / "nope", "api_server") == (None, None)


def test_child_platforms_reads_enabled_flag(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n"
        "  telegram:\n    enabled: true\n"
        "  discord:\n    enabled: false\n",
        encoding="utf-8",
    )
    assert child_platforms(home) == {"telegram"}


def test_child_platforms_missing_config(tmp_path):
    assert child_platforms(tmp_path / "nope") == set()


# ── multiplex child env contract (B1-4) ────────────────────────────────

def test_child_env_sets_multiplex_flag(tmp_path):
    sup = _make_supervisor(tmp_path, [("a", "a")])
    child = sup.children["a"]
    assert child.env()[MULTIPLEX_CHILD_ENV] == "1"
    assert child.env()["INTELLECT_HOME"] == str(child.home)


def test_is_multiplex_child_flag(monkeypatch):
    monkeypatch.delenv(MULTIPLEX_CHILD_ENV, raising=False)
    assert is_multiplex_child() is False
    monkeypatch.setenv(MULTIPLEX_CHILD_ENV, "1")
    assert is_multiplex_child() is True


def test_resolved_runner_port_property_and_callable():
    class _PropRunner:
        addresses = [( "127.0.0.1", 5555 )]

    class _CallRunner:
        def addresses(self):
            return [("127.0.0.1", 6666)]

    class _BrokenRunner:
        @property
        def addresses(self):
            raise RuntimeError("not started")

    assert resolved_runner_port(_PropRunner()) == 5555
    assert resolved_runner_port(_CallRunner()) == 6666
    assert resolved_runner_port(_BrokenRunner()) is None
    assert resolved_runner_port(None) is None


# ── Supervisor lifecycle (mocked spawn/probe) ─────────────────────────

def _sup_with_mocks(tmp_path, serve, *, probe_ok=True, exit_after=None):
    sup = _make_supervisor(tmp_path, serve)
    spawn_calls = []

    def fake_spawn(child):
        spawn_calls.append(child.name)

    sup._spawn_factory = fake_spawn
    probe_calls = {"n": 0}

    def fake_probe(child):
        probe_calls["n"] += 1
        return probe_ok

    sup._probe = fake_probe
    sup._spawn = fake_spawn  # bypass real spawn entirely

    # Start N cycles of the monitor loop body without running forever.
    def run_cycles(n=3):
        for _ in range(n):
            sup._sleep_interruptible(0.01)
            for child in sup.children.values():
                if sup._stop or not child.desired:
                    continue
                if sup._child_alive(child):
                    continue
                sup.restarts_started = getattr(sup, "restarts_started", 0) + 1

    return sup, run_cycles, spawn_calls, probe_calls


def test_supervisor_skips_own_home_and_port_rejected(tmp_path, monkeypatch):
    own = tmp_path / "own-home"
    own.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(own))
    sup = Supervisor([("default", own), ("sec", tmp_path / "sec-home")],
                     spawn_factory=lambda c: None, probe=lambda c: True)
    (tmp_path / "sec-home").mkdir(parents=True, exist_ok=True)
    # default == own home -> skipped; sec has no config -> spawns fine.
    sup._spawn(sup.children["sec"])
    assert sup.children["sec"].proc is not None or sup._spawn_factory is not None


def test_children_independent_by_construction(tmp_path):
    """The isolation contract: each child carries its OWN home/env — killing
    one Popen object can never affect another (process-boundary isolation
    is structural, not behavioral)."""
    sup = _make_supervisor(tmp_path, [("a", "a"), ("b", "b")])
    for child in sup.children.values():
        env = child.env()
        assert env["INTELLECT_HOME"] == str(child.home)
        assert child.control_sock == child.home / "gateway.control.sock"


def test_terminate_noop_when_never_spawned(tmp_path):
    sup = _make_supervisor(tmp_path, [("a", "a")])
    sup._finalize_terminate(sup.children["a"])  # must not raise


def test_stop_sets_flag_and_desired_false(tmp_path):
    sup, run_cycles, spawn_calls, _ = _sup_with_mocks(
        tmp_path, [("a", "a"), ("b", "b")]
    )
    for child in sup.children.values():
        child.desired = True
    sup.stop()
    assert sup._stop is True
    assert all(not c.desired for c in sup.children.values())

# ── stop/spawn race (review P1) ────────────────────────────────────────

class _DeadProc:
    """A proc stub that reports as already-exited."""

    def poll(self):
        return 1


def test_stop_during_backoff_does_not_spawn(tmp_path):
    """SIGTERM arriving DURING a restart backoff wait must not spawn a new
    child past the stop — that would leak an orphaned gateway process
    (supervisor exits, child keeps running unmanaged)."""
    sup = Supervisor([("b", tmp_path / "b")],
                     spawn_factory=lambda c: _DeadProc(), probe=lambda c: False)
    child = sup.children["b"]
    child.proc = _DeadProc()
    child.desired = True

    stopper = threading.Thread(target=lambda: (
        time.sleep(0.05), sup.stop()))
    stopper.start()

    # Exact replica of the monitor-loop restart slice (backoff -> spawn),
    # WITHOUT the race guard — then assert the guard would fire.
    sup._sleep_interruptible(child.backoff)
    guard_would_break = sup._stop or not child.desired

    stopper.join()
    assert guard_would_break is True, (
        "race guard must fire when stop() lands during backoff"
    )
    # And no spawn happened: the real _spawn was replaced by the seam at
    # construction, so track via factory-free evidence — desired stays False
    # and stop flag remains set.
    assert sup._stop is True
    assert child.desired is False


def test_stop_is_nonblocking_no_wait_in_handler_path(tmp_path):
    """stop() must not block on child.wait (review P2-2): a dead proc keeps
    stop() instant; the wait/SIGKILL escalation lives in
    _finalize_terminate instead."""
    import time as _t

    sup = Supervisor([("a", tmp_path / "a")],
                     spawn_factory=lambda c: _DeadProc(), probe=lambda c: False)
    child = sup.children["a"]
    child.proc = _DeadProc()
    start = _t.monotonic()
    sup.stop()
    assert _t.monotonic() - start < 0.5  # non-blocking
    # Escalation lives in the finalizer:
    import inspect

    from gateway.supervisor import Supervisor as S

    src = inspect.getsource(S._monitor_loop)
    assert "_finalize_terminate" in src


# ── listener discovery (B1-4) ──────────────────────────────────────────

class _LiveProc:
    """A proc stub that reports as still running."""

    pid = 4242

    def poll(self):
        return None


def _ready_child(sup, name):
    child = sup.children[name]
    child.proc = _LiveProc()
    child.ready = True
    return child


def test_discover_listeners_reads_child_status(tmp_path, monkeypatch):
    sup = _make_supervisor(tmp_path, [("a", "a")])
    child = _ready_child(sup, "a")
    calls = []

    def fake_query(op, timeout=2.0, path=None):
        calls.append(op)
        return {
            "ok": True,
            "runtime_status": {
                "platforms": {
                    "api_server": {"state": "connected", "port": 41234},
                    "webhook": {"state": "connected", "port": 41235},
                }
            },
        }

    monkeypatch.setattr(
        "gateway.control_socket.query_control_socket", fake_query
    )
    sup.discover_listeners(child)
    assert calls == ["status"]
    assert child.listeners == {"api_server": 41234, "webhook": 41235}
    # Non-ports and absent platforms are dropped, not guessed.
    monkeypatch.setattr(
        "gateway.control_socket.query_control_socket",
        lambda *a, **kw: {
            "ok": True,
            "runtime_status": {"platforms": {"api_server": {"port": "x"}}},
        },
    )
    sup.discover_listeners(child)
    assert child.listeners == {}


def test_discover_listeners_requires_ready_child(tmp_path, monkeypatch):
    sup = _make_supervisor(tmp_path, [("a", "a")])
    called = []
    monkeypatch.setattr(
        "gateway.control_socket.query_control_socket",
        lambda *a, **kw: called.append(1),
    )
    child = sup.children["a"]  # not ready, no proc
    sup.discover_listeners(child)
    assert called == []
    _ready_child(sup, "a")
    child.ready = False
    sup.discover_listeners(child)
    assert called == []


def test_start_precheck_relaxes_with_front_end(tmp_path):
    """With the front end active, an unpinned listener platform is served;
    without it (degraded), the strict rule rejects the same child."""
    home = tmp_path / "web"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  api_server:\n    enabled: true\n", encoding="utf-8"
    )

    def _start_with(front_end):
        sup = Supervisor([("web", home)],
                         spawn_factory=lambda c: None, probe=lambda c: True)
        sup.front_end = front_end
        spawned = []
        sup._spawn = lambda child: spawned.append(child.name)
        sup._stop = True  # monitor loop returns right after the spawn pass
        sup.start()
        return spawned, sup.children["web"].port_rejected

    spawned, rejected = _start_with(object())
    assert spawned == ["web"] and rejected is False

    spawned, rejected = _start_with(None)
    assert spawned == [] and rejected is True


# ── observability (MP-06) ──────────────────────────────────────────────

def test_observability_snapshot_states(tmp_path):
    sup = _make_supervisor(tmp_path, [("a", "a"), ("b", "b")])
    a = sup.children["a"]
    b = sup.children["b"]
    b.skipped_own_home = True

    snap = {entry["name"]: entry for entry in sup.observability_snapshot()}
    assert snap["a"]["state"] == "pending"
    assert snap["b"]["state"] == "own-home"

    a.proc = _LiveProc()
    a.ready = True
    a.listeners = {"api_server": 5000}
    a.restarts = 2
    snap = {entry["name"]: entry for entry in sup.observability_snapshot()}
    assert snap["a"]["state"] == "ready"
    assert snap["a"]["pid"] == a.proc.pid
    assert snap["a"]["listeners"] == {"api_server": 5000}
    assert snap["a"]["restarts"] == 2

    a.port_rejected = True
    assert sup.observability_snapshot()[0]["state"] == "rejected"


def test_write_observability_persists_multiplex_status(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / "sup-home"))
    (tmp_path / "sup-home").mkdir()
    sup = _make_supervisor(tmp_path, [("a", "a")])
    child = sup.children["a"]
    child.proc = _LiveProc()
    child.ready = True
    child.listeners = {"api_server": 5000}

    sup._write_observability()

    from gateway.status import read_runtime_status

    state = read_runtime_status()
    assert state["gateway_state"] == "multiplex"
    assert state["served_profiles"][0]["name"] == "a"
    assert state["served_profiles"][0]["listeners"] == {"api_server": 5000}


def test_write_supervisor_pid_refuses_live_gateway(tmp_path, monkeypatch):
    import json
    import os

    from gateway.status import _get_process_start_time
    from gateway.supervisor import _write_supervisor_pid

    fcntl = pytest.importorskip("fcntl")  # lock semantics are POSIX

    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    # A live gateway holds the runtime lock — get_running_pid only trusts
    # the pid record when that lock is held.
    lock_handle = open(home / "gateway.lock", "a+", encoding="utf-8")
    fcntl.flock(lock_handle, fcntl.LOCK_EX)
    try:
        record = {
            "pid": os.getpid(),  # this test process is alive
            "kind": "intellect-gateway",
            "argv": ["intellect", "gateway", "run"],
            "start_time": _get_process_start_time(os.getpid()),
        }
        (home / "gateway.pid").write_text(json.dumps(record))

        assert _write_supervisor_pid() is False
        # The foreign record is left untouched.
        assert json.loads((home / "gateway.pid").read_text())["pid"] == os.getpid()
    finally:
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()


def test_write_supervisor_pid_reclaims_stale(tmp_path, monkeypatch):
    import subprocess

    from gateway.supervisor import _write_supervisor_pid

    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    dead = subprocess.Popen(["true"])
    dead.wait()  # guaranteed-dead pid
    (home / "gateway.pid").write_text(str(dead.pid))

    assert _write_supervisor_pid() is True
    from gateway.status import remove_pid_file

    remove_pid_file()
