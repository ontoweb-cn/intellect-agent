"""Encrypted API key storage for provider credentials migrated from config.yaml.

Secrets live in ``SecretStore`` (``secret-store.json``) under stable key names:

- ``provider:<provider_id>:api_key`` — model or ``providers.*`` entries
- ``custom_provider:<slug>:api_key`` — ``custom_providers[]`` entries (slug from name)

Resolution order (after migration removes plaintext from config):

1. SecretStore
2. Environment / ``.env`` (handled by callers)
3. Inline ``config.yaml`` values (legacy, emits save-time warning)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Iterator

from intellect_cli.providers import custom_provider_slug

logger = logging.getLogger(__name__)

_MIGRATION_HINT = (
    "Plaintext API keys detected in config.yaml. "
    "Run: intellect secrets store migrate-api-keys"
)

_PLACEHOLDER_SECRET_VALUES = frozenset({
    "your_api_key",
    "your_api_key_here",
    "your-api-key",
    "placeholder",
    "example",
    "dummy",
    "null",
    "none",
})


def _has_usable_secret(value: Any, *, min_length: int = 4) -> bool:
    """Return True when a configured secret looks usable, not empty/placeholder."""
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    if len(cleaned) < min_length:
        return False
    if cleaned.lower() in _PLACEHOLDER_SECRET_VALUES:
        return False
    return True


def provider_secret_key(provider_id: str) -> str:
    """SecretStore key for a registered or config provider id."""
    pid = (provider_id or "").strip().lower()
    if pid.startswith("custom:"):
        return f"custom_provider:{pid[7:]}:api_key"
    return f"provider:{pid}:api_key"


def custom_provider_secret_key(display_name: str) -> str:
    """SecretStore key for a ``custom_providers[]`` entry."""
    slug = custom_provider_slug(display_name)
    return provider_secret_key(slug)


def is_inline_plaintext_api_key(value: Any) -> bool:
    """True when *value* is a literal secret (not empty, not ``${ENV}``)."""
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    if not cleaned:
        return False
    if cleaned.startswith("${") and cleaned.endswith("}"):
        return False
    return _has_usable_secret(cleaned)


@dataclass
class PlaintextApiKeyEntry:
    """One plaintext key found in config.yaml."""

    secret_key: str
    value: str
    config_path: str
    provider_id: str = ""


@dataclass
class MigrateApiKeysResult:
    migrated: list[PlaintextApiKeyEntry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False


def iter_plaintext_api_keys(config: dict[str, Any]) -> Iterator[PlaintextApiKeyEntry]:
    """Yield plaintext API keys stored inline in *config*."""
    if not isinstance(config, dict):
        return

    model = config.get("model")
    if isinstance(model, dict):
        provider = str(model.get("provider") or "").strip().lower()
        for field_name in ("api_key", "api"):
            raw = model.get(field_name)
            if is_inline_plaintext_api_key(raw):
                sk = provider_secret_key(provider) if provider else "model:api_key"
                yield PlaintextApiKeyEntry(
                    secret_key=sk,
                    value=str(raw).strip(),
                    config_path=f"model.{field_name}",
                    provider_id=provider,
                )
                break

    providers_cfg = config.get("providers")
    if isinstance(providers_cfg, dict):
        for pid, entry in providers_cfg.items():
            if not isinstance(entry, dict):
                continue
            raw = entry.get("api_key")
            if is_inline_plaintext_api_key(raw):
                pid_norm = str(pid).strip().lower()
                yield PlaintextApiKeyEntry(
                    secret_key=provider_secret_key(pid_norm),
                    value=str(raw).strip(),
                    config_path=f"providers.{pid}.api_key",
                    provider_id=pid_norm,
                )

    custom_providers = config.get("custom_providers")
    if isinstance(custom_providers, list):
        for idx, entry in enumerate(custom_providers):
            if not isinstance(entry, dict):
                continue
            raw = entry.get("api_key")
            if not is_inline_plaintext_api_key(raw):
                continue
            name = str(entry.get("name") or f"entry-{idx}").strip()
            slug = custom_provider_slug(name)
            yield PlaintextApiKeyEntry(
                secret_key=provider_secret_key(slug),
                value=str(raw).strip(),
                config_path=f"custom_providers[{idx}].api_key",
                provider_id=slug,
            )


def _remove_plaintext_from_config(config: dict[str, Any], entry: PlaintextApiKeyEntry) -> None:
    """Remove one migrated plaintext key from *config* (in place)."""
    path = entry.config_path
    if path.startswith("model."):
        field_name = path.split(".", 1)[1]
        model = config.get("model")
        if isinstance(model, dict) and field_name in model:
            model.pop(field_name, None)
        return

    if path.startswith("providers."):
        parts = path.split(".")
        if len(parts) >= 3:
            pid = parts[1]
            providers_cfg = config.get("providers")
            if isinstance(providers_cfg, dict):
                pentry = providers_cfg.get(pid)
                if isinstance(pentry, dict):
                    pentry.pop("api_key", None)
        return

    if path.startswith("custom_providers["):
        try:
            idx = int(path.split("[", 1)[1].split("]", 1)[0])
        except (IndexError, ValueError):
            return
        custom = config.get("custom_providers")
        if isinstance(custom, list) and 0 <= idx < len(custom):
            centry = custom[idx]
            if isinstance(centry, dict):
                centry.pop("api_key", None)


def migrate_api_keys_from_config(
    config: dict[str, Any],
    *,
    dry_run: bool = False,
    store=None,
) -> MigrateApiKeysResult:
    """Move plaintext config.yaml API keys into SecretStore."""
    from agent.secret_store import SecretStore

    result = MigrateApiKeysResult(dry_run=dry_run)
    if store is None:
        store = SecretStore()

    seen_secret_keys: set[str] = set()
    entries = list(iter_plaintext_api_keys(config))

    for entry in entries:
        if entry.secret_key in seen_secret_keys:
            result.skipped.append(
                f"{entry.config_path} (duplicate of {entry.secret_key})"
            )
            if not dry_run:
                _remove_plaintext_from_config(config, entry)
            continue

        existing = store.get_secret(entry.secret_key)
        if existing and existing.strip() and existing.strip() != entry.value:
            result.skipped.append(
                f"{entry.config_path} ({entry.secret_key} already set to a different value)"
            )
            continue

        if not dry_run:
            store.set_secret(entry.secret_key, entry.value)
            _remove_plaintext_from_config(config, entry)

        seen_secret_keys.add(entry.secret_key)
        result.migrated.append(entry)

    return result


def resolve_secret_store_provider_key(provider_id: str, *, store=None) -> tuple[str, str]:
    """Return (api_key, source) from SecretStore for *provider_id*."""
    pid = (provider_id or "").strip().lower()
    if not pid:
        return "", ""

    from agent.secret_store import SecretStore

    if store is None:
        store = SecretStore()

    key_name = provider_secret_key(pid)
    val = store.get_secret(key_name)
    if val and _has_usable_secret(val.strip()):
        return val.strip(), f"secret_store:{key_name}"

    return "", ""


def resolve_config_yaml_provider_key(
    provider_id: str, *, config: dict | None = None
) -> tuple[str, str]:
    """Return (api_key, source) from inline config.yaml for *provider_id*."""
    from intellect_cli.config import load_config

    if config is None:
        raw = load_config()
        config = raw if isinstance(raw, dict) else {}

    pid = (provider_id or "").strip().lower()
    if not pid:
        return "", ""

    model = config.get("model")
    if isinstance(model, dict):
        active = str(model.get("provider") or "").strip().lower()
        if active == pid:
            for field_name in ("api_key", "api"):
                raw = model.get(field_name)
                if is_inline_plaintext_api_key(raw) and _has_usable_secret(str(raw).strip()):
                    return str(raw).strip(), f"config:model.{field_name}"

    providers_cfg = config.get("providers")
    if isinstance(providers_cfg, dict):
        pentry = providers_cfg.get(pid)
        if isinstance(pentry, dict):
            raw = pentry.get("api_key")
            if is_inline_plaintext_api_key(raw) and _has_usable_secret(str(raw).strip()):
                return str(raw).strip(), f"config:providers.{pid}.api_key"

    custom_providers = config.get("custom_providers")
    if isinstance(custom_providers, list):
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            slug = custom_provider_slug(name) if name else ""
            if slug == pid or (name and name.strip().lower() == pid):
                raw = entry.get("api_key")
                if is_inline_plaintext_api_key(raw) and _has_usable_secret(str(raw).strip()):
                    return str(raw).strip(), "config:custom_providers.api_key"

    return "", ""


def resolve_model_inline_api_key(
    model_cfg: dict[str, Any],
    *,
    provider_hint: str = "",
    store=None,
) -> str:
    """Resolve model-level api_key: SecretStore first, then inline config."""
    provider = str(model_cfg.get("provider") or provider_hint or "").strip().lower()
    if provider:
        key, _ = resolve_secret_store_provider_key(provider, store=store)
        if key:
            return key

    for field_name in ("api_key", "api"):
        raw = model_cfg.get(field_name)
        if is_inline_plaintext_api_key(raw):
            return str(raw).strip()
    return ""


def find_plaintext_api_key_paths(config: dict[str, Any]) -> list[str]:
    """Return config paths that still hold inline plaintext API keys."""
    return [e.config_path for e in iter_plaintext_api_keys(config)]


def warn_if_plaintext_api_keys_in_config(config: dict[str, Any]) -> None:
    """Log and print a migration hint when config still has plaintext keys."""
    paths = find_plaintext_api_key_paths(config)
    if not paths:
        return
    detail = ", ".join(paths)
    logger.warning("%s (%s)", _MIGRATION_HINT, detail)
    try:
        sys.stderr.write(f"⚠️  intellect: {_MIGRATION_HINT}\n")
        sys.stderr.write(f"   Found in: {detail}\n")
        sys.stderr.flush()
    except Exception:
        logger.debug("failed to write plaintext api_key migration hint", exc_info=True)


def cmd_migrate_api_keys(args) -> int:
    """CLI handler for ``intellect secrets store migrate-api-keys``."""
    from intellect_cli.config import load_config, save_config

    config = load_config()
    if not isinstance(config, dict):
        config = {}

    dry_run = bool(getattr(args, "dry_run", False))
    result = migrate_api_keys_from_config(config, dry_run=dry_run)

    if not result.migrated and not result.skipped:
        print("No plaintext API keys found in config.yaml.")
        return 0

    for entry in result.migrated:
        prefix = "[dry-run] would migrate" if dry_run else "Migrated"
        print(f"{prefix} {entry.config_path} → {entry.secret_key}")

    for note in result.skipped:
        print(f"Skipped: {note}")

    if result.migrated and not dry_run:
        save_config(config)
        print("Removed plaintext keys from config.yaml.")

    return 0
