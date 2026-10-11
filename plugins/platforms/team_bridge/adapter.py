"""Intellect Team bridge platform adapter (Intellect plugin).

Remote-worker bridge for an upstream Intellect Team deployment. The
adapter polls the upstream gateway's kanban HTTP API for ``ready`` tasks
assigned to this worker, claims them atomically, runs each task as a
local agent turn through the standard gateway message pipeline, and
reports the agent's final reply back via the task ``complete`` endpoint.

Everything is outbound HTTPS — no inbound ports. Auth uses an upstream
member token (``imt_...``) as a Bearer credential.

This adapter ships as an Intellect platform plugin under
``plugins/platforms/team_bridge/``. The plugin loader scans the directory
at startup, calls :func:`register`, and the platform becomes available to
``gateway/run.py`` through the registry — no edits to core files required.

Configuration in config.yaml::

    platforms:
      team_bridge:
        enabled: true
        extra:
          server_url: "https://ai.example.edu/intellect"
          api_token: "imt_..."           # upstream member token
          board: "webui"
          worker_id: "intranet-w1"       # must match the task assignee
          task_signing_key: "..."        # optional HMAC allow-list for producers
          approval_mode: "deny"          # or "approve" (trust the producer)

Environment variables (env wins over config.yaml ``extra``):

    TEAM_BRIDGE_SERVER_URL      Upstream gateway base URL (required)
    TEAM_BRIDGE_API_TOKEN       Upstream member token, Bearer (required)
    TEAM_BRIDGE_BOARD           Board slug (default: webui)
    TEAM_BRIDGE_WORKER_ID       Assignee this worker pulls for (default: team-bridge)
    TEAM_BRIDGE_POLL_SECONDS    Ready-queue poll interval (default: 5)
    TEAM_BRIDGE_CLAIM_TTL_SECONDS  Claim TTL, heartbeated at ttl/3 (default: 300)
    TEAM_BRIDGE_MAX_CONCURRENT  Max in-flight claims (default: 2)
    TEAM_BRIDGE_TASK_SIGNING_KEY   HMAC-SHA256 key; when set only tasks whose
                                body ends with a valid ``sig: <hex>`` line are
                                claimed (computed over
                                ``"{session_id}|{title}|{body-without-sig}"``)
    TEAM_BRIDGE_APPROVAL_MODE   deny (default) | approve — pre-approve
                                dangerous commands in bridged sessions
    TEAM_BRIDGE_ALLOWED_USERS   Gateway authorization allowlist (worker ids)
    TEAM_BRIDGE_ALLOW_ALL_USERS Dev-only escape from the allowlist

Headless semantics: this platform has no human on the other end. The
interactive callbacks the gateway fires mid-turn are overridden so they
never leak through :meth:`send` (which completes a task):

- ``send_clarify`` auto-resolves with a "proceed on best judgment" answer.
- ``send_exec_approval`` swallows the prompt (the dangerous command has
  already been rejected at the tool-result layer; the agent routes around
  it). With ``approval_mode=approve`` bridged sessions are pre-approved
  via the session-YOLO bypass instead.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import socket
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
)

logger = logging.getLogger(__name__)


class _FatalBridgeError(Exception):
    """Unrecoverable upstream error (e.g. 401) — stop the pull loop."""


DEFAULT_BOARD = "webui"
DEFAULT_WORKER_ID = "team-bridge"
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_CLAIM_TTL_SECONDS = 300
DEFAULT_MAX_CONCURRENT = 2
MAX_MESSAGE_LENGTH = 60000  # upstream gateway normalizes task text at 64 KiB
POLL_BACKOFF = [2, 5, 10, 30, 60]
_SIG_LINE_PREFIX = "sig: "
_UNSIGNED_WARN_REPEAT_SECONDS = 300.0
_FAILED_SLOT_TTL_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Task signature (shared contract with the upstream task producer)
# ---------------------------------------------------------------------------


def compute_task_signature(signing_key: str, session_id: str, title: str, body: str) -> str:
    """HMAC-SHA256 over ``"{session_id}|{title}|{len(body)}|{body}"`` — hex digest.

    The producer (e.g. the upstream WebUI BFF) appends the digest to the
    task body as a final ``sig: <hex>`` line; this adapter strips that line
    and recomputes over the remainder, so the signature never covers itself.
    The body length is bound so a message whose own last line happens to
    look like a ``sig:`` line (stripped by :func:`split_signature_line`)
    fails length verification instead of colliding.
    """
    payload = f"{session_id or ''}|{title or ''}|{len(body or '')}|{body or ''}"
    return hmac.new(
        signing_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def split_signature_line(body: str) -> Tuple[str, Optional[str]]:
    """Split a trailing ``sig: <hex>`` line off a task body.

    Returns ``(body_without_sig, sig_or_None)``. Blank trailing lines are
    ignored; the sig line is only recognized as the last non-blank line.
    """
    lines = body.rstrip("\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        last = lines[-1].strip()
        if last.startswith(_SIG_LINE_PREFIX):
            return "\n".join(lines[:-1]).rstrip("\n"), last[len(_SIG_LINE_PREFIX):].strip()
    return body, None


def verify_task_signature(
    signing_key: str, session_id: str, title: str, body: str
) -> bool:
    """Check a task's trailing ``sig:`` line against the signing key.

    An empty ``signing_key`` disables verification (unsigned deployments
    accept every task; a warning is logged at connect time).
    """
    if not signing_key:
        return True
    core, sig = split_signature_line(body)
    if not sig:
        return False
    expected = compute_task_signature(signing_key, session_id, title, core)
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Plugin pre-flight hooks
# ---------------------------------------------------------------------------


def check_requirements() -> bool:
    """Minimal env-only pre-flight for the registry's auto-enable pass."""
    if not HTTPX_AVAILABLE:
        return False
    server = os.getenv("TEAM_BRIDGE_SERVER_URL", "").strip()
    token = os.getenv("TEAM_BRIDGE_API_TOKEN", "").strip()
    return bool(server and token)


