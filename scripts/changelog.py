#!/usr/bin/env python3
"""Generate a structured changelog from git history between two tags.

Groups commits by conventional-commit type (feat/fix/perf/refactor/docs/…)
and outputs a release-ready markdown changelog.

Usage:
    python scripts/changelog.py                    # since last tag
    python scripts/changelog.py v0.6.5..v0.6.6    # between two tags
    python scripts/changelog.py --since v0.6.5     # since a specific tag
    python scripts/changelog.py --all              # all tagged releases
"""

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Conventional commit pattern: type(scope)?!?: description
# Also handles merge commits and squashed PR titles
CONVENTIONAL_RE = re.compile(
    r'^(?P<type>feat|fix|perf|refactor|docs|style|test|chore|ci|build|revert)'
    r'(?:\((?P<scope>[^)]+)\))?'
    r'(?P<breaking>!)?: '
    r'(?P<description>.+)$',
    re.IGNORECASE,
)

# Emoji + label for each type
TYPE_META = {
    "feat": ("✨", "Features"),
    "fix": ("🐛", "Bug Fixes"),
    "perf": ("⚡", "Performance"),
    "refactor": ("♻️", "Refactoring"),
    "docs": ("📝", "Documentation"),
    "style": ("🎨", "Style"),
    "test": ("✅", "Tests"),
    "chore": ("🔧", "Chores"),
    "ci": ("👷", "CI/CD"),
    "build": ("📦", "Build"),
    "revert": ("⏪", "Reverts"),
}

# Co-authors to credit (extracted from Co-Authored-By trailers)
CO_AUTHOR_RE = re.compile(r'^Co-Authored-By:\s*(.+?)\s*<(.+?)>', re.IGNORECASE | re.MULTILINE)


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT)] + list(args),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"git error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def get_tags() -> list[str]:
    """Return all version tags sorted chronologically."""
    raw = run_git("tag", "--sort=creatordate", "-l", "v*")
    return [t for t in raw.split("\n") if t]


def get_commits_between(from_ref: str, to_ref: str) -> list[str]:
    """Get commit subjects between two refs, one per line."""
    range_spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
    raw = run_git("log", "--no-merges", "--format=%s%n%-(trailers:only)", range_spec)
    return raw.split("\n")


def classify_commit(subject: str) -> tuple[str, str, bool, str]:
    """Classify a commit subject line. Returns (type, description, is_breaking, scope)."""
    match = CONVENTIONAL_RE.match(subject.strip())
    if match:
        return (
            match.group("type").lower(),
            match.group("description").strip(),
            bool(match.group("breaking")),
            match.group("scope") or "",
        )
    # Fallback: try to classify by prefix
    lower = subject.lower().strip()
    if lower.startswith("fix") or lower.startswith("bug"):
        return ("fix", subject, False, "")
    if lower.startswith("add") or lower.startswith("new") or lower.startswith("implement"):
        return ("feat", subject, False, "")
    return ("chore", subject, False, "")


def generate_changelog(from_tag: str | None, to_tag: str) -> str:
    """Generate a changelog entry for a single release."""
    from_ref = from_tag if from_tag else f"{to_tag}~1"
    commits = get_commits_between(from_ref, to_tag)

    groups: dict[str, list[str]] = defaultdict(list)
    breaking_count = 0

    for commit in commits:
        if not commit.strip() or commit.startswith("Co-Authored-By:"):
            continue
        typ, desc, breaking, scope = classify_commit(commit)
        scope_prefix = f"**{scope}**: " if scope else ""
        entry = f"- {scope_prefix}{desc}"
        if breaking:
            entry += " 💥 BREAKING"
            breaking_count += 1
        groups[typ].append(entry)

    if not any(groups.values()):
        return ""

    lines = [f"## {to_tag}", ""]
    if breaking_count:
        lines.append(f"> ⚠️ {breaking_count} breaking change(s) in this release")
        lines.append("")

    for typ in ("feat", "fix", "perf", "refactor", "docs", "style", "test", "ci", "build", "chore", "revert"):
        entries = groups.get(typ, [])
        if not entries:
            continue
        emoji, label = TYPE_META.get(typ, ("📌", typ.capitalize()))
        lines.append(f"### {emoji} {label}")
        for entry in sorted(set(entries)):  # dedup
            lines.append(entry)
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate structured changelog from git history")
    parser.add_argument("range", nargs="?", help="Git range (e.g., v0.6.5..v0.6.6)")
    parser.add_argument("--since", help="Generate changelog since this tag")
    parser.add_argument("--all", action="store_true", help="Generate changelog for all releases")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    args = parser.parse_args()

    if args.all:
        tags = get_tags()
        if len(tags) < 2:
            print("Need at least 2 tags for changelog generation", file=sys.stderr)
            sys.exit(1)
        changelogs = []
        for i in range(1, len(tags)):
            cl = generate_changelog(tags[i - 1], tags[i])
            if cl:
                changelogs.append(cl)
        output = "\n\n".join(changelogs)
    elif args.range:
        parts = args.range.split("..")
        if len(parts) != 2:
            print("Range must be FROM..TO (e.g., v0.6.5..v0.6.6)", file=sys.stderr)
            sys.exit(1)
        output = generate_changelog(parts[0], parts[1])
    elif args.since:
        output = generate_changelog(args.since, "HEAD")
    else:
        tags = get_tags()
        if not tags:
            print("No tags found", file=sys.stderr)
            sys.exit(1)
        output = generate_changelog(tags[-1], "HEAD")

    if not output.strip():
        print("No conventional commits found in range", file=sys.stderr)
        sys.exit(0)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
        print(f"Changelog written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
