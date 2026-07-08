"""1Password secret source — resolve credentials via the ``op`` CLI (HP-404).

Usage::

    from agent.secret_sources.onepassword import fetch_onepassword_secrets, check_op_cli
    if check_op_cli():
        result = fetch_onepassword_secrets(vault="intellect")
        if result.ok:
            for key, value in result.secrets.items():
                os.environ.setdefault(key, value)

Requires the 1Password CLI (`op`) v2+ installed and a signed-in account.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

OP_BINARY = "op"


@dataclass
class FetchResult:
    """Result of fetching secrets from 1Password."""

    secrets: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    source: str = "1Password"

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def find_op() -> Optional[Path]:
    """Locate the ``op`` binary.  Returns None when not found."""
    for candidate in (
        Path.home() / ".local" / "bin" / "op",
        Path("/usr/local/bin/op"),
        Path("/opt/homebrew/bin/op"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    # Fall back to PATH lookup
    import shutil
    found = shutil.which("op")
    return Path(found) if found else None


def check_op_cli() -> bool:
    """Check whether ``op`` is installed and the user is signed in."""
    op = find_op()
    if op is None:
        return False
    try:
        result = subprocess.run(
            [str(op), "whoami"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_op(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run an ``op`` command.  Returns (returncode, stdout, stderr)."""
    op = find_op()
    if op is None:
        return -1, "", "op CLI not found"
    try:
        result = subprocess.run(
            [str(op), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "op CLI not found"
    except subprocess.TimeoutExpired:
        return -1, "", "op CLI timed out"
    except Exception as exc:
        return -1, "", str(exc)


def _list_items(vault: Optional[str] = None) -> list[dict[str, Any]]:
    """List 1Password items in a vault.  Returns empty list on failure."""
    args = ["item", "list", "--format", "json"]
    if vault:
        args.extend(["--vault", vault])
    rc, stdout, stderr = _run_op(args)
    if rc != 0:
        logger.debug("op item list failed: %s", stderr)
        return []
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return []


def _read_item(item_name: str, vault: Optional[str] = None) -> dict[str, Any]:
    """Read a single 1Password item.  Returns empty dict on failure."""
    args = ["item", "get", item_name, "--format", "json"]
    if vault:
        args.extend(["--vault", vault])
    rc, stdout, stderr = _run_op(args)
    if rc != 0:
        logger.debug("op item get '%s' failed: %s", item_name, stderr)
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {}


def fetch_onepassword_secrets(
    vault: Optional[str] = None,
    *,
    item_prefix: Optional[str] = None,
    label: str = "credential",
) -> FetchResult:
    """Fetch secrets from 1Password.

    Scans items in the given vault (or all vaults).  Each item that has a
    ``label`` field matching ``label`` is treated as a credential: the item
    title becomes the env-var name, and the first field of type
    ``concealed`` (or ``password``) becomes the value.

    When ``item_prefix`` is set, only items whose title starts with that
    prefix are considered (e.g. ``"intellect_"``).
    """
    result = FetchResult()

    if not check_op_cli():
        result.errors.append("1Password CLI not available or not signed in")
        return result

    items = _list_items(vault)
    if not items:
        result.errors.append(f"no items found in vault '{vault or 'all'}'")
        return result

    for item_summary in items:
        title = item_summary.get("title", "")
        if item_prefix and not title.startswith(item_prefix):
            continue

        detail = _read_item(title, vault)
        if not detail:
            result.errors.append(f"failed to read item '{title}'")
            continue

        # Extract secret value from the item's fields
        value = _extract_credential_value(detail, label)
        if value:
            result.secrets[title] = value
        else:
            logger.debug("op: item '%s' has no credential field with label '%s'",
                         title, label)

    if not result.secrets and not result.errors:
        result.errors.append(
            f"no credential items found (prefix='{item_prefix or ''}', label='{label}')"
        )

    return result


def _extract_credential_value(item: dict[str, Any], label: str) -> Optional[str]:
    """Extract a credential value from a 1Password item dict.

    Looks for fields where ``label`` matches (case-insensitive), preferring
    ``concealed`` type, then ``password`` purpose, then any field with a
    ``value`` key.
    """
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        return None

    best = None
    for field in fields:
        if not isinstance(field, dict):
            continue
        flabel = (field.get("label") or "").lower()
        if label.lower() not in flabel:
            continue
        ftype = (field.get("type") or "").upper()
        val = field.get("value") or field.get("reference") or ""
        if not val:
            continue
        # Prefer concealed/password fields
        if ftype in ("CONCEALED", "PASSWORD"):
            return str(val)
        if best is None:
            best = str(val)
    return best
