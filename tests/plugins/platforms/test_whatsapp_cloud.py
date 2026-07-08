"""Tests for WhatsApp Cloud API adapter (HP-403)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestWhatsAppCloudAdapter:
    """Unit tests for WhatsAppCloudAdapter."""

    @pytest.fixture
    def adapter(self):
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        return WhatsAppCloudAdapter()

    def test_not_ready_without_credentials(self, adapter):
        assert not adapter.is_ready

    def test_ready_with_credentials(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_CLOUD_TOKEN", "test-token")
        monkeypatch.setenv("WHATSAPP_CLOUD_PHONE_ID", "12345")
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        a = WhatsAppCloudAdapter()
        assert a.is_ready

    def test_send_message_not_configured(self, adapter):
        async def _run():
            return await adapter.send_message("123", "hello")
        import asyncio
        result = asyncio.run(_run())
        assert "error" in result

    def test_verify_webhook_success(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_CLOUD_VERIFY_TOKEN", "my-token")
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        a = WhatsAppCloudAdapter()
        ok, challenge, err = a.verify_webhook("subscribe", "my-token", "challenge123")
        assert ok
        assert challenge == "challenge123"
        assert err is None

    def test_verify_webhook_token_mismatch(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_CLOUD_VERIFY_TOKEN", "my-token")
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        a = WhatsAppCloudAdapter()
        ok, _, err = a.verify_webhook("subscribe", "wrong", "ch")
        assert not ok
        assert "token mismatch" in err

    def test_verify_webhook_not_configured(self):
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        a = WhatsAppCloudAdapter()
        ok, _, err = a.verify_webhook("subscribe", "t", "c")
        assert not ok
        assert "not configured" in err

    def test_parse_webhook_text_message(self):
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        a = WhatsAppCloudAdapter()
        body = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "8613800138000",
                            "id": "msg-001",
                            "timestamp": "1700000000",
                            "type": "text",
                            "text": {"body": "Hello world"},
                        }],
                        "contacts": [{"profile": {"name": "Test User"}}],
                        "metadata": {"display_phone_number": "8613800138999"},
                    }
                }]
            }]
        }
        messages = a.parse_webhook_event(body)
        assert len(messages) == 1
        assert messages[0]["from"] == "8613800138000"
        assert messages[0]["text"] == "Hello world"
        assert messages[0]["profile_name"] == "Test User"

    def test_parse_webhook_empty(self):
        from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
        a = WhatsAppCloudAdapter()
        assert a.parse_webhook_event({}) == []

    def test_acquire_release_lock(self):
        from plugins.platforms.whatsapp_cloud.adapter import (
            acquire_scoped_lock, release_scoped_lock,
        )
        assert acquire_scoped_lock(timeout=1)
        release_scoped_lock()
        # Second acquire should succeed after release
        assert acquire_scoped_lock(timeout=1)
        release_scoped_lock()
