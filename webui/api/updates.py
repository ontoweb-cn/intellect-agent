"""
Intellect Web UI -- Self-update checker.

Checks if the webui and intellect-agent git repos are behind their upstream
branches. Results are cached server-side (30-min TTL) so git fetch runs
at most twice per hour regardless of client count.

Skips repos that are not git checkouts (e.g. Docker baked images where
.git does not exist).
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

from api.config import REPO_ROOT, STREAMS, STREAMS_LOCK

# Lazy -- may be None if agent not found
try:
    from api.config import _AGENT_DIR
except ImportError:
    _AGENT_DIR = None

_update_cache = {'webui': None, 'agent': None, 'checked_at': 0, 'include_agent': True}
_SUMMARY_CACHE_MAX = 16
_summary_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_check_in_progress = False
_apply_lock = threading.Lock()   # prevents concurrent stash/pull/pop on same repo
CACHE_TTL = 1800  # 30 minutes
_GIT_DIAGNOSTIC_MAX_CHARS = 300
_CREDENTIAL_IN_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s'\"]+)@")
_GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_QUERY_SECRET_RE = re.compile(r"([?&](?:access_token|token|password|auth|key)=)[^&\s'\"]+", re.IGNORECASE)


def _sanitize_git_diagnostic(output: str, *, limit: int = _GIT_DIAGNOSTIC_MAX_CHARS) -> str:
    """Return a user-facing git diagnostic with credentials removed.

    Git can echo remote URLs in failure output.  Keep the actionable error text,
    but strip URL userinfo, common GitHub token shapes, and secret-looking query
    parameter values before any message reaches the update-check API/UI.
    """
    if not output:
        return ""
    sanitized = _CREDENTIAL_IN_URL_RE.sub(r"\1<redacted>@", str(output))
    sanitized = _GITHUB_TOKEN_RE.sub("<redacted>", sanitized)
    sanitized = _QUERY_SECRET_RE.sub(r"\1<redacted>", sanitized)
    sanitized = sanitized.strip()
    if len(sanitized) > limit:
        sanitized = sanitized[:limit].rstrip() + "…"
    return sanitized


def _active_stream_count() -> int:
    """Return the current in-memory chat stream count.

    Self-update schedules an in-process re-exec after git pull/reset.  That is
    restart-equivalent for live streams, even when systemd does not see a unit
    restart.  Refuse update/force-update while a stream exists so a browser
    update click cannot recreate the pending-message loss class fixed in #1543.
    """
    with STREAMS_LOCK:
        return len(STREAMS)


def _restart_blocked_response(target: str, active_streams: int) -> dict:
    plural = "s" if active_streams != 1 else ""
    return {
        'ok': False,
        'message': (
            f'Cannot update {target} while {active_streams} active chat stream{plural} '
            'is running. Wait for the response to finish, then retry the update.'
        ),
        'target': target,
        'restart_blocked': True,
        'active_streams': active_streams,
    }


def _run_git(args, cwd, timeout=10):
    """Run a git command and return (useful output, ok).

    On failure, returns stderr (or stdout as fallback) so callers can
    surface actionable git error messages instead of empty strings.
    """
    try:
        r = subprocess.run(
            ['git'] + args, cwd=str(cwd), capture_output=True,
            text=True, timeout=timeout,
            encoding='utf-8', errors='replace',
        )
        # On non-UTF-8 locales (e.g. Chinese Windows GBK), a binary git
        # output that fails to decode used to leave r.stdout = None and crash
        # the whole import with AttributeError. Guard against None defensively.
        stdout = (r.stdout or '').strip()
        stderr = (r.stderr or '').strip()
        if r.returncode == 0:
            return stdout, True
        return stderr or stdout or f"git exited with status {r.returncode}", False
    except subprocess.TimeoutExpired as exc:
        detail = (getattr(exc, 'stderr', None) or getattr(exc, 'stdout', None) or '').strip()
        return detail or f"git {' '.join(args)} timed out after {timeout}s", False
    except FileNotFoundError:
        return 'git executable not found', False
    except OSError as exc:
        return f'git failed to start: {exc}', False


def _dirty_suffix(path: Path, timeout=1) -> str:
    """Return a best-effort ``-dirty`` suffix without blocking version display."""
    out, ok = _run_git(['diff-index', '--quiet', 'HEAD', '--'], path, timeout=timeout)
    if ok:
        return ""
    # diff-index exits 1 with no output for a dirty tree; _run_git may return
    # a fallback message when both stdout and stderr are empty, which also
    # indicates a dirty tree (not a real error). Timeouts and real git
    # failures include a diagnostic; skip the suffix for those.
    if not out or out.startswith("git exited with status"):
        return "-dirty"
    return ""


def _describe_git_version(path: Path, *, timeout=5, dirty_timeout=1) -> str | None:
    """Return a fast git version string for a checkout, if available."""
    out, ok = _run_git(['describe', '--tags', '--always'], path, timeout=timeout)
    if not (ok and out):
        return None
    return out + _dirty_suffix(path, timeout=dirty_timeout)


def _detect_webui_version() -> str:
    """Detect the running WebUI version from git or a baked-in fallback file.

    Resolution order:
      1. ``git describe --tags --always --dirty`` — works in any git checkout.
         Returns the exact tag on tagged commits (e.g. ``v0.50.124``), a
         post-tag descriptor between releases (e.g. ``v0.50.124-1-ge91325d``),
         or a bare SHA when no tags exist (shallow clones, fresh forks).
      2. ``api/_version.py`` — a fallback written by the Docker / CI release
         workflow when ``.git`` is not present in the image.  Expected to define
         ``__version__ = 'vX.Y.Z'``.
      3. ``'unknown'`` — last resort; displayed as-is in the settings badge.
    """
    # Timeout capped at 3s: git describe on a healthy local repo is <50ms;
    # a 10s stall on import (NFS-mounted .git, broken git binary) is unacceptable.
    # REPO_ROOT is webui/ inside the agent project; .git lives in REPO_ROOT.parent
    out = _describe_git_version(REPO_ROOT.parent)
    if out:
        return out

    # Docker / baked-image fallback: api/_version.py written by CI at build time.
    # Parse with regex rather than exec() — the file holds exactly one assignment
    # and regex is sufficient; exec() on a build artifact is an unnecessary surface.
    version_file = REPO_ROOT / 'api' / '_version.py'
    if version_file.exists():
        try:
            import re as _re
            m = _re.search(
                r"""__version__\s*=\s*['"]([^'"]+)['"]""",
                version_file.read_text(encoding='utf-8'),
            )
            if m:
                return m.group(1)
        except Exception:
            pass

    return 'unknown'


def _detect_agent_version_from_metadata() -> str | None:
    """Return the pip-installed intellect-agent version, if any.

    Docker images pre-build a venv with ``intellect-agent[all]`` but often have
    no agent *source* checkout on disk — ``_discover_agent_dir()`` is None and
    git-based detection fails.  ``importlib.metadata`` reads the installed
    distribution version instead.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        v = version("intellect-agent")
        return v.strip() if v and v.strip() else None
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def _detect_agent_version() -> str:
    """Detect the running Intellect Agent version for UI display."""
    if _AGENT_DIR is not None:
        version_file = Path(_AGENT_DIR) / "VERSION"
        try:
            if version_file.exists():
                text = version_file.read_text(encoding='utf-8').strip()
                if text:
                    return text
        except Exception:
            pass

        # Fallback: infer from git describe when the checkout exists but no VERSION
        # file is available (common in source checkouts and developer environments).
        if Path(_AGENT_DIR).exists():
            # Symmetric with _detect_webui_version() above — `--dirty` flags a
            # locally-modified checkout so operators can see when their agent has
            # uncommitted changes vs a clean tag. Per Opus advisor on stage-293.
            out, ok = _run_git(['describe', '--tags', '--always', '--dirty'], _AGENT_DIR, timeout=3)
            if ok and out:
                return out

    meta = _detect_agent_version_from_metadata()
    if meta:
        return meta

    return 'not detected'


