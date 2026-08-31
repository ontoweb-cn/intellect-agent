from __future__ import annotations

from collections.abc import Mapping


def _coerce_timeout(raw: object) -> float | None:
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    if timeout <= 0:
        return None
    return timeout


def _get_provider_scalar(
    provider_id: str,
    model: str | None,
    model_key: str,
    provider_key: str,
) -> float | None:
    """Resolve a per-provider/per-model scalar (seconds) from config.

    Shared preamble for the request/stale/run-budget getters: load config,
    resolve ``providers.<id>`` then ``providers.<id>.models.<model>``, and
    coerce the value.  The model-level key wins over the provider-level key.
    """
    if not provider_id:
        return None

    try:
        from intellect_cli.config import load_config_readonly
        config = load_config_readonly()
    except Exception:
        return None

    providers = config.get("providers", {}) if isinstance(config, Mapping) else {}
    provider_config = (
        providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    )
    if not isinstance(provider_config, dict):
        return None

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        value = _coerce_timeout(model_config.get(model_key))
        if value is not None:
            return value

    return _coerce_timeout(provider_config.get(provider_key))


def get_provider_request_timeout(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return a configured provider request timeout in seconds, if any."""
    return _get_provider_scalar(
        provider_id, model, "timeout_seconds", "request_timeout_seconds"
    )


def get_provider_stale_timeout(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return a configured non-stream stale timeout in seconds, if any."""
    return _get_provider_scalar(
        provider_id, model, "stale_timeout_seconds", "stale_timeout_seconds"
    )


def get_provider_run_budget(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return a configured wall-clock run budget in seconds, if any.

    Bounds a whole turn by elapsed wall-clock time (independent of iteration
    count).  ``None`` means the budget is disabled.
    """
    return _get_provider_scalar(
        provider_id, model, "run_budget_seconds", "run_budget_seconds"
    )


def _get_model_config(
    provider_config: dict[str, object], model: str | None
) -> dict[str, object] | None:
    if not model:
        return None

    models = provider_config.get("models", {})
    model_config = models.get(model, {}) if isinstance(models, dict) else {}
    if isinstance(model_config, dict):
        return model_config
    return None
