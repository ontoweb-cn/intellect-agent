"""Merge staged wiki contributions into the Global LLM Wiki."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intellect_cli.wiki_scaffold import (
    _FORBIDDEN_CONTRIB_PATHS,
    contributions_staging_root,
    global_wiki_dir,
    safe_slug,
)


@dataclass
class MergeResult:
    ok: bool
    merged_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    error: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_log(log_path: Path, line: str) -> None:
    if not log_path.parent.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def _add_provenance(content: str, *, contrib_id: str, submitter_id: str, source_rel: str) -> str:
    today = _utc_now()
    extra = (
        f"contributors: [{submitter_id}]\n"
        f"promoted_from: {source_rel}\n"
        f"promoted_contrib_id: {contrib_id}\n"
        f"promoted_at: {today}"
    )
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            front = content[4:end]
            body = content[end + 5 :]
            if "promoted_contrib_id:" not in front:
                front = front.rstrip() + "\n" + extra + "\n"
            return f"---\n{front}---\n{body}"
    return f"---\n{extra}\n---\n\n{content}"


def merge_contribution_snapshot(
    *,
    intellect_home: Path | str,
    contrib_id: str,
    submitter_id: str,
    page_paths: list[str],
    merge_strategy: str = "add_new",
    reviewer_id: str | None = None,
    source_scope: str = "member",
    source_scope_id: str | None = None,
) -> MergeResult:
    """Copy staged pages from wiki/.contributions/{id}/snapshot into wiki/global."""
    home = Path(intellect_home).expanduser()
    staging = contributions_staging_root(home) / contrib_id / "snapshot"
    if not staging.is_dir():
        return MergeResult(ok=False, error="staging snapshot not found")

    global_root = global_wiki_dir(home)
    global_root.mkdir(parents=True, exist_ok=True)
    merged: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    strategy = str(merge_strategy or "add_new").strip().lower()

    for rel in page_paths:
        rel_norm = str(rel).strip().replace("\\", "/").lstrip("/")
        if not rel_norm or rel_norm in _FORBIDDEN_CONTRIB_PATHS or ".." in rel_norm.split("/"):
            skipped.append(rel_norm or str(rel))
            continue
        src = staging / rel_norm
        if not src.is_file():
            skipped.append(rel_norm)
            continue
        dest = global_root / rel_norm
        if dest.exists() and strategy == "add_new":
            conflicts.append(rel_norm)
            continue
        if dest.exists() and strategy == "skip_conflicts":
            skipped.append(rel_norm)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        source_rel = f"{source_scope}s/{source_scope_id}/wiki/{rel_norm}" if source_scope_id else rel_norm
        text = src.read_text(encoding="utf-8", errors="replace")
        if rel_norm.endswith(".md"):
            text = _add_provenance(
                text,
                contrib_id=contrib_id,
                submitter_id=submitter_id,
                source_rel=source_rel,
            )
        dest.write_text(text, encoding="utf-8")
        merged.append(rel_norm)

    if merged:
        log_line = (
            f"- {_utc_now()} | merge | {contrib_id} | {submitter_id} → "
            f"{', '.join(merged)} | approved by {reviewer_id or 'admin'}"
        )
        _append_log(global_root / "log.md", log_line)
        if source_scope == "member" and source_scope_id:
            member_log = home / "members" / source_scope_id / "wiki" / "log.md"
            if member_log.is_file():
                _append_log(
                    member_log,
                    f"- {_utc_now()} | promotion_merged | {contrib_id} → global: {', '.join(merged)}",
                )

    ok = bool(merged) or (not conflicts and not page_paths)
    return MergeResult(
        ok=ok,
        merged_paths=merged,
        skipped_paths=skipped,
        conflicts=conflicts,
        error=None if ok else "no paths merged",
    )


def copy_contribution_snapshot(
    *,
    intellect_home: Path | str,
    contrib_id: str,
    source_wiki: Path,
    page_paths: list[str],
    manifest: dict[str, Any],
) -> Path:
    """Create staging snapshot from a live member wiki."""
    root = contributions_staging_root(intellect_home) / contrib_id
    snapshot = root / "snapshot"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for rel in page_paths:
        rel_norm = str(rel).strip().replace("\\", "/").lstrip("/")
        if not rel_norm or rel_norm in _FORBIDDEN_CONTRIB_PATHS or ".." in rel_norm.split("/"):
            continue
        src = source_wiki / rel_norm
        if not src.is_file():
            continue
        dest = snapshot / rel_norm
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(rel_norm)
    manifest = dict(manifest)
    manifest["page_paths"] = copied
    manifest["snapshot_dir"] = str(snapshot)
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return snapshot


def validate_contribution_page_paths(
    source_wiki: Path,
    page_paths: list[str],
    *,
    max_paths: int = 20,
) -> tuple[list[str], str | None]:
    """Return sanitized relative paths under source_wiki or an error message."""
    if not page_paths:
        return [], "page_paths required"
    if len(page_paths) > max_paths:
        return [], f"at most {max_paths} paths per contribution"
    source = source_wiki.expanduser().resolve()
    clean: list[str] = []
    for rel in page_paths:
        rel_norm = str(rel).strip().replace("\\", "/").lstrip("/")
        if not rel_norm or rel_norm in _FORBIDDEN_CONTRIB_PATHS:
            return [], f"forbidden path: {rel}"
        if ".." in rel_norm.split("/"):
            return [], f"invalid path: {rel}"
        candidate = (source / rel_norm).resolve()
        try:
            candidate.relative_to(source)
        except ValueError:
            return [], f"path outside wiki: {rel}"
        if not candidate.is_file():
            return [], f"file not found: {rel}"
        clean.append(rel_norm)
    return clean, None
