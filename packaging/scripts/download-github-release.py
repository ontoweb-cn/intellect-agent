#!/usr/bin/env python3
"""Download release assets from a GitHub Release.

Used by .gitee/workflows/release.yml to pull artifacts built by GitHub Actions
from the intermediate GitHub Release before publishing to Gitee.

Usage:
  GITHUB_PAT=... python packaging/scripts/download-github-release.py \\
      --tag v2026.6.16 \\
      --dist-dir dist/combined \\
      --max-wait 600
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_OWNER = "ontoweb-cn"
GITHUB_REPO = "intellect-agent"
GITHUB_API = "https://api.github.com"
# Absolute maximum wait (overridable per-call). Default 600 s = 10 min covers
# the typical matrix build: GitHub Actions matrix takes 30-45 min, but Gitee CI
# starts at the same tag push so the remaining wait is shorter.
DEFAULT_MAX_WAIT = 600


def _api_request(path: str, *, token: str | None = None) -> dict:
    """GET a GitHub REST API endpoint. Returns parsed JSON."""
    url = f"{GITHUB_API}{path}"
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_file(url: str, dest: Path, *, token: str | None = None) -> None:
    """Download a single file to *dest* (overwrites)."""
    headers: dict[str, str] = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
        dest.write_bytes(resp.read())


def _verify_checksums(dist_dir: Path) -> None:
    """Check every downloaded file against SHA256SUMS (if present)."""
    sums_file = dist_dir / "SHA256SUMS"
    if not sums_file.exists():
        print("No SHA256SUMS file found — skipping verification")
        return
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, filename = line.partition("  ")
        filepath = dist_dir / filename.strip()
        if not filepath.exists():
            print(f"  WARNING: {filename} listed in SHA256SUMS but not found on disk")
            continue
        actual = hashlib.sha256(filepath.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(
                f"Checksum mismatch for {filename}: "
                f"expected {digest}, got {actual}"
            )
        print(f"  OK: {filename}")
    print("SHA256SUMS verification: OK")


def get_release_by_tag(
    tag: str,
    *,
    owner: str = GITHUB_OWNER,
    repo: str = GITHUB_REPO,
    token: str | None = None,
) -> dict | None:
    """Return a GitHub Release dict, or None if 404."""
    encoded = urllib.request.quote(tag, safe="")
    path = f"/repos/{owner}/{repo}/releases/tags/{encoded}"
    try:
        return _api_request(path, token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def download_release_assets(
    release: dict,
    dest_dir: Path,
    *,
    token: str | None = None,
) -> list[Path]:
    """Download every asset of *release* into *dest_dir*."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    assets = release.get("assets") or []
    if not assets:
        print("Release has no assets yet")
        return []
    downloaded: list[Path] = []
    for asset in assets:
        name: str = asset["name"]
        url: str = asset["browser_download_url"]
        size: int = asset.get("size", 0)
        dest = dest_dir / name
        print(f"  Downloading: {name} ({size} bytes)")
        _download_file(url, dest, token=token)
        downloaded.append(dest)
    return downloaded


def wait_for_release(
    tag: str,
    *,
    owner: str = GITHUB_OWNER,
    repo: str = GITHUB_REPO,
    token: str | None = None,
    max_wait: int = DEFAULT_MAX_WAIT,
    interval: int = 15,
) -> dict:
    """Poll GitHub API until *tag* release exists and has assets.

    Returns the release dict.  Raises TimeoutError if *max_wait* seconds
    elapse without finding a release that has assets.
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        release = get_release_by_tag(tag, owner=owner, repo=repo, token=token)
        if release and release.get("assets"):
            print(
                f"Found GitHub Release {tag} with {len(release['assets'])} asset(s)"
            )
            return release
        remaining = int(deadline - time.time())
        if release:
            print(
                f"Release {tag} exists but has no assets yet "
                f"({remaining}s remaining)"
            )
        else:
            print(
                f"Waiting for GitHub Release {tag}... "
                f"({remaining}s remaining)"
            )
        time.sleep(interval)
    raise TimeoutError(
        f"Release {tag} not found on {owner}/{repo} after {max_wait}s"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download release assets from a GitHub Release"
    )
    parser.add_argument(
        "--tag", required=True,
        help="Release tag, e.g. v2026.6.16",
    )
    parser.add_argument(
        "--dist-dir", type=Path, required=True,
        help="Directory to save downloaded assets",
    )
    parser.add_argument(
        "--max-wait", type=int, default=DEFAULT_MAX_WAIT,
        help=f"Max seconds to poll for GitHub Release (default: {DEFAULT_MAX_WAIT})",
    )
    parser.add_argument(
        "--owner", default=GITHUB_OWNER,
        help=f"GitHub owner (default: {GITHUB_OWNER})",
    )
    parser.add_argument(
        "--repo", default=GITHUB_REPO,
        help=f"GitHub repo (default: {GITHUB_REPO})",
    )
    args = parser.parse_args(argv)

    # Strip refs/tags/ prefix if present (Gitee CI may pass full ref)
    tag: str = args.tag
    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]

    token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "WARNING: GITHUB_PAT not set — using anonymous access "
            "(may hit rate limits on private repos)"
        )

    # Prepare clean dist dir
    dist_dir: Path = args.dist_dir.resolve()
    if dist_dir.exists():
        import shutil
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)

    # Poll until the GitHub Release is ready
    release = wait_for_release(
        tag,
        owner=args.owner,
        repo=args.repo,
        token=token,
        max_wait=args.max_wait,
    )

    # Download all assets
    paths = download_release_assets(release, dist_dir, token=token)
    if not paths:
        print(f"ERROR: release {tag} has no downloadable assets", file=sys.stderr)
        return 1

    # Verify integrity
    _verify_checksums(dist_dir)

    print(f"Downloaded {len(paths)} file(s) to {dist_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
