"""Tests for gateway.systemd_notify (best-effort sd_notify client)."""

import socket

from gateway import systemd_notify


def _recv_notify(server_sock, results):
    try:
        data, _ = server_sock.recvfrom(4096)
        results.append(data.decode("utf-8"))
    except OSError as exc:
        results.append(f"<error: {exc}>")


def test_notify_no_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert systemd_notify.notify_socket_path() is None
    assert not systemd_notify.is_managed()
    assert not systemd_notify.sd_notify("READY=1")


def test_notify_ready_sends_datagram(tmp_path, monkeypatch):
    path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(path))
    server.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", str(path))

    assert systemd_notify.is_managed()
    assert systemd_notify.notify_ready()
    assert server.recvfrom(4096)[0] == b"READY=1"

    assert systemd_notify.notify_stopping()
    assert server.recvfrom(4096)[0] == b"STOPPING=1"

    assert systemd_notify.notify_watchdog()
    assert server.recvfrom(4096)[0] == b"WATCHDOG=1"
    server.close()


def test_notify_abstract_socket(monkeypatch):
    import sys

    if sys.platform != "linux":
        import pytest

        pytest.skip("abstract AF_UNIX sockets are Linux-only")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind("\0intellect-test-notify")
    server.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", "@intellect-test-notify")

    assert systemd_notify.notify_ready()
    assert server.recvfrom(4096)[0] == b"READY=1"
    server.close()


def test_notify_dead_socket_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "missing.sock"))
    assert not systemd_notify.sd_notify("READY=1")


def test_watchdog_usec(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert systemd_notify.watchdog_usec() == 0
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    assert systemd_notify.watchdog_usec() == 30_000_000
    monkeypatch.setenv("WATCHDOG_USEC", "bogus")
    assert systemd_notify.watchdog_usec() == 0


def test_unsupported_socket_form(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "tcp:127.0.0.1:1")
    assert not systemd_notify.sd_notify("READY=1")
