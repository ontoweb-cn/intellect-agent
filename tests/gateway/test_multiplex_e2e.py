"""门-3 E2E: real-process multiplex isolation (roadmap gate-3 clause).

Env-gated real integration test — run explicitly with (the run_tests.sh
wrapper scrubs the environment via env -i, so invoke pytest directly):

    source .venv/bin/activate
    INTELLECT_MULTIPLEX_E2E=1 python -m pytest \\
        tests/gateway/test_multiplex_e2e.py -q

Builds a temp HOME with three profiles (default pinned to the front-end
port, alpha/beta unpinned), starts the REAL supervisor subprocess (front
end + one gateway child per profile), and asserts the gate-3 clauses:

1. isolation — each profile's API key opens ONLY its own prefix (cross
   profile key → 401 from the target child's own auth layer);
2. kill -9 one child — the others keep serving and the killed child
   restarts with a NEW pid under the supervisor's backoff;
3. SIGTERM the supervisor — the front end and every child exit.

Follows scripts/verify/spike_supervisor.py. Loopback only, no external
services, no fixed ports (the front-end port is a pre-probed free port).
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("INTELLECT_MULTIPLEX_E2E") != "1",
        reason="real-process multiplex E2E — set INTELLECT_MULTIPLEX_E2E=1",
    ),
    pytest.mark.timeout(180),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(url: str, key: str = None, timeout: float = 5.0):
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body
    except (urllib.error.URLError, ConnectionError, OSError):
        return None, None


def _poll(fn, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    return None


def _status(url: str):
    _, payload = _http(url, timeout=3.0)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    return {p["name"]: p for p in payload.get("profiles", [])}


def test_multiplex_isolation_kill_and_shutdown(tmp_path):
    key_default, key_alpha, key_beta = (
        "e2e-key-default-0123456789abcdef",
        "e2e-key-alpha-0123456789abcdef",
        "e2e-key-beta-0123456789abcdef",
    )
    # SHORT home path — child control sockets are AF_UNIX (macOS cap ~104
    # bytes); a pytest-basetemp-depth path silently exceeds it and the
    # children can never become ready.
    home_root = Path("/tmp") / f"intellect-mx-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    shutil.rmtree(home_root, ignore_errors=True)
    default_home = home_root / ".intellect"
    profiles_root = default_home / "profiles"
    sup_home = profiles_root / "sup"
    default_home.mkdir(parents=True)
    sup_home.mkdir(parents=True)

    front_port = _free_port()
    (default_home / "config.yaml").write_text(
        "platforms:\n"
        "  api_server:\n"
        "    enabled: true\n"
        "    extra:\n"
        "      host: 127.0.0.1\n"
        f"      port: {front_port}\n"
        f"      key: {key_default}\n",
        encoding="utf-8",
    )
    for name, key in (("alpha", key_alpha), ("beta", key_beta)):
        pdir = profiles_root / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "config.yaml").write_text(
            "platforms:\n"
            "  api_server:\n"
            "    enabled: true\n"
            "    extra:\n"
            f"      key: {key}\n",
            encoding="utf-8",
        )

    env = dict(os.environ)
    env["HOME"] = str(home_root)
    env["INTELLECT_HOME"] = str(sup_home)
    log = open(home_root / "supervisor.log", "w+")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from gateway.supervisor import run_supervisor; "
            "sys.exit(run_supervisor())",
        ],
        cwd=str(REPO),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{front_port}"
    try:
        # ── startup: front end up, all three children ready ─────────────
        def _startup_check():
            snap = _status(f"{base}/multiplex/status")
            if not snap:
                return None
            for n in ("default", "alpha", "beta"):
                state = snap.get(n, {}).get("state")
                if state == "rejected":
                    raise AssertionError(
                        f"profile {n!r} rejected at startup — supervisor.log:\n"
                        + open(home_root / "supervisor.log").read()[-2000:]
                    )
            if all(
                snap.get(n, {}).get("state") == "ready"
                for n in ("default", "alpha", "beta")
            ):
                return snap
            return None

        ready = _poll(_startup_check, timeout=90)
        assert ready, (
            "children never became ready — supervisor.log:\n"
            + open(home_root / "supervisor.log").read()[-2000:]
        )
        pids = {n: ready[n]["pid"] for n in ("default", "alpha", "beta")}
        assert all(pids.values())

        # ── clause 1: key/prefix isolation ──────────────────────────────
        status, _ = _http(f"{base}/p/alpha/v1/models", key=key_alpha)
        assert status == 200, "alpha key must open alpha's own prefix"
        status, _ = _http(f"{base}/p/alpha/v1/models", key=key_beta)
        assert status == 401, "beta's key must NOT open alpha's prefix"
        status, _ = _http(f"{base}/p/beta/v1/models", key=key_beta)
        assert status == 200
        status, _ = _http(f"{base}/v1/models", key=key_default)
        assert status == 200, "default key must open the unprefixed route"
        status, _ = _http(f"{base}/v1/models", key=key_alpha)
        assert status == 401, "alpha's key must NOT open the default profile"

        # ── clause 2: kill -9 beta — alpha unaffected, beta restarts ────
        os.kill(pids["beta"], signal.SIGKILL)
        # alpha keeps serving through the disruption (poll a few rounds
        # spanning the supervisor's 2s monitor cadence + restart backoff).
        alpha_ok = _poll(
            lambda: _http(
                f"{base}/p/alpha/v1/models", key=key_alpha, timeout=3.0
            )[0]
            == 200,
            timeout=15,
        )
        assert alpha_ok, "alpha must keep serving while beta restarts"
        recovered = _poll(
            lambda: (
                _http(f"{base}/p/beta/v1/models", key=key_beta, timeout=3.0)[0]
                == 200
                and _status(f"{base}/multiplex/status")["beta"]["pid"]
                != pids["beta"]
            ),
            timeout=60,
        )
        assert recovered, (
            "beta never recovered with a new pid — supervisor.log:\n"
            + open(home_root / "supervisor.log").read()[-2000:]
        )
        new_beta_pid = _status(f"{base}/multiplex/status")["beta"]["pid"]

        # ── clause 3: SIGTERM supervisor — everything exits ─────────────
        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=30)
        assert exit_code == 0, f"supervisor exit code {exit_code}"
        deadline = time.time() + 10
        while time.time() < deadline:
            if _http(f"{base}/multiplex/status", timeout=2.0)[0] is None:
                break
            time.sleep(0.3)
        assert _http(f"{base}/multiplex/status", timeout=2.0)[0] is None, (
            "front end still answering after SIGTERM"
        )
        time.sleep(1.0)  # children get SIGTERM from stop(); then check
        pids["beta"] = new_beta_pid  # the restarted child must be gone too
        for name, pid in pids.items():
            try:
                os.kill(pid, 0)
                raise AssertionError(f"{name} child pid {pid} still alive")
            except ProcessLookupError:
                pass
    finally:
        # Graceful-first teardown: SIGKILLing the supervisor would orphan
        # its children (they live in their own process groups), and an
        # orphaned child keeps reconnecting/holding its home — poison for
        # the next run. SIGTERM lets the supervisor's own stop() sweep the
        # children; the pid-file sweep is the last-resort net.
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        log.close()
        for pidfile in home_root.rglob("gateway.pid"):
            try:
                pid = int(json.loads(pidfile.read_text())["pid"])
                os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError, KeyError):
                pass
        shutil.rmtree(home_root, ignore_errors=True)
