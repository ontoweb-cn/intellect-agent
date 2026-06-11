"""
WebUI user profile — display name and avatar stored under STATE_DIR.

Legacy profile (``members.enabled: false``) uses key ``local``. Multi-user profiles are keyed
by sanitized member id so each member can have their own avatar.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from api.config import STATE_DIR
from api.helpers import bad, j, read_body
from api.upload import parse_multipart

logger = logging.getLogger(__name__)

MAX_AVATAR_BYTES = 512 * 1024
_AVATAR_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_PROFILE_KEY_RE = re.compile(r"^[\w.\-@]{1,120}$")


def _profiles_dir() -> Path:
    return STATE_DIR / "user_profiles"


def _avatars_dir() -> Path:
    return STATE_DIR / "avatars"


def _sanitize_profile_key(raw: str) -> str:
    key = re.sub(r"[^\w.\-@]", "_", (raw or "").strip())[:120]
    if not key or not _PROFILE_KEY_RE.match(key):
        return "member"
    return key


def resolve_profile_key(handler, parsed) -> str:
    """Return filesystem key for the active user profile."""
    from api.auth import build_login_context

    ctx = build_login_context(handler, parsed)
    if ctx.get("mode") == "multi_user" and ctx.get("actor_member_id"):
        return _sanitize_profile_key(str(ctx["actor_member_id"]))
    return "local"


def _profile_json_path(key: str) -> Path:
    return _profiles_dir() / f"{key}.json"


def _load_profile_data(key: str) -> dict[str, Any]:
    path = _profile_json_path(key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to read user profile %s: %s", path, exc)
        return {}


def _save_profile_data(key: str, data: dict[str, Any]) -> None:
    _profiles_dir().mkdir(parents=True, exist_ok=True)
    path = _profile_json_path(key)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _find_avatar_path(key: str) -> Optional[Path]:
    base = _avatars_dir()
    for ext in _AVATAR_EXTS:
        candidate = base / f"{key}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _remove_avatar(key: str) -> None:
    for ext in _AVATAR_EXTS:
        path = _avatars_dir() / f"{key}{ext}"
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                logger.debug("Failed to remove avatar %s: %s", path, exc)


def _oauth_display_name(handler, parsed, member_id: str) -> Optional[str]:
    try:
        from agent.members_oauth import format_member_identities
        from api.members import _store

        store = _store()
        try:
            identities = format_member_identities(store, member_id)
        finally:
            store.close()
        if not identities:
            return None
        first = identities[0]
        for field in ("name", "email"):
            val = first.get(field)
            if isinstance(val, str) and val.strip():
                return val.strip()
        disp = first.get("display_name")
        return str(disp).strip() if disp else None
    except Exception as exc:
        logger.debug("OAuth display name lookup failed for %s: %s", member_id, exc)
        return None


def build_profile_payload(handler, parsed) -> dict[str, Any]:
    from api.auth import build_login_context, is_auth_enabled, parse_cookie, verify_session

    ctx = build_login_context(handler, parsed)
    mode = str(ctx.get("mode") or "legacy")
    auth_enabled = is_auth_enabled()
    logged_in = False
    if auth_enabled:
        cv = parse_cookie(handler)
        logged_in = bool(cv and verify_session(cv))

    key = resolve_profile_key(handler, parsed)
    stored = _load_profile_data(key)
    member_id = ctx.get("actor_member_id")
    oauth_name = _oauth_display_name(handler, parsed, member_id) if member_id else None

    display_name = ""
    username_editable = mode == "legacy"
    if mode == "multi_user":
        # Look up the member's actual display_name from the DB
        db_name = ""
        if member_id:
            try:
                from api.members import _store
                store = _store()
                try:
                    row = store.get_member(member_id)
                    if row:
                        db_name = str(row.get("display_name") or row.get("login_name") or "")
                finally:
                    store.close()
            except Exception:
                pass  # intentionally silent — cleanup/teardown path
        display_name = oauth_name or db_name or str(member_id or "")
    else:
        display_name = str(stored.get("display_name") or "").strip() or "User"

    avatar_path = _find_avatar_path(key)
    password_env_var = bool(__import__("os").getenv("INTELLECT_WEBUI_PASSWORD", "").strip())

    return {
        "mode": mode,
        "profile_key": key,
        "display_name": display_name,
        "member_id": member_id,
        "oauth_display_name": oauth_name,
        "stored_display_name": str(stored.get("display_name") or "").strip(),
        "username_editable": username_editable,
        "has_avatar": avatar_path is not None,
        "avatar_url": f"/api/user/profile/avatar?k={key}" if avatar_path else None,
        "auth_enabled": auth_enabled,
        "logged_in": logged_in,
        "password_env_var": password_env_var,
        "show_sign_out": bool(
            (mode == "multi_user" and member_id)
            or (mode == "legacy" and auth_enabled and logged_in)
        ),
        "show_disable_auth": bool(mode == "legacy" and auth_enabled),
    }


def handle_get(handler, parsed) -> bool:
    path = parsed.path
    if path == "/api/user/profile":
        j(handler, build_profile_payload(handler, parsed))
        return True
    if path == "/api/user/profile/avatar":
        return _serve_avatar(handler, parsed)
    return False


def handle_post(handler, parsed) -> bool:
    path = parsed.path
    if path == "/api/user/profile":
        return _post_profile(handler, parsed)
    if path == "/api/user/profile/avatar":
        return _post_avatar(handler, parsed)
    return False


def _post_profile(handler, parsed) -> bool:
    ctx = __import__("api.auth", fromlist=["build_login_context"]).build_login_context(
        handler, parsed
    )
    mode = str(ctx.get("mode") or "legacy")
    key = resolve_profile_key(handler, parsed)

    try:
        body = read_body(handler)
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    if body.get("remove_avatar"):
        _remove_avatar(key)
        j(handler, {"ok": True, **build_profile_payload(handler, parsed)})
        return True

    if mode != "legacy":
        bad(handler, "Display name is read-only in multi-user mode", status=403)
        return True

    display_name = str(body.get("display_name") or "").strip()
    if not display_name:
        bad(handler, "display_name is required")
        return True
    if len(display_name) > 80:
        bad(handler, "display_name too long (max 80)")
        return True

    data = _load_profile_data(key)
    data["display_name"] = display_name
    _save_profile_data(key, data)
    j(handler, {"ok": True, **build_profile_payload(handler, parsed)})
    return True


def _post_avatar(handler, parsed) -> bool:
    key = resolve_profile_key(handler, parsed)
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        bad(handler, "Expected multipart/form-data upload")
        return True
    try:
        content_length = int(handler.headers.get("Content-Length", 0) or 0)
    except ValueError:
        bad(handler, "Invalid Content-Length")
        return True
    if content_length <= 0 or content_length > MAX_AVATAR_BYTES:
        bad(
            handler,
            f"Avatar too large (max {MAX_AVATAR_BYTES // 1024}KB)",
            status=413,
        )
        return True

    try:
        _fields, files = parse_multipart(handler.rfile, content_type, content_length)
    except ValueError as exc:
        bad(handler, str(exc))
        return True

    if "file" not in files:
        bad(handler, "No file field in request")
        return True
    _filename, file_bytes = files["file"]
    if not file_bytes:
        bad(handler, "Empty upload")
        return True
    if len(file_bytes) > MAX_AVATAR_BYTES:
        bad(
            handler,
            f"Avatar too large (max {MAX_AVATAR_BYTES // 1024}KB)",
            status=413,
        )
        return True

    mime = _detect_image_mime(file_bytes)
    if not mime:
        bad(handler, "Avatar must be JPEG, PNG, or WebP")
        return True
    ext = _ALLOWED_CONTENT_TYPES[mime]

    _avatars_dir().mkdir(parents=True, exist_ok=True)
    _remove_avatar(key)
    dest = (_avatars_dir() / f"{key}{ext}").resolve()
    if not dest.is_relative_to(_avatars_dir().resolve()):
        bad(handler, "Invalid avatar path")
        return True
    dest.write_bytes(file_bytes)
    j(handler, {"ok": True, **build_profile_payload(handler, parsed)})
    return True


def _detect_image_mime(data: bytes) -> Optional[str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _serve_avatar(handler, parsed) -> bool:
    qs = parse_qs(parsed.query or "")
    requested_key = (qs.get("k") or [""])[0].strip()
    key = requested_key or resolve_profile_key(handler, parsed)
    if requested_key and _sanitize_profile_key(requested_key) != key:
        bad(handler, "Invalid profile key", status=403)
        return True

    path = _find_avatar_path(key)
    if not path:
        handler.send_response(404)
        handler.end_headers()
        return True

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "private, max-age=60")
    handler.end_headers()
    handler.wfile.write(data)
    return True
