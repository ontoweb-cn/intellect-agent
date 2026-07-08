"""WhatsApp Cloud API adapter — Meta Graph API integration (HP-403).

Uses the official WhatsApp Business Platform Cloud API:
  https://developers.facebook.com/docs/whatsapp/cloud-api

Requires:
  - ``WHATSAPP_CLOUD_TOKEN`` env var (Meta access token)
  - ``WHATSAPP_CLOUD_PHONE_ID`` env var (phone number ID from Meta dashboard)
  - ``WHATSAPP_CLOUD_VERIFY_TOKEN`` env var (webhook verification token)

Coexists with the web-bridge WhatsApp adapter (``plugins/platforms/whatsapp/``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
CLOUD_API_VERSION = "v21.0"

# Per-adapter send lock to serialize outbound Cloud API calls.
_send_lock = threading.Lock()


def acquire_scoped_lock(timeout: float = 30.0) -> bool:
    """Acquire the per-adapter send lock.  Returns True on success."""
    return _send_lock.acquire(timeout=timeout)


def release_scoped_lock() -> None:
    """Release the per-adapter send lock."""
    try:
        _send_lock.release()
    except RuntimeError:
        pass


class WhatsAppCloudAdapter:
    """WhatsApp Cloud API platform adapter.

    Sends and receives messages via the Meta Graph API.
    """

    name = "whatsapp_cloud"
    display_name = "WhatsApp Cloud API"

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._token = os.getenv("WHATSAPP_CLOUD_TOKEN", "")
        self._phone_id = os.getenv("WHATSAPP_CLOUD_PHONE_ID", "")
        self._verify_token = os.getenv("WHATSAPP_CLOUD_VERIFY_TOKEN", "")
        self._session: Optional[aiohttp.ClientSession] = None

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._token:
            logger.warning("whatsapp_cloud: WHATSAPP_CLOUD_TOKEN not set")
        if not self._phone_id:
            logger.warning("whatsapp_cloud: WHATSAPP_CLOUD_PHONE_ID not set")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def is_ready(self) -> bool:
        return bool(self._token and self._phone_id)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
            )
        return self._session

    # ── send ────────────────────────────────────────────────────────────

    async def send_message(
        self,
        to: str,
        text: str,
        *,
        preview_url: bool = False,
    ) -> dict[str, Any]:
        """Send a text message via Cloud API.

        ``to`` is the recipient WhatsApp ID (e.g. ``"8613800138000"``).
        Returns the API response dict.
        """
        if not self.is_ready:
            return {"error": "WhatsApp Cloud API not configured"}

        url = f"{GRAPH_API_BASE}/{self._phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": preview_url, "body": text},
        }

        try:
            session = self._get_session()
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    logger.error(
                        "whatsapp_cloud: send failed (HTTP %s): %s",
                        resp.status, data,
                    )
                    return {"error": data.get("error", {}).get("message", str(data))}
                logger.debug("whatsapp_cloud: message sent to %s", to)
                return {"ok": True, "message_id": data.get("messages", [{}])[0].get("id", "")}
        except aiohttp.ClientError as exc:
            logger.error("whatsapp_cloud: send error: %s", exc)
            return {"error": str(exc)}

    async def send_message_safe(self, to: str, text: str, **kwargs) -> dict[str, Any]:
        """Send with scoped lock to serialize outbound calls."""
        if not acquire_scoped_lock(timeout=30):
            return {"error": "send lock timeout"}
        try:
            return await self.send_message(to, text, **kwargs)
        finally:
            release_scoped_lock()

    # ── webhook ──────────────────────────────────────────────────────────

    def verify_webhook(
        self, mode: str, token: str, challenge: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Verify a webhook subscription request from Meta.

        Returns ``(verified, challenge_response, error)``.
        """
        if not self._verify_token:
            return False, None, "WHATSAPP_CLOUD_VERIFY_TOKEN not configured"
        if mode != "subscribe":
            return False, None, f"unexpected mode: {mode}"
        if token != self._verify_token:
            return False, None, "token mismatch"
        return True, challenge, None

    def parse_webhook_event(
        self, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Parse incoming webhook events into normalized message dicts.

        Returns a list of ``{from, to, text, timestamp, message_id}`` dicts.
        """
        messages: list[dict[str, Any]] = []
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Only process incoming messages (not status updates)
                msg_objs = value.get("messages", [])
                contact = value.get("contacts", [{}])[0] if value.get("contacts") else {}
                metadata = value.get("metadata", {})
                for msg in msg_objs:
                    if msg.get("type") == "text":
                        messages.append({
                            "from": msg.get("from", ""),
                            "to": metadata.get("display_phone_number", ""),
                            "text": msg.get("text", {}).get("body", ""),
                            "timestamp": int(msg.get("timestamp", 0)),
                            "message_id": msg.get("id", ""),
                            "profile_name": contact.get("profile", {}).get("name", ""),
                        })
        return messages

    # ── media (stub) ─────────────────────────────────────────────────────

    async def download_media(self, media_id: str) -> Optional[bytes]:
        """Download media attachment by ID.  Stub — extend as needed."""
        try:
            # Step 1: get media URL (use auth session)
            session = self._get_session()
            async with session.get(f"{GRAPH_API_BASE}/{media_id}") as resp:
                meta = await resp.json()
            media_url = meta.get("url", "")
            if not media_url:
                return None
            # Step 2: download from CDN WITHOUT auth headers
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as dl:
                async with dl.get(media_url) as resp:
                    return await resp.read()
        except Exception as exc:
            logger.debug("whatsapp_cloud: media download failed: %s", exc)
            return None
