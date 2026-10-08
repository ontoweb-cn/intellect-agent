#!/usr/bin/env bash
# Bump version across all files that carry a version number.
#
# Usage:
#   ./packaging/scripts/bump-version.sh 0.6.6
#   ./packaging/scripts/bump-version.sh 0.6.6 --dry-run
#
set -euo pipefail

NEW_VERSION="${1:?Usage: $0 <new_version> [--dry-run]}"
DRY_RUN=0
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

OLD_VERSION="$(grep -E '^version\s*=' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"

RELEASE_DATE="$(date +%Y.%-m.%-d)"

log() { printf '\e[1;33m[bump]\e[0m %s\n' "$*"; }
apply() {
    local file="$1" pattern="$2" replacement="$3" soft="${4:-0}"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        if [[ -f "$file" ]] && ! grep -q "${pattern}" "$file"; then
            if [[ "$soft" -eq 1 ]]; then
                log "STALE (soft): pattern not found in $file — carrier needs manual refresh ($pattern)"
            else
                log "WARN: pattern not found in $file — carrier drifted? ($pattern)"
            fi
        fi
        log "Would update: $file ($pattern → $replacement)"
        return
    fi
    if [[ -f "$file" ]]; then
        # Fail loudly when the pattern is missing: a silent no-op is how the
        # compose example drifted three releases behind (0.6.3 while pyproject
        # was already 0.7.x) — sed succeeds with zero substitutions.
        # soft=1 downgrades to a warning for carriers whose refresh needs a
        # release-process decision (see the homebrew / artifacts.yaml entries).
        if ! grep -q "${pattern}" "$file"; then
            if [[ "$soft" -eq 1 ]]; then
                log "STALE (soft, skipped): $file — carrier needs manual refresh ($pattern)"
                return
            fi
            log "ERROR: pattern not found in $file — carrier drifted, fix the pattern ($pattern)"
            exit 1
        fi
        if command -v gsed >/dev/null 2>&1; then
            gsed -i.bak -e "s/${pattern}/${replacement}/g" "$file" && rm -f "${file}.bak"
        else
            sed -i.bak -e "s/${pattern}/${replacement}/g" "$file" && rm -f "${file}.bak"
        fi
        log "Updated: $file"
    else
        log "Skipped (not found): $file"
    fi
}

log "Bumping version: ${OLD_VERSION} → ${NEW_VERSION}"
log "Release date: ${RELEASE_DATE}"
echo

# Python package versions
apply "pyproject.toml" \
    "version = \"${OLD_VERSION}\"" \
    "version = \"${NEW_VERSION}\""

# NOTE: intellect_cli/__init__.py no longer carries literals — __version__ /
# __release_date__ resolve dynamically (pyproject → importlib.metadata → git).

# ACP registry
apply "acp_registry/agent.json" \
    "\"version\": \"${OLD_VERSION}\"" \
    "\"version\": \"${NEW_VERSION}\""

# Homebrew formula — SOFT: the formula still points at the v0.6.4 Gitee
# tarball and refreshing it requires knowing which release attachments the
# (lightweight-tag) releases actually publish. Surfaced as STALE until the
# release process decides; bump continues.
apply "packaging/homebrew/intellect-agent.rb" \
    "/tag/v${OLD_VERSION}" \
    "/tag/v${NEW_VERSION}" 1

# Docker compose example
apply "packaging/docker/docker-compose.example.yml" \
    "intellect-agent:${OLD_VERSION}" \
    "intellect-agent:${NEW_VERSION}"

# Dockerfile version labels (injected by CI via build-args; the ARG defaults
# are the local-build fallback and must track the release)
apply "Dockerfile" \
    "ARG INTELLECT_VERSION=${OLD_VERSION}" \
    "ARG INTELLECT_VERSION=${NEW_VERSION}"
apply "Dockerfile" \
    "ARG INTELLECT_RUST_VERSION=${OLD_VERSION}" \
    "ARG INTELLECT_RUST_VERSION=${NEW_VERSION}"

# Root compose default image (CI-published tag)
apply "docker-compose.yml" \
    "ontoweb/intellect-agent:${OLD_VERSION}" \
    "ontoweb/intellect-agent:${NEW_VERSION}"

# Artifact manifest — SOFT: python_semver is stuck at 0.6.3 in-tree; whether
# release tooling regenerates or consumes this file needs a maintainer call.
apply "packaging/manifests/artifacts.yaml" \
    "python_semver: \"${OLD_VERSION}\"" \
    "python_semver: \"${NEW_VERSION}\"" 1

echo
log "Version bump complete."
log "Next steps:"
log "  1. Review changes: git diff"
log "  2. Update lockfile: uv lock"
log "  3. Commit: git commit -am 'release: v${NEW_VERSION}'"
log "  4. Tag: git tag v${NEW_VERSION}"
log "  5. Push: git push --follow-tags"
echo
