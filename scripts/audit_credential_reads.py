#!/usr/bin/env python3
"""Credential-read audit for the multiplex security prerequisite (MP-01 / P0-4).

Scans ``agent/ gateway/ plugins/ tools/`` for raw credential reads:

- ``os.environ.get(...)`` / ``os.getenv(...)`` / ``os.environ[...]``

and classifies each hit as ``global-ok`` (process-global by design),
``credential`` (key-shaped — hard migration target), or ``tuning`` (knobs
like BASE_URL/TIMEOUT — migrated on touch).

**Known scan boundaries** (tracked elsewhere, do not assume full coverage):
1. Whole-environment passing — ``env=dict(os.environ)`` into subprocesses —
   is NOT detected here; that is the multiplex subprocess-env seam, tracked
   in the MP-00 audit (plan §主题H / deep-dive §10).
2. Direct ``.env`` file loads outside ``intellect_cli.config.load_env`` are
   not scanned (the original docstring promised this; environ reads only).
3. Test files and ``scripts/`` are excluded by design.

The CI gate (``--check``, wired in lint.yml) keys on **(file, env) pairs** —
NOT line numbers — so routine edits that shift lines never false-fail; only
genuinely new credential-shaped reads do. Baseline:
``docs/plans/2026-08-31-credential-audit.csv`` (frozen 2026-08-31; refresh
with ``--update-baseline`` only after a deliberate cleanup PR).

Exit code 1 when new credential hits appear that are absent from the baseline.

Usage:
    python scripts/audit_credential_reads.py --check   # CI gate vs baseline
    python scripts/audit_credential_reads.py           # print full report
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("agent", "gateway", "plugins", "tools")
BASELINE = REPO / "docs/plans/2026-08-31-credential-audit.csv"

# Raw-read patterns. Kept deliberately tight: only credential-ish calls.
PATTERNS = [
    re.compile(r"os\.environ\.get\(\s*['\"]([A-Z_][A-Z0-9_]{2,})['\"]"),
    re.compile(r"os\.getenv\(\s*['\"]([A-Z_][A-Z0-9_]{2,})['\"]"),
    re.compile(r"os\.environ\[\s*['\"]([A-Z_][A-Z0-9_]{2,})['\"]\s*\]"),
]

# Env names that are process-global by design (mirrors agent/secret_scope).
GLOBAL_OK_EXACT = {
    "INTELLECT_HOME", "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "PWD",
    "SHELL", "TMPDIR", "VIRTUAL_ENV", "PYTHONPATH", "SSL_CERT_FILE",
    "INTELLECT_PROFILE", "INTELLECT_CONFIG", "INTELLECT_ENV",
    "INTELLECT_MAX_ITERATIONS", "INTELLECT_MAX_TOKENS", "INTELLECT_API_TIMEOUT",
    "INTELLECT_REDACT_SECRETS", "INTELLECT_NOUS_TIMEOUT_SECONDS",
    "INTELLECT_CRON_TIMEOUT", "INTELLECT_MODEL", "INTELLECT_KANBAN_DB",
    "INTELLECT_KANBAN_WORKSPACES_ROOT", "INTELLECT_KANBAN_BOARD",
    "TERMINAL_CWD", "INTELLECT_SESSION_SOURCE",
}
GLOBAL_OK_PREFIX = (
    "INTELLECT_KANBAN_", "INTELLECT_TELEGRAM_", "TERMINAL_",
    "PYTHON_", "PYTHONDONTWRITE", "UV_", "CARGO_", "CI_", "GITHUB_",
    "RUNNER_", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
)

# Test files never ship credentials and mock env heavily.
TEST_FILE_RE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|conftest\.py$)")


def is_global_ok(name: str) -> bool:
    return name in GLOBAL_OK_EXACT or any(name.startswith(p) for p in GLOBAL_OK_PREFIX)


# Credential-shaped names — the hard migration target for MP-01.
_CREDENTIAL_RE = re.compile(
    r"(API_KEY|APIKEY|_TOKEN|TOKEN_|_SECRET|SECRET_|PASSWORD|_KEY$|CLIENT_ID$)",
    re.IGNORECASE,
)
# Tuning knobs that merely CONTAIN a credential substring (e.g. MAX_TOKENS
# matches _TOKEN) — not secrets, tracked in the baseline as tuning.
_FALSE_CREDENTIAL = re.compile(
    r"(MAX_TOKENS|TOKEN_BUDGET|_TOKEN_LIMIT|TOKENS_|_TOKENS$|"
    r"TOKEN_EFFICIENCY|TOKEN_THRESHOLD)",
    re.IGNORECASE,
)


def classify(name: str) -> str:
    if is_global_ok(name):
        return "global-ok"
    if _CREDENTIAL_RE.search(name) and not _FALSE_CREDENTIAL.search(name):
        return "credential"
    # BASE_URL / TIMEOUT / WORKERS / MODE / PATH / ENABLED … tuning knobs:
    # profile-safe in practice (no cross-profile secret risk), migrated on touch.
    return "tuning"


def scan() -> list[dict]:
    hits = []
    for root in SCAN_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if TEST_FILE_RE.search(rel) or "__pycache__" in rel:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if "secret_scope" in line or "get_secret" in line:
                    continue
                for pat in PATTERNS:
                    m = pat.search(line)
                    if m:
                        name = m.group(1)
                        hits.append(
                            {
                                "file": rel,
                                "line": lineno,
                                "env": name,
                                "kind": classify(name),
                            }
                        )
                        break
    return hits


def load_baseline() -> set:
    """Gate key set: (file, env) pairs — deliberately line-free.

    Line numbers shift on any upstream edit, which would flag existing
    reads as "new" and fail CI spuriously. The gate keys on
    (file, env-name) so the baseline survives routine refactors; the
    ``line`` column in the CSV is informational only.
    """
    if not BASELINE.exists():
        return set()
    with BASELINE.open(encoding="utf-8", newline="") as f:
        return {(row["file"], row["env"]) for row in csv.DictReader(f)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="CI gate: fail on new raw hits")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()

    hits = scan()
    raw = [h for h in hits if h["kind"] == "credential"]
    tuning = [h for h in hits if h["kind"] == "tuning"]

    if args.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        with BASELINE.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["file", "line", "env", "kind"])
            w.writeheader()
            w.writerows(hits)
        print(f"baseline updated: {len(hits)} entries ({len(raw)} credential) -> {BASELINE}")
        return 0

    print(f"scanned: {len(hits)} hits, {len(raw)} credential, {len(tuning)} tuning")
    for h in raw:
        print(f"  CRED {h['file']}:{h['line']} {h['env']}")

    if args.check:
        baseline = load_baseline()
        # Removing lines is fine (subset of baseline); a NEW (file, env) pair
        # is the only fail condition — line shifts cannot false-positive.
        new = [h for h in raw if (h["file"], h["env"]) not in baseline]
        if new:
            print(f"\nGATE FAIL: {len(new)} new credential-shaped raw read(s) not in baseline:")
            for h in new:
                print(f"  {h['file']}:{h['line']} {h['env']}")
            print("migrate via agent.secret_scope.get_secret() or extend the baseline")
            return 1
        print("GATE OK: no new credential-shaped raw reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
