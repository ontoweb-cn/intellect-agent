#!/usr/bin/env python3
"""Download GitHub Actions Artifacts directly (bypass Release API).

Resolves a git tag to its workflow run, waits for completion, then downloads
the specified platform artifacts.  Used by .gitee/workflows/release-sync.yml
as a fast path before falling back to the Release API.

Usage:
  GITHUB_PAT=... python packaging/scripts/download-github-artifacts.py \\
      --tag v2026.6.16 \\
      --dist-dir dist/combined \\
      --platforms darwin,windows \\
      --max-wait 1800
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

GITHUB_OWNER = "ontoweb-cn"
GITHUB_REPO = "intellect-agent"
GITHUB_API = "https://api.github.com"
# Workflow file name as it appears in .github/workflows/
GITEE_RELEASE_WORKFLOW = "gitee-release.yml"
# Default: sync macOS + Windows (Linux built natively by release-linux.yml)
DEFAULT_PLATFORMS = ["darwin", "windows"]
# Artifact name prefixes to download
PLATFORM_ARTIFACT_PREFIX = "platform-"
# Also download the python-dist artifact (contains intellect_agent-*.whl pure Python wheel)
PYTHON_DIST_ARTIFACT = "python-dist"
# Default max wait for workflow run to complete (30 min)
DEFAULT_MAX_WAIT = 1800
# Polling interval for workflow run status
POLL_INTERVAL = 20


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


def _paginated(path: str, *, token: str | None = None, max_pages: int = 5) -> list[dict]:
    """GET a paginated GitHub API endpoint. Returns combined results."""
    results: list[dict] = []
    url = f"{GITHUB_API}{path}"
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ctx = ssl.create_default_context()
    for _ in range(max_pages):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                results.extend(data)
            else:
                return [data]
            # Follow Link header for next page
            link = resp.getheader("Link") or ""
            if 'rel="next"' not in link:
                break
            # Extract next URL from Link header
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break
    return results


def resolve_tag_to_sha(tag: str) -> str:
    """Resolve a git tag to its commit SHA using the local git repo."""
    import subprocess
    result = subprocess.run(
        ["git", "rev-list", "-n", "1", tag],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(
            f"Cannot resolve tag '{tag}' to commit SHA. "
            f"Ensure the tag exists: git fetch --tags"
        )
    return result.stdout.strip()


def find_workflow_run(
    head_sha: str,
    *,
    owner: str = GITHUB_OWNER,
    repo: str = GITHUB_REPO,
    token: str | None = None,
) -> dict | None:
    """Find the gitee-release workflow run for a given commit SHA.

    Returns the most recent completed+success run, or the latest run if
    none has completed yet.
    """
    path = (
        f"/repos/{owner}/{repo}/actions/runs"
        f"?head_sha={head_sha}&event=push&per_page=20"
    )
    try:
        runs = _paginated(path, token=token)
    except urllib.error.HTTPError as exc:
        print(f"  WARNING: Cannot list workflow runs: {exc}", file=sys.stderr)
        return None

    if not runs:
        return None

    # Filter to gitee-release workflow only (multiple workflows may run on tag push)
    matching = [
        r for r in runs
        if r.get("name") == "Gitee Release Artifacts"
        or GITEE_RELEASE_WORKFLOW in (r.get("path") or "")
    ]
    if not matching:
        # Fallback: return the first run (likely correct if only one workflow runs on tag)
        print("  WARNING: No gitee-release workflow run found, using first available run")
        return runs[0] if runs else None

    # Prefer completed+success runs, otherwise return latest (still in progress)
    for run in matching:
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            return run
    return matching[0]


def wait_for_run_completion(
    run_id: int,
    *,
    owner: str = GITHUB_OWNER,
    repo: str = GITHUB_REPO,
    token: str | None = None,
    max_wait: int = DEFAULT_MAX_WAIT,
) -> dict:
    """Poll until the workflow run completes. Raises TimeoutError if exceeded."""
    deadline = time.time() + max_wait
    path = f"/repos/{owner}/{repo}/actions/runs/{run_id}"
    while time.time() < deadline:
        run = _api_request(path, token=token)
        status = run.get("status", "unknown")
        conclusion = run.get("conclusion", "")
        remaining = int(deadline - time.time())
        if status == "completed":
            if conclusion == "success":
                print(
                    f"  Workflow run {run_id} completed successfully "
                    f"({remaining}s remaining)"
                )
                return run
            else:
                raise RuntimeError(
                    f"Workflow run {run_id} completed with conclusion='{conclusion}'. "
                    f"Check: https://github.com/{owner}/{repo}/actions/runs/{run_id}"
                )
        print(
            f"  Waiting for workflow run {run_id}... "
            f"status={status} ({remaining}s remaining)"
        )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"Workflow run {run_id} did not complete within {max_wait}s"
    )


def list_artifacts(
    run_id: int,
    *,
    owner: str = GITHUB_OWNER,
    repo: str = GITHUB_REPO,
    token: str | None = None,
) -> list[dict]:
    """List all artifacts for a workflow run."""
    path = f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts?per_page=100"
    try:
        result = _api_request(path, token=token)
        return result.get("artifacts", [])
    except urllib.error.HTTPError as exc:
        # 404/403: may need authentication
        print(f"  WARNING: Cannot list artifacts: {exc}", file=sys.stderr)
        return []


def _download_file(url: str, dest: Path, *, token: str | None = None) -> None:
    """Download a single file to *dest* (overwrites)."""
    headers: dict[str, str] = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
        dest.write_bytes(resp.read())


def download_artifacts(
    artifacts: list[dict],
    dist_dir: Path,
    *,
    platforms: list[str],
    token: str | None = None,
) -> list[Path]:
    """Download and extract specified platform artifacts into *dist_dir*.

    Each artifact is a zip; files are extracted flat into *dist_dir*.
    Returns list of extracted file paths.
    """
    dist_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    for artifact in artifacts:
        name: str = artifact.get("name", "")
        artifact_id: int = artifact.get("id", 0)
        expired: bool = artifact.get("expired", False)
        size_bytes: int = artifact.get("size_in_bytes", 0)

        if expired:
            print(f"  Skipping expired artifact: {name}")
            continue

        # Filter: download python-dist (pure Python wheel) and matching platform artifacts
        is_python_dist = name == PYTHON_DIST_ARTIFACT
        is_platform = name.startswith(PLATFORM_ARTIFACT_PREFIX)
        if not is_python_dist and not is_platform:
            print(f"  Skipping artifact: {name}")
            continue
        if is_platform:
            artifact_platform = name[len(PLATFORM_ARTIFACT_PREFIX):].split("-")[0]
            if artifact_platform not in platforms:
                print(f"  Skipping artifact for platform '{artifact_platform}': {name}")
                continue

        print(f"  Downloading artifact: {name} ({size_bytes} bytes)")
        url = (
            f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/actions/artifacts/{artifact_id}/zip"
        )
        zip_path = dist_dir / f"{name}.zip"
        try:
            _download_file(url, zip_path, token=token)
        except urllib.error.HTTPError as exc:
            print(f"  ERROR downloading {name}: {exc}", file=sys.stderr)
            continue

        # Extract zip
        print(f"  Extracting: {name}.zip")
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                # Skip directory entries
                if member.endswith("/"):
                    continue
                # Extract flat (ignore internal directory structure)
                basename = Path(member).name
                dest = dist_dir / basename
                with zf.open(member) as src:
                    dest.write_bytes(src.read())
                extracted.append(dest)
                print(f"    → {basename}")

        # Clean up zip
        zip_path.unlink()

    return extracted


def verify_expected_artifacts(
    artifacts: list[dict],
    platforms: list[str],
) -> bool:
    """Verify that all expected platform and python-dist artifacts are present and non-expired."""
    expected_platform = {f"{PLATFORM_ARTIFACT_PREFIX}{p}" for p in platforms}
    expected_all = expected_platform | {PYTHON_DIST_ARTIFACT}
    found: set[str] = set()
    expired: set[str] = set()
    for a in artifacts:
        name = a.get("name", "")
        is_python_dist = name == PYTHON_DIST_ARTIFACT
        is_platform = name.startswith(PLATFORM_ARTIFACT_PREFIX)
        if not is_python_dist and not is_platform:
            continue
        if is_python_dist:
            if a.get("expired", False):
                expired.add(name)
            else:
                found.add(name)
            continue
        for prefix in expected_platform:
            if name.startswith(prefix):
                if a.get("expired", False):
                    expired.add(name)
                else:
                    found.add(name)
    missing = expected_all - {_strip_suffix(n) for n in found}
    if missing:
        print(f"  Missing artifacts: {missing}")
        return False
    if expired:
        print(f"  Expired artifacts: {expired}")
        return False
    print(f"  All expected artifacts present: {sorted(found)}")
    return True


def _strip_suffix(name: str) -> str:
    """Strip trailing -arch suffix from artifact name for comparison."""
    # platform-darwin-universal2 → platform-darwin
    # platform-windows-amd64 → platform-windows
    parts = name.rsplit("-", 1)
    return parts[0] if len(parts) > 1 else name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download GitHub Actions Artifacts for a release tag"
    )
    parser.add_argument(
        "--tag", required=True,
        help="Release tag, e.g. v2026.6.16",
    )
    parser.add_argument(
        "--dist-dir", type=Path, required=True,
        help="Directory to save extracted artifact files",
    )
    parser.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS),
        help=f"Comma-separated platforms to download "
             f"(default: {','.join(DEFAULT_PLATFORMS)})",
    )
    parser.add_argument(
        "--max-wait", type=int, default=DEFAULT_MAX_WAIT,
        help=f"Max seconds to wait for workflow run completion "
             f"(default: {DEFAULT_MAX_WAIT})",
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

    # Strip refs/tags/ prefix if present
    tag: str = args.tag
    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]

    platforms: list[str] = [p.strip() for p in args.platforms.split(",") if p.strip()]
    if not platforms:
        print("ERROR: no platforms specified", file=sys.stderr)
        return 1

    token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "ERROR: GITHUB_PAT or GITHUB_TOKEN is required to download "
            "GitHub Actions artifacts (even for public repos).",
            file=sys.stderr,
        )
        return 1

    dist_dir: Path = args.dist_dir.resolve()
    # Clean and recreate dist dir
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)

    # Step 1: Resolve tag → commit SHA
    print(f"=== Step 1: Resolving tag '{tag}' to commit SHA ===")
    try:
        head_sha = resolve_tag_to_sha(tag)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"  Tag {tag} → {head_sha[:8]}")

    # Step 2: Find workflow run
    print(f"=== Step 2: Finding workflow run for commit {head_sha[:8]} ===")
    run = find_workflow_run(head_sha, owner=args.owner, repo=args.repo, token=token)
    if run is None:
        print("ERROR: No workflow run found for this tag", file=sys.stderr)
        return 1
    run_id = int(run["id"])
    run_status = run.get("status", "unknown")
    run_conclusion = run.get("conclusion", "")
    run_url = run.get("html_url", "")
    print(f"  Run ID: {run_id}")
    print(f"  Run URL: {run_url}")
    print(f"  Status: {run_status}, Conclusion: {run_conclusion}")

    # Step 3: Wait for completion
    print(f"=== Step 3: Waiting for workflow run to complete (max {args.max_wait}s) ===")
    try:
        run = wait_for_run_completion(
            run_id,
            owner=args.owner,
            repo=args.repo,
            token=token,
            max_wait=args.max_wait,
        )
    except (TimeoutError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Step 4: List artifacts
    print(f"=== Step 4: Listing artifacts ===")
    artifacts = list_artifacts(run_id, owner=args.owner, repo=args.repo, token=token)
    if not artifacts:
        print("ERROR: No artifacts found for this workflow run", file=sys.stderr)
        return 1
    for a in artifacts:
        expired_mark = " [EXPIRED]" if a.get("expired", False) else ""
        print(
            f"  {a.get('name', '?')}: "
            f"{a.get('size_in_bytes', 0)} bytes"
            f"{expired_mark}"
        )

    # Step 5: Verify expected artifacts exist
    print(f"=== Step 5: Verifying expected artifacts for platforms: {platforms} ===")
    if not verify_expected_artifacts(artifacts, platforms):
        print(
            "ERROR: Not all expected artifacts are available. "
            "Will fall back to Release API.",
            file=sys.stderr,
        )
        return 1

    # Step 6: Download and extract
    print(f"=== Step 6: Downloading and extracting artifacts ===")
    extracted = download_artifacts(artifacts, dist_dir, platforms=platforms, token=token)
    if not extracted:
        print("ERROR: No files extracted", file=sys.stderr)
        return 1
    print(f"  Downloaded and extracted {len(extracted)} file(s) to {dist_dir}")
    for f in sorted(extracted):
        print(f"    {f.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
