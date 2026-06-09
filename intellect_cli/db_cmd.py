"""CLI: ``intellect db`` — storage migration and maintenance."""

from __future__ import annotations

import argparse
import json
import sys

from intellect_constants import display_intellect_home, get_intellect_home


def register_cli(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="db_command")

    bak = subs.add_parser(
        "backup",
        help="Create profile backup tarball (T6: config + DB + webui/sessions)",
    )
    bak.add_argument(
        "-o",
        "--output",
        default="",
        help="Output .tar.gz path (default: ~/.intellect/backups/intellect-backup-<ts>.tar.gz)",
    )
    bak.set_defaults(func=_cmd_backup)

    insp = subs.add_parser(
        "backup-inspect",
        help="Print manifest.json from an existing backup archive",
    )
    insp.add_argument("archive", help="Path to intellect-backup-*.tar.gz")
    insp.set_defaults(func=_cmd_backup_inspect)

    rst = subs.add_parser(
        "restore",
        help="Restore profile from intellect-backup-*.tar.gz (T6)",
    )
    rst.add_argument("archive", help="Path to backup .tar.gz")
    rst.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be restored without writing",
    )
    rst.set_defaults(func=_cmd_restore)

    mk = subs.add_parser(
        "migrate-kanban",
        help="Copy legacy kanban.db into unified storage (T1)",
    )
    mk.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    mk.add_argument("--no-backup", action="store_true", help="Skip kanban.db backup copies")
    mk.add_argument(
        "--apply-config",
        action="store_true",
        help="Set kanban.storage=unified in config.yaml after success",
    )
    mk.add_argument(
        "--allow-multi-board",
        action="store_true",
        help="Allow migrating non-default boards (experimental)",
    )
    mk.set_defaults(func=_cmd_migrate_kanban)


def _cmd_backup(args) -> int:
    from pathlib import Path

    from agent.storage.profile_backup import create_profile_backup
    from intellect_cli.config import load_config

    config = load_config()
    output = Path(args.output).expanduser() if args.output else None
    try:
        report = create_profile_backup(config=config, output=output)
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    print(f"Archive: {report.archive_path}")
    print(f"Bytes:   {report.manifest.get('archive_bytes', 0)}")
    print(f"Backend: {report.manifest.get('storage_backend')}")
    print(f"Files:   {len(report.files)}")
    for w in report.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    return 0


def _cmd_migrate_kanban(args) -> int:
    from agent.storage.migrate_kanban import migrate_kanban_to_unified_storage
    from intellect_cli.config import load_config

    config = load_config()
    try:
        report = migrate_kanban_to_unified_storage(
            config=config,
            dry_run=args.dry_run,
            backup=not args.no_backup,
            update_config=args.apply_config,
            allow_multi_board=args.allow_multi_board,
        )
    except Exception as exc:
        print(f"Kanban migration failed: {exc}", file=sys.stderr)
        return 1

    print(f"Target: {report.target}")
    print(f"Mode:   {'dry-run' if report.dry_run else 'apply'}")
    for src in report.sources:
        print(f"Source: {src[0]} → {src[1]}")
    if report.backup_paths:
        for bp in report.backup_paths:
            print(f"Backup: {bp}")
    if report.config_updated:
        print(f"Config: updated {display_intellect_home()}/config.yaml → kanban.storage=unified")
    for row in report.tables:
        status = "skip" if row.skipped else "ok"
        if row.error:
            status = f"error: {row.error}"
        print(
            f"  {row.board}:{row.name:24} source={row.source_rows:5} "
            f"copied={row.copied_rows:5} [{status}]"
        )
    for w in report.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    return 0


def _cmd_restore(args) -> int:
    from pathlib import Path

    from agent.storage.profile_backup import restore_profile_backup
    from intellect_cli.config import load_config

    config = load_config()
    path = Path(args.archive).expanduser()
    try:
        report = restore_profile_backup(path, config=config, dry_run=args.dry_run)
    except Exception as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    mode = "dry-run" if report.dry_run else "apply"
    print(f"Archive: {report.archive_path}")
    print(f"Mode:    {mode}")
    print(f"Backend: {report.manifest.get('storage_backend')}")
    for arc in report.restored:
        print(f"  restore: {arc}")
    for arc in report.skipped:
        print(f"  skip:    {arc}")
    for w in report.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    return 0


def _cmd_backup_inspect(args) -> int:
    import json
    import tarfile
    from pathlib import Path

    path = Path(args.archive).expanduser()
    if not path.is_file():
        print(f"Not found: {path}", file=sys.stderr)
        return 1
    try:
        with tarfile.open(path, "r:gz") as tar:
            member = tar.getmember("manifest.json")
            data = tar.extractfile(member)
            if data is None:
                print("manifest.json missing", file=sys.stderr)
                return 1
            manifest = json.loads(data.read().decode("utf-8"))
    except Exception as exc:
        print(f"Failed to read backup: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


def cli_main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="intellect db")
    register_cli(parser)
    args = parser.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn is None:
        parser.print_help()
        return 0
    return int(fn(args) or 0)
