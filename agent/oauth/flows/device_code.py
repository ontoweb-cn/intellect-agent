"""RFC 8628 Device Authorization Grant (Device Code) flow.

Unified implementation consumed by:
- ``agent/oauth/login_flow.py`` — OAuthEngine routing
- ``intellect_cli/main.py`` — CLI ``intellect auth login``
- ``agent/members_oauth.py`` — member OAuth registration
- ``plugins/model-providers/*`` — provider-specific login

Usage::

    from agent.oauth.flows.device_code import DeviceCodeFlow

    flow = DeviceCodeFlow(
        client_id="...",
        device_endpoint="https://example.com/device",
        token_endpoint="https://example.com/token",
        scopes=["openid", "profile"],
    )
    result = flow.run()  # blocks until user authorizes or timeout
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# RFC 8628 §3.2: recommended intervals
_DEFAULT_POLL_INTERVAL = 5.0   # seconds between polls
_DEFAULT_EXPIRES_IN = 600      # 10 minutes
_MIN_POLL_INTERVAL = 2.0
_MAX_POLL_INTERVAL = 30.0


@dataclass
class DeviceCodeConfig:
    """Configuration for a Device Code flow."""

    client_id: str
    device_endpoint: str
    token_endpoint: str
    scopes: list[str] = field(default_factory=list)
    audience: str = ""
    client_secret: str = ""
    extra_params: dict[str, str] = field(default_factory=dict)
    poll_interval: float = _DEFAULT_POLL_INTERVAL
    expires_in: int = _DEFAULT_EXPIRES_IN
    open_browser: bool = True
    quiet: bool = False


@dataclass
class DeviceCodeResult:
    """Outcome of a device code flow."""

    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "bearer"
    expires_in: int = 0
    scope: str = ""
    id_token: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class DeviceCodeError(Exception):
    """Raised when the device code flow fails irrecoverably."""


class DeviceCodeExpired(DeviceCodeError):
    """The user did not authorize before the device code expired."""


class DeviceCodeDeclined(DeviceCodeError):
    """The user explicitly declined authorization (authorization_pending too long)."""


class DeviceCodeFlow:
    """Execute an RFC 8628 Device Authorization Grant.

    Call :meth:`run` to block until the user authorizes, the code expires,
    or an unrecoverable error occurs.
    """

    def __init__(self, config: DeviceCodeConfig) -> None:
        self._cfg = config
        self._user_code: str = ""
        self._device_code: str = ""
        self._verification_uri: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, *, timeout: float | None = None) -> DeviceCodeResult:
        """Execute the full device code flow, blocking until completion.

        Raises :class:`DeviceCodeExpired` if the user does not authorize
        before the code expires.  Raises :class:`DeviceCodeError` for
        protocol-level failures.
        """
        self._request_device_code()
        self._show_user_instructions()
        return self._poll_for_token(timeout=timeout)

    def request_only(self) -> tuple[str, str, str]:
        """Request a device code and return (user_code, verification_uri, device_code).

        The caller is responsible for displaying the URI + code to the user
        and calling :meth:`poll` separately.  Useful for WebUI or gateway
        integrations where the browser flow is handled out-of-band.
        """
        self._request_device_code()
        return self._user_code, self._verification_uri, self._device_code

    def poll(self, device_code: str | None = None) -> DeviceCodeResult:
        """Poll the token endpoint with *device_code* (uses stored code if None)."""
        if device_code is not None:
            self._device_code = device_code
        if not self._device_code:
            raise DeviceCodeError("No device_code to poll — call run() or request_only() first")
        return self._poll_for_token()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _request_device_code(self) -> None:
        """POST to the device endpoint to obtain a user_code + device_code."""
        payload: dict[str, str] = {
            "client_id": self._cfg.client_id,
            "scope": " ".join(self._cfg.scopes),
        }
        if self._cfg.audience:
            payload["audience"] = self._cfg.audience
        for k, v in self._cfg.extra_params.items():
            payload.setdefault(k, v)

        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(
            self._cfg.device_endpoint,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise DeviceCodeError(
                f"Device endpoint returned {exc.code}: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise DeviceCodeError(f"Cannot reach device endpoint: {exc}") from exc

        self._device_code = body.get("device_code", "")
        self._user_code = body.get("user_code", "")
        self._verification_uri = body.get(
            "verification_uri", body.get("verification_url", "")
        )
        if not self._device_code or not self._user_code:
            raise DeviceCodeError(
                f"Device endpoint did not return device_code/user_code: {list(body.keys())}"
            )

        # Respect server-suggested intervals (RFC 8628 §3.2)
        poll = body.get("interval")
        if isinstance(poll, (int, float)) and _MIN_POLL_INTERVAL <= poll <= _MAX_POLL_INTERVAL:
            self._cfg.poll_interval = float(poll)
        expires = body.get("expires_in")
        if isinstance(expires, (int, float)):
            self._cfg.expires_in = int(expires)

    def _show_user_instructions(self) -> None:
        """Display the verification URI and user code to the user."""
        if self._cfg.quiet:
            return
        print()
        print(f"Open {self._verification_uri} and enter this code:")
        print()
        print(f"  {self._user_code}")
        print()
        if self._cfg.open_browser:
            try:
                webbrowser.open(self._verification_uri)
                print("  (Browser opened automatically)")
            except Exception:
                pass

    def _poll_for_token(self, *, timeout: float | None = None) -> DeviceCodeResult:
        """Poll the token endpoint until success, expiry, or *timeout*."""
        deadline = time.time() + min(
            timeout or float("inf"),
            float(self._cfg.expires_in),
        )
        interval = max(self._cfg.poll_interval, _MIN_POLL_INTERVAL)

        while time.time() < deadline:
            time.sleep(interval)

            payload = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": self._device_code,
                "client_id": self._cfg.client_id,
            }
            if self._cfg.client_secret:
                payload["client_secret"] = self._cfg.client_secret

            data = urllib.parse.urlencode(payload).encode()
            req = urllib.request.Request(
                self._cfg.token_endpoint,
                data=data,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read())
                except Exception:
                    detail = {}
                error = detail.get("error", "")
                if error == "authorization_pending":
                    continue  # user hasn't acted yet — keep polling
                if error == "slow_down":
                    interval = min(interval + 5, _MAX_POLL_INTERVAL)
                    continue
                if error in ("expired_token", "access_denied"):
                    raise DeviceCodeExpired(
                        "Device code expired or was denied"
                    ) from exc
                raise DeviceCodeError(
                    f"Token endpoint returned {error}: {detail.get('error_description', '')}"
                ) from exc
            except OSError as exc:
                logger.debug("Token poll network error — retrying: %s", exc)
                continue

            # Success
            return DeviceCodeResult(
                access_token=body.get("access_token", ""),
                refresh_token=body.get("refresh_token", ""),
                token_type=body.get("token_type", "bearer"),
                expires_in=body.get("expires_in", 0),
                scope=body.get("scope", ""),
                id_token=body.get("id_token", ""),
                raw=body,
            )

        raise DeviceCodeExpired(
            f"Device code expired after {self._cfg.expires_in}s without authorization"
        )
