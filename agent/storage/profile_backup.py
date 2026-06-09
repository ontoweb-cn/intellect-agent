"""Profile-wide backup tarball (T6 / W3)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from intellect_constants import get_intellect_home


@dataclass
class BackupFileEntry:
    archive_path: str
    source_path: str
    bytes: int
    sha256: str


@dataclass
class BackupReport:
    archive_path: Path
    manifest: dict[str, Any]
    files: list[BackupFileEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_backup_sources(home: Path) -> Iterator[tuple[str, Path]]:
    """Yield (archive_relative_path, source_path) pairs to include."""
    config = home / "config.yaml"
    if config.is_file():
        yield "config.yaml", config

    env_file = home / ".env"
    if env_file.is_file():
        yield ".env", env_file

    for name in ("state.db", "state.db-wal", "state.db-shm"):
        p = home / name
        if p.is_file():
            yield name, p

    webui_root = home / "webui"
    if webui_root.is_dir():
        for path in sorted(webui_root.rglob("*")):
            if path.is_file():
                rel = path.relative_to(home)
                yield str(rel).replace("\\", "/"), path

    member_sessions = home / ".member-sessions"
    if member_sessions.is_file():
        yield ".member-sessions", member_sessions

    kanban_db = home / "kanban.db"
    if kanban_db.is_file():
        yield "kanban.db", kanban_db

    kanban_dir = home / "kanban"
    if kanban_dir.is_dir():
        for path in sorted(kanban_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(home)
                yield str(rel).replace("\\", "/"), path


def create_profile_backup(
    *,
    intellect_home: Path | None = None,
    config: dict | None = None,
    output: Path | None = None,
) -> BackupReport:
    """Create a gzip tarball + manifest for the active profile (T6)."""
    if config is None:
        from intellect_cli.config import load_config

        config = load_config()

    home = Path(intellect_home or get_intellect_home()).expanduser().resolve()

    if output is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backups_dir = home / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        output = backups_dir / f"intellect-backup-{stamp}.tar.gz"

    output = Path(output).expanduser().resolve()
    warnings: list[str] = []
    entries: list[BackupFileEntry] = []

    try:
        from importlib.metadata import version as _pkg_version

        agent_version = _pkg_version("intellect-agent")
    except Exception:
        agent_version = "unknown"

    with tarfile.open(output, "w:gz") as tar:
        for arcname, source in _iter_backup_sources(home):
            if not source.is_file():
                continue
            tar.add(source, arcname=arcname)
            entries.append(
                BackupFileEntry(
                    archive_path=arcname,
                    source_path=str(source),
                    bytes=source.stat().st_size,
                    sha256=_sha256_file(source),
                )
            )

        manifest = {
            "format": "intellect-profile-backup-v1",
            "created_at": time.time(),
            "intellect_agent_version": agent_version,
            "intellect_home": str(home),
            "storage_backend": "sqlite",
            "files": [
                {
                    "path": e.archive_path,
                    "bytes": e.bytes,
                    "sha256": e.sha256,
                }
                for e in entries
            ],
            "warnings": warnings,
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        import io

        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

    manifest["archive_path"] = str(output)
    manifest["archive_bytes"] = output.stat().st_size
    return BackupReport(archive_path=output, manifest=manifest, files=entries, warnings=warnings)


import tarfile
import shutil  # noqa: E402


@dataclass
class RestoreReport:
    archive_path: Path
    manifest: dict[str, Any]
    dry_run: bool
    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _backup_existing(path: Path) -> Path | None:
    if not path.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.pre-restore.{stamp}")
    shutil.copy2(path, backup)
    return backup


def restore_profile_backup(
    archive: Path,
    *,
    intellect_home: Path | None = None,
    config: dict | None = None,
    dry_run: bool = False,
) -> RestoreReport:
    """Restore a profile tarball created by :func:`create_profile_backup`."""
    archive = Path(archive).expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"backup archive not found: {archive}")

    if config is None:
        from intellect_cli.config import load_config

        config = load_config()

    home = Path(intellect_home or get_intellect_home()).expanduser().resolve()

    with tarfile.open(archive, "r:gz") as tar:
        try:
            manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
        except KeyError as exc:
            raise ValueError("backup missing manifest.json") from exc

        report = RestoreReport(
            archive_path=archive,
            manifest=manifest,
            dry_run=dry_run,
        )

        members = [m for m in tar.getmembers() if m.isfile() and m.name != "manifest.json"]
        for member in members:
            arc = member.name
            # Skip PG dump artifacts from old backups
            if arc in ("state.pg.dump", "postgresql.note.txt"):
                report.skipped.append(arc)
                report.warnings.append(f"skipped {arc}: PostgreSQL backend is no longer supported")
                continue

            dest = home / arc
            if dry_run:
                report.restored.append(arc)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file():
                _backup_existing(dest)
            extracted = tar.extractfile(member)
            if extracted is None:
                report.skipped.append(arc)
                continue
            data = extracted.read()
            dest.write_bytes(data)
            if arc == "config.yaml":
                try:
                    os.chmod(dest, 0o600)
                except OSError:
                    pass
            report.restored.append(arc)

        return report