def validate_config(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    server = extra.get("server_url") or os.getenv("TEAM_BRIDGE_SERVER_URL", "")
    token = extra.get("api_token") or os.getenv("TEAM_BRIDGE_API_TOKEN", "")
    return bool(server and token)


def is_connected(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    server = extra.get("server_url") or os.getenv("TEAM_BRIDGE_SERVER_URL", "")
    token = extra.get("api_token") or os.getenv("TEAM_BRIDGE_API_TOKEN", "")
    return bool(server and token)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class TeamBridgeAdapter(BasePlatformAdapter):
    """Pull-loop bridge between an upstream Intellect Team kanban board
    and the local gateway agent pipeline."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        platform = Platform("team_bridge")
        super().__init__(config=config, platform=platform)

        extra = config.extra or {}

        def _cfg(key: str, env: str, default: str = "") -> str:
            # env wins over config.yaml extra (same precedence as ntfy).
            value = os.getenv(env, "")
            if not value.strip():
                value = extra.get(key)
            return str(value or "").strip()

        self._server: str = _cfg("server_url", "TEAM_BRIDGE_SERVER_URL").rstrip("/")
        self._token: str = _cfg("api_token", "TEAM_BRIDGE_API_TOKEN")
        self._board: str = _cfg("board", "TEAM_BRIDGE_BOARD") or DEFAULT_BOARD
        self._worker_id: str = (
            _cfg("worker_id", "TEAM_BRIDGE_WORKER_ID") or DEFAULT_WORKER_ID
        )
        self._signing_key: str = _cfg("task_signing_key", "TEAM_BRIDGE_TASK_SIGNING_KEY")
        self._approval_mode: str = _cfg(
            "approval_mode", "TEAM_BRIDGE_APPROVAL_MODE"
        ).lower() or "deny"

        try:
            self._poll_seconds = max(1.0, float(_cfg(
                "poll_seconds", "TEAM_BRIDGE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))))
        except ValueError:
            self._poll_seconds = DEFAULT_POLL_SECONDS
        try:
            self._claim_ttl = max(30, int(_cfg(
                "claim_ttl_seconds", "TEAM_BRIDGE_CLAIM_TTL_SECONDS",
                str(DEFAULT_CLAIM_TTL_SECONDS))))
        except ValueError:
            self._claim_ttl = DEFAULT_CLAIM_TTL_SECONDS
        try:
            self._max_concurrent = max(1, int(_cfg(
                "max_concurrent", "TEAM_BRIDGE_MAX_CONCURRENT",
                str(DEFAULT_MAX_CONCURRENT))))
        except ValueError:
            self._max_concurrent = DEFAULT_MAX_CONCURRENT

        self._http_client: Optional["httpx.AsyncClient"] = None
        self._pull_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # chat_id -> {"task_id", "claimed_at", "failed", "failed_at"}
        # claimed_at doubles as "last successful heartbeat" — refreshed by
        # the heartbeat loop, consumed by the 3x-TTL watchdog.
        # chat_id is the upstream session_id for chat-forwarded tasks (so
        # multi-turn context keeps a stable local session) or the task id
        # for plain kanban tasks. Keyed by chat_id because the gateway's
        # reply path hands us back the source chat_id.
        self._inflight: Dict[str, Dict[str, Any]] = {}

        # task_id -> last warn time, to quiet repeated unsigned-task logs
        self._unsigned_warned: Dict[str, float] = {}

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self) -> bool:
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx not installed. Run: pip install httpx", self.name)
            return False
        if not self._server or not self._token:
            logger.warning(
                "[%s] TEAM_BRIDGE_SERVER_URL / TEAM_BRIDGE_API_TOKEN not configured",
                self.name,
            )
            return False
        if not self._signing_key:
            logger.warning(
                "[%s] TEAM_BRIDGE_TASK_SIGNING_KEY unset — UNSIGNED tasks on board "
                "'%s' will be accepted. Set the key to restrict execution to "
                "signed producers.", self.name, self._board,
            )

        try:
            from plugins.http_deps import require_httpx

            _http = require_httpx(httpx, plugin="team_bridge")
            self._http_client = _http.AsyncClient(timeout=30.0)
        except Exception as e:
            logger.error("[%s] Failed to initialize HTTP client: %s", self.name, e)
            return False

        if not self._acquire_platform_lock(
            "team-bridge-worker",
            f"{self._server}|{self._board}|{self._worker_id}",
            "Intellect Team bridge worker identity",
        ):
            await self._http_client.aclose()
            self._http_client = None
            return False

        self._pull_task = asyncio.create_task(self._pull_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._mark_connected()
        logger.info(
            "[%s] Connected — polling %s board '%s' as worker '%s' "
            "(max_concurrent=%d, claim_ttl=%ds)",
            self.name, self._server, self._board, self._worker_id,
            self._max_concurrent, self._claim_ttl,
        )
        return True

    async def disconnect(self) -> None:
        self._running = False
        self._mark_disconnected()

        for task in (self._pull_task, self._heartbeat_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._pull_task = None
        self._heartbeat_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._inflight.clear()
        self._unsigned_warned.clear()
        self._release_platform_lock()
        logger.info("[%s] Disconnected", self.name)

    # -- Pull loop -----------------------------------------------------------

    async def _pull_loop(self) -> None:
        backoff_idx = 0
        while self._running:
            claimed_any = False
            try:
                claimed_any = await self._poll_once()
            except asyncio.CancelledError:
                return
            except _FatalBridgeError:
                self._running = False
                return
            except Exception as e:
                if not self._running:
                    return
                logger.warning("[%s] Poll error: %s", self.name, e)

            if not self._running:
                return

            if claimed_any:
                backoff_idx = 0
                continue  # queue may hold more work — poll again immediately

            delay = POLL_BACKOFF[min(backoff_idx, len(POLL_BACKOFF) - 1)]
            await asyncio.sleep(delay)
            backoff_idx += 1

    async def _poll_once(self) -> bool:
        """One ready-queue poll; returns True if a task was claimed."""
        assert self._http_client is not None
        params = {
            "board": self._board,
            "status": "ready",
            "assignee": self._worker_id,
            "order_by": "priority",
            "limit": 20,
        }
        resp = await self._http_client.get(
            f"{self._server}/api/kanban/tasks",
            params=params,
            headers=self._auth_headers(),
        )
        if resp.status_code == 401:
            self._set_fatal_error(
                "team_bridge_unauthorized",
                "Upstream gateway rejected auth (401). Check TEAM_BRIDGE_API_TOKEN.",
                retryable=False,
            )
            raise _FatalBridgeError("401 Unauthorized")
        resp.raise_for_status()

        tasks = resp.json() or []
        for task in tasks:
            if len(self._inflight) >= self._max_concurrent:
                return False
            if await self._try_claim(task):
                return True
        return False

    async def _try_claim(self, task: Dict[str, Any]) -> bool:
        """Verify, claim (CAS), and dispatch one ready task."""
        assert self._http_client is not None
        task_id = str(task.get("id") or "")
        if not task_id:
            return False
        if not self._task_allowed(task):
            return False

        claimer = f"{self._worker_id}@{socket.gethostname()}"
        resp = await self._http_client.post(
            f"{self._server}/api/kanban/tasks/{task_id}/claim",
            json={"claimer": claimer, "ttl_seconds": self._claim_ttl},
            headers=self._auth_headers(),
        )
        if resp.status_code == 409:
            return False  # another claimant won the race
        if resp.status_code == 401:
            self._set_fatal_error(
                "team_bridge_unauthorized",
                "Upstream gateway rejected auth (401). Check TEAM_BRIDGE_API_TOKEN.",
                retryable=False,
            )
            raise _FatalBridgeError("401 Unauthorized")
        if resp.status_code >= 300:
            logger.warning(
                "[%s] Claim %s failed HTTP %d: %s",
                self.name, task_id, resp.status_code, resp.text[:200],
            )
            return False

        claimed = resp.json().get("task") or task
        return await self._dispatch_task(claimed)

    def _task_allowed(self, task: Dict[str, Any]) -> bool:
        """Signature gate: unsigned producers are only allowed when no
        signing key is configured."""
        task_id = str(task.get("id") or "")
        body = str(task.get("body") or "")
        title = str(task.get("title") or "")
        session_id = str(task.get("session_id") or "")
        if verify_task_signature(self._signing_key, session_id, title, body):
            return True
        now = time.monotonic()
        last = self._unsigned_warned.get(task_id, 0.0)
        if now - last >= _UNSIGNED_WARN_REPEAT_SECONDS:
            self._unsigned_warned[task_id] = now
            logger.warning(
                "[%s] Skipping task %s — missing/invalid task signature "
                "(TEAM_BRIDGE_TASK_SIGNING_KEY is set)", self.name, task_id,
            )
        return False

    async def _dispatch_task(self, task: Dict[str, Any]) -> bool:
        """Register the claim and hand the task to the gateway pipeline."""
        task_id = str(task.get("id") or "")
        session_id = str(task.get("session_id") or "").strip()
        title = str(task.get("title") or "")
        body = str(task.get("body") or "")

        # Chat-forwarded tasks carry the upstream session id; reusing it as
        # the local chat id keeps multi-turn context in one local session.
        chat_id = session_id or task_id

        core_body, _sig = split_signature_line(body)
        self._inflight[chat_id] = {
            "task_id": task_id,
            "claimed_at": time.monotonic(),
            "failed": False,
            "failed_at": 0.0,
        }

        if self._approval_mode == "approve":
            self._preapprove_session(chat_id)

        text = (
            f"intellect-team kanban task {task_id} (board {self._board}):\n\n"
            f"# {title}\n\n{core_body}"
        )
        source = self.build_source(
            chat_id=chat_id,
            chat_name=f"team_bridge:{task_id}",
            chat_type="dm",
            user_id=self._worker_id,
            user_name=self._worker_id,
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=task_id,
            raw_message=task,
            timestamp=datetime.now(),
        )
        logger.info(
            "[%s] Claimed task %s (chat %s, %d in flight)",
            self.name, task_id, chat_id, len(self._inflight),
        )
        # handle_message returns quickly (the gateway spawns the agent turn
        # in the background); a dispatch failure means the pipeline refused
        # the event — block the task so the board settles it.
        try:
            await self.handle_message(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._fail_claim(chat_id, f"dispatch error: {e}")
            return False
        return True

    async def on_processing_complete(
        self, event: MessageEvent, outcome: ProcessingOutcome,
    ) -> None:
        """Mark the claim failed when a bridged turn fails.

        The base class swallows agent-turn exceptions and sends a fallback
        error text via ``send()`` — which on this platform would complete
        the task with the error text. Flagging the slot here makes that
        fallback a no-op (``send()`` drops failed slots) and routes the
        failure to ``/block`` via the watchdog's grace period instead.
        Cancellation (/stop) keeps the slot: the upstream board reclaims
        the expired claim.
        """
        if outcome != ProcessingOutcome.FAILURE:
            return
        chat_id = event.source.chat_id if event.source else None
        if not chat_id:
            return
        entry = self._inflight.get(chat_id)
        if not entry or entry.get("failed"):
            return
        entry["failed"] = True
        entry["failed_at"] = time.monotonic()
        logger.warning(
            "[%s] Turn for task %s failed — blocking task, reply channel muted",
            self.name, entry["task_id"],
        )
        await self._block_task(entry["task_id"], "worker turn failed (see gateway log)")

    def _preapprove_session(self, chat_id: str) -> None:
        """Pre-approve dangerous commands for this bridged session.

        Mirrors the session key the gateway will compute for the same
        source (``group_sessions_per_user`` comes from the same injected
        config extra), then flips the session-YOLO bypass.
        """
        try:
            from gateway.session import build_session_key
            from tools.approval import enable_session_yolo

            source = self.build_source(
                chat_id=chat_id, chat_type="dm", user_id=self._worker_id,
            )
            session_key = build_session_key(
                source,
                group_sessions_per_user=self.config.extra.get(
                    "group_sessions_per_user", True),
            )
            enable_session_yolo(session_key)
            logger.warning(
                "[%s] approval_mode=approve — session %s pre-approved for "
                "dangerous commands", self.name, session_key,
            )
        except Exception as e:
            logger.warning(
                "[%s] Failed to pre-approve session for chat %s: %s",
                self.name, chat_id, e,
            )

    async def _fail_claim(self, chat_id: str, reason: str) -> None:
        entry = self._inflight.get(chat_id)
        if not entry or entry.get("failed"):
            return
        entry["failed"] = True
        entry["failed_at"] = time.monotonic()
        logger.warning(
            "[%s] Task %s failed: %s", self.name, entry["task_id"], reason,
        )
        await self._block_task(entry["task_id"], reason)

    async def _block_task(self, task_id: str, reason: str) -> None:
        assert self._http_client is not None
        try:
            resp = await self._http_client.post(
                f"{self._server}/api/kanban/tasks/{task_id}/block",
                json={"reason": reason[:500]},
                headers=self._auth_headers(),
            )
            if resp.status_code >= 300:
                logger.warning(
                    "[%s] Block %s failed HTTP %d", self.name, task_id,
                    resp.status_code,
                )
        except Exception as e:
            logger.warning("[%s] Block %s error: %s", self.name, task_id, e)

    # -- Heartbeat / watchdog --------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        interval = max(5.0, self._claim_ttl / 3.0)
        while self._running:
            await asyncio.sleep(interval)
            if not self._running or not self._inflight:
                continue
            try:
                await self._heartbeat_once()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("[%s] Heartbeat pass error: %s", self.name, e)

    async def _heartbeat_once(self) -> None:
        assert self._http_client is not None
        now = time.monotonic()
        for chat_id, entry in list(self._inflight.items()):
            # Failed slots: block is sticky (no heartbeat needed); clear
            # after their short grace period.
            if entry.get("failed"):
                if now - entry.get("failed_at", now) > _FAILED_SLOT_TTL_SECONDS:
                    self._inflight.pop(chat_id, None)
                continue
            # claimed_at doubles as "last successful heartbeat" — refreshed
            # below on every successful beat. The watchdog therefore only
            # fires after 3x TTL WITHOUT contact, never on long healthy
            # turns.
            try:
                resp = await self._http_client.post(
                    f"{self._server}/api/kanban/tasks/{entry['task_id']}/heartbeat",
                    json={"ttl_seconds": self._claim_ttl},
                    headers=self._auth_headers(),
                )
                if resp.status_code == 404:
                    logger.warning(
                        "[%s] Claim for task %s no longer held (404) — "
                        "releasing local slot", self.name, entry["task_id"],
                    )
                    self._inflight.pop(chat_id, None)
                elif resp.status_code < 300:
                    entry["claimed_at"] = now
                else:
                    self._watchdog_release(chat_id, entry, now)
            except Exception as e:
                logger.debug(
                    "[%s] Heartbeat for task %s error: %s",
                    self.name, entry["task_id"], e,
                )
                self._watchdog_release(chat_id, entry, now)

    def _watchdog_release(
        self, chat_id: str, entry: Dict[str, Any], now: float,
    ) -> None:
        """Release the local slot after 3x TTL without a successful
        heartbeat — the upstream board will reclaim the expired claim."""
        if now - entry["claimed_at"] <= 3 * self._claim_ttl:
            return
        logger.warning(
            "[%s] Claim for chat %s (task %s) exceeded 3x TTL without a "
            "successful heartbeat — releasing local slot; upstream board "
            "will reclaim", self.name, chat_id, entry["task_id"],
        )
        self._inflight.pop(chat_id, None)

    # -- Outbound ------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Complete the claimed task behind ``chat_id`` with the agent reply.

        Replies with no matching in-flight claim (pairing prompts, stray
        notices, base-layer error fallbacks) are dropped — a task result
        must only ever be the agent's answer for that claim.
        """
        entry = self._inflight.get(chat_id)
        if entry is None:
            logger.debug(
                "[%s] No in-flight claim for chat %s; dropping reply", self.name, chat_id,
            )
            return SendResult(success=True)
        if entry.get("failed"):
            # Base-layer error text after a dispatch failure must not be
            # recorded as the task result. The slot clears via watchdog.
            logger.debug(
                "[%s] Claim for chat %s already failed; dropping error text",
                self.name, chat_id,
            )
            return SendResult(success=True)

        result = content
        if len(result) > MAX_MESSAGE_LENGTH:
            result = (
                result[:MAX_MESSAGE_LENGTH]
                + "\n\n…（结果超过长度上限，已截断）"
            )

        assert self._http_client is not None
        try:
            resp = await self._http_client.post(
                f"{self._server}/api/kanban/tasks/{entry['task_id']}/complete",
                json={"result": result},
                headers=self._auth_headers(),
            )
            if resp.status_code >= 300:
                logger.warning(
                    "[%s] Complete %s failed HTTP %d: %s",
                    self.name, entry["task_id"], resp.status_code, resp.text[:200],
                )
                # Keep the slot so a gateway-level retry can re-complete;
                # the watchdog clears it eventually.
                return SendResult(success=False, retryable=True)
        except Exception as e:
            logger.warning(
                "[%s] Complete %s error: %s", self.name, entry["task_id"], e,
            )
            return SendResult(success=False, error=str(e), retryable=True)

        self._inflight.pop(chat_id, None)
        logger.info(
            "[%s] Task %s completed (%d in flight)",
            self.name, entry["task_id"], len(self._inflight),
        )
        return SendResult(success=True, message_id=entry["task_id"])

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    # -- Headless interaction semantics ---------------------------------------

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Auto-resolve clarifies — there is no human to answer.

        The default base implementation routes the question through
        ``send()``, which would complete the task with the question text
        and leave the agent thread parked forever. Resolving immediately
        with a "use your best judgment" answer keeps the turn (and the
        task result) intact.
        """
        try:
            from tools.clarify_gateway import resolve_gateway_clarify

            resolve_gateway_clarify(
                clarify_id,
                "（无头 worker 无法向用户澄清）请基于已有信息选择最合理的假设并继续。",
            )
        except Exception as e:
            logger.warning(
                "[%s] Failed to auto-resolve clarify %s: %s",
                self.name, clarify_id, e,
            )
        return SendResult(success=True)

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str = "",
        session_key: str = "",
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Swallow dangerous-command approval prompts.

        In gateway sessions a rejected command does NOT block the agent
        thread — the tool result already says "approval required". Surfacing
        the prompt here would complete the task with the warning text while
        the agent keeps running. Simply returning success keeps the reply
        channel clean; the agent routes around the rejection. (Use
        ``approval_mode=approve`` to pre-approve instead.)
        """
        logger.info(
            "[%s] Suppressed exec approval prompt (chat %s, session %s): %s",
            self.name, chat_id, session_key, (command or "")[:120],
        )
        return SendResult(success=True)

    # -- Helpers ---------------------------------------------------------------

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def _env_enablement() -> Optional[dict]:
    """Seed ``PlatformConfig.extra`` from env for ``gateway status`` /
    auto-enable, before any adapter exists. ``None`` = not configured."""
    server = os.getenv("TEAM_BRIDGE_SERVER_URL", "").strip()
    token = os.getenv("TEAM_BRIDGE_API_TOKEN", "").strip()
    if not server or not token:
        return None
    seed: dict = {"server_url": server, "api_token": token}
    board = os.getenv("TEAM_BRIDGE_BOARD", "").strip()
    if board:
        seed["board"] = board
    worker_id = os.getenv("TEAM_BRIDGE_WORKER_ID", "").strip()
    if worker_id:
        seed["worker_id"] = worker_id
    signing_key = os.getenv("TEAM_BRIDGE_TASK_SIGNING_KEY", "").strip()
    if signing_key:
        seed["task_signing_key"] = signing_key
    approval_mode = os.getenv("TEAM_BRIDGE_APPROVAL_MODE", "").strip().lower()
    if approval_mode:
        seed["approval_mode"] = approval_mode
    return seed


def register(ctx) -> None:
    """Plugin entry point — called by the Intellect plugin system at startup."""
    ctx.register_platform(
        name="team_bridge",
        label="Intellect Team Bridge",
        adapter_factory=lambda cfg: TeamBridgeAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["TEAM_BRIDGE_SERVER_URL", "TEAM_BRIDGE_API_TOKEN"],
        install_hint="pip install httpx   # already an Intellect dependency",
        env_enablement_fn=_env_enablement,
        allowed_users_env="TEAM_BRIDGE_ALLOWED_USERS",
        allow_all_env="TEAM_BRIDGE_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="🛰️",
        # Chat content transits the kanban board; not PII-safe.
        pii_safe=False,
        platform_hint=(
            "You are executing tasks pulled from an Intellect Team kanban "
            "board as a remote worker. Work toward the task goal "
            "autonomously — there is no interactive user on this channel. "
            "Your final reply is recorded verbatim as the task result, so "
            "make it a self-contained summary of what was done."
        ),
    )
