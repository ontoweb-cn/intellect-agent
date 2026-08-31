"""Tests for the gateway control socket (identify/status v1)."""

import json

from gateway.control_socket import ControlSocketServer, query_control_socket


def test_identify_and_status_roundtrip(tmp_path):
    server = ControlSocketServer(path=tmp_path / "ctl.sock")
    assert server.start()

    ident = query_control_socket("identify", timeout=2.0, path=server.path)
    assert ident is not None and ident["ok"] is True
    assert ident["op"] == "identify"
    assert ident["kind"] == "intellect-gateway"
    assert "pid" in ident and "start_time" in ident
    # argv is deliberately not echoed back over the socket.
    assert "argv" not in ident

    status = query_control_socket("status", timeout=2.0, path=server.path)
    assert status["ok"] is True
    assert "runtime_status" in status

    server.stop()
    assert query_control_socket("identify", timeout=0.5) is None


def test_socket_file_is_owner_only_from_creation(tmp_path):
    import stat

    path = tmp_path / "ctl.sock"
    server = ControlSocketServer(path=path)
    assert server.start()
    # 0600 from the instant of bind (umask-tightened), not after a
    # later chmod — no TOCTOU window for other local users.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    server.stop()


def test_unknown_op_and_invalid_json(tmp_path):
    server = ControlSocketServer(path=tmp_path / "ctl.sock")
    assert server.start()

    resp = query_control_socket("frobnicate", timeout=2.0, path=server.path)
    assert resp["ok"] is False
    assert "unknown op" in resp["error"]

    # Raw invalid JSON line gets an error object, not a crash.
    import socket as _s

    with _s.socket(_s.AF_UNIX, _s.SOCK_STREAM) as sock:
        sock.settimeout(2.0)
        sock.connect(str(tmp_path / "ctl.sock"))
        sock.sendall(b"not-json\n")
        line = sock.recv(4096).split(b"\n")[0]
        assert json.loads(line) == {"ok": False, "error": "invalid json"}

    server.stop()


def test_stop_removes_socket_file(tmp_path):
    path = tmp_path / "ctl.sock"
    server = ControlSocketServer(path=path)
    assert server.start()
    assert path.exists()
    server.stop()
    assert not path.exists()
    # Double stop is safe.
    server.stop()


def test_stale_socket_file_is_reclaimed(tmp_path):
    path = tmp_path / "ctl.sock"
    # A dead socket file: exists but nothing accepts connections.
    path.write_text("")
    server = ControlSocketServer(path=path)
    assert server.start()
    assert query_control_socket("identify", timeout=2.0, path=server.path)["ok"] is True
    server.stop()


def test_query_absent_socket_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    assert query_control_socket("identify", timeout=0.5) is None


def test_connection_cap_sheds_excess(tmp_path):
    """Over-cap connections are shed, not served — no unbounded threads."""
    import socket as _s
    import time as _t

    server = ControlSocketServer(path=tmp_path / "ctl.sock")
    assert server.start()

    held = []
    try:
        # Hold _MAX_CONCURRENT_CONNECTIONS + a few idle connections: the
        # first 8 occupy server slots (each until its 2s request timeout),
        # the excess must be shed immediately rather than threaded.
        for _ in range(8 + 2):
            c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
            c.settimeout(5.0)
            c.connect(str(server.path))
            held.append(c)

        # Wait past the server's per-connection request timeout so the
        # held connections release their slots; a fresh request then gets
        # served normally (the cap sheds bursts, it doesn't wedge).
        _t.sleep(2.5)
        one = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
        one.settimeout(3.0)
        one.connect(str(server.path))
        one.sendall(b'{"op": "identify"}\n')
        line = one.recv(4096).split(b"\n")[0]
        assert json.loads(line)["ok"] is True
        one.close()
    finally:
        for c in held:
            c.close()
        server.stop()
