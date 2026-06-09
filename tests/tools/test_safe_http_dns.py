"""Connect-time SSRF / DNS-rebinding tests for tools.safe_http."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from tools.safe_http import SSRFConnectBlocked, safe_http_transport
from tools.url_safety import is_safe_peer_ip


class TestIsSafePeerIp:
    def test_public_peer_allowed(self):
        assert is_safe_peer_ip("93.184.216.34") is True

    def test_loopback_peer_blocked(self):
        assert is_safe_peer_ip("127.0.0.1") is False

    def test_metadata_peer_blocked(self):
        assert is_safe_peer_ip("169.254.169.254") is False

    def test_ipv4_mapped_loopback_blocked(self):
        assert is_safe_peer_ip("::ffff:127.0.0.1") is False


class TestConnectTimeValidation:
    """Simulate DNS rebinding: pre-flight DNS may differ from actual peer IP."""

    class _FakeStream:
        def __init__(self, peer_ip: str):
            self._peer = (peer_ip, 443)

        def get_extra_info(self, info: str):
            if info == "server_addr":
                return self._peer
            return None

        def close(self) -> None:
            pass

    def test_blocks_private_peer_after_connect(self):
        from httpcore._backends.sync import SyncBackend

        def fake_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
            return TestConnectTimeValidation._FakeStream("127.0.0.1")

        transport = safe_http_transport()
        with patch.object(SyncBackend, "connect_tcp", fake_connect):
            client = httpx.Client(transport=transport)
            try:
                with pytest.raises((SSRFConnectBlocked, httpx.ConnectError, OSError)):
                    client.get("https://example.com/", timeout=2.0)
            finally:
                client.close()

    def test_allows_public_peer_after_connect(self):
        from httpcore._backends.sync import SyncBackend

        def fake_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
            return TestConnectTimeValidation._FakeStream("93.184.216.34")

        transport = safe_http_transport()
        with patch.object(SyncBackend, "connect_tcp", fake_connect):
            with patch("httpx.Client.send") as mock_send:
                mock_send.return_value = httpx.Response(200, request=httpx.Request("GET", "https://example.com/"))
                client = httpx.Client(transport=transport)
                try:
                    resp = client.get("https://example.com/", timeout=2.0)
                    assert resp.status_code == 200
                finally:
                    client.close()
