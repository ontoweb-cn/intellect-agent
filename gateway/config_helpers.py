"""Gateway config/runtime helpers extracted from run.py."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from intellect_cli.config import cfg_get
from intellect_cli.env_loader import load_intellect_dotenv
from intellect_cli.fallback_config import get_fallback_chain
from gateway.helpers import _log_non_critical, _get_intellect_home

logger = logging.getLogger(__name__)


def _resolve_intellect_bin() -> Optional[list[str]]:
    """Resolve the Intellect update command as argv parts.

    Tries in order:
    1. ``shutil.which("intellect")`` — standard PATH lookup
    2. ``sys.executable -m intellect_cli.main`` — fallback when Intellect is running
       from a venv/module invocation and the ``intellect`` shim is not on PATH

    Returns argv parts ready for quoting/joining, or ``None`` if neither works.
    """
    import shutil

    intellect_bin = shutil.which("intellect")
    if intellect_bin:
        return [intellect_bin]

    try:
        import importlib.util

        if importlib.util.find_spec("intellect_cli") is not None:
            return [sys.executable, "-m", "intellect_cli.main"]
    except Exception:
        _log_non_critical()
    return None


def _home_target_env_var(platform_name: str) -> str:
    """Return the configured home-target env var for a platform.

    Consults built-in ``_HOME_TARGET_ENV_VARS`` first, then the plugin
    registry via ``cron.scheduler._resolve_home_env_var``, then falls back
    to ``<PLATFORM>_HOME_CHANNEL`` for unknown names.
    """
    from cron.scheduler import _resolve_home_env_var

    resolved = _resolve_home_env_var(platform_name)
    if resolved:
        return resolved
    return f"{platform_name.upper()}_HOME_CHANNEL"


def _home_thread_env_var(platform_name: str) -> str:
    """Return the optional thread/topic env var for a platform home target."""
    return f"{_home_target_env_var(platform_name)}_THREAD_ID"

def _restart_notification_pending() -> bool:

    """Return True when a /restart completion marker is waiting to be delivered."""

    return (_get_intellect_home() / ".restart_notify.json").exists()


def _reload_runtime_env_preserving_config_authority() -> None:

    """Reload .env for fresh credentials without letting stale .env override config.

    Gateway processes are long-lived, so per-turn code reloads ~/.intellect/.env to

    pick up rotated API keys. config.yaml remains authoritative for agent budget

    settings such as agent.max_turns; otherwise a stale INTELLECT_MAX_ITERATIONS in

    .env can replace the startup bridge on later turns.

    """

    load_intellect_dotenv(

        intellect_home=_get_intellect_home(),

        project_env=Path(__file__).resolve().parents[1] / '.env',

    )

    config_path = _get_intellect_home() / 'config.yaml'

    if not config_path.exists():

        return

    try:

        import yaml as _yaml

        with open(config_path, encoding="utf-8") as f:

            cfg = _yaml.safe_load(f) or {}

        from intellect_cli.config import _expand_env_vars

        cfg = _expand_env_vars(cfg)

    except Exception:

        return

    agent_cfg = cfg.get("agent", {})

    if isinstance(agent_cfg, dict) and "max_turns" in agent_cfg:

        os.environ["INTELLECT_MAX_ITERATIONS"] = str(agent_cfg["max_turns"])


def _resolve_runtime_agent_kwargs() -> dict:

    """Resolve provider credentials for gateway-created AIAgent instances.

    Provider is read from ``config.yaml`` ``model.provider`` (the single

    source of truth). ``resolve_runtime_provider()`` falls through to env

    var lookups internally for legacy compatibility, but the gateway does

    not consult environment variables for behavioral config — config.yaml

    is authoritative.

    If the primary provider fails with an authentication error, attempt to

    resolve credentials using the fallback provider chain from config.yaml

    before giving up.

    """

    from intellect_cli.runtime_provider import (

        resolve_runtime_provider,

        format_runtime_provider_error,

    )

    from intellect_cli.auth import AuthError, is_rate_limited_auth_error

    try:

        runtime = resolve_runtime_provider()

    except AuthError as auth_exc:

        # Distinguish a transient rate-limit/quota cap (credentials are fine,

        # re-auth cannot help) from a genuine auth failure (expired/revoked

        # token). Both fall through to the fallback chain, but the log message

        # must not mislabel a quota exhaustion as an auth failure (#32790).

        if is_rate_limited_auth_error(auth_exc):

            logger.warning("Primary provider rate-limited (429): %s — trying fallback", auth_exc)

        else:

            logger.warning("Primary provider auth failed: %s — trying fallback", auth_exc)

        fb_config = _try_resolve_fallback_provider()

        if fb_config is not None:

            return fb_config

        raise RuntimeError(format_runtime_provider_error(auth_exc)) from auth_exc

    except Exception as exc:

        raise RuntimeError(format_runtime_provider_error(exc)) from exc

    return {

        "api_key": runtime.get("api_key"),

        "base_url": runtime.get("base_url"),

        "provider": runtime.get("provider"),

        "api_mode": runtime.get("api_mode"),

        "command": runtime.get("command"),

        "args": list(runtime.get("args") or []),

        "credential_pool": runtime.get("credential_pool"),

    }


def _try_resolve_fallback_provider() -> dict | None:

    """Attempt to resolve credentials from the fallback_model/fallback_providers config."""

    import time as _time

    _t0 = _time.perf_counter()

    try:

        return _try_resolve_fallback_provider_inner()

    finally:

        _elapsed = _time.perf_counter() - _t0

        if _elapsed > 0.050:

            logger.debug("timing _try_resolve_fallback_provider: %.3fs", _elapsed)


def _try_resolve_fallback_provider_inner() -> dict | None:

    """Core logic for fallback provider resolution (unwrapped)."""

    from intellect_cli.runtime_provider import resolve_runtime_provider

    try:

        import yaml as _y

        cfg_path = _get_intellect_home() / "config.yaml"

        if not cfg_path.exists():

            return None

        with open(cfg_path, encoding="utf-8") as _f:

            cfg = _y.safe_load(_f) or {}

        fb_list = get_fallback_chain(cfg)

        if not fb_list:

            return None

        for entry in fb_list:

            try:

                explicit_api_key = entry.get("api_key")

                if not explicit_api_key:

                    key_env = str(

                        entry.get("key_env") or entry.get("api_key_env") or ""

                    ).strip()

                    if key_env:

                        explicit_api_key = os.getenv(key_env, "").strip() or None

                # Check SecretStore before giving up on api_key

                if not explicit_api_key:

                    try:

                        from intellect_cli.api_key_secrets import (

                            resolve_secret_store_provider_key,

                        )

                        _pid = str(entry.get("provider") or "").strip()

                        if _pid:

                            _store_key, _ = resolve_secret_store_provider_key(_pid)

                            if _store_key:

                                explicit_api_key = _store_key

                    except Exception:

                        logger.debug(

                            "SecretStore lookup failed for fallback provider",

                            exc_info=True,

                        )

                runtime = resolve_runtime_provider(

                    requested=entry.get("provider"),

                    explicit_base_url=entry.get("base_url"),

                    explicit_api_key=explicit_api_key,

                )

                # Log the literal `provider` key from config, not the resolved

                # runtime category — an Ollama fallback resolves through the

                # OpenAI-compatible path and would otherwise be logged as

                # "openrouter", contradicting the operator's config (#32790).

                logger.info(

                    "Fallback provider resolved: %s model=%s",

                    entry.get("provider") or runtime.get("provider"),

                    entry.get("model"),

                )

                return {

                    "api_key": runtime.get("api_key"),

                    "base_url": runtime.get("base_url"),

                    "provider": runtime.get("provider"),

                    "api_mode": runtime.get("api_mode"),

                    "command": runtime.get("command"),

                    "args": list(runtime.get("args") or []),

                    "credential_pool": runtime.get("credential_pool"),

                    "model": entry.get("model"),

                }

            except Exception as fb_exc:

                logger.debug("Fallback entry %s failed: %s", entry.get("provider"), fb_exc)

                continue

    except Exception:

        _log_non_critical()

    return None


def _teams_pipeline_plugin_enabled() -> bool:

    """Return True when the standalone Teams pipeline plugin is enabled."""

    config = _load_gateway_config()

    enabled = cfg_get(config, "plugins", "enabled", default=[])

    if not isinstance(enabled, list):

        return False

    return "teams_pipeline" in enabled or "teams-pipeline" in enabled


def _load_gateway_config() -> dict:

    """Load and parse ~/.intellect/config.yaml, returning {} on any error.

    Uses the module-level ``_get_intellect_home()`` (so tests that monkeypatch it

    still see their fixture) and shares the mtime-keyed raw-yaml cache

    from ``intellect_cli.config.read_raw_config`` when the paths match.

    """

    config_path = _get_intellect_home() / 'config.yaml'

    try:

        from intellect_cli.config import get_config_path, read_raw_config

        # Fast path: if _get_intellect_home() agrees with the canonical config

        # location, reuse the shared cache. Otherwise fall through to a

        # direct read (keeps test fixtures with a monkeypatched

        # _get_intellect_home() working).

        if config_path == get_config_path():

            return read_raw_config()

    except Exception:

        _log_non_critical()

    try:

        if config_path.exists():

            import yaml

            with open(config_path, 'r', encoding='utf-8') as f:

                return yaml.safe_load(f) or {}

    except Exception:

        logger.debug("Could not load gateway config from %s", config_path)

    return {}


_RUNTIME_CONFIG_CACHE: dict = {"mtime_ns": 0, "data": None}


def _load_gateway_runtime_config() -> dict:

    """Load gateway config for runtime reads, expanding supported ``${VAR}`` refs.

    Cached per config-file mtime to avoid deepcopy on every message turn.

    """

    config_path = _get_intellect_home() / "config.yaml"

    try:

        mtime_ns = config_path.stat().st_mtime_ns

    except OSError:

        mtime_ns = 0

    if _RUNTIME_CONFIG_CACHE["mtime_ns"] == mtime_ns and _RUNTIME_CONFIG_CACHE["data"] is not None:

        return _RUNTIME_CONFIG_CACHE["data"]

    cfg = _load_gateway_config()

    if not isinstance(cfg, dict) or not cfg:

        _RUNTIME_CONFIG_CACHE["mtime_ns"] = mtime_ns

        _RUNTIME_CONFIG_CACHE["data"] = {}

        return {}

    from intellect_cli.config import _expand_env_vars

    expanded = _expand_env_vars(cfg)

    result = expanded if isinstance(expanded, dict) else {}

    _RUNTIME_CONFIG_CACHE["mtime_ns"] = mtime_ns

    _RUNTIME_CONFIG_CACHE["data"] = result

    return result


def _resolve_gateway_model(config: dict | None = None) -> str:

    """Read model from config.yaml — single source of truth.

    Without this, temporary AIAgent instances (e.g. /compress) fall

    back to the hardcoded default which fails when the active provider is

    openai-codex.

    """

    cfg = config if config is not None else _load_gateway_config()

    model_cfg = cfg.get("model", {})

    if isinstance(model_cfg, str):

        return model_cfg

    elif isinstance(model_cfg, dict):

        return model_cfg.get("default") or model_cfg.get("model") or ""

    return ""

