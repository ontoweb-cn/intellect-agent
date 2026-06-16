"""Gitee Release API helpers for Intellect Agent packaging.

Used by scripts/release.py and documented in docs/packaging/gitee-releases.md.

Requires GITEE_TOKEN (private token with repo scope) for create/upload.
Public read endpoints work without a token.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

GITEE_OWNER = "ontoweb"
GITEE_REPO = "intellect-agent"
GITEE_API = "https://gitee.com/api/v5"
GITEE_RELEASES_PAGE = f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/releases"


def _api_url(path: str, *, token: str | None = None, params: dict | None = None) -> str:
    q: dict[str, str] = dict(params or {})
    if token:
        q["access_token"] = token
    query = urllib.parse.urlencode(q)
    base = f"{GITEE_API}{path}"
    return f"{base}?{query}" if query else base


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> Any:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def get_token() -> str | None:
    return os.environ.get("GITEE_TOKEN") or os.environ.get("GITEE_ACCESS_TOKEN")


def get_latest_release(*, token: str | None = None) -> dict | None:
    """Return the newest release dict or None."""
    url = _api_url(
        f"/repos/{GITEE_OWNER}/{GITEE_REPO}/releases",
        token=token,
        params={"page": "1", "per_page": "1", "direction": "desc"},
    )
    try:
        releases = _request(url)
    except urllib.error.HTTPError:
        return None
    if isinstance(releases, list) and releases:
        return releases[0]
    return None


def get_release_by_tag(tag_name: str, *, token: str | None = None) -> dict | None:
    encoded = urllib.parse.quote(tag_name, safe="")
    url = _api_url(
        f"/repos/{GITEE_OWNER}/{GITEE_REPO}/releases/tags/{encoded}",
        token=token,
    )
    try:
        return _request(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def create_release(
    tag_name: str,
    name: str,
    body: str,
    *,
    token: str,
    target_commitish: str = "main",
) -> dict:
    """Create a Gitee release. Raises if the API returns an error."""
    payload = json.dumps(
        {
            "tag_name": tag_name,
            "name": name,
            "body": body,
            "target_commitish": target_commitish,
            "prerelease": False,
        }
    ).encode("utf-8")
    url = _api_url(f"/repos/{GITEE_OWNER}/{GITEE_REPO}/releases", token=token)
    return _request(
        url,
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )


def upload_release_asset(release_id: int, file_path: Path, *, token: str) -> dict:
    """Upload a single attachment to an existing release."""
    boundary = "----IntellectAgentBoundary7MA4YWxkTrZu0gW"
    file_path = Path(file_path)
    file_bytes = file_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = _api_url(
        f"/repos/{GITEE_OWNER}/{GITEE_REPO}/releases/{release_id}/attach_files",
        token=token,
    )
    return _request(
        url,
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=600.0,
    )


def publish_gitee_release(
    tag_name: str,
    title: str,
    body: str,
    artifact_paths: list[Path],
    *,
    token: str | None = None,
) -> tuple[bool, str]:
    """Create or reuse a Gitee release and upload artifacts.

    Returns (success, message).
    """
    token = token or get_token()
    if not token:
        return False, "GITEE_TOKEN not set — skipping Gitee Release upload"

    existing = get_release_by_tag(tag_name, token=token)
    if existing and existing.get("id"):
        release_id = int(existing["id"])
        msg_prefix = f"Reusing Gitee release {tag_name} (id={release_id})"
    else:
        created = create_release(tag_name, title, body, token=token)
        release_id = int(created["id"])
        msg_prefix = f"Created Gitee release {tag_name} (id={release_id})"

    uploaded: list[str] = []
    for path in artifact_paths:
        path = Path(path)
        if not path.is_file():
            continue
        upload_release_asset(release_id, path, token=token)
        uploaded.append(path.name)

    if not uploaded:
        return True, f"{msg_prefix}; no artifact files to upload"

    return True, f"{msg_prefix}; uploaded {len(uploaded)} file(s): {', '.join(uploaded)}"


def find_rust_wheel_url(
    *,
    tag_name: str | None = None,
    platform_hint: str,
) -> str | None:
    """Find a matching intellect_community_core wheel URL on a Gitee release.

    platform_hint examples: manylinux_2_28_x86_64, macosx_11_0_universal2, win_amd64
    """
    release = None
    if tag_name:
        release = get_release_by_tag(tag_name)
    if release is None:
        release = get_latest_release()
    if not release:
        return None

    assets = release.get("assets") or []
    needle = platform_hint.lower()
    for asset in assets:
        name = (asset.get("name") or "").lower()
        url = asset.get("browser_download_url") or asset.get("url") or ""
        if "intellect_community_core" not in name:
            continue
        if needle in name and name.endswith(".whl"):
            return url
    return None
