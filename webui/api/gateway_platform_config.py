"""WebUI API for gateway platform configuration (credentials + behavior).

Backs ``GET /api/gateway/platforms`` and ``PUT /api/gateway/platforms/<name>``
— the Settings → Gateway pane. See docs/plans/2026-10-09-webui-gateway-config.md.

Security model:
- Secrets are write-only. GET responses carry ``set: true/false`` markers for
  env-backed fields; no secret value is ever echoed back, logged, or included
  in error messages.
- Mutations require the same localhost gate as ``POST /api/config`` (plus the
  CSRF check the routes layer already applies to PUT).
- All reads/writes resolve against the active profile's INTELLECT_HOME; writes
  run under the per-request profile context so ``save_env_value`` lands in the
  right ``.env``.
- Changes take effect on the next gateway restart — this API never touches a
  running gateway process; the frontend offers "save & restart" via the
  existing gateway lifecycle endpoints.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Plugin ``is_connected`` implementations are supposed to be pure env/extra
# reads, but a third-party plugin could block (a slow import, a network probe).
# Bound each check so one slow checker can't stall the whole GET — a timed-out
# check reads as "not configured" and the runtime dot stays ground truth.
# NB: a timed-out worker thread finishes in the background (threads can't be
# killed); at most max_workers of them can linger.
_CHECK_TIMEOUT_S = 2.0
_CHECK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="gw-platform-check"
)


class ConfigYamlSyntaxError(Exception):
    """config.yaml exists but doesn't parse — refuses saves to avoid clobbering."""


# Bot-token-shaped substrings (e.g. Telegram "123456789:AAE...") that adapters
# sometimes embed in fatal error messages/URLs. Masked before they reach the
# WebUI payload.
_TOKENISH_RE = re.compile(r"\b\d{4,}:[A-Za-z0-9_-]{12,}\b")

# Generic-editor keys whose values are masked in GET responses. Values come
# back as the _MASKED_SENTINEL; saving the sentinel back keeps the stored
# value (mirrors the "(set)" convention of the memory-provider panel).
_SECRETISH_KEY_RE = re.compile(
    r"(pass|passwd|secret|token|api_?key|credential|private_key|client_state)", re.I
)
_MASKED_SENTINEL = "(set)"


