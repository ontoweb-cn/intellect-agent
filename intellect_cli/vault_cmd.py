"""``intellect vault …`` — vault build scheduling helpers."""

from __future__ import annotations

import argparse
import json
import sys

from intellect_cli.vault_build import load_vault_config, run_scheduled_vault_tick


def register_subparsers(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="vault_action", required=True)
    tick = sub.add_parser("tick", help="Run one scheduled vault build tick")
    tick.add_argument(
        "--force",
        action="store_true",
        help="Build all eligible vaults regardless of schedule/mtime",
    )
    tick.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def vault_command(args: argparse.Namespace) -> int:
    if args.vault_action == "tick":
        vcfg = load_vault_config()
        result = run_scheduled_vault_tick(vcfg, force=bool(args.force))
        if args.json:
            payload = {
                "ok": result.ok,
                "ran": result.ran,
                "skipped_reason": result.skipped_reason,
                "built": result.built,
                "skipped": result.skipped,
                "failed": result.failed,
                "summary": result.summary,
                "details": [
                    {"scope": d.scope, "scope_id": d.scope_id, "action": d.action, "reason": d.reason}
                    for d in result.details
                ],
            }
            print(json.dumps(payload, indent=2))
        else:
            if result.skipped_reason:
                print(f"Vault tick skipped: {result.skipped_reason}")
            elif result.summary:
                print(result.summary)
            else:
                print("Vault tick completed.")
            if result.failed:
                print(f"Failures: {result.failed}", file=sys.stderr)
        return 0 if result.ok else 1
    return 1
