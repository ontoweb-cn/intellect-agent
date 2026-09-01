#!/usr/bin/env python3
"""MP-00 supervisor spike (P0-3): two-profile process isolation smoke.

Starts two gateway child processes, each with its own INTELLECT_HOME and
ZERO platforms (the gateway enters its degraded-but-running state, which is
exactly the supervisor child shape), then verifies:

1. Each child writes its pid file / control socket / heartbeat inside its
   OWN home (process-boundary isolation of the ①-④ state classes).
2. Each control socket identifies the correct profile/pid.
3. Killing child B does not affect child A.

Results are printed as JSON for the ADR (docs/plans/2026-08-31-adr-
multiplex-architecture.md §4). Requires `intellect gateway` to import
cleanly; no platform credentials are needed.

Usage: python scripts/verify/spike_supervisor.py [--wait 20]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

RESULTS: dict = {"children": {}, "kill_b_isolation": None, "errors": []}


def _wait_for(condition, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _start_child(name: str, home: Path, python: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["INTELLECT_HOME"] = str(home)
    env["INTELLECT_STATE_READ_POOL"] = env.get("INTELLECT_STATE_READ_POOL", "")
    return subprocess.Popen(
        [python, "-m", "gateway.run"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # own process group — kill B must not touch A
    )


def _identify(home: Path):
    try:
        from gateway.control_socket import query_control_socket

        return query_control_socket("identify", timeout=3.0, path=home / "gateway.control.sock")
    except Exception as exc:
        return {"error": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=20.0,
                    help="seconds to wait for each child to become ready")
    args = ap.parse_args()

    python = sys.executable
    tmp = Path(tempfile.mkdtemp(prefix="intellect-spike-"))
    homes = {name: tmp / name for name in ("profile-a", "profile-b")}
    children: dict[str, subprocess.Popen] = {}

    try:
        # ── Start two zero-platform children ────────────────────────────
        for name, home in homes.items():
            home.mkdir(parents=True, exist_ok=True)
            children[name] = _start_child(name, home, python)

        # ── Wait for readiness: control socket answers in BOTH homes ───
        def _ready(name: str) -> bool:
            ident = _identify(homes[name])
            return bool(ident and ident.get("ok"))

        def _diag(name: str) -> dict:
            home = homes[name]
            sock = home / "gateway.control.sock"
            pidf = home / "gateway.pid"
            return {
                "sock_exists": sock.exists(),
                "pid_file": pidf.exists(),
                "heartbeat": (home / "gateway.heartbeat").exists(),
                "log_tail": "",
            }

        def _log_tail(home: Path) -> str:
            try:
                log = home / "logs" / "gateway.log"
                return log.read_text(encoding="utf-8", errors="replace")[-2500:]
            except OSError:
                return ""

        for name in homes:
            ok = _wait_for(lambda n=name: _ready(n), args.wait)
            pid = children[name].pid
            alive = _pid_alive(pid)
            ident = _identify(homes[name])
            ident_pid = (ident or {}).get("pid")
            ident_matches = ident_pid == pid
            RESULTS["children"][name] = {
                "pid": pid,
                "alive": alive,
                "control_socket_ok": ok,
                "identify_pid": ident_pid,
                "pid_matches_own_home": ident_matches,
                "home_isolated": homes[name].exists()
                and (homes[name] / "gateway.pid").exists(),
                "diag": _diag(name),
                "log_tail": _log_tail(homes[name])[-300:],
            }

        # ── Kill B; A must be unaffected ────────────────────────────────
        pid_b = children["profile-b"].pid
        try:
            os.killpg(os.getpgid(pid_b), signal.SIGKILL)
        except OSError:
            children["profile-b"].kill()
        time.sleep(2.0)
        a_ident = _identify(homes["profile-a"])
        RESULTS["kill_b_isolation"] = {
            "b_dead": not _pid_alive(pid_b),
            "a_still_alive": _pid_alive(children["profile-a"].pid),
            "a_control_socket_ok": bool(a_ident and a_ident.get("ok")),
        }
    except Exception as exc:
        RESULTS["errors"].append(str(exc))
    finally:
        for proc in children.values():
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)

    print(json.dumps(RESULTS, indent=2))

    # Verdict
    kids = RESULTS["children"]
    iso = RESULTS.get("kill_b_isolation") or {}
    ok = (
        len(kids) == 2
        and all(k.get("control_socket_ok") and k.get("pid_matches_own_home")
                for k in kids.values())
        and iso.get("b_dead") and iso.get("a_still_alive")
    )
    print("SPIKE VERDICT:", "PASS" if ok else "FAIL", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