def _mask_tokenish(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return _TOKENISH_RE.sub(lambda m: m.group(0)[:3] + "…", text)


# ── helpers ──────────────────────────────────────────────────────────────────


def _config_path() -> Path:
    from api.config import _get_config_path

    return _get_config_path()


def _profile_ctx():
    """Per-request active-profile context for INTELLECT_HOME-scoped writes."""
    from api.profiles import cron_profile_context_for_home, get_active_intellect_home

    return cron_profile_context_for_home(get_active_intellect_home())


def _load_user_yaml(path: Path, strict: bool = False) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        # Log the exception type only — PyYAML messages can quote the offending
        # line, and that line may contain an inline secret.
        logger.warning("Failed to parse %s: %s", path, type(exc).__name__)
        if strict and path.stat().st_size > 0:
            raise ConfigYamlSyntaxError(
                "config.yaml has syntax errors — fix it by hand (or via "
                "`intellect config`) before saving gateway settings"
            ) from exc
        return {}
    if not isinstance(data, dict):
        # A valid-but-non-mapping yaml (a bare list/string) would be treated
        # as empty and clobbered by the yaml write path — refuse under strict.
        if strict and path.stat().st_size > 0:
            raise ConfigYamlSyntaxError(
                "config.yaml is not a mapping (expected top-level keys) — "
                "fix it by hand before saving gateway settings"
            )
        return {}
    return data


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key:
                values[key] = val
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
    return values


def _collect_env_values() -> Dict[str, str]:
    """Mirror the gateway's env view: profile .env > repo .env > process env.

    ``gateway/run.py`` loads the profile ``.env`` with ``override=True`` and
    the repo ``.env`` as fill-missing fallback, so a key set in the profile
    ``.env`` wins even over a stale shell export.
    """
    from api.profiles import get_active_intellect_home

    merged: Dict[str, str] = {}
    home_env = get_active_intellect_home() / ".env"
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    # Later sources win: profile .env overrides the repo .env overrides the
    # inherited process environment — matching load_intellect_dotenv.
    for source in (os.environ, _parse_env_file(repo_env), _parse_env_file(home_env)):
        merged.update({k: v for k, v in source.items() if v})
    return merged


def _read_runtime_platforms() -> Dict[str, dict]:
    """Per-platform live state written by the gateway process, read once."""
    try:
        from gateway.status import read_runtime_status

        data = read_runtime_status() or {}
        platforms = data.get("platforms")
        return platforms if isinstance(platforms, dict) else {}
    except Exception:
        logger.debug("runtime status unavailable", exc_info=True)
        return {}


def _platform_runtime(name: str, runtime_platforms: Optional[Dict[str, dict]] = None) -> Optional[dict]:
    """One platform's entry from the gateway's runtime status, token-masked."""
    try:
        platforms = runtime_platforms if runtime_platforms is not None else _read_runtime_platforms()
        entry = platforms.get(name)
        if isinstance(entry, dict):
            if "error_message" in entry:
                entry = {**entry, "error_message": _mask_tokenish(entry.get("error_message"))}
            return entry
    except Exception:
        logger.debug("runtime status unavailable", exc_info=True)
    return None


def _env_name(field_def) -> str:
    return field_def.target.split(":", 1)[1]


_RESERVED_PLATFORM_KEYS = {"enabled", "extra", "token", "api_key", "home_channel"}


def _coerce_scalar(value: Any) -> Any:
    """Parse a generic-editor string as JSON when possible, else keep the str.

    "8645" → int, "true" → bool, "[1, 2]" → list; anything that doesn't parse
    stays a string. NaN/Infinity stay strings (json.loads accepts them as
    floats by default, which would leak into YAML as ``.nan``).
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        import json

        return json.loads(text, parse_constant=lambda c: c)
    except (ValueError, TypeError):
        return value


def _legacy_gateway_json_platforms() -> Dict[str, dict]:
    """platforms map from the legacy ``<home>/gateway.json`` base layer.

    The loader reads gateway.json under config.yaml; mirror that here so
    users configured only via the legacy file don't show up as unconfigured.
    """
    import json

    from api.profiles import get_active_intellect_home

    path = get_active_intellect_home() / "gateway.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, type(exc).__name__)
        return {}
    platforms = data.get("platforms") if isinstance(data, dict) else None
    return platforms if isinstance(platforms, dict) else {}


def _merged_platform_blocks(user_yaml: dict, legacy_platforms: dict, name: str):
    """Merge the config sources for one platform, mirroring loader precedence.

    Returns ``(section, nested, nested_extra, explicit, legacy_enabled)``.
    Source order, weakest first — matching ``load_gateway_config``:
    ``gateway.json`` → ``gateway.platforms.<name>`` → ``platforms.<name>``
    → top-level ``<name>:`` section. ``explicit`` reflects any explicit
    ``enabled`` in the config.yaml layers (all of them get the loader's
    explicit marker since D1); the legacy gateway.json flag never does.
    """
    section = user_yaml.get(name)
    section = section if isinstance(section, dict) else {}
    platforms_map = user_yaml.get("platforms")
    platforms_map = platforms_map if isinstance(platforms_map, dict) else {}
    nested = platforms_map.get(name)
    nested = nested if isinstance(nested, dict) else {}
    nested_extra = nested.get("extra")
    nested_extra = nested_extra if isinstance(nested_extra, dict) else {}

    gateway_cfg = user_yaml.get("gateway")
    gp_map = gateway_cfg.get("platforms") if isinstance(gateway_cfg, dict) else None
    gp_block = gp_map.get(name) if isinstance(gp_map, dict) else {}
    gp_block = gp_block if isinstance(gp_block, dict) else {}
    gp_extra = gp_block.get("extra")
    gp_extra = gp_extra if isinstance(gp_extra, dict) else {}

    legacy = legacy_platforms.get(name)
    legacy = legacy if isinstance(legacy, dict) else {}
    legacy_extra = legacy.get("extra")
    legacy_extra = legacy_extra if isinstance(legacy_extra, dict) else {}

    merged_nested = {**legacy, **gp_block, **nested}
    merged_extra = {**legacy_extra, **gp_extra, **nested_extra}

    explicit = None
    # Use the loader's own bool coercion — hand-written yaml may hold
    # ``enabled: "false"`` as a string, and bool("false") is True.
    from gateway.config import _coerce_bool

    if "enabled" in section:
        explicit = _coerce_bool(section.get("enabled"), False)
    elif "enabled" in nested:
        explicit = _coerce_bool(nested.get("enabled"), False)
    elif "enabled" in gp_block:
        explicit = _coerce_bool(gp_block.get("enabled"), False)
    legacy_enabled = _coerce_bool(legacy.get("enabled"), False) if "enabled" in legacy else None
    return section, merged_nested, merged_extra, explicit, legacy_enabled


def _write_platform_enabled(user_yaml: dict, name: str, value: bool) -> None:
    """Set ``platforms.<name>.enabled``, dropping a hand-written top-level one.

    The loader's shared-key bridging lets the top-level ``<name>:`` section
    win over ``platforms.<name>`` — an explicit ``enabled`` there would
    silently ignore the toggle, so it must not survive the write.
    """
    top = user_yaml.get(name)
    if isinstance(top, dict):
        top.pop("enabled", None)
        if not top:
            user_yaml.pop(name, None)
    platforms_map = user_yaml.get("platforms")
    if not isinstance(platforms_map, dict):
        platforms_map = {}
        user_yaml["platforms"] = platforms_map
    block = platforms_map.get(name)
    if not isinstance(block, dict):
        block = {}
        platforms_map[name] = block
    block["enabled"] = bool(value)


def _iter_generic_platform_names() -> List[str]:
    """Platform names without a declarative schema (generic extra editor)."""
    from gateway.config import Platform

    from api.gateway_platform_schema import PLATFORM_SCHEMAS

    names: List[str] = []
    for member in Platform:
        if member == Platform.LOCAL:
            continue
        if member.value not in PLATFORM_SCHEMAS:
            names.append(member.value)
    try:
        from intellect_cli.plugins import discover_plugins
        from gateway.platform_registry import platform_registry

        discover_plugins()  # idempotent
        for entry in platform_registry.plugin_entries():
            if entry.name in PLATFORM_SCHEMAS or entry.name in names:
                continue
            try:
                Platform(entry.name)
            except (ValueError, KeyError):
                continue
            names.append(entry.name)
    except Exception:
        logger.debug("plugin platform discovery skipped", exc_info=True)
    return names


def _platform_connected(name: str, section: dict, nested: dict, nested_extra: dict,
                        env_vals: Dict[str, str], schema=None) -> bool:
    from gateway.config import Platform, platform_is_connected

    synthetic = _synthetic_platform_config(schema, section, nested, nested_extra, env_vals)
    try:
        future = _CHECK_EXECUTOR.submit(platform_is_connected, Platform(name), synthetic)
        return bool(future.result(timeout=_CHECK_TIMEOUT_S))
    except concurrent.futures.TimeoutError:
        logger.warning("connected check timed out for %s after %.1fs", name, _CHECK_TIMEOUT_S)
        return False
    except Exception:
        logger.debug("connected check failed for %s", name, exc_info=True)
        return False


def _yaml_target(field_def) -> Tuple[str, str]:
    section, key = field_def.target.split(":", 1)[1].split(".", 1)
    return section, key


def _field_set(field_def, section: dict, nested_extra: dict, env_vals: Dict[str, str]) -> bool:
    if field_def.target.startswith("env:"):
        return bool(env_vals.get(_env_name(field_def)))
    section_name, key = _yaml_target(field_def)
    if section.get(key) not in (None, "", [], {}):
        return True
    return nested_extra.get(key) not in (None, "", [], {})


def _required_satisfied(field_def, section: dict, nested: dict, nested_extra: dict,
                        env_vals: Dict[str, str]) -> bool:
    """Required-field check that also honors non-schema credential sources.

    Beyond the field's own env/yaml target, the equivalent credential may
    already live in the merged ``platforms.<name>`` block (e.g. a ``token``
    from legacy gateway.json, or ``extra.app_secret`` written by hand) — the
    connected checker consumes those, so the strict layer must too.
    """
    if _field_set(field_def, section, nested_extra, env_vals):
        return True
    if nested_extra.get(field_def.key) not in (None, "", [], {}):
        return True
    if field_def.key == "token" and nested.get("token"):
        return True
    if field_def.key == "api_key" and nested.get("api_key"):
        return True
    return False


def _yaml_field_value(field_def, section: dict, nested_extra: dict) -> Any:
    section_name, key = _yaml_target(field_def)
    if key in section:
        return section[key]
    return nested_extra.get(key)


def _synthetic_platform_config(schema, section: dict, nested: dict, nested_extra: dict,
                               env_vals: Dict[str, str]):
    """Build a PlatformConfig from merged sources to run the real connected-check."""
    from gateway.config import PlatformConfig

    extra: Dict[str, Any] = {}
    if isinstance(nested_extra, dict):
        extra.update(nested_extra)
    extra.update({k: v for k, v in section.items() if k not in ("enabled", "extra", "token", "api_key", "home_channel")})
    token = nested.get("token") or section.get("token")
    api_key = nested.get("api_key") or section.get("api_key")
    for field_def in (schema.env_fields if schema is not None else []):
        val = env_vals.get(_env_name(field_def))
        if not val:
            continue
        if field_def.key == "token":
            token = token or val
        elif field_def.key == "api_key":
            api_key = api_key or val
        else:
            if field_def.type == "int":
                try:
                    val = int(str(val).strip())
                except (TypeError, ValueError):
                    pass
            extra.setdefault(field_def.key, val)
    return PlatformConfig(
        enabled=bool(nested.get("enabled", section.get("enabled", True))),
        token=token,
        api_key=api_key,
        extra=extra,
    )


# ── GET ──────────────────────────────────────────────────────────────────────


def build_payload() -> dict:
    """Desensitized per-platform snapshot for the Gateway settings pane."""
    from api.gateway_platform_schema import PLATFORM_SCHEMAS

    user_yaml = _load_user_yaml(_config_path())
    legacy_platforms = _legacy_gateway_json_platforms()
    env_vals = _collect_env_values()
    runtime_platforms = _read_runtime_platforms()

    platforms_out: List[dict] = []
    for schema in PLATFORM_SCHEMAS.values():
        section, nested, nested_extra, explicit, legacy_enabled = _merged_platform_blocks(
            user_yaml, legacy_platforms, schema.name
        )

        missing_required = [
            f.key for f in schema.required_fields
            if not _required_satisfied(f, section, nested, nested_extra, env_vals)
        ]
        # "Configured" is the strict UI view: the real connected-check passes
        # AND every required field is present. The runtime connection dot
        # (from the gateway's own status) remains the ground truth for
        # "actually connected".
        check_ok = _platform_connected(
            schema.name, section, nested, nested_extra, env_vals, schema
        )
        configured = check_ok and not missing_required

        # Effective enabled mirrors the loader exactly:
        # - explicit config.yaml wins (top-level over nested);
        # - the legacy gateway.json enabled flag (non-explicit) holds unless
        #   env credentials auto-enable;
        # - without any explicit key, the trigger is the LOADER's trigger:
        #   complete env credentials for env-driven platforms, the tri-state
        #   enable_env switch (WHATSAPP_ENABLED) for bridge-paired platforms,
        #   or a plain yaml/legacy block for platforms with no env trigger.
        env_required = [f for f in schema.required_fields if f.target.startswith("env:")]
        has_block = bool(section or nested or legacy_enabled is not None)
        if schema.name == "weixin":
            # Loader's bespoke rule: auto-enable when the token+account_id
            # PAIR is complete from any source mix (env var or yaml-side
            # credential) — mirror it source-for-source or a mixed setup
            # shows disabled while the gateway runs it.
            has_token = bool(
                env_vals.get("WEIXIN_TOKEN")
                or nested.get("token")
                or nested_extra.get("token")
            )
            has_account = bool(
                env_vals.get("WEIXIN_ACCOUNT_ID") or nested_extra.get("account_id")
            )
            trigger = has_token and has_account
        elif schema.name == "qqbot":
            # Same pair-complete rule (app_id + client_secret, any source mix)
            # as the loader's qqbot env block.
            has_app_id = bool(env_vals.get("QQ_APP_ID") or nested_extra.get("app_id"))
            has_secret = bool(
                env_vals.get("QQ_CLIENT_SECRET") or nested_extra.get("client_secret")
            )
            trigger = has_app_id and has_secret
        elif env_required:
            trigger = all(bool(env_vals.get(_env_name(f))) for f in env_required)
        elif schema.enable_env:
            trigger = any(
                (env_vals.get(v) or "").strip().lower() in ("true", "1", "yes")
                for v in schema.enable_env
            )
        else:
            trigger = has_block
        enabled = explicit if explicit is not None else bool(legacy_enabled or trigger)

        fields_out: List[dict] = []
        for field_def in schema.fields:
            info: Dict[str, Any] = {
                "key": field_def.key,
                "label": field_def.label,
                "type": field_def.type,
                "required": field_def.required,
                "placeholder": field_def.placeholder,
                "help": field_def.help,
                "options": list(field_def.options),
            }
            if field_def.target.startswith("env:"):
                info["target"] = "env"
                info["env"] = _env_name(field_def)
                info["set"] = bool(env_vals.get(_env_name(field_def)))
            else:
                info["target"] = "yaml"
                info["value"] = _yaml_field_value(field_def, section, nested_extra)
            fields_out.append(info)

        platforms_out.append({
            "name": schema.name,
            "label": schema.label,
            "description": schema.description,
            "docs_url": schema.docs_url,
            "enabled": enabled,
            "enabled_explicit": explicit,
            "configured": configured,
            "missing_required": missing_required,
            "fields": fields_out,
            "runtime": _platform_runtime(schema.name, runtime_platforms),
        })

    # Generic fallback: platforms without a schema get the key-value extra
    # editor. The display dict is yaml-visible state only (the loader's
    # gateway.json base layer < nested extra < top-level bridged keys).
    # Secret-shaped keys are masked to the "(set)" sentinel; saving that
    # sentinel back keeps the stored value (see _save_generic_platform).
    for name in _iter_generic_platform_names():
        section, nested, nested_extra, explicit, legacy_enabled = _merged_platform_blocks(
            user_yaml, legacy_platforms, name
        )
        configured = _platform_connected(
            name, section, nested, nested_extra, env_vals
        )
        # Deliberately conservative (mirrors the loader, which never
        # auto-enables a plain yaml block): a pure-env setup may show
        # disabled here because generic platforms have no declared env
        # trigger — the runtime dot is the ground truth for those.
        enabled = explicit if explicit is not None else bool(legacy_enabled)
        display_extra: Dict[str, Any] = dict(nested_extra)
        display_extra.update({
            k: v for k, v in section.items() if k not in _RESERVED_PLATFORM_KEYS
        })
        masked_keys: List[str] = []
        for key in list(display_extra):
            if _SECRETISH_KEY_RE.search(str(key)) and display_extra[key] not in (None, "", [], {}):
                display_extra[key] = _MASKED_SENTINEL
                masked_keys.append(str(key))
        platforms_out.append({
            "name": name,
            "label": name.replace("_", " ").title(),
            "description": "",
            "docs_url": "",
            "generic": True,
            "enabled": enabled,
            "enabled_explicit": explicit,
            "configured": configured,
            "missing_required": [],
            "fields": [],
            "extra": display_extra,
            "extra_masked": sorted(masked_keys),
            "runtime": _platform_runtime(name, runtime_platforms),
        })

    return {"platforms": platforms_out}


# ── PUT ──────────────────────────────────────────────────────────────────────


def save_platform(handler, name: str, body: dict) -> bool:
    """Handle PUT /api/gateway/platforms/<name>."""
    from api.auth import is_loopback_client
    from api.helpers import bad

    if not is_loopback_client(handler):
        return bad(handler, "Gateway config changes require localhost access", 403)
    # Managed installs refuse early: save_env_value silently no-ops there, so
    # a save would write config.yaml while dropping the .env half. Must be a
    # direct return — bad() sends the response itself and returns None.
    from intellect_cli.config import is_managed

    if is_managed():
        return bad(
            handler,
            "This Intellect installation is managed — gateway settings are "
            "controlled by the managing system (e.g. the NixOS module), not "
            "the WebUI",
            403,
        )

    from api.gateway_platform_schema import get_schema

    schema = get_schema(name)
    if schema is None:
        return _save_generic_platform(handler, name, body)

    from api.helpers import j
    from api.gateway_platform_schema import normalize_value
    from intellect_cli.config import save_env_value
    from utils import atomic_yaml_write
    body = body if isinstance(body, dict) else {}
    fields_in = body.get("fields")
    secrets_in = body.get("secrets")
    enabled_in = body.get("enabled")
    fields_in = fields_in if isinstance(fields_in, dict) else {}
    secrets_in = secrets_in if isinstance(secrets_in, dict) else {}
    if enabled_in is not None and not isinstance(enabled_in, bool):
        return bad(handler, "enabled must be a boolean", 400)
    if not fields_in and not secrets_in and enabled_in is None:
        return bad(handler, "Nothing to save: provide fields, secrets and/or enabled", 400)

    env_updates: List[Tuple[Any, Optional[Any]]] = []
    yaml_updates: List[Tuple[Any, Optional[Any]]] = []
    for key, val in list(fields_in.items()) + list(secrets_in.items()):
        field_def = schema.field(str(key))
        if field_def is None:
            return bad(handler, f"Unknown field {key!r} for platform {schema.name}", 400)
        if str(key) in secrets_in and field_def.type != "secret":
            return bad(handler, f"Field {key!r} is not a secret", 400)
        if str(key) in fields_in and field_def.type == "secret":
            return bad(handler, f"Field {key!r} must be sent via secrets", 400)
        normalized, err = normalize_value(field_def, val)
        if err:
            return bad(handler, err, 400)
        if field_def.target.startswith("env:"):
            env_updates.append((field_def, normalized))
        else:
            yaml_updates.append((field_def, normalized))

    # Validate the enable request against post-write state: every required
    # field must be satisfied — from the request itself, or from ANY source
    # the loader would accept (env, top-level/nested yaml, legacy extra),
    # matching the GET view so "configured" users are never refused here.
    if enabled_in is True:
        env_vals = _collect_env_values()
        user_yaml = _load_user_yaml(_config_path())
        section, nested, nested_extra, _, _ = _merged_platform_blocks(
            user_yaml, _legacy_gateway_json_platforms(), schema.name
        )
        incoming_env = {
            _env_name(f): v for f, v in env_updates if v is not None
        }
        incoming_yaml = {}
        for f, v in yaml_updates:
            if v is not None:
                _, key = _yaml_target(f)
                incoming_yaml[key] = v
        missing = []
        for field_def in schema.required_fields:
            if field_def.target.startswith("env:"):
                if incoming_env.get(_env_name(field_def)):
                    continue
            else:
                _, key = _yaml_target(field_def)
                if key in incoming_yaml:
                    continue
            if _required_satisfied(field_def, section, nested, nested_extra, env_vals):
                continue
            missing.append(field_def.key)
        if missing:
            return bad(
                handler,
                f"Cannot enable {schema.name}: missing required fields: {', '.join(missing)}",
                400,
            )

    warnings: List[str] = []
    try:
        from intellect_cli.auth import has_usable_secret
    except ImportError:
        has_usable_secret = None
    required_env_names = {
        _env_name(f) for f in schema.required_fields if f.target.startswith("env:")
    }
    for field_def, val in env_updates:
        if field_def.type == "secret" and val and has_usable_secret is not None:
            if not has_usable_secret(val, min_length=4):
                warnings.append(
                    f"{field_def.key} looks like a placeholder value; "
                    "the gateway will refuse to start this adapter"
                )
        if val is None and _env_name(field_def) in required_env_names:
            warnings.append(
                f"{field_def.key} was cleared — {schema.name} cannot connect without it"
            )

    with _profile_ctx():
        # Preflight: refuse to touch anything if config.yaml is a non-empty
        # file that doesn't parse — the yaml write path would otherwise
        # replace the user's whole config with a near-empty one.
        if yaml_updates or enabled_in is not None:
            try:
                _load_user_yaml(_config_path(), strict=True)
            except ConfigYamlSyntaxError as exc:
                return bad(handler, str(exc), 400)
        for field_def, val in env_updates:
            # Empty value = clear the credential (dotenv load yields an empty
            # string, which every consumer treats as unset).
            save_env_value(_env_name(field_def), "" if val is None else str(val))

        if yaml_updates or enabled_in is not None:
            path = _config_path()
            user_yaml = _load_user_yaml(path)
            for field_def, val in yaml_updates:
                section_name, key = _yaml_target(field_def)
                block = user_yaml.get(section_name)
                if not isinstance(block, dict):
                    block = {}
                    user_yaml[section_name] = block
                if val is None:
                    block.pop(key, None)
                else:
                    block[key] = val
                if not block:
                    # Don't leave empty sections behind when clearing.
                    user_yaml.pop(section_name, None)
            if enabled_in is not None:
                _write_platform_enabled(user_yaml, schema.name, enabled_in)
            atomic_yaml_write(path, user_yaml, sort_keys=False)

        from api.config import reload_config

        reload_config()

    # Audit log: field keys only — never values, never secret contents.
    logger.info(
        "gateway platform config updated: platform=%s fields=%s secrets=%s enabled=%s",
        schema.name,
        sorted(fields_in),
        sorted(secrets_in),
        enabled_in,
    )
    return j(handler, {"ok": True, "needs_restart": True, "warnings": warnings})


def _save_generic_platform(handler, name: str, body: dict) -> bool:
    """PUT fallback for platforms without a schema: raw ``extra`` editor.

    Accepts ``{"extra": {key: value}, "enabled": bool}``. Values are coerced
    via JSON-parse-when-possible so ports/flags/whitelists keep their types.
    The whole ``platforms.<name>.extra`` block is replaced by what the form
    submitted (the form always edits the full dict). Loopback/managed gates
    are already enforced by :func:`save_platform`.
    """
    from api.helpers import bad, j

    from gateway.config import Platform

    try:
        Platform(name)
    except (ValueError, KeyError):
        try:
            from gateway.platform_registry import platform_registry

            if platform_registry.get(name) is None:
                return bad(handler, f"Unknown gateway platform {name!r}", 404)
        except Exception:
            return bad(handler, f"Unknown gateway platform {name!r}", 404)

    body = body if isinstance(body, dict) else {}
    extra_in = body.get("extra")
    enabled_in = body.get("enabled")
    if extra_in is None and enabled_in is None:
        return bad(handler, "Nothing to save: provide extra and/or enabled", 400)
    if extra_in is not None and not isinstance(extra_in, dict):
        return bad(handler, "extra must be an object", 400)
    if enabled_in is not None and not isinstance(enabled_in, bool):
        return bad(handler, "enabled must be a boolean", 400)

    cleaned: Optional[Dict[str, Any]] = None
    if extra_in is not None:
        cleaned = {}
        for raw_key, raw_val in extra_in.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if key in _RESERVED_PLATFORM_KEYS:
                return bad(
                    handler,
                    f"Key {key!r} is reserved and cannot be set via the "
                    "generic editor",
                    400,
                )
            cleaned[key] = _coerce_scalar(raw_val)
        # Sentinel keep: GET masks secret-shaped values as "(set)"; saving the
        # sentinel back restores the stored value instead of writing the
        # literal string. A masked key with no stored value is dropped.
        sentinel_keys = [k for k, v in cleaned.items() if v == _MASKED_SENTINEL]
        if sentinel_keys:
            user_yaml_now = _load_user_yaml(_config_path())
            _sec, _nested, _nextra, _, _ = _merged_platform_blocks(
                user_yaml_now, _legacy_gateway_json_platforms(), name
            )
            stored: Dict[str, Any] = dict(_nextra)
            stored.update({
                k: v for k, v in _sec.items() if k not in _RESERVED_PLATFORM_KEYS
            })
            for k in sentinel_keys:
                if k in stored and stored[k] != _MASKED_SENTINEL:
                    cleaned[k] = stored[k]
                else:
                    cleaned.pop(k)

    warnings: List[str] = []
    from utils import atomic_yaml_write

    with _profile_ctx():
        # Preflight against a broken config.yaml (see save_platform).
        try:
            _load_user_yaml(_config_path(), strict=True)
        except ConfigYamlSyntaxError as exc:
            return bad(handler, str(exc), 400)

        if cleaned is not None or enabled_in is not None:
            path = _config_path()
            user_yaml = _load_user_yaml(path)
            platforms_map = user_yaml.get("platforms")
            if not isinstance(platforms_map, dict):
                platforms_map = {}
                user_yaml["platforms"] = platforms_map
            block = platforms_map.get(name)
            if not isinstance(block, dict):
                block = {}
                platforms_map[name] = block
            if cleaned is not None:
                if cleaned:
                    block["extra"] = cleaned
                else:
                    block.pop("extra", None)
                if not block and enabled_in is None:
                    platforms_map.pop(name, None)
            if enabled_in is not None:
                _write_platform_enabled(user_yaml, name, enabled_in)
            atomic_yaml_write(path, user_yaml, sort_keys=False)

        if enabled_in is True:
            env_vals = _collect_env_values()
            user_yaml = _load_user_yaml(_config_path())
            section, nested, nested_extra, _, _ = _merged_platform_blocks(
                user_yaml, {}, name
            )
            if not _platform_connected(name, section, nested, nested_extra, env_vals):
                warnings.append(
                    f"Could not verify credentials for {name} — the gateway "
                    "will report the real connection state after restart"
                )

        from api.config import reload_config

        reload_config()

    logger.info(
        "gateway platform config updated: platform=%s generic extra keys=%s enabled=%s",
        name,
        sorted(cleaned) if cleaned is not None else None,
        enabled_in,
    )
    return j(handler, {"ok": True, "needs_restart": True, "warnings": warnings})
