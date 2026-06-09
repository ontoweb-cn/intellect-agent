"""Quartz vault discovery, build execution, and scheduled tick orchestration."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intellect_cli.wiki_scaffold import is_forbidden_path, resolve_wiki_target, safe_slug

logger = logging.getLogger(__name__)

VAULT_DEFAULTS: dict[str, Any] = {
    "routing": "context",
    "build_trigger": "auto",
    "build_schedule": "0 * * * *",
    "build_throttle_seconds": 600,
    "build_timeout_seconds": 300,
}

_PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
_SCOPE_DIR_MAP = {
    "project": "projects",
    "team": "teams",
    "member": "members",
}

_PROCESS_BUILD_LOCK = threading.Lock()


@dataclass(frozen=True)
class VaultBuildTarget:
    scope: str
    scope_id: str | None
    wiki_path: Path
    vault_output: Path
    base_path: str
    title: str

    @property
    def vault_key(self) -> str:
        return str(self.vault_output.expanduser().resolve())


@dataclass
class BuildResult:
    ok: bool
    target: VaultBuildTarget
    error: str | None = None
    trigger: str = "manual"


@dataclass
class TargetTickDetail:
    scope: str
    scope_id: str | None
    action: str  # built | skipped | failed
    reason: str | None = None


@dataclass
class ScheduledTickResult:
    ok: bool
    ran: bool
    skipped_reason: str | None = None
    built: int = 0
    skipped: int = 0
    failed: int = 0
    summary: str = ""
    details: list[TargetTickDetail] = field(default_factory=list)


def get_intellect_home() -> Path:
    try:
        from intellect_constants import get_intellect_home
        return Path(get_intellect_home()).expanduser()
    except Exception:
        return Path(os.getenv("INTELLECT_HOME", str(Path.home() / ".intellect"))).expanduser()


def load_vault_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(VAULT_DEFAULTS)
    cfg = config
    if cfg is None:
        try:
            from intellect_cli.config import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
    if isinstance(cfg, dict):
        vault_cfg = cfg.get("vault") or {}
        if isinstance(vault_cfg, dict):
            for key, value in vault_cfg.items():
                if key in merged and value is not None:
                    merged[key] = value
    return merged


def last_build_state_path(intellect_home: Path | None = None) -> Path:
    home = intellect_home or get_intellect_home()
    return home / "vaults" / ".last-build-state.json"


def read_last_build_state(intellect_home: Path | None = None) -> dict[str, Any]:
    path = last_build_state_path(intellect_home)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_last_build_state(state: dict[str, Any], intellect_home: Path | None = None) -> None:
    path = last_build_state_path(intellect_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def find_build_script() -> Path | None:
    plugin_root = Path(__file__).resolve().parent.parent / "plugins" / "vault-builder"
    script_name = "build.ps1" if sys.platform == "win32" else "build.sh"
    candidates = [
        plugin_root.parent.parent.parent / "intellect-webui" / "quartz" / script_name,
        Path(__file__).resolve().parent.parent / "quartz" / script_name,
        Path.home() / "workspace" / "intellect-webui" / "quartz" / script_name,
        Path("/opt/intellect-webui/quartz") / script_name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def wiki_latest_mtime(wiki_path: Path) -> float:
    latest = 0.0
    if not wiki_path.exists() or not wiki_path.is_dir():
        return latest
    try:
        for md in wiki_path.rglob("*.md"):
            try:
                latest = max(latest, md.stat().st_mtime)
            except OSError:
                continue
    except Exception:
        pass
    return latest


def wiki_is_valid_candidate(wiki_path: Path) -> bool:
    if not wiki_path.exists() or not wiki_path.is_dir():
        return False
    if is_forbidden_path(wiki_path):
        return False
    for name in ("SCHEMA.md", "index.md"):
        if (wiki_path / name).is_file():
            return True
    for section in _PAGE_DIRS:
        section_path = wiki_path / section
        if section_path.exists():
            try:
                if any(section_path.rglob("*.md")):
                    return True
            except Exception:
                continue
    return False


def target_from_wiki_path(wiki_path: Path, intellect_home: Path | None = None) -> VaultBuildTarget | None:
    home = (intellect_home or get_intellect_home()).resolve()
    wiki = wiki_path.expanduser().resolve()
    try:
        rel = wiki.relative_to(home)
    except ValueError:
        rel = None

    if rel is not None and len(rel.parts) >= 3 and rel.parts[-1] == "wiki":
        dir_name, scope_id = rel.parts[0], rel.parts[1]
        scope = {v: k for k, v in _SCOPE_DIR_MAP.items()}.get(dir_name)
        if scope and safe_slug(scope_id):
            return VaultBuildTarget(
                scope=scope,
                scope_id=scope_id,
                wiki_path=wiki,
                vault_output=home / "vaults" / dir_name / scope_id,
                base_path=f"/vault/{dir_name[0]}/{scope_id}",
                title=f"{scope.title()} Wiki — {scope_id}",
            )

    return VaultBuildTarget(
        scope="global",
        scope_id=None,
        wiki_path=wiki,
        vault_output=home / "vaults" / "global",
        base_path="/vault",
        title="LLM Wiki",
    )


def discover_vault_build_targets(
    intellect_home: Path | None = None,
    config: dict[str, Any] | None = None,
) -> list[VaultBuildTarget]:
    home = intellect_home or get_intellect_home()
    targets: list[VaultBuildTarget] = []
    seen: set[str] = set()

    for scope, dir_name in _SCOPE_DIR_MAP.items():
        root = home / dir_name
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not safe_slug(child.name):
                continue
            wiki = child / "wiki"
            if not wiki_is_valid_candidate(wiki):
                continue
            target = target_from_wiki_path(wiki, home)
            if target and target.vault_key not in seen:
                seen.add(target.vault_key)
                targets.append(target)

    cfg = config
    if cfg is None:
        try:
            from intellect_cli.config import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
    global_target = resolve_wiki_target(
        intellect_home=home,
        env_wiki_path=os.getenv("WIKI_PATH"),
        config=cfg if isinstance(cfg, dict) else None,
    )
    if wiki_is_valid_candidate(global_target.path):
        target = target_from_wiki_path(global_target.path, home)
        if target and target.vault_key not in seen:
            targets.append(target)

    return targets


def should_build_vault(
    target: VaultBuildTarget,
    *,
    last_build_ts: float,
    wiki_mtime: float,
    throttle_seconds: int,
    force: bool = False,
) -> tuple[bool, str]:
    now = time.time()
    if not force and wiki_mtime <= last_build_ts:
        return False, "unchanged"
    if now - last_build_ts < throttle_seconds:
        return False, "throttled"
    return True, "ok"


def validate_cron_schedule(expr: str) -> None:
    try:
        from croniter import croniter
    except ImportError as exc:
        raise ValueError(
            "Cron expressions require the 'croniter' package. "
            "Install with: pip install croniter"
        ) from exc
    croniter(str(expr).strip())


def is_cron_due(expr: str, last_tick_at: float | None, now: float | None = None) -> bool:
    validate_cron_schedule(expr)
    from croniter import croniter

    now_ts = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    if last_tick_at:
        base = datetime.fromtimestamp(last_tick_at, tz=timezone.utc)
    else:
        base = datetime.fromtimestamp(0, tz=timezone.utc)
    cron = croniter(str(expr).strip(), base)
    next_run = cron.get_next(datetime)
    return next_run.timestamp() <= now_ts


def estimate_next_cron_run(expr: str, last_tick_at: float | None, now: float | None = None) -> str | None:
    try:
        validate_cron_schedule(expr)
    except ValueError:
        return None
    from croniter import croniter

    now_ts = now if now is not None else time.time()
    if last_tick_at:
        base = datetime.fromtimestamp(last_tick_at, tz=timezone.utc)
    else:
        base = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    cron = croniter(str(expr).strip(), base)
    next_run = cron.get_next(datetime)
    return next_run.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_scheduler_status(vcfg: dict[str, Any] | None = None, intellect_home: Path | None = None) -> dict[str, Any]:
    cfg = vcfg or load_vault_config()
    home = intellect_home or get_intellect_home()
    state = read_last_build_state(home)
    sched = state.get("_scheduler") if isinstance(state.get("_scheduler"), dict) else {}
    last_tick_at = sched.get("last_scheduled_tick_at")
    last_tick_ts = None
    if isinstance(last_tick_at, (int, float)):
        last_tick_ts = float(last_tick_at)
    elif isinstance(last_tick_at, str) and last_tick_at:
        try:
            last_tick_ts = datetime.fromisoformat(last_tick_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            last_tick_ts = None

    trigger = str(cfg.get("build_trigger", "auto"))
    schedule = str(cfg.get("build_schedule", VAULT_DEFAULTS["build_schedule"]))
    return {
        "active": trigger == "scheduled",
        "gateway_required": True,
        "build_trigger": trigger,
        "build_schedule": schedule,
        "last_tick_at": sched.get("last_scheduled_tick_at_iso"),
        "last_tick_status": sched.get("last_scheduled_status"),
        "last_tick_summary": sched.get("last_scheduled_summary"),
        "next_tick_estimate": estimate_next_cron_run(schedule, last_tick_ts) if trigger == "scheduled" else None,
    }


def run_vault_build(
    target: VaultBuildTarget,
    *,
    timeout: int = 300,
    trigger: str = "manual",
    build_script: Path | None = None,
) -> BuildResult:
    script = build_script or find_build_script()
    if not script:
        return BuildResult(ok=False, target=target, error="Build script not found", trigger=trigger)

    if sys.platform == "win32":
        script_cmd = ["powershell", "-NoProfile", "-NonInteractive", "-File", str(script)]
    else:
        script_cmd = ["bash", str(script)]

    try:
        build_args = script_cmd + [
            str(target.wiki_path), str(target.vault_output), target.title, target.base_path
        ]
        result = subprocess.run(
            build_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return BuildResult(ok=False, target=target, error=f"Build timed out after {timeout}s", trigger=trigger)
    except Exception as exc:
        return BuildResult(ok=False, target=target, error=str(exc), trigger=trigger)

    if result.returncode == 0:
        return BuildResult(ok=True, target=target, trigger=trigger)
    stderr = (result.stderr or result.stdout or "")[:500]
    return BuildResult(ok=False, target=target, error=stderr or f"Exit code {result.returncode}", trigger=trigger)


def _record_build_result(
    state: dict[str, Any],
    target: VaultBuildTarget,
    result: BuildResult,
    *,
    intellect_home: Path | None = None,
) -> None:
    entry = state.get(target.vault_key, {}) if isinstance(state.get(target.vault_key), dict) else {}
    if result.ok:
        entry.update({
            "last_build_ts": time.time(),
            "status": "ok",
            "trigger": result.trigger,
        })
    else:
        entry.update({
            "last_build_ts": entry.get("last_build_ts", 0),
            "status": "error",
            "trigger": result.trigger,
            "error": result.error,
        })
    state[target.vault_key] = entry
    write_last_build_state(state, intellect_home)


def maybe_build_single_wiki(
    wiki_path: Path,
    *,
    vcfg: dict[str, Any] | None = None,
    trigger: str = "auto",
    force: bool = False,
    intellect_home: Path | None = None,
) -> BuildResult | None:
    """Build one wiki if eligible (used by vault-builder on_session_end)."""
    cfg = vcfg or load_vault_config()
    home = intellect_home or get_intellect_home()
    wiki = wiki_path.expanduser()
    if not wiki_is_valid_candidate(wiki):
        return None

    target = target_from_wiki_path(wiki, home)
    if target is None:
        return None

    state = read_last_build_state(home)
    last_build = float((state.get(target.vault_key) or {}).get("last_build_ts", 0) or 0)
    wiki_mtime = wiki_latest_mtime(wiki)
    throttle = int(cfg.get("build_throttle_seconds", 600))
    should, reason = should_build_vault(
        target,
        last_build_ts=last_build,
        wiki_mtime=wiki_mtime,
        throttle_seconds=throttle,
        force=force,
    )
    if not should:
        logger.debug("vault-build: skip %s (%s)", target.vault_key, reason)
        return None

    if not _PROCESS_BUILD_LOCK.acquire(blocking=False):
        logger.debug("vault-build: build already in progress")
        return None

    try:
        timeout = int(cfg.get("build_timeout_seconds", 300))
        result = run_vault_build(target, timeout=timeout, trigger=trigger)
        _record_build_result(state, target, result, intellect_home=home)
        return result
    finally:
        _PROCESS_BUILD_LOCK.release()


def run_scheduled_vault_tick(
    vcfg: dict[str, Any] | None = None,
    *,
    intellect_home: Path | None = None,
    force: bool = False,
    now: float | None = None,
) -> ScheduledTickResult:
    cfg = vcfg or load_vault_config()
    home = intellect_home or get_intellect_home()

    if str(cfg.get("build_trigger")) != "scheduled" and not force:
        return ScheduledTickResult(ok=True, ran=False, skipped_reason="not_scheduled")

    schedule = str(cfg.get("build_schedule", VAULT_DEFAULTS["build_schedule"]))
    state = read_last_build_state(home)
    sched = state.get("_scheduler") if isinstance(state.get("_scheduler"), dict) else {}
    last_tick_ts = sched.get("last_scheduled_tick_at")
    last_tick = float(last_tick_ts) if isinstance(last_tick_ts, (int, float)) else None

    now_ts = now if now is not None else time.time()
    if not force and not is_cron_due(schedule, last_tick, now_ts):
        return ScheduledTickResult(ok=True, ran=False, skipped_reason="not_due")

    if not _PROCESS_BUILD_LOCK.acquire(blocking=False):
        return ScheduledTickResult(ok=True, ran=False, skipped_reason="build_in_progress")

    details: list[TargetTickDetail] = []
    built = skipped = failed = 0

    try:
        state.setdefault("_scheduler", {})
        state["_scheduler"]["last_scheduled_tick_at"] = now_ts
        state["_scheduler"]["last_scheduled_tick_at_iso"] = (
            datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        )

        targets = discover_vault_build_targets(home)
        throttle = int(cfg.get("build_throttle_seconds", 600))
        timeout = int(cfg.get("build_timeout_seconds", 300))

        for target in targets:
            vault_state = state.get(target.vault_key, {}) if isinstance(state.get(target.vault_key), dict) else {}
            last_build = float(vault_state.get("last_build_ts", 0) or 0)
            wiki_mtime = wiki_latest_mtime(target.wiki_path)
            should, reason = should_build_vault(
                target,
                last_build_ts=last_build,
                wiki_mtime=wiki_mtime,
                throttle_seconds=throttle,
                force=force,
            )
            if not should:
                skipped += 1
                details.append(TargetTickDetail(target.scope, target.scope_id, "skipped", reason))
                continue

            result = run_vault_build(target, timeout=timeout, trigger="scheduled")
            _record_build_result(state, target, result, intellect_home=home)
            if result.ok:
                built += 1
                details.append(TargetTickDetail(target.scope, target.scope_id, "built", None))
            else:
                failed += 1
                details.append(TargetTickDetail(target.scope, target.scope_id, "failed", result.error))

        summary = f"built {built}/{len(targets)} vaults, skipped {skipped} unchanged"
        status = "ok" if failed == 0 else "partial"
        state["_scheduler"]["last_scheduled_status"] = status
        state["_scheduler"]["last_scheduled_summary"] = summary
        write_last_build_state(state, home)

        logger.info("vault-scheduler: %s (failed=%d)", summary, failed)
        return ScheduledTickResult(
            ok=failed == 0,
            ran=True,
            built=built,
            skipped=skipped,
            failed=failed,
            summary=summary,
            details=details,
        )
    finally:
        _PROCESS_BUILD_LOCK.release()


def maybe_run_vault_scheduled_builds() -> ScheduledTickResult | None:
    """Lightweight poll entry for the gateway cron ticker."""
    cfg = load_vault_config()
    if str(cfg.get("build_trigger")) != "scheduled":
        return None
    home = get_intellect_home()
    state = read_last_build_state(home)
    sched = state.get("_scheduler") if isinstance(state.get("_scheduler"), dict) else {}
    last_tick = sched.get("last_scheduled_tick_at")
    last_tick_ts = float(last_tick) if isinstance(last_tick, (int, float)) else None
    schedule = str(cfg.get("build_schedule", VAULT_DEFAULTS["build_schedule"]))
    try:
        if not is_cron_due(schedule, last_tick_ts):
            return None
    except ValueError as exc:
        logger.warning("vault-scheduler: invalid build_schedule: %s", exc)
        return None
    return run_scheduled_vault_tick(cfg, intellect_home=home)
