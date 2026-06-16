#!/usr/bin/env python3
"""Publish merged CI artifacts to Gitee Release.

Called by .github/workflows/gitee-release.yml after matrix builds complete.

Usage:
  GITEE_TOKEN=... python packaging/scripts/publish-gitee-from-ci.py \\
      --tag v2026.6.16 \\
      --dist-dir dist/combined
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_gitee():
    path = REPO_ROOT / "packaging" / "gitee_release.py"
    spec = importlib.util.spec_from_file_location("intellect_gitee_release", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_semver() -> str:
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def write_sha256sums(dist_dir: Path) -> Path:
    lines: list[str] = []
    for path in sorted(dist_dir.iterdir()):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    out = dist_dir / "SHA256SUMS"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def release_notes(tag: str, semver: str) -> str:
    result = subprocess.run(
        ["git", "tag", "-l", tag, "--format=%(contents)"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    body = (result.stdout or "").strip()
    if body:
        return body
    return (
        f"Intellect Agent v{semver} ({tag})\n\n"
        f"Downloads: https://gitee.com/ontoweb/intellect-agent/releases/tag/{tag}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload CI artifacts to Gitee Release")
    parser.add_argument("--tag", required=True, help="CalVer tag, e.g. v2026.6.16")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=REPO_ROOT / "dist" / "combined",
        help="Directory containing all release files",
    )
    args = parser.parse_args(argv)

    dist_dir = args.dist_dir.resolve()
    if not dist_dir.is_dir():
        print(f"ERROR: dist dir not found: {dist_dir}", file=sys.stderr)
        return 1

    artifacts = sorted(p for p in dist_dir.iterdir() if p.is_file())
    if not artifacts:
        print(f"ERROR: no files in {dist_dir}", file=sys.stderr)
        return 1

    write_sha256sums(dist_dir)
    artifacts = sorted(p for p in dist_dir.iterdir() if p.is_file())

    semver = _read_semver()
    title = f"Intellect Agent v{semver} ({args.tag.lstrip('v')})"
    body = release_notes(args.tag, semver)

    gitee = _load_gitee()
    ok, msg = gitee.publish_gitee_release(args.tag, title, body, artifacts)
    if ok:
        print(msg)
        print(f"Release page: {gitee.GITEE_RELEASES_PAGE}/tag/{args.tag}")
        return 0
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