# Resolved once at import time — tags cannot change without a process restart.
WEBUI_VERSION: str = _detect_webui_version()
AGENT_VERSION: str = _detect_agent_version()


def _normalize_remote_url(remote_url):
    """Return the browser-facing repository URL for update compare links.

    Git remotes may be HTTPS or SSH and may include a literal ``.git`` suffix.
    Strip only that literal suffix — never use ``str.rstrip('.git')`` because it
    treats the argument as a character set and can truncate ``intellect-webui`` to
    ``intellect-webu``.
    """
    if not remote_url:
        return remote_url
    remote_url = remote_url.strip()
    if remote_url.startswith('git@'):
        remote_url = remote_url.replace(':', '/', 1).replace('git@', 'https://', 1)
    remote_url = remote_url.rstrip('/')
    if remote_url.endswith('.git'):
        remote_url = remote_url[:-4]
    return remote_url.rstrip('/')


def _build_compare_url(repo_url, current_sha, latest_sha):
    """Return a safe browser compare URL, or None when any piece is missing."""
    if not (repo_url and current_sha and latest_sha):
        return None
    parsed = urlparse(repo_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    return f"{repo_url}/compare/{current_sha}...{latest_sha}"


def _split_remote_ref(ref):
    """Split 'origin/branch-name' into ('origin', 'branch-name').

    Returns (None, ref) if ref contains no slash.
    """
    if '/' not in ref:
        return None, ref
    remote, branch = ref.split('/', 1)
    return remote, branch


def _detect_default_branch(path):
    """Detect the remote default branch (master or main)."""
    out, ok = _run_git(['symbolic-ref', 'refs/remotes/origin/HEAD'], path)
    if ok and out:
        # refs/remotes/origin/master -> master
        return out.split('/')[-1]
    # Fallback: try master, then main
    for branch in ('master', 'main'):
        _, ok = _run_git(['rev-parse', '--verify', f'origin/{branch}'], path)
        if ok:
            return branch
    return 'master'


def _release_tags(path):
    """Return release tags newest-first, using the repo's version-sort order."""
    out, ok = _run_git(['tag', '--list', 'v*', '--sort=-v:refname'], path)
    if not (ok and out):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _current_release_tag(path):
    """Return the latest release tag reachable from HEAD, if one exists."""
    out, ok = _run_git(['describe', '--tags', '--abbrev=0'], path)
    return out if ok and out else None


def _release_gap(tags, current, latest):
    """Count release tags between current and latest in a newest-first list."""
    if not latest or current == latest:
        return 0
    if current in tags:
        return tags.index(current)
    return 1


def _head_is_past_latest_tag(path, current_tag):
    """Return True when HEAD has moved past the latest reachable release tag.

    `git describe --tags --always` returns the bare tag name (e.g. ``v2026.5.16``)
    when HEAD is exactly on the tag, and a ``v2026.5.16-608-g1d22b9c2`` suffix
    when HEAD has moved 608 commits past it. Used by both the update check and
    the update apply path so they agree on which ref to advance to — see #2653
    (check side) and #2846 (apply side).
    """
    if not current_tag:
        return False
    full_desc, ok = _run_git(['describe', '--tags', '--always'], path)
    return bool(ok and full_desc and full_desc != current_tag)


def _head_contains_ref(path, ref):
    """Return True when ``ref`` is reachable from HEAD (HEAD already contains it).

    When a newer tag exists but HEAD already contains that commit, a positive
    tag gap is not an available update; applying the tag would move backwards
    or fail fast-forward. Use the commit graph to detect that state.
    """
    if not ref:
        return False
    _, ok = _run_git(['merge-base', '--is-ancestor', ref, 'HEAD'], path)
    return bool(ok)


def _can_fast_forward_to(path, ref):
    """Return True when ``ref`` is a descendant of HEAD (``git pull --ff-only`` can reach it)."""
    if not ref:
        return False
    _, ok = _run_git(['merge-base', '--is-ancestor', 'HEAD', ref], path)
    return bool(ok)


def _select_apply_compare_ref(path):
    """Return the same remote ref family that the update check reports.

    The update banner prefers published release tags when they exist. Applying
    an update must therefore advance to the latest release tag too; otherwise a
    checkout on a local/fork tracking branch can report release updates, pull a
    different branch that is already current, restart, and still remain behind.

    When HEAD is past the latest tag (the agent repo's day-to-day state between
    tagged releases), the check side falls through to the branch comparison via
    `_check_repo_release` returning None. The apply side must mirror that
    decision — otherwise we run `git pull --ff-only <latest-tag>` against a
    checkout that's already past the tag, no-op, restart, and the banner
    re-appears with the same N commits available. See #2846.
    """
    tags = _release_tags(path)
    if tags:
        latest_tag = tags[0]
        current_tag = _current_release_tag(path)
        behind = _release_gap(tags, current_tag, latest_tag)
        # Mirror the check side exactly: fall through to the branch comparison
        # whenever the checkout has already moved past the release tag that the
        # banner would otherwise advertise. The common case is behind == 0 and
        # HEAD is past its nearest tag, but main-tracking checkouts can also
        # have behind > 0 after fetching a newer tag that HEAD already contains
        # (#3140). In both cases applying the tag would no-op, move backwards,
        # or fail fast-forward; branch comparison is the truthful update path.
        if (
            behind == 0 and _head_is_past_latest_tag(path, current_tag)
        ) or (
            behind > 0 and _head_contains_ref(path, latest_tag)
        ) or (
            behind > 0 and not _can_fast_forward_to(path, latest_tag)
        ):
            pass
        else:
            return latest_tag

    upstream, ok = _run_git(['rev-parse', '--abbrev-ref', '@{upstream}'], path)
    if ok and upstream:
        return upstream

    branch = _detect_default_branch(path)
    return f'origin/{branch}'


def _check_repo_release(path, name):
    """Check if a git repo is behind its latest published release tag."""
    tags = _release_tags(path)
    if not tags:
        return None

    latest_tag = tags[0]
    current_tag = _current_release_tag(path)
    behind = _release_gap(tags, current_tag, latest_tag)

    # If behind == 0 but HEAD has moved past the tag (e.g. the agent repo
    # keeps committing to master between tagged releases), the release check
    # would report "Up to date" even though hundreds of commits are missing.
    # Fall through to _check_repo_branch so the real commit count is reported
    # instead. The same predicate is used by _select_apply_compare_ref so the
    # check and apply sides cannot drift again. See #2653 (check), #2846 (apply).
    if behind == 0 and _head_is_past_latest_tag(path, current_tag):
        return None

    # Users tracking main can already contain the newest fetched release tag
    # while their nearest reachable tag is older. A positive tag gap then means
    # only "there is a newer tag name", not "HEAD is behind that tag" (#3140).
    # Fall through to the branch check so the banner compares against the
    # configured upstream instead of advertising a tag that cannot fast-forward.
    if behind > 0 and _head_contains_ref(path, latest_tag):
        return None

    # Patch releases can land on a side branch while day-to-day installs track
    # main past an older tag. A positive tag-name gap then advertises an update
    # that `git pull --ff-only <latest-tag>` cannot reach.
    if behind > 0 and not _can_fast_forward_to(path, latest_tag):
        return None

    remote_url, _ = _run_git(['remote', 'get-url', 'origin'], path)
    remote_url = _normalize_remote_url(remote_url)

    return {
        'name': name,
        'behind': behind,
        # GitHub compare URLs accept tag names, and tag-to-tag links are the
        # clearest "what changed in this release?" view for operators.
        'current_sha': current_tag,
        'latest_sha': latest_tag,
        'branch': latest_tag,
        'repo_url': remote_url,
        'release_based': True,
        'current_version': current_tag,
        'latest_version': latest_tag,
    }


def _check_repo_branch(path, name, *, fetch=True):
    """Fallback: check if a git repo is behind its upstream branch."""

    # Fetch latest from origin (network call, cached by TTL)
    if fetch:
        _, fetch_ok = _run_git(['fetch', 'origin', '--quiet'], path, timeout=15)
        if not fetch_ok:
            return {'name': name, 'behind': 0, 'error': 'fetch failed'}

    # Use the current branch's upstream tracking branch, not the repo default.
    # This avoids false "N updates behind" alerts when the user is on a feature
    # branch and master/main has moved forward with unrelated commits.
    # If no upstream is set (brand-new local branch), fall back to the default branch.
    upstream, ok = _run_git(['rev-parse', '--abbrev-ref', '@{upstream}'], path)
    if ok and upstream:
        # upstream is like "origin/feat/foo" — use it directly in rev-list
        compare_ref = upstream
    else:
        branch = _detect_default_branch(path)
        compare_ref = f'origin/{branch}'

    # Count commits behind
    out, ok = _run_git(['rev-list', '--count', f'HEAD..{compare_ref}'], path)
    behind = int(out) if ok and out.isdigit() else 0

    # Get short SHAs for display.
    #
    # latest_sha = upstream tip (compare_ref). Always exists on github.com
    # because it is literally the commit `git fetch` just pulled.
    #
    # current_sha is trickier. The intuitive choice — local HEAD — breaks
    # the "What's new?" compare URL whenever HEAD is not a public commit:
    # unpushed work, dirty stage branches, forks, in-flight rebases, or
    # release-time merge commits whose SHA only lives in the maintainer's
    # checkout. We saw exactly this in #1579: a banner reporting "17 updates"
    # linked to /compare/<localHEAD>...<upstream> and 404'd because <localHEAD>
    # was never pushed to the canonical repo.
    #
    # The right base is the merge-base between HEAD and the upstream ref —
    # that's the most recent commit both sides agree on, and (because
    # `git fetch` succeeded above) it is guaranteed to be present upstream.
    # If a user is 17 commits behind with no local-only commits, merge-base
    # equals local HEAD and the URL is identical to what we shipped before;
    # if they ARE ahead with local-only commits, the URL still resolves to
    # the public history they share with upstream. If merge-base fails for
    # any reason (e.g. shallow clone where the bases diverge before the
    # cutoff), fall back to None so the JS link guard suppresses the link
    # rather than emitting a known-broken URL.
    mb_full, mb_ok = _run_git(['merge-base', 'HEAD', compare_ref], path)
    if mb_ok and mb_full:
        short, ok = _run_git(['rev-parse', '--short', mb_full], path)
        current = short if (ok and short) else None
    else:
        current = None
    latest, _ = _run_git(['rev-parse', '--short', compare_ref], path)

    # Get repo URL for "What's new?" link
    remote_url, _ = _run_git(['remote', 'get-url', 'origin'], path)
    remote_url = _normalize_remote_url(remote_url)

    return {
        'name': name,
        'behind': behind,
        'current_sha': current,
        'latest_sha': latest,
        'branch': compare_ref,
        'repo_url': remote_url,
        'compare_url': _build_compare_url(remote_url, current, latest),
    }


def _check_repo(path, name):
    """Check if a git repo is behind its latest release. Returns dict or None."""
    if path is None or not (path / '.git').exists():
        return None

    # Fetch tags first so update prompts track published releases, not every
    # development commit that lands on master/main after the latest release.
    #
    # --force is required because the WebUI is a release-tracking consumer:
    # it never pushes tags, so it should always defer to whatever the remote
    # says a release tag points to. Without --force, a remote re-tag (e.g.
    # after a squash-merge that re-points a release tag at a new SHA) jams
    # the update path indefinitely with "would clobber existing tag" errors.
    # See #2756.
    fetch_out, fetch_ok = _run_git(['fetch', 'origin', '--tags', '--force'], path, timeout=15)
    if not fetch_ok:
        release_info = _check_repo_release(path, name)
        message = 'fetch failed'
        if fetch_out:
            message = f'{message}: {_sanitize_git_diagnostic(fetch_out)}'
        if release_info is not None:
            release_info = dict(release_info)
            release_info['error'] = message
            release_info['stale_check'] = True
            return release_info
        return {
            'name': name,
            'behind': None,
            'error': message,
            'stale_check': True,
        }

    release_info = _check_repo_release(path, name)
    if release_info is not None:
        return release_info

    return _check_repo_branch(path, name, fetch=False)


def _ignored_agent_update_info() -> dict:
    """Return a stable update-check payload for intentionally ignored Agent updates."""
    return {'name': 'agent', 'behind': 0, 'ignored': True}


def check_for_updates(force=False, *, include_agent=True):
    """Return cached update status for webui and agent repos."""
    global _check_in_progress
    include_agent = bool(include_agent)
    with _cache_lock:
        if (
            not force
            and _update_cache.get('include_agent') == include_agent
            and time.time() - _update_cache['checked_at'] < CACHE_TTL
        ):
            return dict(_update_cache)
        if _check_in_progress:
            return dict(_update_cache)  # another thread is already checking
        _check_in_progress = True

    try:
        # Run checks outside the lock (network I/O)
        # REPO_ROOT is webui/ inside the agent project; .git lives in REPO_ROOT.parent
        webui_info = _check_repo(REPO_ROOT.parent, 'webui')
        agent_info = _check_repo(_AGENT_DIR, 'agent') if include_agent else _ignored_agent_update_info()

        with _cache_lock:
            _update_cache['webui'] = webui_info
            _update_cache['agent'] = agent_info
            _update_cache['checked_at'] = time.time()
            _update_cache['include_agent'] = include_agent
            return dict(_update_cache)
    finally:
        _check_in_progress = False


def _repo_path_for_update_target(target: str):
    if target == 'webui':
        # REPO_ROOT is webui/ inside the agent project; .git lives in REPO_ROOT.parent
        return REPO_ROOT.parent
    if target == 'agent':
        return _AGENT_DIR
    return None


def _commit_subjects_for_update(info: dict, *, limit: int = 24) -> list[str]:
    """Return commit subjects for an update range, if the local git refs exist."""
    subjects, _truncated = _commit_subjects_for_update_with_limit(info, limit=limit)
    return subjects


def _commit_subjects_for_update_with_limit(info: dict, *, limit: int = 24) -> tuple[list[str], bool]:
    """Return recent commit subjects plus whether the local list was capped."""
    if not isinstance(info, dict):
        return [], False
    target = info.get('name')
    if target not in ('webui', 'agent'):
        target = 'webui' if info.get('repo_url', '').endswith('intellect-webui') else target
    path = _repo_path_for_update_target(target)
    if path is None or not (Path(path) / '.git').exists():
        return [], False
    current = str(info.get('current_sha') or '').strip()
    latest = str(info.get('latest_sha') or '').strip()
    if not (current and latest):
        return [], False
    probe_limit = max(1, int(limit)) + 1
    out, ok = _run_git(['log', '--format=%s', f'{current}..{latest}', f'-n{probe_limit}'], path, timeout=5)
    if not ok or not out:
        return [], False
    subjects = [line.strip() for line in out.splitlines() if line.strip()]
    truncated = len(subjects) > limit
    return subjects[:limit], truncated


def _summary_cache_key(updates: dict, details: list[dict]) -> str:
    """Stable key for the exact update range being summarized."""
    payload = []
    for item in details:
        payload.append({
            'name': item.get('name'),
            'behind': item.get('behind'),
            'current_sha': item.get('current_sha'),
            'latest_sha': item.get('latest_sha'),
            'compare_url': item.get('compare_url'),
        })
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _clean_summary_bullet(line: str) -> str:
    line = re.sub(r'^\s*(?:[-*•]+|\d+[.)])\s*', '', str(line or '')).strip()
    line = re.sub(r'\s+', ' ', line)
    if not line:
        return ''
    if line[-1] not in '.!?':
        line += '.'
    return line[:240]


def _split_summary_category(line: str) -> tuple[str | None, str]:
    raw = str(line or '').strip()
    match = re.match(r'^\s*(?:[-*•]+|\d+[.)])?\s*(notice|what you(?:ll|\'ll| will) notice|user(?:s)? will notice|worth knowing|worth|note)\s*:\s*(.+)$', raw, re.I)
    if not match:
        return None, raw
    label = match.group(1).lower()
    category = 'worth' if label in {'worth knowing', 'worth', 'note'} else 'notice'
    return category, match.group(2)


def _unique_summary_bullets(items: list[str]) -> list[str]:
    seen = set()
    bullets = []
    for item in items:
        cleaned = _clean_summary_bullet(item)
        key = cleaned.lower()
        if cleaned and key not in seen:
            bullets.append(cleaned)
            seen.add(key)
    return bullets


def _summary_bullets_from_text(text: str, *, fallback_items: list[str]) -> list[str]:
    raw = str(text or '').strip()
    candidates = []
    for line in raw.splitlines():
        _category, body = _split_summary_category(line)
        cleaned = _clean_summary_bullet(body)
        if cleaned:
            candidates.append(cleaned)
    if len(candidates) <= 1 and raw:
        candidates = [_clean_summary_bullet(part) for part in re.split(r'(?<=[.!?])\s+', raw)]
        candidates = [item for item in candidates if item]
    if not candidates:
        candidates = [_clean_summary_bullet(item) for item in fallback_items]
    bullets = _unique_summary_bullets(candidates)
    return bullets or ['Updates are available.']


def _categorized_summary_bullets_from_text(text: str) -> tuple[list[str], list[str]]:
    notice_items: list[str] = []
    worth_items: list[str] = []
    for line in str(text or '').splitlines():
        category, body = _split_summary_category(line)
        if category == 'notice':
            notice_items.append(body)
        elif category == 'worth':
            worth_items.append(body)
        elif re.match(r'^\s*(?:[-*•]+|\d+[.)])?\s*[A-Za-z][A-Za-z ]{1,32}\s*:', str(line or '')):
            notice_items.append(body)
    return _unique_summary_bullets(notice_items), _unique_summary_bullets(worth_items)


def _fallback_update_bullets(details: list[dict]) -> list[str]:
    bullets = []
    for item in details:
        label = item.get('label') or item.get('name') or 'Intellect'
        behind = item.get('behind') or 0
        commits = item.get('commits') or []
        if commits:
            highlights = '; '.join(commits[:3])
            qualifier = 'recent updates' if item.get('commits_truncated') else 'updates'
            bullets.append(f"{label} has {behind} update(s), including {qualifier}: {highlights}.")
        else:
            bullets.append(f"{label} has {behind} update(s) available.")
    return bullets or ['Updates are available.']


def _worth_knowing_bullets(details: list[dict]) -> list[str]:
    items = []
    truncated = [item for item in details if item.get('commits_truncated') and item.get('commits_limit')]
    for item in truncated[:2]:
        label = item.get('label') or item.get('name') or 'Intellect'
        behind = item.get('behind') or 0
        limit = item.get('commits_limit') or len(item.get('commits') or [])
        items.append(
            f"{label} has {behind} updates; this summary uses the latest {limit} commit subjects, with the full comparison still available in the diff link."
        )
    if items:
        return items
    targets = [
        f"{item.get('label') or item.get('name') or 'Intellect'} ({item.get('behind') or 0} update{'s' if (item.get('behind') or 0) != 1 else ''})"
        for item in details
        if item.get('behind')
    ]
    if len(targets) > 1:
        return ['This summary combines updates from ' + ' and '.join(targets) + '.']
    return []


def _format_update_summary_sections(summary_text: str, details: list[dict]) -> tuple[list[dict], str]:
    notice_items, worth_items = _categorized_summary_bullets_from_text(summary_text)
    if not notice_items:
        notice_items = _summary_bullets_from_text(summary_text, fallback_items=_fallback_update_bullets(details))
    notice_keys = {item.lower() for item in notice_items}
    worth_items = [item for item in worth_items if item.lower() not in notice_keys]
    worth_items.extend(
        item for item in _worth_knowing_bullets(details)
        if item.lower() not in notice_keys and item.lower() not in {existing.lower() for existing in worth_items}
    )
    sections = [
        {
            'title': "What you'll notice",
            'items': notice_items,
        },
    ]
    if worth_items:
        sections.append(
            {
                'title': 'Worth knowing',
                'items': worth_items,
            }
        )
    lines = []
    for section in sections:
        lines.append(section['title'])
        lines.extend(f"- {item}" for item in section['items'])
        lines.append('')
    return sections, '\n'.join(lines).strip()


def _fallback_update_summary(updates: dict, details: list[dict]) -> str:
    _sections, summary = _format_update_summary_sections('', details)
    return summary


def _update_summary_prompt(details: list[dict]) -> tuple[str, str]:
    system = (
        "You write human-readable release summaries for Intellect users. "
        "Focus on what the user will notice in the product. Keep it simple, specific, and short. "
        "avoid technical jargon, implementation details, SHA names, branch names, and file paths unless necessary. "
        "Return only bullets. Do not include headings, markdown tables, intro paragraphs, or closing notes."
    )
    user_lines = [
        "Summarize these available updates as concise bullets.",
        "Prefix each bullet with `Notice:` for user-visible behavior changes or `Worth knowing:` for useful context.",
        "Put user-visible Notice bullets first and include every meaningful user-facing change from the available commit subjects.",
        "Use Worth knowing only for helpful context that is not a duplicate of a Notice bullet.",
        "Use everyday language and explain visible behavior changes, not code mechanics.",
        "Return only prefixed bullets; the WebUI will add the fixed section headings separately.",
        "",
    ]
    for item in details:
        user_lines.append(f"{item['label']}: {item['behind']} commit(s) behind")
        commits = item.get('commits') or []
        if commits:
            if item.get('commits_truncated'):
                user_lines.append(
                    f"- Showing latest {len(commits)} of {item['behind']} commit subjects; summarize trends, not every commit."
                )
            user_lines.extend(f"- {subject}" for subject in commits)
        else:
            user_lines.append("- No local commit subjects available; summarize only the update count.")
        user_lines.append("")
    return system, '\n'.join(user_lines)


def summarize_update_payload(updates: dict, llm_callback=None, *, target: str | None = None, use_cache: bool = True) -> dict:
    """Build a human-readable What's New summary and keep regular diff comparison links.

    ``llm_callback`` receives ``(system_prompt, user_prompt)`` and returns text.
    The caller may wire that to AIAgent; this module keeps a deterministic
    fallback so the banner remains useful when no LLM provider is configured.
    Summaries are cached per exact update range so refreshes do not generate
    slightly different wording for the same available updates.
    """
    if not isinstance(updates, dict):
        updates = {}
    requested_target = target if target in ('webui', 'agent') else None
    details = []
    for key, label in (('webui', 'WebUI'), ('agent', 'Agent')):
        if requested_target and key != requested_target:
            continue
        info = updates.get(key)
        if not isinstance(info, dict) or int(info.get('behind') or 0) <= 0:
            continue
        commit_limit = 24
        commits, commits_truncated = _commit_subjects_for_update_with_limit({'name': key, **info}, limit=commit_limit)
        behind = int(info.get('behind') or 0)
        item = {
            'name': key,
            'label': label,
            'behind': behind,
            'current_sha': info.get('current_sha'),
            'latest_sha': info.get('latest_sha'),
            'compare_url': info.get('compare_url'),
            'commits': commits,
            'commits_limit': commit_limit,
            'commits_truncated': bool(commits_truncated or (commits and behind > len(commits))),
        }
        details.append(item)
    cache_key = _summary_cache_key(updates, details)
    if use_cache:
        with _cache_lock:
            cached = _summary_cache.get(cache_key)
            if cached:
                _summary_cache.move_to_end(cache_key)
        if cached:
            result = dict(cached)
            result['cached'] = True
            return result

    generated_by = 'fallback'
    candidate = ''
    if details and callable(llm_callback):
        system, prompt = _update_summary_prompt(details)
        try:
            candidate = (llm_callback(system, prompt) or '').strip()
            if candidate:
                generated_by = 'llm'
        except Exception:
            candidate = ''
    sections, summary = _format_update_summary_sections(candidate, details)
    result = {
        'ok': True,
        'summary': summary,
        'summary_sections': sections,
        'generated_by': generated_by,
        'cached': False,
        'cache_key': cache_key,
        'target': requested_target,
        'targets': details,
    }
    if use_cache:
        with _cache_lock:
            if len(_summary_cache) >= _SUMMARY_CACHE_MAX and cache_key not in _summary_cache:
                _summary_cache.popitem(last=False)
            _summary_cache[cache_key] = dict(result)
    return result


# ── Self-update application ───────────────────────────────────────────────────


def _try_rebuild_rust_extension() -> bool:
    """Rebuild or download the Rust extension after a git pull update.

    Strategy (best-effort):
    1. Download pre-built wheel from Gitee Release (no Rust toolchain needed)
    2. Fall back to local maturin build (requires cargo + maturin)
    3. Failure is non-fatal — the agent will fail fast at next startup
    """
    import os as _os
    import subprocess as _sp
    import sys as _sys
    import shutil as _shutil

    _rust_dir = REPO_ROOT.parent / "rust-core"
    if not _rust_dir.is_dir() or not (_rust_dir / "Cargo.toml").exists():
        return False

    # 1. Try Gitee Release pre-built wheel (no Rust toolchain needed)
    if _try_download_gitee_rust_wheel():
        return True

    # 2. Fall back to local maturin build
    if not _shutil.which("cargo"):
        return False
    try:
        _maturin = _shutil.which("maturin")
        if not _maturin:
            _sp.run(
                [_sys.executable, "-m", "pip", "install", "-q", "maturin"],
                check=True, capture_output=True,
            )
            _maturin = _shutil.which("maturin") or "maturin"
        _result = _sp.run(
            [_maturin, "develop", "--release"],
            cwd=str(_rust_dir), capture_output=True, text=True,
        )
        return _result.returncode == 0
    except Exception:
        return False


def _try_download_gitee_rust_wheel() -> bool:
    """Download a pre-built intellect_community_core wheel from Gitee Release.

    Queries the Gitee API for the latest release, finds the wheel asset
    matching the current platform, downloads and installs it via pip.
    """
    import json as _json
    import platform as _platform
    import subprocess as _sp
    import sys as _sys
    import tempfile as _tempfile
    import urllib.request as _ur

    # Determine platform hint for wheel name matching
    _sys_plat = _sys.platform
    _arch = _platform.machine().lower()
    if _sys_plat == "darwin":
        _hint = "macosx"  # matches macosx_11_0_universal2 or macosx_11_0_arm64
    elif _sys_plat == "win32":
        _hint = "win_amd64"
    elif "linux" in _sys_plat:
        if _arch in ("aarch64", "arm64"):
            _hint = "manylinux"  # matches manylinux_*_aarch64
        else:
            _hint = "manylinux"  # matches manylinux_*_x86_64
    else:
        return False

    try:
        _api_url = (
            "https://gitee.com/api/v5/repos/ontoweb/intellect-agent/"
            "releases?page=1&per_page=1&direction=desc"
        )
        with _ur.urlopen(_api_url, timeout=30) as _resp:
            _releases = _json.load(_resp)
        _release = _releases[0] if _releases else None
        if not _release:
            return False

        _wheel_url = None
        for _asset in _release.get("assets") or []:
            _name = (_asset.get("name") or "").lower()
            if "intellect_community_core" not in _name:
                continue
            if not _name.endswith(".whl"):
                continue
            if _hint in _name or ("universal2" in _name and "macosx" in _hint):
                _wheel_url = _asset.get("browser_download_url") or _asset.get("url") or ""
                break

        if not _wheel_url:
            return False

        # Download wheel to temp file and install
        _tmp = _tempfile.mktemp(suffix=".whl", prefix="intellect-rust-")
        _ur.urlretrieve(_wheel_url, _tmp)
        _sp.run(
            [_sys.executable, "-m", "pip", "install", "-q", _tmp],
            check=True, capture_output=True,
        )
        return True
    except Exception:
        return False


def _schedule_restart(delay: float = 2.0) -> None:
    """Re-exec this process after *delay* seconds.

    Called after a successful update so that the freshly-pulled code is
    loaded on the next request, rather than running with a mix of old and
    new Python modules in sys.modules.

    On POSIX, ``os.execv()`` replaces the current process image atomically.
    On Windows, ``os.execv`` is emulated by spawning a new process that
    may not inherit the ``CREATE_NO_WINDOW`` flag, creating an unwanted
    blank console window.  Use ``subprocess.Popen`` with the base Python's
    pythonw.exe (GUI-subsystem, truly windowless) and set PYTHONPATH so
    venv-installed packages are still importable.
    """
    import os
    import subprocess as _sp
    import sys

    _WIN32 = sys.platform == "win32"
    if _WIN32:
        _CREATE_NO_WINDOW = 0x08000000
        _DETACHED_PROCESS = 0x00000008
        _CREATE_NEW_PROCESS_GROUP = 0x00000200
        _pyw = os.path.join(getattr(sys, "base_prefix", sys.prefix), "pythonw.exe")
        if os.path.isfile(_pyw):
            _RESTART_EXE = _pyw
        else:
            _RESTART_EXE = sys.executable

    def _do():
        import time
        time.sleep(delay)
        with _apply_lock:
            if _WIN32:
                _env = os.environ.copy()
                _venv_sp = os.path.join(sys.prefix, "Lib", "site-packages")
                _existing = _env.get("PYTHONPATH", "")
                _env["PYTHONPATH"] = f"{_venv_sp}{os.pathsep}{_existing}".rstrip(os.pathsep)
                try:
                    _sp.Popen(
                        [_RESTART_EXE] + sys.argv,
                        env=_env,
                        stdin=_sp.DEVNULL,
                        stdout=_sp.DEVNULL,
                        stderr=_sp.DEVNULL,
                        close_fds=True,
                        creationflags=_CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS | _CREATE_NO_WINDOW,
                    )
                    os._exit(0)
                except Exception:
                    pass
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                os._exit(0)

    threading.Thread(target=_do, daemon=True).start()


def apply_force_update(target: str) -> dict:
    """Force-reset the target repo to the latest remote HEAD.

    Unlike apply_update() which requires a clean working tree and refuses
    merge conflicts, this discards all local modifications (checkout .) and
    resets to origin/<branch> — equivalent to what the diverged/conflict
    error messages ask the user to run manually.

    Should only be called when apply_update() has already returned a
    response with ``conflict: True`` or ``diverged: True`` and the user
    has confirmed they want to discard local changes.
    """
    active_streams = _active_stream_count()
    if active_streams:
        return _restart_blocked_response(target, active_streams)

    if not _apply_lock.acquire(blocking=False):
        return {'ok': False, 'message': 'Update already in progress'}
    try:
        if target == 'webui':
            # REPO_ROOT is webui/ inside the agent project; .git lives in REPO_ROOT.parent
            path = REPO_ROOT.parent
        elif target == 'agent':
            path = _AGENT_DIR
        else:
            return {'ok': False, 'message': f'Unknown target: {target}'}

        if path is None or not (path / '.git').exists():
            return {'ok': False, 'message': 'Not a git repository'}

        # --force so a remote re-tag (e.g. squash-merge that re-points an
        # existing release tag) doesn't jam the apply path with "would clobber
        # existing tag". See #2756.
        _, fetch_ok = _run_git(['fetch', 'origin', '--quiet', '--tags', '--force'], path, timeout=15)
        if not fetch_ok:
            return {
                'ok': False,
                'message': 'Could not reach the remote repository. Check your connection.',
            }

        compare_ref = _select_apply_compare_ref(path)

        # Discard local modifications then reset to remote HEAD
        _run_git(['checkout', '.'], path)
        _, ok = _run_git(['reset', '--hard', compare_ref], path)
        if not ok:
            return {'ok': False, 'message': f'Force reset to {compare_ref} failed'}

        with _cache_lock:
            _update_cache['checked_at'] = 0

        _try_rebuild_rust_extension()
        _schedule_restart()

        return {
            'ok': True,
            'message': f'{target} force-updated to {compare_ref}',
            'target': target,
            'restart_scheduled': True,
        }
    finally:
        _apply_lock.release()


def apply_update(target):
    """Stash, pull --ff-only, pop for the given target repo."""
    active_streams = _active_stream_count()
    if active_streams:
        return _restart_blocked_response(target, active_streams)

    if not _apply_lock.acquire(blocking=False):
        return {'ok': False, 'message': 'Update already in progress'}
    try:
        return _apply_update_inner(target)
    finally:
        _apply_lock.release()


def _apply_update_inner(target):
    """Inner implementation of apply_update, called under _apply_lock."""
    if target == 'webui':
        # REPO_ROOT is webui/ inside the agent project; .git lives in REPO_ROOT.parent
        path = REPO_ROOT.parent
    elif target == 'agent':
        path = _AGENT_DIR
    else:
        return {'ok': False, 'message': f'Unknown target: {target}'}

    if path is None or not (path / '.git').exists():
        return {'ok': False, 'message': 'Not a git repository'}

    # Fetch before attempting pull, so the remote ref is current.
    # --force so a remote re-tag doesn't block the update path (see #2756).
    _, fetch_ok = _run_git(['fetch', 'origin', '--quiet', '--tags', '--force'], path, timeout=15)
    if not fetch_ok:
        return {
            'ok': False,
            'message': (
                'Could not reach the remote repository. '
                'Check your internet connection and try again.'
            ),
        }

    compare_ref = _select_apply_compare_ref(path)

    # Check for dirty working tree (ignore untracked files — git stash
    # doesn't include them, so stashing on '??' alone leaves nothing to pop)
    status_out, status_ok = _run_git(
        ['status', '--porcelain', '--untracked-files=no'], path
    )
    if not status_ok:
        return {'ok': False, 'message': f'Failed to inspect repo status: {status_out[:200]}'}
    # Fail early on unresolved merge conflicts
    if any(line[:2] in {'DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'}
           for line in status_out.splitlines()):
        return {
            'ok': False,
            'message': (
                f'The local {target} repo has unresolved merge conflicts. '
                'To reset to the latest remote version run: '
                'git -C ' + str(path) + ' checkout . && '
                'git -C ' + str(path) + ' pull --ff-only'
            ),
            'conflict': True,
        }
    stashed = False
    if status_out:
        _, ok = _run_git(['stash'], path)
        if not ok:
            return {'ok': False, 'message': 'Failed to stash local changes'}
        stashed = True

    # Pull with ff-only (no merge commits).
    # Split tracking refs like 'origin/main' into separate remote + branch
    # arguments — git treats 'origin/main' as a repository name otherwise.
    remote, branch = _split_remote_ref(compare_ref)
    pull_args = ['pull', '--ff-only']
    if remote:
        pull_args.extend([remote, branch])
    else:
        pull_args.extend(['origin', compare_ref])
    pull_out, pull_ok = _run_git(pull_args, path, timeout=30)
    if not pull_ok:
        if stashed:
            _run_git(['stash', 'pop'], path)

        # Diagnose the most common failure modes and surface actionable messages.
        pull_lower = pull_out.lower()
        if 'not possible to fast-forward' in pull_lower or 'diverged' in pull_lower:
            return {
                'ok': False,
                'message': (
                    f'The local {target} repo has commits that are not on the remote '
                    'branch, so a fast-forward update is not possible. '
                    'Run: git -C ' + str(path) + ' fetch origin && '
                    'git -C ' + str(path) + ' reset --hard ' + compare_ref
                ),
                'diverged': True,
            }
        if 'does not track' in pull_lower or 'no tracking information' in pull_lower:
            return {
                'ok': False,
                'message': (
                    f'The local {target} branch has no upstream tracking branch configured. '
                    'Run: git -C ' + str(path) + ' branch --set-upstream-to=' + compare_ref
                ),
            }
        # Generic fallback — include the raw git output for debugging.
        detail = pull_out.strip()[:300] if pull_out.strip() else '(no output from git)'
        return {'ok': False, 'message': f'Pull failed: {detail}'}

    # Pop stash if we stashed
    if stashed:
        _, pop_ok = _run_git(['stash', 'pop'], path)
        if not pop_ok:
            return {
                'ok': False,
                'message': 'Updated but stash pop failed -- manual merge needed',
                'stash_conflict': True,
            }

    # Invalidate cache
    with _cache_lock:
        _update_cache['checked_at'] = 0

    # Rebuild the Rust extension so the compiled accelerator matches the
    # freshly-pulled Python code.  Best-effort — failure is non-fatal.
    _try_rebuild_rust_extension()

    # Schedule a self-restart so the updated code is loaded fresh.  A plain
    # git pull leaves stale Python modules in sys.modules — agent imports that
    # reference new symbols (functions, classes) added in the update will fail
    # on the next request with AttributeError / ImportError.  os.execv() re-
    # execs the same interpreter with the same argv, picking up the new code
    # cleanly without requiring the user to restart manually.
    #
    # The 2 s delay gives the HTTP response time to flush to the client before
    # the process replaces itself.  The client already does
    # setTimeout(() => location.reload(), 1500) on success, so the page reload
    # and the restart land at roughly the same time.
    _schedule_restart()

    return {
        'ok': True,
        'message': f'{target} updated successfully',
        'target': target,
        'restart_scheduled': True,
    }
