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
    local file="$1" pattern="$2" replacement="$3"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "Would update: $file ($pattern → $replacement)"
        return
    fi
    if [[ -f "$file" ]]; then
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

apply "intellect_cli/__init__.py" \
    "__version__ = \"${OLD_VERSION}\"" \
    "__version__ = \"${NEW_VERSION}\""

apply "intellect_cli/__init__.py" \
    "__release_date__ = \"[0-9.]*\"" \
    "__release_date__ = \"${RELEASE_DATE}\""

# ACP registry
apply "acp_registry/agent.json" \
    "\"version\": \"${OLD_VERSION}\"" \
    "\"version\": \"${NEW_VERSION}\""

# Homebrew formula
apply "packaging/homebrew/intellect-agent.rb" \
    "/tag/v${OLD_VERSION}" \
    "/tag/v${NEW_VERSION}"

# Docker compose example
apply "packaging/docker/docker-compose.example.yml" \
    "intellect-agent:${OLD_VERSION}" \
    "intellect-agent:${NEW_VERSION}"

# Artifact manifest
if [[ -f "packaging/manifests/artifacts.yaml" ]]; then
    apply "packaging/manifests/artifacts.yaml" \
        "python_semver: \"${OLD_VERSION}\"" \
        "python_semver: \"${NEW_VERSION}\""
fi

echo
log "Version bump complete."
log "Next steps:"
log "  1. Review changes: git diff"
log "  2. Update lockfile: uv lock"
log "  3. Commit: git commit -am 'release: v${NEW_VERSION}'"
log "  4. Tag: git tag v${NEW_VERSION}"
log "  5. Push: git push --follow-tags"
echo
