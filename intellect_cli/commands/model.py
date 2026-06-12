"""Model provider selection and configuration.

Extracted from ``intellect_cli/main.py`` — the ``intellect model`` command
and all provider-specific setup flows.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def cmd_model(args):
    """Select default model — starts with provider selection, then model picker."""
    from intellect_cli.main import _require_tty
    _require_tty("model")
    if getattr(args, "refresh", False):
        try:
            from intellect_cli.models import clear_provider_models_cache
            clear_provider_models_cache()
            print("  Cleared model picker cache.")
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
    select_provider_and_model(args=args)


def _is_profile_api_key_provider(provider_id: str) -> bool:
    """Return True when provider_id maps to a profile with auth_type='api_key'.

    Used as a catch-all in select_provider_and_model() so that new providers
    declared in plugins/model-providers/<name>/ automatically dispatch to _model_flow_api_key_provider
    without requiring an explicit elif branch here.
    """
    try:
        from providers import get_provider_profile
        _p = get_provider_profile(provider_id)
        return _p is not None and _p.auth_type == "api_key"
    except Exception:
        return False


def select_provider_and_model(args=None):
    """Core provider selection + model picking logic.

    Shared by ``cmd_model`` (``intellect model``) and the setup wizard
    (``setup_model_provider`` in setup.py).  Handles the full flow:
    provider picker, credential prompting, model selection, and config
    persistence.
    """
    from intellect_cli.auth import (
        resolve_provider,
        AuthError,
        format_auth_error,
    )
    from intellect_cli.config import (
        get_compatible_custom_providers,
        load_config,
        get_env_value,
    )
    from intellect_cli.providers import resolve_provider_full

    config = load_config()
    current_model = config.get("model")
    if isinstance(current_model, dict):
        current_model = current_model.get("default", "")
    current_model = current_model or "(not set)"

    # Read effective provider the same way the CLI does at startup:
    # config.yaml model.provider > env var > auto-detect
    config_provider = None
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        config_provider = model_cfg.get("provider")

    effective_provider = (
        config_provider or os.getenv("INTELLECT_INFERENCE_PROVIDER") or "auto"
    )
    compatible_custom_providers = get_compatible_custom_providers(config)
    def _named_custom_provider_map(cfg) -> dict[str, dict[str, str]]:
        from intellect_cli.config import read_raw_config

        # Build lookups of raw (un-expanded) templates keyed by a
        # stable identity. We intentionally bypass
        # ``get_compatible_custom_providers(read_raw_config())`` here because
        # its ``_normalize_custom_provider_entry`` step calls ``urlparse()``
        # on ``base_url`` and drops any entry whose ``base_url`` is itself an
        # env-ref template (e.g. ``${NEURALWATT_API_BASE}``). Dropping those
        # entries is exactly how env-ref preservation fails for the user
        # config that motivated this fix.
        raw_api_key_refs: dict[tuple, str] = {}
        raw_base_url_refs: dict[tuple, str] = {}
        raw_cfg = read_raw_config()

        def _record_raw(
            name: str,
            provider_key: str,
            model: str,
            api_key: str,
            base_url: str,
        ) -> None:
            template = str(api_key or "").strip()
            base_template = str(base_url or "").strip()
            name = str(name or "").strip()
            provider_key = str(provider_key or "").strip()
            model = str(model or "").strip()
            # Index by every plausible identity the loaded (expanded) config
            # might present: (name), (name, model), (provider_key), and
            # (provider_key, model). Case-insensitive on name/provider_key so
            # the loaded entry matches regardless of display casing.
            identities = []
            if name:
                identities.extend(((name.lower(),), (name.lower(), model)))
            if provider_key:
                identities.extend(
                    ((provider_key.lower(),), (provider_key.lower(), model))
                )
            if "${" in template:
                for identity in identities:
                    raw_api_key_refs.setdefault(identity, template)
            if "${" in base_template:
                for identity in identities:
                    raw_base_url_refs.setdefault(identity, base_template)

        raw_list = raw_cfg.get("custom_providers")
        if isinstance(raw_list, list):
            for raw_entry in raw_list:
                if not isinstance(raw_entry, dict):
                    continue
                _record_raw(
                    raw_entry.get("name", ""),
                    "",
                    raw_entry.get("model", "") or raw_entry.get("default_model", ""),
                    raw_entry.get("api_key", ""),
                    raw_entry.get("base_url", "")
                    or raw_entry.get("url", "")
                    or raw_entry.get("api", ""),
                )
        raw_providers = raw_cfg.get("providers")
        if isinstance(raw_providers, dict):
            for raw_key, raw_entry in raw_providers.items():
                if not isinstance(raw_entry, dict):
                    continue
                _record_raw(
                    raw_entry.get("name", "") or raw_key,
                    raw_key,
                    raw_entry.get("model", "") or raw_entry.get("default_model", ""),
                    raw_entry.get("api_key", ""),
                    raw_entry.get("base_url", "")
                    or raw_entry.get("url", "")
                    or raw_entry.get("api", ""),
                )

        def _lookup_ref(
            refs: dict[tuple, str],
            name: str,
            provider_key: str,
            model: str,
        ) -> str:
            name_lc = str(name or "").strip().lower()
            pkey_lc = str(provider_key or "").strip().lower()
            model = str(model or "").strip()
            for identity in (
                (pkey_lc, model),
                (pkey_lc,),
                (name_lc, model),
                (name_lc,),
            ):
                if identity[0] and identity in refs:
                    return refs[identity]
            return ""

        custom_provider_map = {}
        for entry in get_compatible_custom_providers(cfg):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").strip()
            base_url = (entry.get("base_url") or "").strip()
            if not name or not base_url:
                continue
            key = "custom:" + name.lower().replace(" ", "-")
            provider_key = (entry.get("provider_key") or "").strip()
            if provider_key:
                try:
                    resolve_provider(provider_key)
                except AuthError:
                    key = provider_key
            custom_provider_map[key] = {
                "name": name,
                "base_url": base_url,
                "api_key": entry.get("api_key", ""),
                "key_env": entry.get("key_env", ""),
                "model": entry.get("model", ""),
                "api_mode": entry.get("api_mode", ""),
                "provider_key": provider_key,
                "api_key_ref": _lookup_ref(
                    raw_api_key_refs, name, provider_key, entry.get("model", "")
                ),
                "base_url_ref": _lookup_ref(
                    raw_base_url_refs, name, provider_key, entry.get("model", "")
                ),
            }
        return custom_provider_map

    def _norm_base_url(url: str) -> str:
        return str(url or "").strip().rstrip("/").lower()

    # Add user-defined custom providers from config.yaml
    _custom_provider_map = _named_custom_provider_map(
        config
    )  # key → {name, base_url, api_key}

    def _active_custom_key_from_base_url() -> str:
        if effective_provider != "custom" or not isinstance(model_cfg, dict):
            return ""
        current_base = _norm_base_url(model_cfg.get("base_url", ""))
        if not current_base:
            return ""
        for key, provider_info in _custom_provider_map.items():
            if _norm_base_url(provider_info.get("base_url", "")) == current_base:
                return key
        return ""

    active = _active_custom_key_from_base_url()
    if active is None:
        active = ""
    if not active and effective_provider != "auto":
        active_def = resolve_provider_full(
            effective_provider,
            config.get("providers"),
            compatible_custom_providers,
        )
        if active_def is not None:
            active = active_def.id
        else:
            warning = (
                f"Unknown provider '{effective_provider}'. Check 'intellect model' for "
                "available providers, or run 'intellect doctor' to diagnose config "
                "issues."
            )
            print(f"Warning: {warning} Falling back to auto provider detection.")
    if not active:
        try:
            active = resolve_provider("auto")
        except AuthError as exc:
            if effective_provider == "auto":
                warning = format_auth_error(exc)
                print(f"Warning: {warning} Falling back to auto provider detection.")
            active = None  # no provider yet; default to first in list

    # Detect custom endpoint
    if active == "openrouter" and get_env_value("OPENAI_BASE_URL"):
        active = "custom"

    from intellect_cli.models import (
        CANONICAL_PROVIDERS,
        _PROVIDER_LABELS,
        group_providers,
        provider_group_for_slug,
    )

    provider_labels = dict(_PROVIDER_LABELS)  # derive from canonical list
    if active and active in _custom_provider_map:
        active_label = _custom_provider_map[active]["name"]
    else:
        active_label = provider_labels.get(active, active) if active else "none"

    print()
    print(f"  Current model:    {current_model}")
    print(f"  Active provider:  {active_label}")
    print()

    # Step 1: Provider selection.
    #
    # Canonical providers are folded into top-level groups (display only — see
    # PROVIDER_GROUPS in intellect_cli/models.py). A multi-member group shows one
    # row ("Kimi / Moonshot ▸"); picking it opens a member sub-picker that
    # resolves back to a concrete slug, so the dispatch chain below is
    # unchanged. Custom providers and the trailing actions stay flat.
    canonical_descs = {p.slug: p.tui_desc for p in CANONICAL_PROVIDERS}
    grouped_rows = group_providers([p.slug for p in CANONICAL_PROVIDERS])

    # The group/slug that should be pre-selected: the active provider's group
    # if it's grouped, otherwise the active slug itself.
    active_group = provider_group_for_slug(active) if active else ""

    # ordered entries: (key, label, members)
    #   members == [] → leaf row, key is a provider slug / action
    #   members != [] → group row, key is "group:<gid>"
    ordered: list[tuple[str, str, list[str]]] = []
    default_idx = 0
    for row in grouped_rows:
        if row["kind"] == "group":
            gid = row["group_id"]
            label = f"{row['label']} ▸"
            key = f"group:{gid}"
            is_active = bool(active_group) and gid == active_group
            members = row["members"]
        else:
            slug = row["slug"]
            label = canonical_descs.get(slug, provider_labels.get(slug, slug))
            key = slug
            is_active = bool(active) and slug == active
            members = []
        if is_active:
            ordered.append((key, f"{label}  ← currently active", members))
            default_idx = len(ordered) - 1
        else:
            ordered.append((key, label, members))

    for key, provider_info in _custom_provider_map.items():
        name = provider_info["name"]
        base_url = provider_info["base_url"]
        short_url = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        saved_model = provider_info.get("model", "")
        model_hint = f" — {saved_model}" if saved_model else ""
        label = f"{name} ({short_url}){model_hint}"
        if active and key == active:
            ordered.append((key, f"{label}  ← currently active", []))
            default_idx = len(ordered) - 1
        else:
            ordered.append((key, label, []))

    ordered.append(("custom", "Custom endpoint (enter URL manually)", []))
    _has_saved_custom_list = isinstance(config.get("custom_providers"), list) and bool(
        config.get("custom_providers")
    )
    if _has_saved_custom_list:
        ordered.append(("remove-custom", "Remove a saved custom provider", []))
    ordered.append(("aux-config", "Configure auxiliary models...", []))
    ordered.append(("cancel", "Leave unchanged", []))

    provider_idx = _prompt_provider_choice(
        [label for _, label, _ in ordered],
        default=default_idx,
    )
    if provider_idx is None or ordered[provider_idx][0] == "cancel":
        print("No change.")
        return

    selected_key = ordered[provider_idx][0]
    selected_members = ordered[provider_idx][2]

    # Group row → drill into a member sub-picker. Default to the active member
    # if the active provider lives in this group.
    if selected_members:
        member_default = 0
        if active in selected_members:
            member_default = selected_members.index(active)
        member_labels = [
            canonical_descs.get(m, provider_labels.get(m, m)) for m in selected_members
        ]
        member_idx = _prompt_provider_choice(member_labels, default=member_default)
        if member_idx is None:
            print("No change.")
            return
        selected_provider = selected_members[member_idx]
    else:
        selected_provider = selected_key

    if selected_provider == "aux-config":
        _aux_config_menu()
        return

    # Step 2: Provider-specific setup + model selection
    if selected_provider == "openrouter":
        _model_flow_openrouter(config, current_model)
    elif selected_provider == "ontoweb":
        _model_flow_ontoweb(config, current_model, args=args)
    elif selected_provider == "openai-codex":
        _model_flow_openai_codex(config, current_model)
    elif selected_provider == "xai-oauth":
        _model_flow_xai_oauth(config, current_model, args=args)
    elif selected_provider == "qwen-oauth":
        _model_flow_qwen_oauth(config, current_model)
    elif selected_provider == "minimax-oauth":
        _model_flow_minimax_oauth(config, current_model, args=args)
    elif selected_provider == "google-gemini-cli":
        _model_flow_google_gemini_cli(config, current_model)
    elif selected_provider == "copilot-acp":
        _model_flow_copilot_acp(config, current_model)
    elif selected_provider == "copilot":
        _model_flow_copilot(config, current_model)
    elif selected_provider == "custom":
        _model_flow_custom(config)
    elif (
        selected_provider.startswith("custom:")
        or selected_provider in _custom_provider_map
    ):
        provider_info = _named_custom_provider_map(load_config()).get(selected_provider)
        if provider_info is None:
            print(
                "Warning: the selected saved custom provider is no longer available. "
                "It may have been removed from config.yaml. No change."
            )
            return
        _model_flow_named_custom(config, provider_info)
    elif selected_provider == "remove-custom":
        _remove_custom_provider(config)
    elif selected_provider == "anthropic":
        _model_flow_anthropic(config, current_model)
    elif selected_provider == "kimi-coding":
        _model_flow_kimi(config, current_model)
    elif selected_provider == "stepfun":
        _model_flow_stepfun(config, current_model)
    elif selected_provider == "bedrock":
        _model_flow_bedrock(config, current_model)
    elif selected_provider == "azure-foundry":
        _model_flow_azure_foundry(config, current_model)
    elif selected_provider in {
        "openai-api",
        "gemini",
        "deepseek",
        "xai",
        "zai",
        "kimi-coding-cn",
        "minimax",
        "minimax-cn",
        "kilocode",
        "opencode-zen",
        "opencode-go",
        "alibaba",
        "huggingface",
        "xiaomi",
        "arcee",
        "gmi",
        "nvidia",
        "ollama-cloud",
        "tencent-tokenhub",
        "lmstudio",
    } or _is_profile_api_key_provider(selected_provider):
        _model_flow_api_key_provider(config, selected_provider, current_model)

    # ── Post-switch cleanup: clear stale OPENAI_BASE_URL ──────────────
    # When the user switches to a named provider (anything except "custom"),
    # a leftover OPENAI_BASE_URL in ~/.intellect/.env can poison auxiliary
    # clients that use provider:auto. Clear it proactively.  (#5161)
    if selected_provider not in {
        "custom",
        "cancel",
        "remove-custom",
    } and not selected_provider.startswith("custom:"):
        _clear_stale_openai_base_url()


def _clear_stale_openai_base_url():
    """Remove OPENAI_BASE_URL from ~/.intellect/.env if the active provider is not 'custom'.

    After a provider switch, a leftover OPENAI_BASE_URL causes auxiliary
    clients (compression, vision, delegation) with provider:auto to route
    requests to the old custom endpoint instead of the newly selected
    provider.  See issue #5161.
    """
    from intellect_cli.config import get_env_value, save_env_value, load_config

    cfg = load_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        provider = (model_cfg.get("provider") or "").strip().lower()
    else:
        provider = ""

    if provider == "custom" or not provider:
        return  # custom provider legitimately uses OPENAI_BASE_URL

    stale_url = get_env_value("OPENAI_BASE_URL")
    if stale_url:
        save_env_value("OPENAI_BASE_URL", "")
        print(
            f"Cleared stale OPENAI_BASE_URL from .env (was: {stale_url[:40]}...)"
            if len(stale_url) > 40
            else f"Cleared stale OPENAI_BASE_URL from .env (was: {stale_url})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliary model configuration
#
# Intellect uses lightweight "auxiliary" models for side tasks (vision analysis,
# context compression, web extraction, session search, etc.). Each task has
# its own provider+model pair in config.yaml under `auxiliary.<task>`.
#
# The UI lives behind "Configure auxiliary models..." at the bottom of the
# `intellect model` provider picker. It does NOT re-run credential setup — it
# only routes already-authenticated providers to specific aux tasks. Users
# configure new providers through the normal `intellect model` flow first.
# ─────────────────────────────────────────────────────────────────────────────

# (task_key, display_name, short_description)
_AUX_TASKS: list[tuple[str, str, str]] = [
    ("vision", "Vision", "image/screenshot analysis"),
    ("compression", "Compression", "context summarization"),
    ("web_extract", "Web extract", "web page summarization"),
    ("approval", "Approval", "smart command approval"),
    ("mcp", "MCP", "MCP tool reasoning"),
    ("title_generation", "Title generation", "session titles"),
    ("skills_hub", "Skills hub", "skills search/install"),
    ("triage_specifier", "Triage specifier", "kanban spec fleshing"),
    ("kanban_decomposer", "Kanban decomposer", "task decomposition"),
    ("profile_describer", "Profile describer", "auto profile descriptions"),
    ("curator", "Curator", "skill-usage review pass"),
    ("lightrag", "LightRAG", "conversation summary ingest for RAG"),
]


def _all_aux_tasks() -> list[tuple[str, str, str]]:
    """Return built-in + plugin-registered auxiliary tasks for picker/menu use.

    Built-in tasks come first (preserving order), followed by plugin tasks
    sorted by key. Used by ``_aux_config_menu``, ``_reset_aux_to_auto``, and
    display-name lookups so plugin-registered tasks (registered via
    :meth:`intellect_cli.plugins.PluginContext.register_auxiliary_task`) appear
    in the same surfaces as built-in ones without core knowing about them.
    """
    tasks = list(_AUX_TASKS)
    try:
        from intellect_cli.plugins import get_plugin_auxiliary_tasks
        for entry in get_plugin_auxiliary_tasks():
            tasks.append((entry["key"], entry["display_name"], entry["description"]))
    except Exception:
        # Plugin discovery failure must not break the aux config UI.
        # Built-in tasks remain available.
        pass
    return tasks


def _format_aux_current(task_cfg: dict) -> str:
    """Render the current aux config for display in the task menu."""
    if not isinstance(task_cfg, dict):
        return "auto"
    base_url = str(task_cfg.get("base_url") or "").strip()
    provider = str(task_cfg.get("provider") or "auto").strip() or "auto"
    model = str(task_cfg.get("model") or "").strip()
    if base_url:
        short = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        return f"custom ({short})" + (f" · {model}" if model else "")
    if provider == "auto":
        return "auto" + (f" · {model}" if model else "")
    if model:
        return f"{provider} · {model}"
    return provider


def _save_aux_choice(
    task: str,
    *,
    provider: str,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
) -> None:
    """Persist an auxiliary task's provider/model to config.yaml.

    Only writes the four routing fields — timeout, download_timeout, and any
    other task-specific settings are preserved untouched. The main model
    config (``model.default``/``model.provider``) is never modified.
    """
    from intellect_cli.config import load_config, save_config

    cfg = load_config()
    aux = cfg.setdefault("auxiliary", {})
    if not isinstance(aux, dict):
        aux = {}
        cfg["auxiliary"] = aux
    entry = aux.setdefault(task, {})
    if not isinstance(entry, dict):
        entry = {}
        aux[task] = entry
    entry["provider"] = provider
    entry["model"] = model or ""
    entry["base_url"] = base_url or ""
    entry["api_key"] = api_key or ""
    save_config(cfg)


def _reset_aux_to_auto() -> int:
    """Reset every known aux task back to auto/empty. Returns number reset.

    Includes plugin-registered tasks (via ``_all_aux_tasks``) so a plugin
    that contributed an auxiliary task gets reset alongside built-ins.
    """
    from intellect_cli.config import load_config, save_config

    cfg = load_config()
    aux = cfg.setdefault("auxiliary", {})
    if not isinstance(aux, dict):
        aux = {}
        cfg["auxiliary"] = aux
    count = 0
    for task, _name, _desc in _all_aux_tasks():
        entry = aux.setdefault(task, {})
        if not isinstance(entry, dict):
            entry = {}
            aux[task] = entry
        changed = False
        if entry.get("provider") not in {None, "", "auto"}:
            entry["provider"] = "auto"
            changed = True
        for field in ("model", "base_url", "api_key"):
            if entry.get(field):
                entry[field] = ""
                changed = True
        # Preserve timeout/download_timeout — those are user-tuned, not routing
        if changed:
            count += 1
    save_config(cfg)
    return count


def _aux_config_menu() -> None:
    """Top-level auxiliary-model picker — choose a task to configure.

    Loops until the user picks "Back" so multiple tasks can be configured
    without returning to the main provider menu.
    """
    from intellect_cli.config import load_config

    while True:
        cfg = load_config()
        aux = cfg.get("auxiliary", {}) if isinstance(cfg.get("auxiliary"), dict) else {}

        print()
        print("  Auxiliary models — side-task routing")
        print()
        print("  Side tasks (vision, compression, web extraction, etc.) default")
        print('  to your main chat model.  "auto" means "use my main model" —')
        print("  Intellect only falls back to a lightweight backend (OpenRouter,")
        print("  ONTOWEB Portal) if the main model is unavailable.  Override a")
        print("  task below if you want it pinned to a specific provider/model.")
        print()

        # Build the task menu with current settings inline
        all_tasks = _all_aux_tasks()
        name_col = max(len(name) for _, name, _ in all_tasks) + 2
        desc_col = max(len(desc) for _, _, desc in all_tasks) + 4
        entries: list[tuple[str, str]] = []
        for task_key, name, desc in all_tasks:
            task_cfg = (
                aux.get(task_key, {}) if isinstance(aux.get(task_key), dict) else {}
            )
            current = _format_aux_current(task_cfg)
            label = (
                f"{name.ljust(name_col)}{('(' + desc + ')').ljust(desc_col)}{current}"
            )
            entries.append((task_key, label))
        entries.append(("__reset__", "Reset all to auto"))
        entries.append(("__back__", "Back"))

        idx = _prompt_provider_choice(
            [label for _, label in entries],
            default=0,
        )
        if idx is None:
            return
        key = entries[idx][0]
        if key == "__back__":
            return
        if key == "__reset__":
            n = _reset_aux_to_auto()
            if n:
                print(f"Reset {n} auxiliary task(s) to auto.")
            else:
                print("All auxiliary tasks were already set to auto.")
            print()
            continue
        # Otherwise configure the specific task
        _aux_select_for_task(key)


def _aux_select_for_task(task: str) -> None:
    """Pick a provider + model for a single auxiliary task and persist it.

    Uses ``list_authenticated_providers()`` to only show providers the user
    has already configured. This avoids re-running OAuth/credential flows
    inside the aux picker — users set up new providers through the normal
    ``intellect model`` flow, then route aux tasks to them here.
    """
    from intellect_cli.config import load_config
    from intellect_cli.model_switch import list_authenticated_providers

    cfg = load_config()
    aux = cfg.get("auxiliary", {}) if isinstance(cfg.get("auxiliary"), dict) else {}
    task_cfg = aux.get(task, {}) if isinstance(aux.get(task), dict) else {}
    current_provider = str(task_cfg.get("provider") or "auto").strip() or "auto"
    current_model = str(task_cfg.get("model") or "").strip()
    current_base_url = str(task_cfg.get("base_url") or "").strip()

    display_name = next((name for key, name, _ in _all_aux_tasks() if key == task), task)

    # Gather authenticated providers (has credentials + curated model list)
    try:
        providers = list_authenticated_providers(
            current_provider=current_provider,
            current_model=current_model,
            current_base_url=current_base_url,
        )
    except Exception as exc:
        print(f"Could not detect authenticated providers: {exc}")
        providers = []

    entries: list[tuple[str, str, list[str]]] = []  # (slug, label, models)
    # "auto" always first
    auto_marker = (
        "  ← current" if current_provider == "auto" and not current_base_url else ""
    )
    entries.append(("__auto__", f"auto (recommended){auto_marker}", []))

    for p in providers:
        slug = p.get("slug", "")
        name = p.get("name") or slug
        total = p.get("total_models", 0)
        models = p.get("models") or []
        model_hint = f" — {total} models" if total else ""
        marker = (
            "  ← current" if slug == current_provider and not current_base_url else ""
        )
        entries.append((slug, f"{name}{model_hint}{marker}", list(models)))

    # Custom endpoint (raw base_url)
    custom_marker = "  ← current" if current_base_url else ""
    entries.append(("__custom__", f"Custom endpoint (direct URL){custom_marker}", []))
    entries.append(("__back__", "Back", []))

    print()
    print(f"  Configure {display_name} — current: {_format_aux_current(task_cfg)}")
    print()

    idx = _prompt_provider_choice([label for _, label, _ in entries], default=0)
    if idx is None:
        return
    slug, _label, models = entries[idx]

    if slug == "__back__":
        return

    if slug == "__auto__":
        _save_aux_choice(task, provider="auto", model="", base_url="", api_key="")
        print(f"{display_name}: reset to auto.")
        return

    if slug == "__custom__":
        _aux_flow_custom_endpoint(task, task_cfg)
        return

    # Regular provider — pick a model from its curated list
    _aux_flow_provider_model(task, slug, models, current_model)


def _aux_flow_provider_model(
    task: str,
    provider_slug: str,
    curated_models: list,
    current_model: str = "",
) -> None:
    """Prompt for a model under an already-authenticated provider, save to aux."""
    from intellect_cli.auth import _prompt_model_selection
    from intellect_cli.models import get_pricing_for_provider

    display_name = next((name for key, name, _ in _all_aux_tasks() if key == task), task)

    # Fetch live pricing for this provider (non-blocking)
    pricing: dict = {}
    try:
        pricing = get_pricing_for_provider(provider_slug) or {}
    except Exception:
        pricing = {}

    model_list = list(curated_models)

    # Let the user pick a model. _prompt_model_selection supports "Enter custom
    # model name" and cancel.  When there's no curated list (rare), fall back
    # to a raw input prompt.
    if not model_list:
        print(f"No curated model list for {provider_slug}.")
        print("Enter a model slug manually (blank = use provider default):")
        try:
            val = input("Model: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        selected = val or ""
    else:
        selected = _prompt_model_selection(
            model_list,
            current_model=current_model,
            pricing=pricing,
        )
        if selected is None:
            print("No change.")
            return

    _save_aux_choice(
        task, provider=provider_slug, model=selected or "", base_url="", api_key=""
    )
    if selected:
        print(f"{display_name}: {provider_slug} · {selected}")
    else:
        print(f"{display_name}: {provider_slug} (provider default model)")


def _aux_flow_custom_endpoint(task: str, task_cfg: dict) -> None:
    """Prompt for a direct OpenAI-compatible base_url + optional api_key/model."""
    from intellect_cli.secret_prompt import masked_secret_prompt

    display_name = next((name for key, name, _ in _all_aux_tasks() if key == task), task)
    current_base_url = str(task_cfg.get("base_url") or "").strip()
    current_model = str(task_cfg.get("model") or "").strip()

    print()
    print(f"  Custom endpoint for {display_name}")
    print("  Provide an OpenAI-compatible base URL (e.g. http://localhost:11434/v1)")
    print()
    try:
        url_prompt = (
            f"Base URL [{current_base_url}]: " if current_base_url else "Base URL: "
        )
        url = input(url_prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    url = url or current_base_url
    if not url:
        print("No URL provided. No change.")
        return
    try:
        model_prompt = (
            f"Model slug (optional) [{current_model}]: "
            if current_model
            else "Model slug (optional): "
        )
        model = input(model_prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    model = model or current_model
    try:
        api_key = masked_secret_prompt(
            "API key (optional, blank = use OPENAI_API_KEY): "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    _save_aux_choice(
        task,
        provider="custom",
        model=model,
        base_url=url,
        api_key=api_key,
    )
    short_url = url.replace("https://", "").replace("http://", "").rstrip("/")
    print(f"{display_name}: custom ({short_url})" + (f" · {model}" if model else ""))


def _prompt_provider_choice(choices, *, default=0):
    """Show provider selection menu with curses arrow-key navigation.

    Falls back to a numbered list when curses is unavailable (e.g. piped
    stdin, non-TTY environments).  Returns the selected index, or None
    if the user cancels.
    """
    try:
        from intellect_cli.setup import _curses_prompt_choice

        idx = _curses_prompt_choice("Select provider:", choices, default)
        if idx >= 0:
            print()
            return idx
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    # Fallback: numbered list
    print("Select provider:")
    for i, c in enumerate(choices, 1):
        marker = "→" if i - 1 == default else " "
        print(f"  {marker} {i}. {c}")
    print()
    while True:
        try:
            val = input(f"Choice [1-{len(choices)}] ({default + 1}): ").strip()
            if not val:
                return default
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            print(f"Please enter 1-{len(choices)}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def _model_flow_openrouter(config, current_model=""):
    """OpenRouter provider: ensure API key, then pick model."""
    from intellect_constants import OPENROUTER_BASE_URL
    from intellect_cli.auth import (
        ProviderConfig,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import get_env_value

    # Route through _prompt_api_key so users can replace a stale/broken key
    # in-flow (K/R/C) instead of having to edit ~/.intellect/.env by hand. The
    # previous bypass-when-key-exists branch left no way to recover from a
    # bad paste short of re-running `intellect setup` from scratch. OpenRouter
    # isn't in PROVIDER_REGISTRY so we synthesize a minimal pconfig.
    pconfig = ProviderConfig(
        id="openrouter",
        name="OpenRouter",
        auth_type="api_key",
        api_key_env_vars=("OPENROUTER_API_KEY",),
    )
    existing_key = get_env_value("OPENROUTER_API_KEY") or ""
    if not existing_key:
        print("Get one at: https://openrouter.ai/keys")
        print()
    _resolved, abort = _prompt_api_key(pconfig, existing_key, provider_id="openrouter")
    if abort:
        return

    from intellect_cli.models import model_ids, get_pricing_for_provider

    openrouter_models = model_ids(force_refresh=True)

    # Fetch live pricing (non-blocking — returns empty dict on failure)
    pricing = get_pricing_for_provider("openrouter", force_refresh=True)

    selected = _prompt_model_selection(
        openrouter_models, current_model=current_model, pricing=pricing
    )
    if selected:
        _save_model_choice(selected)

        # Update config provider and deactivate any OAuth provider
        from intellect_cli.config import load_config, save_config

        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "openrouter"
        model["base_url"] = OPENROUTER_BASE_URL
        model["api_mode"] = "chat_completions"
        save_config(cfg)
        deactivate_provider()
        print(f"Default model set to: {selected} (via OpenRouter)")
    else:
        print("No change.")


def _model_flow_ontoweb(config, current_model="", args=None):
    """ONTOWEB Portal provider: ensure logged in, then pick model."""
    from intellect_cli.auth import (
        get_provider_auth_state,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
        resolve_ontoweb_runtime_credentials,
        AuthError,
        format_auth_error,
        _login_ontoweb,
        PROVIDER_REGISTRY,
    )
    from intellect_cli.config import (
        get_env_value,
        load_config,
        save_config,
        save_env_value,
    )
    from intellect_cli.ontoweb_subscription import prompt_enable_tool_gateway

    state = get_provider_auth_state("ontoweb")
    if not state or not state.get("access_token"):
        print("Not logged into ONTOWEB Portal. Starting login...")
        print()
        try:
            mock_args = argparse.Namespace(
                portal_url=getattr(args, "portal_url", None),
                inference_url=getattr(args, "inference_url", None),
                client_id=getattr(args, "client_id", None),
                scope=getattr(args, "scope", None),
                no_browser=bool(getattr(args, "no_browser", False)),
                timeout=getattr(args, "timeout", None) or 15.0,
                ca_bundle=getattr(args, "ca_bundle", None),
                insecure=bool(getattr(args, "insecure", False)),
            )
            _login_ontoweb(mock_args, PROVIDER_REGISTRY["ontoweb"])
            # Offer Tool Gateway enablement for paid subscribers
            try:
                _refreshed = load_config() or {}
                prompt_enable_tool_gateway(_refreshed)
            except Exception:
                logger.debug('non-critical operation failed', exc_info=True)
        except SystemExit:
            print("Login cancelled or failed.")
            return
        except Exception as exc:
            print(f"Login failed: {exc}")
            return
        # login_nous already handles model selection + config update
        return

    # Already logged in — use curated model list (same as OpenRouter defaults).
    # The live /models endpoint returns hundreds of models; the curated list
    # shows only agentic models users recognize from OpenRouter.
    from intellect_cli.models import (
        get_curated_ontoweb_model_ids,
        get_pricing_for_provider,
        check_ontoweb_free_tier,
        partition_ontoweb_models_by_tier,
        union_with_portal_free_recommendations,
        union_with_portal_paid_recommendations,
    )

    model_ids = get_curated_ontoweb_model_ids()
    if not model_ids:
        print("No curated models available for ONTOWEB Portal.")
        return

    # Verify credentials are still valid (catches expired sessions early)
    try:
        creds = resolve_ontoweb_runtime_credentials()
    except Exception as exc:
        relogin = isinstance(exc, AuthError) and exc.relogin_required
        msg = format_auth_error(exc) if isinstance(exc, AuthError) else str(exc)
        if relogin:
            print(f"Session expired: {msg}")
            print("Re-authenticating with ONTOWEB Portal...\n")
            try:
                mock_args = argparse.Namespace(
                    portal_url=None,
                    inference_url=None,
                    client_id=None,
                    scope=None,
                    no_browser=False,
                    timeout=15.0,
                    ca_bundle=None,
                    insecure=False,
                )
                _login_ontoweb(mock_args, PROVIDER_REGISTRY["ontoweb"])
            except Exception as login_exc:
                print(f"Re-login failed: {login_exc}")
            return
        print(f"Could not verify credentials: {msg}")
        return

    # Fetch live pricing (non-blocking — returns empty dict on failure)
    pricing = get_pricing_for_provider("ontoweb")

    # Force fresh account data for model selection so recent credit purchases
    # are reflected immediately.
    free_tier = check_ontoweb_free_tier(force_fresh=True)
    if not free_tier:
        try:
            refreshed_creds = resolve_ontoweb_runtime_credentials(
                force_refresh=True,
            )
            if refreshed_creds:
                creds = refreshed_creds
        except Exception:
            # Runtime inference has its own paid-entitlement recovery path; do
            # not block model selection if this opportunistic refresh fails.
            pass

    # Resolve portal URL early — needed both for upgrade links and for the
    # freeRecommendedModels endpoint below.
    _ontoweb_portal_url = ""
    try:
        _ontoweb_state = get_provider_auth_state("ontoweb")
        if _ontoweb_state:
            _ontoweb_portal_url = _ontoweb_state.get("portal_base_url", "")
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    # For free users: partition models into selectable/unavailable based on
    # whether they are free per the Portal-reported pricing.  First augment
    # with the Portal's freeRecommendedModels list so newly-launched free
    # models show up even if this CLI build's hardcoded curated list and
    # docs-hosted manifest haven't caught up yet.
    #
    # For paid users: mirror the same idea with paidRecommendedModels so
    # newly-launched paid models surface in the picker too — independent
    # of CLI release cadence.
    unavailable_models: list[str] = []
    unavailable_message = ""
    if free_tier:
        try:
            from intellect_cli.ontoweb_account import (
                format_ontoweb_portal_entitlement_message,
                get_ontoweb_portal_account_info,
            )

            _account_info = get_ontoweb_portal_account_info(force_fresh=True)
            unavailable_message = (
                format_ontoweb_portal_entitlement_message(
                    _account_info,
                    capability="paid OntoWeb models",
                )
                or ""
            )
        except Exception:
            unavailable_message = ""
        model_ids, pricing = union_with_portal_free_recommendations(
            model_ids, pricing, _ontoweb_portal_url,
        )
        model_ids, unavailable_models = partition_ontoweb_models_by_tier(
            model_ids, pricing, free_tier=True
        )
    else:
        model_ids, pricing = union_with_portal_paid_recommendations(
            model_ids, pricing, _ontoweb_portal_url,
        )

    if not model_ids and not unavailable_models:
        print("No models available for ONTOWEB Portal after filtering.")
        return

    if free_tier and not model_ids:
        print("No free models currently available.")
        if unavailable_models:
            from intellect_cli.auth import DEFAULT_ONTOWEB_PORTAL_URL

            _url = (_ontoweb_portal_url or DEFAULT_ONTOWEB_PORTAL_URL).rstrip("/")
            print(unavailable_message or f"Upgrade at {_url} to access paid models.")
        return

    print(
        f'Showing {len(model_ids)} curated models — use "Enter custom model name" for others.'
    )

    selected = _prompt_model_selection(
        model_ids,
        current_model=current_model,
        pricing=pricing,
        unavailable_models=unavailable_models,
        portal_url=_ontoweb_portal_url,
        unavailable_message=unavailable_message,
    )
    if selected:
        _save_model_choice(selected)
        # Reactivate OntoWeb as the provider and update config
        inference_url = creds.get("base_url", "")
        _update_config_for_provider("ontoweb", inference_url)
        current_model_cfg = config.get("model")
        if isinstance(current_model_cfg, dict):
            model_cfg = dict(current_model_cfg)
        elif isinstance(current_model_cfg, str) and current_model_cfg.strip():
            model_cfg = {"default": current_model_cfg.strip()}
        else:
            model_cfg = {}
        model_cfg["provider"] = "ontoweb"
        model_cfg["default"] = selected
        if inference_url and inference_url.strip():
            model_cfg["base_url"] = inference_url.rstrip("/")
        else:
            model_cfg.pop("base_url", None)
        config["model"] = model_cfg
        # Clear any custom endpoint that might conflict
        if get_env_value("OPENAI_BASE_URL"):
            save_env_value("OPENAI_BASE_URL", "")
            save_env_value("OPENAI_API_KEY", "")
        save_config(config)
        print(f"Default model set to: {selected} (via ONTOWEB Portal)")
        # Offer Tool Gateway enablement for paid subscribers
        prompt_enable_tool_gateway(config)
    else:
        print("No change.")


def _model_flow_openai_codex(config, current_model=""):
    """OpenAI Codex provider: ensure logged in, then pick model."""
    from intellect_cli.auth import (
        get_codex_auth_status,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
        _login_openai_codex,
        PROVIDER_REGISTRY,
        DEFAULT_CODEX_BASE_URL,
    )
    from intellect_cli.codex_models import get_codex_model_ids

    status = get_codex_auth_status()
    if status.get("logged_in"):
        print("  OpenAI Codex credentials: ✓")
        print()
        print("    1. Use existing credentials")
        print("    2. Reauthenticate (new OAuth login)")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1/2/3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = "1"

        if choice == "2":
            print("Starting a fresh OpenAI Codex login...")
            print()
            try:
                mock_args = argparse.Namespace()
                _login_openai_codex(
                    mock_args,
                    PROVIDER_REGISTRY["openai-codex"],
                    force_new_login=True,
                )
            except SystemExit:
                print("Login cancelled or failed.")
                return
            except Exception as exc:
                print(f"Login failed: {exc}")
                return
            status = get_codex_auth_status()
            if not status.get("logged_in"):
                print("Login failed.")
                return
        elif choice == "3":
            return
    else:
        print("Not logged into OpenAI Codex. Starting login...")
        print()
        try:
            mock_args = argparse.Namespace()
            _login_openai_codex(mock_args, PROVIDER_REGISTRY["openai-codex"])
        except SystemExit:
            print("Login cancelled or failed.")
            return
        except Exception as exc:
            print(f"Login failed: {exc}")
            return

    _codex_token = None
    # Prefer credential pool (where `intellect auth` stores device_code tokens),
    # fall back to legacy provider state.
    try:
        _codex_status = get_codex_auth_status()
        if _codex_status.get("logged_in"):
            _codex_token = _codex_status.get("api_key")
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)
    if not _codex_token:
        try:
            from intellect_cli.auth import resolve_codex_runtime_credentials

            _codex_creds = resolve_codex_runtime_credentials()
            _codex_token = _codex_creds.get("api_key")
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)

    codex_models = get_codex_model_ids(access_token=_codex_token)

    selected = _prompt_model_selection(codex_models, current_model=current_model)
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider("openai-codex", DEFAULT_CODEX_BASE_URL)
        print(f"Default model set to: {selected} (via OpenAI Codex)")
    else:
        print("No change.")


def _model_flow_xai_oauth(_config, current_model="", *, args=None):
    """xAI Grok OAuth (SuperGrok / Premium+) provider: ensure logged in, then pick model."""
    from intellect_cli.auth import (
        get_xai_oauth_auth_status,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
        resolve_xai_oauth_runtime_credentials,
        _login_xai_oauth,
        DEFAULT_XAI_OAUTH_BASE_URL,
        PROVIDER_REGISTRY,
    )
    from intellect_cli.models import _PROVIDER_MODELS

    status = get_xai_oauth_auth_status()
    if status.get("logged_in"):
        print("  xAI Grok OAuth (SuperGrok / Premium+) credentials: ✓")
        print()
        print("    1. Use existing credentials")
        print("    2. Reauthenticate (new OAuth login)")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1/2/3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = "1"

        if choice == "2":
            print("Starting a fresh xAI OAuth login...")
            print()
            try:
                # Forward CLI flags from ``intellect model --manual-paste``
                # / ``--no-browser`` / ``--timeout`` into the loopback
                # login. Without this, browser-only remotes (#26923)
                # can't reach the manual-paste path via ``intellect model``.
                mock_args = argparse.Namespace(
                    manual_paste=bool(getattr(args, "manual_paste", False)),
                    no_browser=bool(getattr(args, "no_browser", False)),
                    timeout=getattr(args, "timeout", None),
                )
                _login_xai_oauth(
                    mock_args,
                    PROVIDER_REGISTRY["xai-oauth"],
                    force_new_login=True,
                )
            except SystemExit:
                print("Login cancelled or failed.")
                return
            except Exception as exc:
                print(f"Login failed: {exc}")
                return
        elif choice == "3":
            return
    else:
        print("Not logged into xAI Grok OAuth (SuperGrok / Premium+). Starting login...")
        print()
        try:
            mock_args = argparse.Namespace(
                manual_paste=bool(getattr(args, "manual_paste", False)),
                no_browser=bool(getattr(args, "no_browser", False)),
                timeout=getattr(args, "timeout", None),
            )
            _login_xai_oauth(mock_args, PROVIDER_REGISTRY["xai-oauth"])
        except SystemExit:
            print("Login cancelled or failed.")
            return
        except Exception as exc:
            print(f"Login failed: {exc}")
            return

    # Resolve a usable base URL.  ``resolve_xai_oauth_runtime_credentials``
    # only reads from the auth.json singleton — but credentials may legitimately
    # live only in the pool (e.g. after ``intellect auth add xai-oauth``).  Fall
    # back to the default base URL in that case so the model picker still
    # completes successfully instead of bailing out with
    # ``Could not resolve xAI OAuth credentials``.
    base_url = DEFAULT_XAI_OAUTH_BASE_URL
    try:
        creds = resolve_xai_oauth_runtime_credentials()
        base_url = (creds.get("base_url") or "").strip().rstrip("/") or base_url
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    models = list(_PROVIDER_MODELS.get("xai-oauth") or _PROVIDER_MODELS.get("xai") or [])
    selected = _prompt_model_selection(models, current_model=current_model or (models[0] if models else "grok-4.3"))
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider("xai-oauth", base_url)
        print(f"Default model set to: {selected} (via xAI Grok OAuth — SuperGrok / Premium+)")
    else:
        print("No change.")


_DEFAULT_QWEN_PORTAL_MODELS = [
    "qwen3-coder-plus",
    "qwen3-coder",
]


def _model_flow_qwen_oauth(_config, current_model=""):
    """Qwen OAuth provider: reuse local Qwen CLI login, then pick model."""
    from intellect_cli.auth import (
        get_qwen_auth_status,
        resolve_qwen_runtime_credentials,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
        DEFAULT_QWEN_BASE_URL,
    )
    from intellect_cli.models import fetch_api_models

    status = get_qwen_auth_status()
    if not status.get("logged_in"):
        print("Not logged into Qwen CLI OAuth.")
        print("Run: qwen auth qwen-oauth")
        auth_file = status.get("auth_file")
        if auth_file:
            print(f"Expected credentials file: {auth_file}")
        if status.get("error"):
            print(f"Error: {status.get('error')}")
        return

    # Try live model discovery, fall back to curated list.
    models = None
    try:
        creds = resolve_qwen_runtime_credentials(refresh_if_expiring=True)
        models = fetch_api_models(creds["api_key"], creds["base_url"])
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)
    if not models:
        models = list(_DEFAULT_QWEN_PORTAL_MODELS)

    default = current_model or (models[0] if models else "qwen3-coder-plus")
    selected = _prompt_model_selection(models, current_model=default)
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider("qwen-oauth", DEFAULT_QWEN_BASE_URL)
        print(f"Default model set to: {selected} (via Qwen OAuth)")
    else:
        print("No change.")


def _model_flow_minimax_oauth(config, current_model="", args=None):
    """MiniMax OAuth provider: ensure logged in, then pick model."""
    from intellect_cli.auth import (
        get_provider_auth_state,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
        resolve_minimax_oauth_runtime_credentials,
        AuthError,
        format_auth_error,
        _login_minimax_oauth,
        PROVIDER_REGISTRY,
    )

    state = get_provider_auth_state("minimax-oauth")
    if not state or not state.get("access_token"):
        print("Not logged into MiniMax. Starting OAuth login...")
        print()
        try:
            mock_args = argparse.Namespace(
                region=getattr(args, "region", None) or "global",
                no_browser=bool(getattr(args, "no_browser", False)),
                timeout=getattr(args, "timeout", None) or 15.0,
            )
            _login_minimax_oauth(mock_args, PROVIDER_REGISTRY["minimax-oauth"])
        except SystemExit:
            print("Login cancelled or failed.")
            return
        except Exception as exc:
            print(f"Login failed: {exc}")
            return

    try:
        creds = resolve_minimax_oauth_runtime_credentials()
    except AuthError as exc:
        print(format_auth_error(exc))
        return

    from intellect_cli.models import _PROVIDER_MODELS

    model_ids = _PROVIDER_MODELS.get("minimax-oauth", [])
    selected = _prompt_model_selection(model_ids, current_model)
    if not selected:
        return
    _save_model_choice(selected)
    _update_config_for_provider("minimax-oauth", creds["base_url"])
    print(f"\u2713 Using MiniMax model: {selected}")


def _model_flow_google_gemini_cli(_config, current_model=""):
    """Google Gemini OAuth (PKCE) via Cloud Code Assist — supports free AND paid tiers.

    Flow:
      1. Show upfront warning about Google's ToS stance (per opencode-gemini-auth).
      2. If creds missing, run PKCE browser OAuth via agent.google_oauth.
      3. Resolve project context (env -> config -> auto-discover -> free tier).
      4. Prompt user to pick a model.
      5. Save to ~/.intellect/config.yaml.
    """
    from intellect_cli.auth import (
        DEFAULT_GEMINI_CLOUDCODE_BASE_URL,
        get_gemini_oauth_auth_status,
        resolve_gemini_oauth_runtime_credentials,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
    )
    from intellect_cli.models import _PROVIDER_MODELS

    print()
    print("⚠  Google considers using the Gemini CLI OAuth client with third-party")
    print("   software a policy violation. Some users have reported account")
    print("   restrictions. You can use your own API key via 'gemini' provider")
    print("   for the lowest-risk experience.")
    print()
    try:
        proceed = input("Continue with OAuth login? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.")
        return
    if proceed not in {"y", "yes"}:
        print("Cancelled.")
        return

    status = get_gemini_oauth_auth_status()
    if not status.get("logged_in"):
        try:
            from agent.google_oauth import resolve_project_id_from_env, start_oauth_flow

            env_project = resolve_project_id_from_env()
            start_oauth_flow(force_relogin=True, project_id=env_project)
        except Exception as exc:
            print(f"OAuth login failed: {exc}")
            return

    # Verify creds resolve + trigger project discovery
    try:
        creds = resolve_gemini_oauth_runtime_credentials(force_refresh=False)
        project_id = creds.get("project_id", "")
        if project_id:
            print(f"  Using GCP project: {project_id}")
        else:
            print(
                "  No GCP project configured — free tier will be auto-provisioned on first request."
            )
    except Exception as exc:
        print(f"Failed to resolve Gemini credentials: {exc}")
        return

    models = list(_PROVIDER_MODELS.get("google-gemini-cli") or [])
    default = current_model or (models[0] if models else "gemini-3-flash-preview")
    selected = _prompt_model_selection(models, current_model=default)
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider(
            "google-gemini-cli", DEFAULT_GEMINI_CLOUDCODE_BASE_URL
        )
        print(
            f"Default model set to: {selected} (via Google Gemini OAuth / Code Assist)"
        )
    else:
        print("No change.")


def _model_flow_custom(config):
    """Custom endpoint: collect URL, API key, and model name.

    Automatically saves the endpoint to ``custom_providers`` in config.yaml
    so it appears in the provider menu on subsequent runs.
    """
    from intellect_cli.auth import _save_model_choice, deactivate_provider
    from intellect_cli.config import get_env_value, load_config, save_config
    from intellect_cli.secret_prompt import masked_secret_prompt

    current_url = get_env_value("OPENAI_BASE_URL") or ""
    current_key = get_env_value("OPENAI_API_KEY") or ""

    print("Custom OpenAI-compatible endpoint configuration:")
    if current_url:
        print(f"  Current URL: {current_url}")
    if current_key:
        print(f"  Current key: {current_key[:8]}...")
    print()

    try:
        base_url = input(
            f"API base URL [{current_url or 'e.g. https://api.example.com/v1'}]: "
        ).strip()
        api_key = masked_secret_prompt(
            f"API key [{current_key[:8] + '...' if current_key else 'optional'}]: "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    if not base_url and not current_url:
        print("No URL provided. Cancelled.")
        return

    # Validate URL format
    effective_url = base_url or current_url
    if not effective_url.startswith(("http://", "https://")):
        print(f"Invalid URL: {effective_url} (must start with http:// or https://)")
        return

    effective_key = api_key or current_key

    # Hint: most local model servers (Ollama, vLLM, llama.cpp) require /v1
    # in the base URL for OpenAI-compatible chat completions.  Prompt the
    # user if the URL looks like a local server without /v1.
    _url_lower = effective_url.rstrip("/").lower()
    _looks_local = any(
        h in _url_lower
        for h in ("localhost", "127.0.0.1", "0.0.0.0", ":11434", ":8080", ":5000")
    )
    if _looks_local and not _url_lower.endswith("/v1"):
        print()
        print(f"  Hint: Did you mean to add /v1 at the end?")
        print(f"  Most local model servers (Ollama, vLLM, llama.cpp) require it.")
        print(f"  e.g. {effective_url.rstrip('/')}/v1")
        try:
            _add_v1 = input("  Add /v1? [Y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            _add_v1 = "n"
        if _add_v1 in {"", "y", "yes"}:
            effective_url = effective_url.rstrip("/") + "/v1"
            if base_url:
                base_url = effective_url
            print(f"  Updated URL: {effective_url}")
        print()

    from intellect_cli.models import probe_api_models

    probe = probe_api_models(effective_key, effective_url)
    if probe.get("used_fallback") and probe.get("resolved_base_url"):
        print(
            f"Warning: endpoint verification worked at {probe['resolved_base_url']}/models, "
            f"not the exact URL you entered. Saving the working base URL instead."
        )
        effective_url = probe["resolved_base_url"]
        if base_url:
            base_url = effective_url
    elif probe.get("models") is not None:
        print(
            f"Verified endpoint via {probe.get('probed_url')} "
            f"({len(probe.get('models') or [])} model(s) visible)"
        )
    else:
        print(
            f"Warning: could not verify this endpoint via {probe.get('probed_url')}. "
            f"Intellect will still save it."
        )
        if probe.get("suggested_base_url"):
            suggested = probe["suggested_base_url"]
            if suggested.endswith("/v1"):
                print(
                    f"  If this server expects /v1 in the path, try base URL: {suggested}"
                )
            else:
                print(f"  If /v1 should not be in the base URL, try: {suggested}")

    # Prompt for API compatibility mode explicitly so codex-compatible custom
    # providers don't silently fall back to chat_completions.
    current_model_cfg = config.get("model")
    current_api_mode = ""
    if isinstance(current_model_cfg, dict):
        current_api_mode = str(current_model_cfg.get("api_mode") or "").strip()
    api_mode = _prompt_custom_api_mode_selection(
        effective_url,
        current_api_mode=current_api_mode,
    )
    if api_mode:
        print(f"  API mode: {api_mode}")
    else:
        print("  API mode: auto-detect")

    # Select model — use probe results when available, fall back to manual input
    model_name = ""
    detected_models = probe.get("models") or []
    try:
        if len(detected_models) == 1:
            print(f"  Detected model: {detected_models[0]}")
            confirm = input("  Use this model? [Y/n]: ").strip().lower()
            if confirm in {"", "y", "yes"}:
                model_name = detected_models[0]
            else:
                model_name = input("Model name (e.g. gpt-4, llama-3-70b): ").strip()
        elif len(detected_models) > 1:
            print("  Available models:")
            for i, m in enumerate(detected_models, 1):
                print(f"    {i}. {m}")
            pick = input(
                f"  Select model [1-{len(detected_models)}] or type name: "
            ).strip()
            if pick.isdigit() and 1 <= int(pick) <= len(detected_models):
                model_name = detected_models[int(pick) - 1]
            elif pick:
                model_name = pick
        else:
            model_name = input("Model name (e.g. gpt-4, llama-3-70b): ").strip()

        context_length_str = input(
            "Context length in tokens [leave blank for auto-detect]: "
        ).strip()

        # Prompt for a display name — shown in the provider menu on future runs
        default_name = _auto_provider_name(effective_url)
        display_name = input(f"Display name [{default_name}]: ").strip() or default_name
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    context_length = None
    if context_length_str:
        try:
            context_length = int(
                context_length_str.replace(",", "")
                .replace("k", "000")
                .replace("K", "000")
            )
            if context_length <= 0:
                context_length = None
        except ValueError:
            print(f"Invalid context length: {context_length_str} — will auto-detect.")
            context_length = None

    if model_name:
        _save_model_choice(model_name)

        # Update config and deactivate any OAuth provider
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "custom"
        model["base_url"] = effective_url
        if effective_key:
            model["api_key"] = effective_key
        if api_mode:
            model["api_mode"] = api_mode
        else:
            model.pop("api_mode", None)
        save_config(cfg)
        deactivate_provider()

        # Sync the caller's config dict so the setup wizard's final
        # save_config(config) preserves our model settings.  Without
        # this, the wizard overwrites model.provider/base_url with
        # the stale values from its own config dict (#4172).
        config["model"] = dict(model)

        print(f"Default model set to: {model_name} (via {effective_url})")
    else:
        if base_url or api_key:
            deactivate_provider()
        # Even without a model name, persist the custom endpoint on the
        # caller's config dict so the setup wizard doesn't lose it.
        _caller_model = config.get("model")
        if not isinstance(_caller_model, dict):
            _caller_model = {"default": _caller_model} if _caller_model else {}
        _caller_model["provider"] = "custom"
        _caller_model["base_url"] = effective_url
        if effective_key:
            _caller_model["api_key"] = effective_key
        if api_mode:
            _caller_model["api_mode"] = api_mode
        else:
            _caller_model.pop("api_mode", None)
        config["model"] = _caller_model
        print("Endpoint saved. Use `/model` in chat or `intellect model` to set a model.")

    # Auto-save to custom_providers so it appears in the menu next time
    _save_custom_provider(
        effective_url,
        effective_key,
        model_name or "",
        context_length=context_length,
        name=display_name,
        api_mode=api_mode,
    )


def _prompt_custom_api_mode_selection(base_url: str, current_api_mode: str = "") -> Optional[str]:
    """Prompt for a custom provider API mode.

    Returns an explicit mode string, or None to keep auto-detect behavior.
    """
    from intellect_cli.runtime_provider import _detect_api_mode_for_url

    detected_mode = _detect_api_mode_for_url(base_url)
    normalized_current = str(current_api_mode or "").strip().lower()
    default_mode = normalized_current or detected_mode or ""

    mode_options = [
        (
            "",
            "Auto-detect",
            "Use Intellect URL heuristics; best for standard OpenAI-compatible endpoints.",
        ),
        (
            "chat_completions",
            "Chat Completions",
            "Use /chat/completions for standard OpenAI-compatible servers.",
        ),
        (
            "codex_responses",
            "Responses / Codex",
            "Use /responses for Codex-compatible tool-calling backends.",
        ),
        (
            "anthropic_messages",
            "Anthropic Messages",
            "Use /v1/messages for Anthropic-compatible endpoints.",
        ),
    ]

    print()
    print("Select API compatibility mode:")
    for idx, (value, label, description) in enumerate(mode_options, 1):
        markers = []
        if value == detected_mode:
            markers.append("detected")
        if value == default_mode:
            markers.append("current")
        suffix = f" [{' / '.join(markers)}]" if markers else ""
        print(f"  {idx}. {label}{suffix}")
        print(f"     {description}")

    try:
        raw = input(
            "Choice [1-4, Enter to keep current/detected]: "
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        raise

    if not raw:
        return default_mode or None

    if raw in {"1", "auto", "detect", "auto-detect"}:
        return None
    if raw in {"2", "chat", "chat_completions", "completions"}:
        return "chat_completions"
    if raw in {"3", "responses", "codex", "codex_responses"}:
        return "codex_responses"
    if raw in {"4", "anthropic", "anthropic_messages", "messages"}:
        return "anthropic_messages"

    print(f"Invalid API mode choice: {raw}. Falling back to auto-detect.")
    return None


def _auto_provider_name(base_url: str) -> str:
    """Generate a display name from a custom endpoint URL.

    Returns a human-friendly label like "Local (localhost:11434)" or
    "RunPod (xyz.runpod.io)".  Used as the default when prompting the
    user for a display name during custom endpoint setup.
    """
    import re

    clean = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    clean = re.sub(r"/v1/?$", "", clean)
    name = clean.split("/")[0]
    if "localhost" in name or "127.0.0.1" in name:
        name = f"Local ({name})"
    elif "runpod" in name.lower():
        name = f"RunPod ({name})"
    else:
        name = name.capitalize()
    return name


def _custom_provider_api_key_config_value(provider_info, resolved_api_key=""):
    """Return the value that should be persisted for a custom provider key."""
    api_key_ref = str(provider_info.get("api_key_ref", "") or "").strip()
    if api_key_ref:
        return api_key_ref

    key_env = str(provider_info.get("key_env", "") or "").strip()
    if key_env and not str(provider_info.get("api_key", "") or "").strip():
        return f"${{{key_env}}}"

    return str(resolved_api_key or "").strip()


def _custom_provider_base_url_config_value(provider_info, resolved_base_url=""):
    """Return the value that should be persisted for a custom provider URL."""
    base_url_ref = str(provider_info.get("base_url_ref", "") or "").strip()
    if base_url_ref:
        return base_url_ref
    return str(resolved_base_url or "").strip()


def _save_custom_provider(
    base_url, api_key="", model="", context_length=None, name=None, api_mode=None
):
    """Save a custom endpoint to custom_providers in config.yaml.

    Deduplicates by base_url — if the URL already exists, updates the
    model name, context_length, and api_mode but doesn't add a duplicate entry.
    Uses *name* when provided, otherwise auto-generates from the URL.
    """
    from intellect_cli.config import load_config, save_config

    cfg = load_config()
    providers = cfg.get("custom_providers") or []
    if not isinstance(providers, list):
        providers = []

    # Check if this URL is already saved — update model/context_length if so
    for entry in providers:
        if isinstance(entry, dict) and entry.get("base_url", "").rstrip(
            "/"
        ) == base_url.rstrip("/"):
            changed = False
            if model and entry.get("model") != model:
                entry["model"] = model
                changed = True
            if model and context_length:
                models_cfg = entry.get("models", {})
                if not isinstance(models_cfg, dict):
                    models_cfg = {}
                models_cfg[model] = {"context_length": context_length}
                entry["models"] = models_cfg
                changed = True
            if api_mode:
                if entry.get("api_mode") != api_mode:
                    entry["api_mode"] = api_mode
                    changed = True
            elif "api_mode" in entry:
                entry.pop("api_mode", None)
                changed = True
            if changed:
                cfg["custom_providers"] = providers
                save_config(cfg)
            return  # already saved, updated if needed

    # Use provided name or auto-generate from URL
    if not name:
        name = _auto_provider_name(base_url)

    entry = {"name": name, "base_url": base_url}
    if api_key:
        entry["api_key"] = api_key
    if model:
        entry["model"] = model
    if api_mode:
        entry["api_mode"] = api_mode
    if model and context_length:
        entry["models"] = {model: {"context_length": context_length}}

    providers.append(entry)
    cfg["custom_providers"] = providers
    save_config(cfg)
    print(f'  💾 Saved to custom providers as "{name}" (edit in config.yaml)')


def _model_flow_azure_foundry(config, current_model=""):
    """Azure Foundry provider: configure endpoint, auth mode, API mode, and model.

    Azure Foundry supports both OpenAI-style (``/v1/chat/completions``) and
    Anthropic-style (``/v1/messages``) endpoints, and two authentication
    modes:

    * **API key** (default) — uses ``AZURE_FOUNDRY_API_KEY`` from .env.
    * **Microsoft Entra ID** — keyless, RBAC-based auth via the
      ``azure-identity`` SDK (Managed Identity / Workload Identity / az
      login / VS Code / azd / service principal env vars). Works on both
      OpenAI-style and Anthropic-style endpoints — Microsoft RBAC is
      per-resource and the same ``Azure AI User`` role grants
      both. For OpenAI-style the OpenAI SDK's native callable
      ``api_key=`` contract is used; for Anthropic-style an
      ``httpx.Client`` with a request event hook (built by
      :func:`agent.azure_identity_adapter.build_bearer_http_client`)
      mints a fresh JWT per request because the Anthropic SDK does not
      accept a callable ``auth_token`` natively.

    The wizard auto-detects the transport and available models when
    possible:

    * URLs ending in ``/anthropic`` → Anthropic Messages API.
    * Successful ``GET <base>/models`` probe → OpenAI-style + populates
      a picker with the returned deployment / model IDs.
    * Anthropic Messages probe fallback when ``/models`` fails.
    * Manual entry when every probe fails (private endpoints, etc.).

    Context lengths for the chosen model are resolved via the standard
    :func:`agent.model_metadata.get_model_context_length` chain
    (models.dev, provider metadata, hardcoded family fallbacks).
    """
    from intellect_cli.auth import _save_model_choice, deactivate_provider  # noqa: F401
    from intellect_cli.config import (
        get_env_value,
        save_env_value,
        load_config,
        save_config,
    )
    from intellect_cli import azure_detect

    # ── Load current Azure Foundry configuration ─────────────────────
    model_cfg = config.get("model", {})
    if isinstance(model_cfg, dict) and model_cfg.get("provider") == "azure-foundry":
        current_base_url = str(model_cfg.get("base_url", "") or "")
        current_api_mode = str(model_cfg.get("api_mode", "") or "")
        current_auth_mode = str(model_cfg.get("auth_mode") or "api_key").strip().lower() or "api_key"
        _cur_entra = model_cfg.get("entra") or {}
        current_entra = _cur_entra if isinstance(_cur_entra, dict) else {}
    else:
        current_base_url = ""
        current_api_mode = ""
        current_auth_mode = "api_key"
        current_entra = {}

    current_api_key = get_env_value("AZURE_FOUNDRY_API_KEY") or ""

    print()
    print("Azure Foundry Configuration")
    print("=" * 50)
    print()
    print("Azure Foundry can host models with either OpenAI-style or")
    print("Anthropic-style API endpoints.  Intellect will probe your")
    print("endpoint to auto-detect the transport and the deployed")
    print("models when possible.")
    print()

    if current_base_url:
        print(f"  Current endpoint:  {current_base_url}")
    if current_api_mode:
        _lbl = (
            "OpenAI-style"
            if current_api_mode == "chat_completions"
            else "Anthropic-style"
        )
        print(f"  Current API mode:  {_lbl}")
    if current_auth_mode == "entra_id":
        print(f"  Current auth mode: Microsoft Entra ID (keyless)")
    elif current_api_key:
        print(f"  Current auth mode: API key ({current_api_key[:8]}...)")
    print()

    # ── Step 1: endpoint URL ─────────────────────────────────────────
    try:
        _placeholder = (
            current_base_url
            or "e.g. https://<resource>.openai.azure.com/openai/v1 "
              "or https://<resource>.services.ai.azure.com/anthropic"
        )
        base_url = input(
            f"API endpoint URL [{_placeholder}]: "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    effective_url = (base_url or current_base_url).rstrip("/")
    if not effective_url:
        print("No endpoint URL provided. Cancelled.")
        return
    if not effective_url.startswith(("http://", "https://")):
        print(f"Invalid URL: {effective_url} (must start with http:// or https://)")
        return

    # ── Step 2: authentication mode ──────────────────────────────────
    print()
    print("Authentication:")
    print("  1. API key                  (AZURE_FOUNDRY_API_KEY in .env)")
    print("  2. Microsoft Entra ID       (managed identity / workload identity / az login)")
    print("     Recommended by Microsoft. Works for both OpenAI-style and Anthropic-style endpoints.")
    print("     Requires the 'Azure AI User' role on the Foundry resource.")
    try:
        _auth_default = "2" if current_auth_mode == "entra_id" else "1"
        auth_choice = (
            input(f"Authentication mode [1/2] ({_auth_default}): ").strip()
            or _auth_default
        )
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return
    use_entra = auth_choice == "2"
    auth_mode_label = "entra_id" if use_entra else "api_key"

    # ── Step 3: credentials (key OR Entra preflight) ─────────────────
    effective_key: str = ""
    entra_overrides: dict = {}
    token_provider = None  # callable when entra
    entra_scope = ""

    if use_entra:
        try:
            from agent.azure_identity_adapter import (
                EntraIdentityConfig,
                SCOPE_AI_AZURE_DEFAULT,
                build_token_provider,
                describe_active_credential,
                has_azure_identity_installed,
            )
        except ImportError as exc:
            print()
            print(f"⚠ Could not import azure-identity adapter: {exc}")
            print("  Falling back to API key auth.")
            use_entra = False
            auth_mode_label = "api_key"

    if use_entra:
        print()
        if not has_azure_identity_installed():
            print("◐ The 'azure-identity' package is not installed yet.")
            print(
                "  Intellect will install it now (the preflight below "
                "triggers the lazy-install). To skip lazy installs, "
                "run:  pip install azure-identity"
            )

        # Preserve only the optional scope override. Identity selection
        # (tenant, user-assigned MI, workload identity, service principal)
        # stays in Azure SDK env vars such as AZURE_CLIENT_ID.
        _persisted_scope_override = str(current_entra.get("scope") or "").strip()
        entra_scope = _persisted_scope_override or SCOPE_AI_AZURE_DEFAULT

        entra_overrides = {}
        if _persisted_scope_override:
            entra_overrides["scope"] = _persisted_scope_override

        print()
        print("◐ Probing Microsoft Entra ID credential chain (up to 10s)...")
        _config = EntraIdentityConfig(
            scope=entra_scope,
        )
        info = describe_active_credential(config=_config, timeout_seconds=10.0)
        if info.get("ok"):
            env_sources = info.get("env_sources") or []
            tag = ", ".join(env_sources) if env_sources else "default chain"
            print(f"✓ Entra ID token acquired ({tag}, scope={entra_scope})")
        else:
            err = info.get("error") or "credential chain exhausted"
            hint = info.get("hint") or (
                "Run `az login`, attach a managed identity to this VM, or "
                "set AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET."
            )
            print(f"⚠ {err}")
            print(f"  Hint: {hint}")
            try:
                ans = input("Save Entra config anyway and validate later? [Y/n]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                return
            if ans and ans not in ("y", "yes"):
                print("Cancelled.")
                return

        # Build the token provider for the detection probe (best-effort —
        # if the credential chain failed above, this will silently return
        # None inside azure_detect and the probe falls back to manual).
        try:
            token_provider = build_token_provider(config=_config)
        except Exception as exc:
            print(f"⚠ Could not build token provider for probing: {exc}")
            token_provider = None
    else:
        print()
        from intellect_cli.secret_prompt import masked_secret_prompt

        try:
            api_key = masked_secret_prompt(
                f"API key [{current_api_key[:8] + '...' if current_api_key else 'required'}]: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return

        effective_key = api_key or current_api_key
        if not effective_key:
            print("No API key provided. Cancelled.")
            return

    # ── Step 4: auto-detect transport + models ───────────────────────
    print()
    print("◐ Probing endpoint to auto-detect transport and models...")
    detection = azure_detect.detect(
        effective_url,
        api_key=effective_key,
        token_provider=token_provider,
    )

    discovered_models: list[str] = list(detection.models)
    api_mode: str = detection.api_mode or ""

    if api_mode:
        mode_label = (
            "OpenAI-style" if api_mode == "chat_completions" else "Anthropic-style"
        )
        print(f"✓ Detected API transport: {mode_label}")
        if detection.reason:
            print(f"    ({detection.reason})")
        if discovered_models:
            print(
                f"✓ Found {len(discovered_models)} deployed model(s) on this endpoint"
            )
    else:
        print(f"⚠ Auto-detection incomplete: {detection.reason}")
        print()
        print("Select the API format your Azure Foundry endpoint uses:")
        print("  1. OpenAI-style  (POST /v1/chat/completions)")
        print("     For: GPT models, Llama, Mistral, and most open models")
        print("  2. Anthropic-style  (POST /v1/messages)")
        print("     For: Claude models deployed via Anthropic API format")
        try:
            default_choice = "2" if current_api_mode == "anthropic_messages" else "1"
            mode_choice = (
                input(f"API format [1/2] ({default_choice}): ").strip()
                or default_choice
            )
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
        api_mode = "anthropic_messages" if mode_choice == "2" else "chat_completions"

    # ── Step 5: model name ───────────────────────────────────────────
    print()
    effective_model = ""
    if discovered_models:
        print("Available models on this endpoint:")
        for i, mid in enumerate(discovered_models[:30], start=1):
            print(f"  {i:>2}. {mid}")
        if len(discovered_models) > 30:
            print(
                f"  ... and {len(discovered_models) - 30} more (type name manually if not shown)"
            )
        print()
        try:
            pick = input(
                f"Pick by number, or type a deployment name [{current_model or discovered_models[0]}]: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
        if not pick:
            effective_model = current_model or discovered_models[0]
        elif pick.isdigit() and 1 <= int(pick) <= min(len(discovered_models), 30):
            effective_model = discovered_models[int(pick) - 1]
        else:
            effective_model = pick
    else:
        try:
            model_name = input(
                f"Model / deployment name [{current_model or 'e.g. gpt-5.4, claude-sonnet-4-6'}]: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
        effective_model = model_name or current_model

    if not effective_model:
        print("No model name provided. Cancelled.")
        return

    # ── Step 6: context-length lookup ────────────────────────────────
    ctx_len = azure_detect.lookup_context_length(
        effective_model,
        effective_url,
        api_key=effective_key,
        token_provider=token_provider,
    )

    # ── Step 7: persist ──────────────────────────────────────────────
    if not use_entra:
        save_env_value("AZURE_FOUNDRY_API_KEY", effective_key)

    cfg = load_config()
    model = cfg.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        cfg["model"] = model

    model["provider"] = "azure-foundry"
    model["base_url"] = effective_url
    model["api_mode"] = api_mode
    model["default"] = effective_model
    model["auth_mode"] = auth_mode_label
    if use_entra:
        # Persist only the non-default Entra scope so config.yaml stays tidy.
        # Azure identity selection stays in standard AZURE_* env vars.
        clean_entra: dict = {}
        for key in ("scope",):
            val = entra_overrides.get(key)
            if val:
                clean_entra[key] = val
        if clean_entra:
            model["entra"] = clean_entra
        elif "entra" in model:
            del model["entra"]
    else:
        if "entra" in model:
            del model["entra"]
    if ctx_len:
        model["context_length"] = ctx_len

    save_config(cfg)
    deactivate_provider()
    config["model"] = dict(model)

    # Clear any conflicting env vars so auxiliary clients don't poison
    # themselves with a stale OpenAI base URL / key.
    if get_env_value("OPENAI_BASE_URL"):
        save_env_value("OPENAI_BASE_URL", "")
    if get_env_value("OPENAI_API_KEY"):
        save_env_value("OPENAI_API_KEY", "")

    mode_label = "OpenAI-style" if api_mode == "chat_completions" else "Anthropic-style"
    auth_label = (
        "Microsoft Entra ID (keyless)" if use_entra else "API key"
    )
    print()
    print("✓ Azure Foundry configured:")
    print(f"    Endpoint:       {effective_url}")
    print(f"    API mode:       {mode_label}")
    print(f"    Auth:           {auth_label}")
    print(f"    Model:          {effective_model}")
    if ctx_len:
        print(f"    Context length: {ctx_len:,} tokens")
    else:
        print("    Context length: not auto-detected (will fall back at runtime)")
    print()


def _remove_custom_provider(config):
    """Let the user remove a saved custom provider from config.yaml."""
    from intellect_cli.config import load_config, save_config

    cfg = load_config()
    providers = cfg.get("custom_providers") or []
    if not isinstance(providers, list) or not providers:
        print("No custom providers configured.")
        return

    print("Remove a custom provider:\n")

    choices = []
    for entry in providers:
        if isinstance(entry, dict):
            name = entry.get("name", "unnamed")
            url = entry.get("base_url", "")
            short_url = url.replace("https://", "").replace("http://", "").rstrip("/")
            choices.append(f"{name} ({short_url})")
        else:
            choices.append(str(entry))
    choices.append("Cancel")

    try:
        from intellect_cli.curses_ui import curses_radiolist

        idx = curses_radiolist(
            "Select provider to remove:",
            list(choices),
            selected=0,
            cancel_returns=-1,
        )
        print()
        if idx < 0:
            idx = None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        print()
        try:
            val = input(f"Choice [1-{len(choices)}]: ").strip()
            idx = int(val) - 1 if val else None
        except (ValueError, KeyboardInterrupt, EOFError):
            idx = None

    if idx is None or idx >= len(providers):
        print("No change.")
        return

    removed = providers.pop(idx)
    cfg["custom_providers"] = providers
    save_config(cfg)
    removed_name = (
        removed.get("name", "unnamed") if isinstance(removed, dict) else str(removed)
    )
    print(f'✅ Removed "{removed_name}" from custom providers.')


def _model_flow_named_custom(config, provider_info):
    """Handle a named custom provider from config.yaml custom_providers list.

    Always probes the endpoint's /models API to let the user pick a model.
    If a model was previously saved, it is pre-selected in the menu.
    Falls back to the saved model if probing fails.
    """
    from intellect_cli.auth import _save_model_choice, deactivate_provider
    from intellect_cli.config import load_config, save_config
    from intellect_cli.models import fetch_api_models

    name = provider_info["name"]
    base_url = provider_info["base_url"]
    api_mode = provider_info.get("api_mode", "")
    api_key = provider_info.get("api_key", "")
    key_env = provider_info.get("key_env", "")
    saved_model = provider_info.get("model", "")
    provider_key = (provider_info.get("provider_key") or "").strip()

    # Resolve key from env var if api_key not set directly
    if not api_key and key_env:
        api_key = os.environ.get(key_env, "")
    config_api_key = _custom_provider_api_key_config_value(provider_info, api_key)

    print(f"  Provider: {name}")
    print(f"  URL:      {base_url}")
    if saved_model:
        print(f"  Current:  {saved_model}")
    print()

    print("Fetching available models...")
    fetch_kwargs = {"timeout": 8.0}
    if api_mode:
        fetch_kwargs["api_mode"] = api_mode
    models = fetch_api_models(api_key, base_url, **fetch_kwargs)

    if models:
        default_idx = 0
        if saved_model and saved_model in models:
            default_idx = models.index(saved_model)

        print(f"Found {len(models)} model(s):\n")
        try:
            from intellect_cli.curses_ui import curses_radiolist

            menu_items = [
                f"{m} (current)" if m == saved_model else m for m in models
            ] + ["Cancel"]
            idx = curses_radiolist(
                f"Select model from {name}:",
                menu_items,
                selected=default_idx,
                cancel_returns=-1,
            )
            print()
            if idx < 0 or idx >= len(models):
                print("Cancelled.")
                return
            model_name = models[idx]
        except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
            for i, m in enumerate(models, 1):
                suffix = " (current)" if m == saved_model else ""
                print(f"  {i}. {m}{suffix}")
            print(f"  {len(models) + 1}. Cancel")
            print()
            try:
                val = input(f"Choice [1-{len(models) + 1}]: ").strip()
                if not val:
                    print("Cancelled.")
                    return
                idx = int(val) - 1
                if idx < 0 or idx >= len(models):
                    print("Cancelled.")
                    return
                model_name = models[idx]
            except (ValueError, KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                return
    elif saved_model:
        print("Could not fetch models from endpoint.")
        try:
            model_name = input(f"Model name [{saved_model}]: ").strip() or saved_model
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
    else:
        print("Could not fetch models from endpoint. Enter model name manually.")
        try:
            model_name = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
        if not model_name:
            print("No model specified. Cancelled.")
            return

    # Activate and save the model to the custom_providers entry
    _save_model_choice(model_name)

    cfg = load_config()
    model = cfg.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        cfg["model"] = model
    if provider_key:
        model["provider"] = provider_key
        model.pop("base_url", None)
        model.pop("api_key", None)
    else:
        model["provider"] = "custom"
        model["base_url"] = _custom_provider_base_url_config_value(
            provider_info, base_url
        )
        if config_api_key:
            model["api_key"] = config_api_key
    # Apply api_mode from custom_providers entry, or clear stale value
    custom_api_mode = provider_info.get("api_mode", "")
    if custom_api_mode:
        model["api_mode"] = custom_api_mode
    else:
        model.pop("api_mode", None)  # let runtime auto-detect from URL
    save_config(cfg)
    deactivate_provider()

    # Persist the selected model back to whichever schema owns this endpoint.
    if provider_key:
        cfg = load_config()
        providers_cfg = cfg.get("providers")
        if isinstance(providers_cfg, dict):
            provider_entry = providers_cfg.get(provider_key)
            if isinstance(provider_entry, dict):
                provider_entry["default_model"] = model_name
                # Only persist an inline api_key when the user originally had
                # one (either a literal secret or a ``${VAR}`` template). When
                # the entry relies on ``key_env``, do not synthesize a
                # ``${key_env}`` api_key — the runtime already resolves the
                # key from ``key_env`` directly, and writing the resolved
                # secret (or even a synthesized template) would silently
                # downgrade credential hygiene on entries that intentionally
                # keep plaintext out of ``config.yaml``. See issue #15803.
                original_api_key_ref = str(
                    provider_info.get("api_key_ref", "") or ""
                ).strip()
                original_api_key = str(provider_info.get("api_key", "") or "").strip()
                had_inline_api_key = bool(original_api_key_ref or original_api_key)
                if (
                    had_inline_api_key
                    and config_api_key
                    and not str(provider_entry.get("api_key", "") or "").strip()
                ):
                    provider_entry["api_key"] = config_api_key
                if key_env and not str(provider_entry.get("key_env", "") or "").strip():
                    provider_entry["key_env"] = key_env
                cfg["providers"] = providers_cfg
                save_config(cfg)
    else:
        # Save model name to the custom_providers entry for next time
        _save_custom_provider(base_url, config_api_key, model_name, api_mode=api_mode)

    print(f"\n✅ Model set to: {model_name}")
    print(f"   Provider: {name} ({base_url})")


# Lazy-export the model catalog at module level. Tests and a handful of
# downstream call sites read `intellect_cli.main._PROVIDER_MODELS` directly,
# so the symbol needs to be reachable as a module attribute. But importing
# the catalog eagerly costs ~55ms on every `intellect` invocation — including
# fast paths like `intellect --version` and slash-command dispatch that never
# touch the catalog. PEP 562 module-level __getattr__ defers the import
# until first attribute access, so the cost is only paid by callers that
# actually look up the catalog. Termux already defers via the same
# mechanism (its model-selection handlers do their own function-local
# imports), so the explicit termux branch from before is no longer needed.
_LAZY_MODEL_EXPORTS = ("_PROVIDER_MODELS",)


def __getattr__(name):
    """Defer the model-catalog import until something actually reads it."""
    if name in _LAZY_MODEL_EXPORTS:
        from intellect_cli.models import _PROVIDER_MODELS
        # Cache on the module so subsequent accesses skip the import machinery.
        globals()[name] = _PROVIDER_MODELS
        return _PROVIDER_MODELS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _current_reasoning_effort(config) -> str:
    agent_cfg = config.get("agent")
    if isinstance(agent_cfg, dict):
        return str(agent_cfg.get("reasoning_effort") or "").strip().lower()
    return ""


def _set_reasoning_effort(config, effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort


def _prompt_reasoning_effort_selection(efforts, current_effort=""):
    """Prompt for a reasoning effort. Returns effort, 'none', or None to keep current."""
    deduped = list(
        dict.fromkeys(
            str(effort).strip().lower() for effort in efforts if str(effort).strip()
        )
    )
    canonical_order = ("minimal", "low", "medium", "high", "xhigh")
    ordered = [effort for effort in canonical_order if effort in deduped]
    ordered.extend(effort for effort in deduped if effort not in canonical_order)
    if not ordered:
        return None

    def _label(effort):
        if effort == current_effort:
            return f"{effort}  ← currently in use"
        return effort

    disable_label = "Disable reasoning"
    skip_label = "Skip (keep current)"

    if current_effort == "none":
        default_idx = len(ordered)
    elif current_effort in ordered:
        default_idx = ordered.index(current_effort)
    elif "medium" in ordered:
        default_idx = ordered.index("medium")
    else:
        default_idx = 0

    try:
        from intellect_cli.curses_ui import curses_radiolist

        choices = [_label(effort) for effort in ordered]
        choices.append(disable_label)
        choices.append(skip_label)
        idx = curses_radiolist(
            "Select reasoning effort:",
            choices,
            selected=default_idx,
            cancel_returns=-1,
        )
        if idx < 0:
            return None
        print()
        if idx < len(ordered):
            return ordered[idx]
        if idx == len(ordered):
            return "none"
        return None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        pass

    print("Select reasoning effort:")
    for i, effort in enumerate(ordered, 1):
        print(f"  {i}. {_label(effort)}")
    n = len(ordered)
    print(f"  {n + 1}. {disable_label}")
    print(f"  {n + 2}. {skip_label}")
    print()

    while True:
        try:
            choice = input(f"Choice [1-{n + 2}] (default: keep current): ").strip()
            if not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= n:
                return ordered[idx - 1]
            if idx == n + 1:
                return "none"
            if idx == n + 2:
                return None
            print(f"Please enter 1-{n + 2}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            return None


def _model_flow_copilot(config, current_model=""):
    """GitHub Copilot flow using env vars, gh CLI, or OAuth device code."""
    from intellect_cli.auth import (
        PROVIDER_REGISTRY,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
        resolve_api_key_provider_credentials,
    )
    from intellect_cli.config import save_env_value, load_config, save_config
    from intellect_cli.models import (
        _PROVIDER_MODELS,
        fetch_api_models,
        fetch_github_model_catalog,
        github_model_reasoning_efforts,
        copilot_model_api_mode,
        normalize_copilot_model_id,
    )

    provider_id = "copilot"
    pconfig = PROVIDER_REGISTRY[provider_id]

    creds = resolve_api_key_provider_credentials(provider_id)
    api_key = creds.get("api_key", "")
    source = creds.get("source", "")

    if not api_key:
        print("No GitHub token configured for GitHub Copilot.")
        print()
        print("  Supported token types:")
        print(
            "    → OAuth token (gho_*)          via `copilot login` or device code flow"
        )
        print("    → Fine-grained PAT (github_pat_*)  with Copilot Requests permission")
        print("    → GitHub App token (ghu_*)     via environment variable")
        print("    ✗ Classic PAT (ghp_*)          NOT supported by Copilot API")
        print()
        print("  Options:")
        print("    1. Login with GitHub (OAuth device code flow)")
        print("    2. Enter a token manually")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1-3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "1":
            try:
                from intellect_cli.copilot_auth import copilot_device_code_login

                token = copilot_device_code_login()
                if token:
                    save_env_value("COPILOT_GITHUB_TOKEN", token)
                    print("  Copilot token saved.")
                    print()
                else:
                    print("  Login cancelled or failed.")
                    return
            except Exception as exc:
                print(f"  Login failed: {exc}")
                return
        elif choice == "2":
            from intellect_cli.secret_prompt import masked_secret_prompt

            try:
                new_key = masked_secret_prompt("  Token (COPILOT_GITHUB_TOKEN): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if not new_key:
                print("  Cancelled.")
                return
            # Validate token type
            try:
                from intellect_cli.copilot_auth import validate_copilot_token

                valid, msg = validate_copilot_token(new_key)
                if not valid:
                    print(f"  ✗ {msg}")
                    return
            except ImportError:
                pass
            save_env_value("COPILOT_GITHUB_TOKEN", new_key)
            print("  Token saved.")
            print()
        else:
            print("  Cancelled.")
            return

        creds = resolve_api_key_provider_credentials(provider_id)
        api_key = creds.get("api_key", "")
        source = creds.get("source", "")
    else:
        if source in {"GITHUB_TOKEN", "GH_TOKEN"}:
            from intellect_cli.env_loader import format_secret_source_suffix
            bw_suffix = format_secret_source_suffix(source)
            print(f"  GitHub token: {api_key[:8]}... ✓ ({source}{bw_suffix})")
        elif source == "gh auth token":
            print("  GitHub token: ✓ (from `gh auth token`)")
        else:
            print("  GitHub token: ✓")
        print()

    effective_base = pconfig.inference_base_url

    catalog = fetch_github_model_catalog(api_key)
    live_models = (
        [item.get("id", "") for item in catalog if item.get("id")]
        if catalog
        else fetch_api_models(api_key, effective_base)
    )
    normalized_current_model = (
        normalize_copilot_model_id(
            current_model,
            catalog=catalog,
            api_key=api_key,
        )
        or current_model
    )
    if live_models:
        model_list = [model_id for model_id in live_models if model_id]
        print(f"  Found {len(model_list)} model(s) from GitHub Copilot")
    else:
        model_list = _PROVIDER_MODELS.get(provider_id, [])
        if model_list:
            print(
                "  ⚠ Could not auto-detect models from GitHub Copilot — showing defaults."
            )
            print('    Use "Enter custom model name" if you do not see your model.')

    if model_list:
        selected = _prompt_model_selection(
            model_list, current_model=normalized_current_model
        )
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        selected = (
            normalize_copilot_model_id(
                selected,
                catalog=catalog,
                api_key=api_key,
            )
            or selected
        )
        initial_cfg = load_config()
        current_effort = _current_reasoning_effort(initial_cfg)
        reasoning_efforts = github_model_reasoning_efforts(
            selected,
            catalog=catalog,
            api_key=api_key,
        )
        selected_effort = None
        if reasoning_efforts:
            print(f"  {selected} supports reasoning controls.")
            selected_effort = _prompt_reasoning_effort_selection(
                reasoning_efforts, current_effort=current_effort
            )

        _save_model_choice(selected)

        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        model["api_mode"] = copilot_model_api_mode(
            selected,
            catalog=catalog,
            api_key=api_key,
        )
        if selected_effort is not None:
            _set_reasoning_effort(cfg, selected_effort)
        save_config(cfg)
        deactivate_provider()

        print(f"Default model set to: {selected} (via {pconfig.name})")
        if reasoning_efforts:
            if selected_effort == "none":
                print("Reasoning disabled for this model.")
            elif selected_effort:
                print(f"Reasoning effort set to: {selected_effort}")
    else:
        print("No change.")


def _model_flow_copilot_acp(config, current_model=""):
    """GitHub Copilot ACP flow using the local Copilot CLI."""
    from intellect_cli.auth import (
        PROVIDER_REGISTRY,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
        get_external_process_provider_status,
        resolve_api_key_provider_credentials,
        resolve_external_process_provider_credentials,
    )
    from intellect_cli.models import (
        _PROVIDER_MODELS,
        fetch_github_model_catalog,
        normalize_copilot_model_id,
    )
    from intellect_cli.config import load_config, save_config

    del config

    provider_id = "copilot-acp"
    pconfig = PROVIDER_REGISTRY[provider_id]

    status = get_external_process_provider_status(provider_id)
    resolved_command = (
        status.get("resolved_command") or status.get("command") or "copilot"
    )
    effective_base = status.get("base_url") or pconfig.inference_base_url

    print("  GitHub Copilot ACP delegates Intellect turns to `copilot --acp`.")
    print("  Intellect currently starts its own ACP subprocess for each request.")
    print("  Intellect uses your selected model as a hint for the Copilot ACP session.")
    print(f"  Command: {resolved_command}")
    print(f"  Backend marker: {effective_base}")
    print()

    try:
        creds = resolve_external_process_provider_credentials(provider_id)
    except Exception as exc:
        print(f"  ⚠ {exc}")
        print(
            "  Set intellect_COPILOT_ACP_COMMAND or COPILOT_CLI_PATH if Copilot CLI is installed elsewhere."
        )
        return

    effective_base = creds.get("base_url") or effective_base

    catalog_api_key = ""
    try:
        catalog_creds = resolve_api_key_provider_credentials("copilot")
        catalog_api_key = catalog_creds.get("api_key", "")
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    catalog = fetch_github_model_catalog(catalog_api_key)
    normalized_current_model = (
        normalize_copilot_model_id(
            current_model,
            catalog=catalog,
            api_key=catalog_api_key,
        )
        or current_model
    )

    if catalog:
        model_list = [item.get("id", "") for item in catalog if item.get("id")]
        print(f"  Found {len(model_list)} model(s) from GitHub Copilot")
    else:
        model_list = _PROVIDER_MODELS.get("copilot", [])
        if model_list:
            print(
                "  ⚠ Could not auto-detect models from GitHub Copilot — showing defaults."
            )
            print('    Use "Enter custom model name" if you do not see your model.')

    if model_list:
        selected = _prompt_model_selection(
            model_list,
            current_model=normalized_current_model,
        )
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if not selected:
        print("No change.")
        return

    selected = (
        normalize_copilot_model_id(
            selected,
            catalog=catalog,
            api_key=catalog_api_key,
        )
        or selected
    )
    _save_model_choice(selected)

    cfg = load_config()
    model = cfg.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        cfg["model"] = model
    model["provider"] = provider_id
    model["base_url"] = effective_base
    model["api_mode"] = "chat_completions"
    save_config(cfg)
    deactivate_provider()

    print(f"Default model set to: {selected} (via {pconfig.name})")


def _prompt_api_key(pconfig, existing_key: str, provider_id: str = "") -> tuple:
    """Shared API-key entry point for ``intellect setup`` / ``intellect model``.

    Handles both first-time entry and the already-configured case.  When a key
    is already present, offers [K]eep / [R]eplace / [C]lear so the user can
    recover from a malformed paste without editing ``~/.intellect/.env`` by hand.

    Returns ``(resolved_key, abort)``.  ``abort=True`` means the caller should
    ``return`` immediately — the user cancelled entry, declined to replace, or
    cleared the key and is now unconfigured.
    """
    from intellect_cli.auth import LMSTUDIO_NOAUTH_PLACEHOLDER
    from intellect_cli.config import save_env_value
    from intellect_cli.secret_prompt import masked_secret_prompt

    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""

    def _prompt_new_key(*, allow_lmstudio_default: bool) -> str:
        if provider_id == "lmstudio" and allow_lmstudio_default:
            prompt = f"{key_env} (Enter for no-auth default {LMSTUDIO_NOAUTH_PLACEHOLDER!r}): "
        else:
            prompt = f"{key_env} (or Enter to cancel): "
        try:
            entered = masked_secret_prompt(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return ""
        if not entered and provider_id == "lmstudio" and allow_lmstudio_default:
            return LMSTUDIO_NOAUTH_PLACEHOLDER
        return entered

    # First-time entry ────────────────────────────────────────────────────
    if not existing_key:
        print(f"No {pconfig.name} API key configured.")
        if not key_env:
            return "", True
        new_key = _prompt_new_key(allow_lmstudio_default=True)
        if not new_key:
            print("Cancelled.")
            return "", True
        save_env_value(key_env, new_key)
        print("API key saved.")
        print()
        return new_key, False

    # Already configured — offer K / R / C ────────────────────────────────
    from intellect_cli.env_loader import format_secret_source_suffix

    source_suffix = format_secret_source_suffix(key_env) if key_env else ""
    print(f"  {pconfig.name} API key: {existing_key[:8]}... ✓{source_suffix}")
    if not key_env:
        # Nothing we can rewrite; just acknowledge and move on.
        print()
        return existing_key, False
    try:
        choice = input("  [K]eep / [R]eplace / [C]lear (default K): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        choice = "k"

    if choice.startswith("r"):
        new_key = _prompt_new_key(allow_lmstudio_default=False)
        if not new_key:
            print("  No change.")
            print()
            return existing_key, False
        save_env_value(key_env, new_key)
        print("  API key updated.")
        print()
        return new_key, False

    if choice.startswith("c"):
        save_env_value(key_env, "")
        print(
            f"  API key cleared.  Re-run `intellect setup` to configure {pconfig.name} again."
        )
        return "", True

    # Keep (default, or any other input)
    print()
    return existing_key, False


def _model_flow_kimi(config, current_model=""):
    """Kimi / Moonshot model selection with automatic endpoint routing.

    - sk-kimi-* keys   → api.kimi.com/coding/v1  (Kimi Coding Plan)
    - Other keys        → api.moonshot.ai/v1      (legacy Moonshot)

    No manual base URL prompt — endpoint is determined by key prefix.
    """
    from intellect_cli.auth import (
        PROVIDER_REGISTRY,
        KIMI_CODE_BASE_URL,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import (
        get_env_value,
        save_env_value,
        load_config,
        save_config,
    )
    from intellect_cli.models import _PROVIDER_MODELS

    provider_id = "kimi-coding"
    pconfig = PROVIDER_REGISTRY[provider_id]
    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""
    base_url_env = pconfig.base_url_env_var or ""

    # Step 1: Check / prompt for API key
    existing_key = ""
    for ev in pconfig.api_key_env_vars:
        existing_key = get_env_value(ev) or os.getenv(ev, "")
        if existing_key:
            break

    existing_key, abort = _prompt_api_key(
        pconfig, existing_key, provider_id=provider_id
    )
    if abort:
        return

    # Step 2: Auto-detect endpoint from key prefix
    is_coding_plan = existing_key.startswith("sk-kimi-")
    if is_coding_plan:
        effective_base = KIMI_CODE_BASE_URL
        print(f"  Detected Kimi Coding Plan key → {effective_base}")
    else:
        effective_base = pconfig.inference_base_url
        print(f"  Using Moonshot endpoint → {effective_base}")
    # Clear any manual base URL override so auto-detection works at runtime
    if base_url_env and get_env_value(base_url_env):
        save_env_value(base_url_env, "")
    print()

    # Step 3: Model selection — show appropriate models for the endpoint
    if is_coding_plan:
        # Coding Plan models (kimi-k2.6 first)
        model_list = [
            "kimi-k2.6",
            "kimi-k2.5",
            "kimi-for-coding",
            "kimi-k2-thinking",
            "kimi-k2-thinking-turbo",
        ]
    else:
        # Legacy Moonshot models (excludes Coding Plan-only models)
        model_list = _PROVIDER_MODELS.get("moonshot", [])

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Enter model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        # Update config with provider and base URL
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        model.pop("api_mode", None)  # let runtime auto-detect from URL
        save_config(cfg)
        deactivate_provider()

        endpoint_label = "Kimi Coding" if is_coding_plan else "Moonshot"
        print(f"Default model set to: {selected} (via {endpoint_label})")
    else:
        print("No change.")


def _infer_stepfun_region(base_url: str) -> str:
    """Infer the current StepFun region from the configured endpoint."""
    normalized = (base_url or "").strip().lower()
    if "api.stepfun.com" in normalized:
        return "china"
    return "international"


def _stepfun_base_url_for_region(region: str) -> str:
    from intellect_cli.auth import (
        STEPFUN_STEP_PLAN_CN_BASE_URL,
        STEPFUN_STEP_PLAN_INTL_BASE_URL,
    )

    return (
        STEPFUN_STEP_PLAN_CN_BASE_URL
        if region == "china"
        else STEPFUN_STEP_PLAN_INTL_BASE_URL
    )


def _model_flow_stepfun(config, current_model=""):
    """StepFun Step Plan flow with region-specific endpoints."""
    from intellect_cli.auth import (
        PROVIDER_REGISTRY,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import (
        get_env_value,
        save_env_value,
        load_config,
        save_config,
    )
    from intellect_cli.models import _PROVIDER_MODELS, fetch_api_models

    provider_id = "stepfun"
    pconfig = PROVIDER_REGISTRY[provider_id]
    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""
    base_url_env = pconfig.base_url_env_var or ""

    existing_key = ""
    for ev in pconfig.api_key_env_vars:
        existing_key = get_env_value(ev) or os.getenv(ev, "")
        if existing_key:
            break

    existing_key, abort = _prompt_api_key(
        pconfig, existing_key, provider_id=provider_id
    )
    if abort:
        return

    current_base = ""
    if base_url_env:
        current_base = get_env_value(base_url_env) or os.getenv(base_url_env, "")
    if not current_base:
        model_cfg = config.get("model")
        if isinstance(model_cfg, dict):
            current_base = str(model_cfg.get("base_url") or "").strip()
    current_region = _infer_stepfun_region(current_base or pconfig.inference_base_url)

    region_choices = [
        (
            "international",
            f"International ({_stepfun_base_url_for_region('international')})",
        ),
        ("china", f"China ({_stepfun_base_url_for_region('china')})"),
    ]
    ordered_regions = []
    for region_key, label in region_choices:
        if region_key == current_region:
            ordered_regions.insert(0, (region_key, f"{label}  ← currently active"))
        else:
            ordered_regions.append((region_key, label))
    ordered_regions.append(("cancel", "Cancel"))

    region_idx = _prompt_provider_choice([label for _, label in ordered_regions])
    if region_idx is None or ordered_regions[region_idx][0] == "cancel":
        print("No change.")
        return

    selected_region = ordered_regions[region_idx][0]
    effective_base = _stepfun_base_url_for_region(selected_region)
    if base_url_env:
        save_env_value(base_url_env, effective_base)

    live_models = fetch_api_models(existing_key, effective_base)
    if live_models:
        model_list = live_models
        print(f"  Found {len(model_list)} model(s) from {pconfig.name} API")
    else:
        model_list = _PROVIDER_MODELS.get(provider_id, [])
        if model_list:
            print(
                f"  Could not auto-detect models from {pconfig.name} API — "
                "showing Step Plan fallback catalog."
            )

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        model.pop("api_mode", None)
        save_config(cfg)
        deactivate_provider()

        config["model"] = dict(model)
        print(f"Default model set to: {selected} (via {pconfig.name})")
    else:
        print("No change.")


def _model_flow_bedrock_api_key(config, region, current_model=""):
    """Bedrock API Key mode — uses the OpenAI-compatible bedrock-mantle endpoint.

    For developers who don't have an AWS account but received a Bedrock API Key
    from their AWS admin. Works like any OpenAI-compatible endpoint.
    """
    from intellect_cli.auth import (
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import (
        load_config,
        save_config,
        get_env_value,
        save_env_value,
    )
    from intellect_cli.models import _PROVIDER_MODELS

    mantle_base_url = f"https://bedrock-mantle.{region}.api.aws/v1"

    # Prompt for API key
    existing_key = get_env_value("AWS_BEARER_TOKEN_BEDROCK") or ""
    if existing_key:
        from intellect_cli.env_loader import format_secret_source_suffix
        source_suffix = format_secret_source_suffix("AWS_BEARER_TOKEN_BEDROCK")
        print(f"  Bedrock API Key: {existing_key[:12]}... ✓{source_suffix}")
    else:
        print(f"  Endpoint: {mantle_base_url}")
        print()
        from intellect_cli.secret_prompt import masked_secret_prompt

        try:
            api_key = masked_secret_prompt("  Bedrock API Key: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not api_key:
            print("  Cancelled.")
            return
        save_env_value("AWS_BEARER_TOKEN_BEDROCK", api_key)
        existing_key = api_key
        print("  ✓ API key saved.")
    print()

    # Model selection — use static list (mantle doesn't need boto3 for discovery)
    model_list = _PROVIDER_MODELS.get("bedrock", [])
    print(f"  Showing {len(model_list)} curated models")

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("  Model ID: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        # Save as custom provider pointing to bedrock-mantle
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "custom"
        model["base_url"] = mantle_base_url
        model.pop("api_mode", None)  # chat_completions is the default

        # Also save region in bedrock config for reference
        bedrock_cfg = cfg.get("bedrock", {})
        if not isinstance(bedrock_cfg, dict):
            bedrock_cfg = {}
        bedrock_cfg["region"] = region
        cfg["bedrock"] = bedrock_cfg

        # Save the API key env var name so intellect knows where to find it
        save_env_value("OPENAI_API_KEY", existing_key)
        save_env_value("OPENAI_BASE_URL", mantle_base_url)

        save_config(cfg)
        deactivate_provider()

        print(f"  Default model set to: {selected} (via Bedrock API Key, {region})")
        print(f"  Endpoint: {mantle_base_url}")
    else:
        print("  No change.")


def _model_flow_bedrock(config, current_model=""):
    """AWS Bedrock provider: verify credentials, pick region, discover models.

    Uses the native Converse API via boto3 — not the OpenAI-compatible endpoint.
    Auth is handled by the AWS SDK default credential chain (env vars, profile,
    instance role), so no API key prompt is needed.
    """
    from intellect_cli.auth import (
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import load_config, save_config
    from intellect_cli.models import _PROVIDER_MODELS

    # 1. Check for AWS credentials
    try:
        from agent.bedrock_adapter import (
            has_aws_credentials,
            resolve_aws_auth_env_var,
            resolve_bedrock_region,
            discover_bedrock_models,
        )
    except ImportError:
        print("  ✗ boto3 is not installed. Install it with:")
        print("    pip install boto3")
        print()
        return

    if not has_aws_credentials():
        print("  ⚠ No AWS credentials detected via environment variables.")
        print("  Bedrock will use boto3's default credential chain (IMDS, SSO, etc.)")
        print()

    auth_var = resolve_aws_auth_env_var()
    if auth_var:
        print(f"  AWS credentials: {auth_var} ✓")
    else:
        print("  AWS credentials: boto3 default chain (instance role / SSO)")
    print()

    # 2. Region selection
    current_region = resolve_bedrock_region()
    try:
        region_input = input(f"  AWS Region [{current_region}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    region = region_input or current_region

    # 2b. Authentication mode
    print("  Choose authentication method:")
    print()
    print("    1. IAM credential chain (recommended)")
    print("       Works with EC2 instance roles, SSO, env vars, aws configure")
    print("    2. Bedrock API Key")
    print("       Enter your Bedrock API Key directly — also supports")
    print("       team scenarios where an admin distributes keys")
    print()
    try:
        auth_choice = input("  Choice [1]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if auth_choice == "2":
        _model_flow_bedrock_api_key(config, region, current_model)
        return

    # 3. Model discovery — try live API first, fall back to static list
    print(f"  Discovering models in {region}...")
    live_models = discover_bedrock_models(region)

    if live_models:
        _EXCLUDE_PREFIXES = (
            "stability.",
            "cohere.embed",
            "twelvelabs.",
            "us.stability.",
            "us.cohere.embed",
            "us.twelvelabs.",
            "global.cohere.embed",
            "global.twelvelabs.",
        )
        _EXCLUDE_SUBSTRINGS = ("safeguard", "voxtral", "palmyra-vision")
        filtered = []
        for m in live_models:
            mid = m["id"]
            if any(mid.startswith(p) for p in _EXCLUDE_PREFIXES):
                continue
            if any(s in mid.lower() for s in _EXCLUDE_SUBSTRINGS):
                continue
            filtered.append(m)

        # Deduplicate: prefer inference profiles (us.*, global.*) over bare
        # foundation model IDs.
        profile_base_ids = set()
        for m in filtered:
            mid = m["id"]
            if mid.startswith(("us.", "global.")):
                base = mid.split(".", 1)[1] if "." in mid[3:] else mid
                profile_base_ids.add(base)

        deduped = []
        for m in filtered:
            mid = m["id"]
            if not mid.startswith(("us.", "global.")) and mid in profile_base_ids:
                continue
            deduped.append(m)

        _RECOMMENDED = [
            "us.anthropic.claude-sonnet-4-6",
            "us.anthropic.claude-opus-4-6",
            "us.anthropic.claude-haiku-4-5",
            "us.amazon.nova-pro",
            "us.amazon.nova-lite",
            "us.amazon.nova-micro",
            "deepseek.v3",
            "us.meta.llama4-maverick",
            "us.meta.llama4-scout",
        ]

        def _sort_key(m):
            mid = m["id"]
            for i, rec in enumerate(_RECOMMENDED):
                if mid.startswith(rec):
                    return (0, i, mid)
            if mid.startswith("global."):
                return (1, 0, mid)
            return (2, 0, mid)

        deduped.sort(key=_sort_key)
        model_list = [m["id"] for m in deduped]
        print(
            f"  Found {len(model_list)} text model(s) (filtered from {len(live_models)} total)"
        )
    else:
        model_list = _PROVIDER_MODELS.get("bedrock", [])
        if model_list:
            print(
                f"  Using {len(model_list)} curated models (live discovery unavailable)"
            )
        else:
            print(
                "  No models found. Check IAM permissions for bedrock:ListFoundationModels."
            )
            return

    # 4. Model selection
    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("  Model ID: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "bedrock"
        model["base_url"] = f"https://bedrock-runtime.{region}.amazonaws.com"
        model.pop("api_mode", None)  # bedrock_converse is auto-detected

        bedrock_cfg = cfg.get("bedrock", {})
        if not isinstance(bedrock_cfg, dict):
            bedrock_cfg = {}
        bedrock_cfg["region"] = region
        cfg["bedrock"] = bedrock_cfg

        save_config(cfg)
        deactivate_provider()

        print(f"  Default model set to: {selected} (via AWS Bedrock, {region})")
    else:
        print("  No change.")


def _model_flow_api_key_provider(config, provider_id, current_model=""):
    """Generic flow for API-key providers (z.ai, MiniMax, OpenCode, etc.)."""
    from intellect_cli.auth import (
        PROVIDER_REGISTRY,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import (
        get_env_value,
        save_env_value,
        load_config,
        save_config,
    )
    from intellect_cli.models import (
        _PROVIDER_MODELS,
        fetch_api_models,
        opencode_model_api_mode,
        normalize_opencode_model_id,
    )

    pconfig = PROVIDER_REGISTRY[provider_id]
    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""
    base_url_env = pconfig.base_url_env_var or ""

    # Check / prompt for API key
    existing_key = ""
    for ev in pconfig.api_key_env_vars:
        existing_key = get_env_value(ev) or os.getenv(ev, "")
        if existing_key:
            break

    existing_key, abort = _prompt_api_key(
        pconfig, existing_key, provider_id=provider_id
    )
    if abort:
        return

    # Gemini free-tier gate: free-tier daily quotas (<= 250 RPD for Flash)
    # are exhausted in a handful of agent turns, so refuse to wire up the
    # provider with a free-tier key. Probe is best-effort; network or auth
    # errors fall through without blocking.
    if provider_id == "gemini" and existing_key:
        try:
            from agent.gemini_native_adapter import probe_gemini_tier
        except Exception:
            probe_gemini_tier = None
        if probe_gemini_tier is not None:
            print("  Checking Gemini API tier...")
            probe_base = (
                (get_env_value(base_url_env) if base_url_env else "")
                or os.getenv(base_url_env or "", "")
                or pconfig.inference_base_url
            )
            tier = probe_gemini_tier(existing_key, probe_base)
            if tier == "free":
                print()
                print(
                    "❌ This Google API key is on the free tier "
                    "(<= 250 requests/day for gemini-2.5-flash)."
                )
                print(
                    "   Intellect typically makes 3-10 API calls per user turn "
                    "(tool iterations + auxiliary tasks),"
                )
                print(
                    "   so the free tier is exhausted after a handful of "
                    "messages and cannot sustain"
                )
                print("   an agent session.")
                print()
                print(
                    "   To use Gemini with Intellect, enable billing on your "
                    "Google Cloud project and regenerate"
                )
                print(
                    "   the key in a billing-enabled project: "
                    "https://aistudio.google.com/apikey"
                )
                print()
                print(
                    "   Alternatives with workable free usage: DeepSeek, "
                    "OpenRouter (free models), Groq, OntoWeb."
                )
                print()
                print("Not saving Gemini as the default provider.")
                return
            if tier == "paid":
                print("  Tier check: paid ✓")
            else:
                # "unknown" -- network issue, auth problem, unexpected response.
                # Don't block; the runtime 429 handler will surface free-tier
                # guidance if the key turns out to be free tier.
                print("  Tier check: could not verify (proceeding anyway).")
            print()

    # Optional base URL override.
    # Precedence: env var → config.yaml model.base_url → registry default.
    # Reading config.yaml prevents silently overwriting a saved remote URL
    # (e.g. a remote LM Studio endpoint) with localhost when the user just
    # presses Enter at the prompt below.
    current_base = ""
    if base_url_env:
        current_base = get_env_value(base_url_env) or os.getenv(base_url_env, "")
    if not current_base:
        try:
            _m = load_config().get("model") or {}
            if str(_m.get("provider") or "").strip().lower() == provider_id:
                current_base = str(_m.get("base_url") or "").strip()
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
    effective_base = current_base or pconfig.inference_base_url

    try:
        override = input(f"Base URL [{effective_base}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        override = ""
    if override and base_url_env:
        if not override.startswith(("http://", "https://")):
            print(
                "  Invalid URL — must start with http:// or https://. Keeping current value."
            )
        else:
            save_env_value(base_url_env, override)
            effective_base = override

    # Model selection — resolution order:
    #   1. models.dev registry (cached, filtered for agentic/tool-capable models)
    #   2. Curated static fallback list (offline insurance)
    #   3. Live /models endpoint probe (small providers without models.dev data)
    #
    # LM Studio: live /api/v1/models probe (no models.dev catalog).
    # Ollama Cloud: merged discovery (live API + models.dev + disk cache).
    if provider_id == "lmstudio":
        from intellect_cli.auth import AuthError
        from intellect_cli.models import fetch_lmstudio_models

        api_key_for_probe = existing_key or (get_env_value(key_env) if key_env else "")
        try:
            model_list = fetch_lmstudio_models(
                api_key=api_key_for_probe, base_url=effective_base
            )
        except AuthError as exc:
            print(f"  LM Studio rejected the request: {exc}")
            print("  Set LM_API_KEY (or update it) to match the server's bearer token.")
            model_list = []
        if model_list:
            print(f"  Found {len(model_list)} model(s) from LM Studio")
    elif provider_id == "ollama-cloud":
        from intellect_cli.models import fetch_ollama_cloud_models

        api_key_for_probe = existing_key or (get_env_value(key_env) if key_env else "")
        # During setup, force a live refresh so the picker reflects newly
        # released models (e.g. deepseek v4 flash, kimi k2.6) the moment
        # the user enters their key — not an hour later when the disk
        # cache TTL expires.
        model_list = fetch_ollama_cloud_models(
            api_key=api_key_for_probe,
            base_url=effective_base,
            force_refresh=True,
        )
        if model_list:
            print(f"  Found {len(model_list)} model(s) from Ollama Cloud")
    elif provider_id == "novita":
        from intellect_cli.models import fetch_api_models

        api_key_for_probe = existing_key or (get_env_value(key_env) if key_env else "")
        curated = _PROVIDER_MODELS.get(provider_id, [])
        live_models = fetch_api_models(api_key_for_probe, effective_base)
        if live_models:
            model_list = live_models
            print(f"  Found {len(model_list)} model(s) from {pconfig.name} API")
        else:
            mdev_models: list = []
            try:
                from agent.models_dev import list_agentic_models

                mdev_models = list_agentic_models(provider_id)
            except Exception:
                logger.debug('non-critical operation failed', exc_info=True)
            if mdev_models:
                seen = {m.lower() for m in mdev_models}
                model_list = list(mdev_models)
                for m in curated:
                    if m.lower() not in seen:
                        model_list.append(m)
                        seen.add(m.lower())
                print(f"  Found {len(model_list)} model(s) from models.dev registry")
            else:
                model_list = curated
                if model_list:
                    print(
                        f'  Showing {len(model_list)} curated models — use "Enter custom model name" for others.'
                    )
    else:
        curated = _PROVIDER_MODELS.get(provider_id, [])

        # Try models.dev first — returns tool-capable models, filtered for noise
        mdev_models: list = []
        try:
            from agent.models_dev import list_agentic_models

            mdev_models = list_agentic_models(provider_id)
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)

        if mdev_models:
            # Merge models.dev with curated list so newly added models
            # (not yet in models.dev) still appear in the picker.
            if curated:
                seen = {m.lower() for m in mdev_models}
                merged = list(mdev_models)
                for m in curated:
                    if m.lower() not in seen:
                        merged.append(m)
                        seen.add(m.lower())
                model_list = merged
            else:
                model_list = mdev_models
            print(f"  Found {len(model_list)} model(s) from models.dev registry")
        elif curated and len(curated) >= 8:
            # Curated list is substantial — use it directly, skip live probe
            model_list = curated
            print(
                f'  Showing {len(model_list)} curated models — use "Enter custom model name" for others.'
            )
        else:
            api_key_for_probe = existing_key or (
                get_env_value(key_env) if key_env else ""
            )
            live_models = fetch_api_models(api_key_for_probe, effective_base)
            if live_models and len(live_models) >= len(curated):
                model_list = live_models
                print(f"  Found {len(model_list)} model(s) from {pconfig.name} API")
            else:
                model_list = curated
                if model_list:
                    print(
                        f'  Showing {len(model_list)} curated models — use "Enter custom model name" for others.'
                    )
            # else: no defaults either, will fall through to raw input

    if provider_id in {"opencode-zen", "opencode-go"}:
        model_list = [
            normalize_opencode_model_id(provider_id, mid) for mid in model_list
        ]
        current_model = normalize_opencode_model_id(provider_id, current_model)
        model_list = list(dict.fromkeys(mid for mid in model_list if mid))

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        if provider_id in {"opencode-zen", "opencode-go"}:
            selected = normalize_opencode_model_id(provider_id, selected)

        _save_model_choice(selected)

        # Update config with provider, base URL, and provider-specific API mode
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        if provider_id in {"opencode-zen", "opencode-go"}:
            model["api_mode"] = opencode_model_api_mode(provider_id, selected)
        else:
            model.pop("api_mode", None)
        save_config(cfg)
        deactivate_provider()

        print(f"Default model set to: {selected} (via {pconfig.name})")
    else:
        print("No change.")


def _run_anthropic_oauth_flow(save_env_value):
    """Run the Claude OAuth setup-token flow. Returns True if credentials were saved."""
    from agent.anthropic_adapter import (
        run_oauth_setup_token,
        read_claude_code_credentials,
        is_claude_code_token_valid,
    )
    from intellect_cli.config import (
        save_anthropic_oauth_token,
        use_anthropic_claude_code_credentials,
    )

    def _activate_claude_code_credentials_if_available() -> bool:
        try:
            creds = read_claude_code_credentials()
        except Exception:
            creds = None
        if creds and (
            is_claude_code_token_valid(creds) or bool(creds.get("refreshToken"))
        ):
            use_anthropic_claude_code_credentials(save_fn=save_env_value)
            print("  ✓ Claude Code credentials linked.")
            from intellect_constants import display_intellect_home as _dhh_fn

            print(
                f"    Intellect will use Claude's credential store directly instead of copying a setup-token into {_dhh_fn()}/.env."
            )
            return True
        return False

    try:
        print()
        print("  Running 'claude setup-token' — follow the prompts below.")
        print("  A browser window will open for you to authorize access.")
        print()
        token = run_oauth_setup_token()
        if token:
            if _activate_claude_code_credentials_if_available():
                return True
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ OAuth credentials saved.")
            return True

        # Subprocess completed but no token auto-detected — ask user to paste
        print()
        print("  If the setup-token was displayed above, paste it here:")
        print()
        from intellect_cli.secret_prompt import masked_secret_prompt

        try:
            manual_token = masked_secret_prompt(
                "  Paste setup-token (or Enter to cancel): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if manual_token:
            save_anthropic_oauth_token(manual_token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True

        print("  ⚠ Could not detect saved credentials.")
        return False

    except FileNotFoundError:
        # Claude CLI not installed — guide user through manual setup
        print()
        print("  The 'claude' CLI is required for OAuth login.")
        print()
        print("  To install and authenticate:")
        print()
        print("    1. Install Claude Code:  npm install -g @anthropic-ai/claude-code")
        print("    2. Run:                  claude setup-token")
        print("    3. Follow the browser prompts to authorize")
        print("    4. Re-run:               intellect model")
        print()
        print("  Or paste an existing setup-token now (sk-ant-oat-...):")
        print()
        from intellect_cli.secret_prompt import masked_secret_prompt

        try:
            token = masked_secret_prompt("  Setup-token (or Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if token:
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True
        print("  Cancelled — install Claude Code and try again.")
        return False


def _model_flow_anthropic(config, current_model=""):
    """Flow for Anthropic provider — OAuth subscription, API key, or Claude Code creds."""
    from intellect_cli.auth import (
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
    )
    from intellect_cli.config import (
        save_env_value,
        load_config,
        save_config,
        save_anthropic_api_key,
    )
    from intellect_cli.models import _PROVIDER_MODELS

    # Check ALL credential sources
    from intellect_cli.auth import get_anthropic_key

    existing_key = get_anthropic_key()
    cc_available = False
    try:
        from agent.anthropic_adapter import (
            read_claude_code_credentials,
            is_claude_code_token_valid,
            _is_oauth_token,
        )

        cc_creds = read_claude_code_credentials()
        if cc_creds and is_claude_code_token_valid(cc_creds):
            cc_available = True
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    # Stale-OAuth guard: if the only existing cred is an expired OAuth token
    # (no valid cc_creds to fall back on), treat it as missing so the re-auth
    # path is offered instead of silently accepting a broken token.
    existing_is_stale_oauth = False
    if existing_key and _is_oauth_token(existing_key) and not cc_available:
        existing_is_stale_oauth = True

    has_creds = (bool(existing_key) and not existing_is_stale_oauth) or cc_available
    needs_auth = not has_creds

    if has_creds:
        # Show what we found
        if existing_key:
            from intellect_cli.env_loader import format_secret_source_suffix
            from intellect_cli.auth import PROVIDER_REGISTRY

            # Surface which env var supplied the key so users with
            # Bitwarden see "(from Bitwarden)" — without this, a detected
            # BSM key looks identical to a key in .env and users assume
            # nothing is wired up.
            source_suffix = ""
            for var in PROVIDER_REGISTRY["anthropic"].api_key_env_vars:
                if os.getenv(var, "").strip() == existing_key:
                    source_suffix = format_secret_source_suffix(var)
                    if source_suffix:
                        break
            print(
                f"  Anthropic credentials: {existing_key[:12]}... ✓{source_suffix}"
            )
        elif cc_available:
            print("  Claude Code credentials: ✓ (auto-detected)")
        print()
        print("    1. Use existing credentials")
        print("    2. Reauthenticate (new OAuth login)")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1/2/3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = "1"

        if choice == "2":
            needs_auth = True
        elif choice == "3":
            return
        # choice == "1" or default: use existing, proceed to model selection

    if needs_auth:
        # Show auth method choice
        print()
        print("  Choose authentication method:")
        print()
        print("    1. Claude Pro/Max subscription (OAuth login)")
        print("    2. Anthropic API key (pay-per-token)")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1/2/3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "1":
            if not _run_anthropic_oauth_flow(save_env_value):
                return

        elif choice == "2":
            print()
            print("  Get an API key at: https://platform.claude.com/settings/keys")
            print()
            from intellect_cli.secret_prompt import masked_secret_prompt

            try:
                api_key = masked_secret_prompt("  API key (sk-ant-...): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if not api_key:
                print("  Cancelled.")
                return
            save_anthropic_api_key(api_key, save_fn=save_env_value)
            print("  ✓ API key saved.")

        else:
            print("  No change.")
            return
    print()

    # Model selection
    model_list = _PROVIDER_MODELS.get("anthropic", [])
    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Model name (e.g., claude-sonnet-4-20250514): ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        # Update config with provider — clear base_url since
        # resolve_runtime_provider() always hardcodes Anthropic's URL.
        # Leaving a stale base_url in config can contaminate other
        # providers if the user switches without running 'intellect model'.
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "anthropic"
        model.pop("base_url", None)
        save_config(cfg)
        deactivate_provider()

        print(f"Default model set to: {selected} (via Anthropic)")
    else:
        print("No change.")


