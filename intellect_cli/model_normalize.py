"""Per-provider model name normalization.

Different LLM providers expect model identifiers in different formats:

- **Aggregators** (OpenRouter, OntoWeb, AI Gateway, Kilo Code) need
  ``vendor/model`` slugs like ``anthropic/claude-sonnet-4.6``.
- **Anthropic** native API expects bare names with dots replaced by
  hyphens: ``claude-sonnet-4-6``.
- **Copilot** expects bare names *with* dots preserved:
  ``claude-sonnet-4.6``.
- **OpenCode Zen** preserves dots for GPT/GLM/Gemini/Kimi/MiniMax-style
  model IDs, but Claude still uses hyphenated native names like
  ``claude-sonnet-4-6``.
- **OpenCode Go** preserves dots in model names: ``minimax-m2.7``.
- **DeepSeek** accepts ``deepseek-chat`` (V3), ``deepseek-reasoner``
  (R1-family), and the first-class V-series IDs (``deepseek-v4-pro``,
  ``deepseek-v4-flash``, and any future ``deepseek-v<N>-*``).  Older
  Intellect revisions folded every non-reasoner input into
  ``deepseek-chat``, which on aggregators routes to V3 — so a user
  picking V4 Pro was silently downgraded.
- **Custom** and remaining providers pass the name through as-is.

This module centralises that translation so callers can simply write::

    api_model = normalize_model_for_provider(user_input, provider)

Inspired by Clawdbot's ``normalizeAnthropicModelId`` pattern.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Vendor prefix mapping
# ---------------------------------------------------------------------------
# Maps the first hyphen-delimited token of a bare model name to the vendor
# slug used by aggregator APIs (OpenRouter, OntoWeb, etc.).
#
# Example: "claude-sonnet-4.6" -> first token "claude" -> vendor "anthropic"
#          -> aggregator slug: "anthropic/claude-sonnet-4.6"

_VENDOR_PREFIXES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "gemini": "google",
    "gemma": "google",
    "deepseek": "deepseek",
    "glm": "z-ai",
    "kimi": "moonshotai",
    "minimax": "minimax",
    "grok": "x-ai",
    "qwen": "qwen",
    "mimo": "xiaomi",
    "trinity": "arcee-ai",
    "nemotron": "nvidia",
    "llama": "meta-llama",
    "step": "stepfun",
    "doubao": "volcengine",
}

# Providers whose APIs consume vendor/model slugs.
_AGGREGATOR_PROVIDERS: frozenset[str] = frozenset({
    "openrouter",
    "ontoweb",
    "kilocode",
})

# Providers that want bare names with dots replaced by hyphens.
_DOT_TO_HYPHEN_PROVIDERS: frozenset[str] = frozenset({
    "anthropic",
})

# Providers that want bare names with dots preserved.
_STRIP_VENDOR_ONLY_PROVIDERS: frozenset[str] = frozenset({
    "copilot",
    "copilot-acp",
    "openai-codex",
})

# Providers whose native naming is authoritative -- pass through unchanged.
_AUTHORITATIVE_NATIVE_PROVIDERS: frozenset[str] = frozenset({
    "gemini",
    "huggingface",
})

# Direct providers that accept bare native names but should repair a matching
# provider/ prefix when users copy the aggregator form into config.yaml.
_MATCHING_PREFIX_STRIP_PROVIDERS: frozenset[str] = frozenset({
    "zai",
    "kimi-coding",
    "kimi-coding-cn",
    "minimax",
    "minimax-oauth",
    "minimax-cn",
    "alibaba",
    "qwen-oauth",
    "xiaomi",
    "arcee",
    "ollama-cloud",
    "custom",
    "volcengine",
    "volcengine-coding-plan",
    "volcengine-agent-plan",
})

# Providers whose APIs require lowercase model IDs.  Xiaomi's
# ``api.xiaomimimo.com`` rejects mixed-case names like ``MiMo-V2.5-Pro``
# that users might copy from marketing docs — it only accepts
# ``mimo-v2.5-pro``.  After stripping a matching provider prefix, these
# providers also get ``.lower()`` applied.
_LOWERCASE_MODEL_PROVIDERS: frozenset[str] = frozenset({
    "xiaomi",
    "volcengine",
    "volcengine-coding-plan",
    "volcengine-agent-plan",
})

# ---------------------------------------------------------------------------
# DeepSeek special handling
# ---------------------------------------------------------------------------
# Verified against the live API 2026-09-15, which states the supported set
# outright: "The supported API model names are deepseek-flash,
# deepseek-v4-pro". The V3-era names — ``deepseek-chat`` (the old default) and
# ``deepseek-reasoner`` — are retired, as is the pre-rename ``deepseek-v4-flash``
# for the same model. The endpoint still accepts the retired names for
# compatibility but serves ``deepseek-flash`` for them, so a request naming one
# must not be reported as reaching a model by that name. They stay accepted as
# *inputs* (configs and aggregator slugs still contain them) and fold to what
# the server actually serves. Anything unrecognised resolves the same way,
# which is also what keeps a bare ``deepseek-r1``-style name working: the
# default model reasons natively, so no separate reasoner id is needed.

#: Retired V3-era identifiers, kept as accepted inputs. DeepSeek's API states
#: the supported set outright — "The supported API model names are
#: deepseek-flash, deepseek-v4-pro" — and serves ``deepseek-flash`` for the
#: retired names rather than erroring. Folding them here keeps what the code
#: says and what the API does in agreement: a request naming ``deepseek-chat``
#: must not be reported as serving a model by that name.
_DEEPSEEK_RETIRED_MODELS: frozenset[str] = frozenset({
    "deepseek-chat",       # V3 default; superseded by deepseek-flash
    "deepseek-reasoner",   # R1 era; flash reasons natively now
    "deepseek-v4-flash",   # the pre-rename id for the same model
})

_DEEPSEEK_CANONICAL_MODELS: frozenset[str] = frozenset({
    "deepseek-flash",      # the default model
    "deepseek-v4-pro",     # the distinct larger model
})

#: What every unrecognised / retired DeepSeek name resolves to, so the
#: reported model equals the one actually answering.
_DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"

# First-class V-series IDs: a major version plus a single name segment
# (``deepseek-v4-pro``, and future ``deepseek-v5-pro`` / ``deepseek-v10-ultra``
# without needing a code change).
#
# Deliberately *not* ``(...)?$``-permissive: that also matched segmented
# catalogue shapes like ``deepseek-v4-pro-0813`` and
# ``deepseek-v4-flash-20260423``, and those are not names this API serves. It
# says so outright — passing one returns
# "The supported API model names are deepseek-flash, deepseek-v4-pro, but you
# passed …" (verified 2026-09-15). They are aggregator slugs (OpenRouter lists
# ``deepseek/deepseek-v4-pro-0813``), i.e. copies from a different namespace,
# so they fold like any other unknown input rather than being forwarded to a
# guaranteed 400.
_DEEPSEEK_V_SERIES_RE = re.compile(r"^deepseek-v\d+-[a-z][a-z0-9]*$")


def _normalize_for_deepseek(model_name: str) -> str:
    """Map a model input to a DeepSeek-accepted identifier.

    Rules:
    - A retired V3-era name (``deepseek-chat``/``deepseek-reasoner``/
      ``deepseek-v4-flash``) -> ``deepseek-flash``, which is what the server
      serves for them today.
    - Already canonical (``deepseek-flash``/``deepseek-v4-pro``) -> pass through.
    - A first-class V-series id — version plus one name segment
      (``deepseek-v5-pro``) -> pass through, so a future model needs no code
      change. Segmented catalogue shapes (``deepseek-v4-pro-0813``) do not
      qualify; see the regex comment.
    - Contains a reasoner keyword (r1, think, reasoning, cot, reasoner)
      -> ``deepseek-reasoner`` when that is still served, else the default.
    - Everything else -> ``deepseek-flash``.

    Args:
        model_name: The bare model name (vendor prefix already stripped).

    Returns:
        A DeepSeek-accepted model identifier.
    """
    bare = _strip_vendor_prefix(model_name).lower()

    if bare in _DEEPSEEK_CANONICAL_MODELS:
        return bare

    if bare in _DEEPSEEK_RETIRED_MODELS:
        return _DEEPSEEK_DEFAULT_MODEL

    # V-series first-class IDs (v4-pro, future v5-*, dated variants)
    if _DEEPSEEK_V_SERIES_RE.match(bare):
        return bare

    # Reasoner-flavoured inputs (``deepseek-r1``, ``*-think-*``, …) no longer
    # select a separate model: ``deepseek-reasoner`` is retired and the default
    # model reasons natively. They resolve to the default rather than to a
    # name the API would silently rewrite.
    return _DEEPSEEK_DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _strip_vendor_prefix(model_name: str) -> str:
    """Remove a ``vendor/`` prefix if present.

    Examples::

        >>> _strip_vendor_prefix("anthropic/claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> _strip_vendor_prefix("claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> _strip_vendor_prefix("meta-llama/llama-4-scout")
        'llama-4-scout'
    """
    if "/" in model_name:
        return model_name.split("/", 1)[1]
    return model_name


def _dots_to_hyphens(model_name: str) -> str:
    """Replace dots with hyphens in a model name.

    Anthropic's native API uses hyphens where marketing names use dots:
    ``claude-sonnet-4.6`` -> ``claude-sonnet-4-6``.
    """
    return model_name.replace(".", "-")


def _normalize_provider_alias(provider_name: str) -> str:
    """Resolve provider aliases to Intellect' canonical ids."""
    raw = (provider_name or "").strip().lower()
    if not raw:
        return raw
    try:
        from intellect_cli.models import normalize_provider

        return normalize_provider(raw)
    except Exception:
        return raw


def _strip_matching_provider_prefix(model_name: str, target_provider: str) -> str:
    """Strip ``provider/`` only when the prefix matches the target provider.

    This prevents arbitrary slash-bearing model IDs from being mangled on
    native providers while still repairing manual config values like
    ``zai/glm-5.1`` for the ``zai`` provider.
    """
    if "/" not in model_name:
        return model_name

    prefix, remainder = model_name.split("/", 1)
    if not prefix.strip() or not remainder.strip():
        return model_name

    normalized_prefix = _normalize_provider_alias(prefix)
    normalized_target = _normalize_provider_alias(target_provider)
    if normalized_prefix and normalized_prefix == normalized_target:
        return remainder.strip()
    return model_name


def detect_vendor(model_name: str) -> Optional[str]:
    """Detect the vendor slug from a bare model name.

    Uses the first hyphen-delimited token of the model name to look up
    the corresponding vendor in ``_VENDOR_PREFIXES``.  Also handles
    case-insensitive matching and special patterns.

    Args:
        model_name: A model name, optionally already including a
            ``vendor/`` prefix.  If a prefix is present it is used
            directly.

    Returns:
        The vendor slug (e.g. ``"anthropic"``, ``"openai"``) or ``None``
        if no vendor can be confidently detected.

    Examples::

        >>> detect_vendor("claude-sonnet-4.6")
        'anthropic'
        >>> detect_vendor("gpt-5.4-mini")
        'openai'
        >>> detect_vendor("anthropic/claude-sonnet-4.6")
        'anthropic'
        >>> detect_vendor("my-custom-model")
    """
    name = model_name.strip()
    if not name:
        return None

    # If there's already a vendor/ prefix, extract it
    if "/" in name:
        return name.split("/", 1)[0].lower() or None

    name_lower = name.lower()

    # Try first hyphen-delimited token (exact match)
    first_token = name_lower.split("-")[0]
    if first_token in _VENDOR_PREFIXES:
        return _VENDOR_PREFIXES[first_token]

    # Handle patterns where the first token includes version digits,
    # e.g. "qwen3.5-plus" -> first token "qwen3.5", but prefix is "qwen"
    for prefix, vendor in _VENDOR_PREFIXES.items():
        if name_lower.startswith(prefix):
            return vendor

    return None


def _prepend_vendor(model_name: str) -> str:
    """Prepend the detected ``vendor/`` prefix if missing.

    Used for aggregator providers that require ``vendor/model`` format.
    If the name already contains a ``/``, it is returned as-is.
    If no vendor can be detected, the name is returned unchanged
    (aggregators may still accept it or return an error).

    Examples::

        >>> _prepend_vendor("claude-sonnet-4.6")
        'anthropic/claude-sonnet-4.6'
        >>> _prepend_vendor("anthropic/claude-sonnet-4.6")
        'anthropic/claude-sonnet-4.6'
        >>> _prepend_vendor("my-custom-thing")
        'my-custom-thing'
    """
    if "/" in model_name:
        return model_name

    vendor = detect_vendor(model_name)
    if vendor:
        return f"{vendor}/{model_name}"
    return model_name


# ---------------------------------------------------------------------------
# Main normalisation entry point
# ---------------------------------------------------------------------------

def normalize_model_for_provider(model_input: str, target_provider: str) -> str:
    """Translate a model name into the format the target provider's API expects.

    This is the primary entry point for model name normalisation.  It
    accepts any user-facing model identifier and transforms it for the
    specific provider that will receive the API call.

    Args:
        model_input: The model name as provided by the user or config.
            Can be bare (``"claude-sonnet-4.6"``), vendor-prefixed
            (``"anthropic/claude-sonnet-4.6"``), or already in native
            format (``"claude-sonnet-4-6"``).
        target_provider: The canonical Intellect provider id, e.g.
            ``"openrouter"``, ``"anthropic"``, ``"copilot"``,
            ``"deepseek"``, ``"custom"``.  Should already be normalised
            via ``intellect_cli.models.normalize_provider()``.

    Returns:
        The model identifier string that the target provider's API
        expects.

    Raises:
        No exceptions -- always returns a best-effort string.

    Examples::

        >>> normalize_model_for_provider("claude-sonnet-4.6", "openrouter")
        'anthropic/claude-sonnet-4.6'

        >>> normalize_model_for_provider("anthropic/claude-sonnet-4.6", "anthropic")
        'claude-sonnet-4-6'

        >>> normalize_model_for_provider("anthropic/claude-sonnet-4.6", "copilot")
        'claude-sonnet-4.6'

        >>> normalize_model_for_provider("openai/gpt-5.4", "copilot")
        'gpt-5.4'

        >>> normalize_model_for_provider("claude-sonnet-4.6", "opencode-zen")
        'claude-sonnet-4-6'

        >>> normalize_model_for_provider("minimax-m2.5-free", "opencode-zen")
        'minimax-m2.5-free'

        >>> normalize_model_for_provider("deepseek-v3", "deepseek")
        'deepseek-chat'

        >>> normalize_model_for_provider("deepseek-r1", "deepseek")
        'deepseek-reasoner'

        >>> normalize_model_for_provider("my-model", "custom")
        'my-model'

        >>> normalize_model_for_provider("claude-sonnet-4.6", "zai")
        'claude-sonnet-4.6'

        >>> normalize_model_for_provider("MiMo-V2.5-Pro", "xiaomi")
        'mimo-v2.5-pro'
    """
    name = (model_input or "").strip()
    if not name:
        return name

    provider = _normalize_provider_alias(target_provider)

    # --- Aggregators: need vendor/model format ---
    if provider in _AGGREGATOR_PROVIDERS:
        return _prepend_vendor(name)

    # --- OpenCode Zen / OpenCode Go: flat-namespace resellers.
    #     Their /v1/models API returns bare IDs only (no vendor prefix), and
    #     the inference endpoint rejects vendor-prefixed names with HTTP 401
    #     "Model not supported".  Strip ANY leading ``vendor/`` so config
    #     entries like ``minimax/minimax-m2.7`` or ``deepseek/deepseek-v4-flash``
    #     — commonly copied from aggregator slugs into fallback_model lists —
    #     resolve to bare ``minimax-m2.7`` / ``deepseek-v4-flash`` the API
    #     actually serves.  See PR reviewing opencode-go fallback 401s. ---
    if provider in {"opencode-zen", "opencode-go"}:
        if "/" in name:
            _, bare_after_slash = name.split("/", 1)
            name = bare_after_slash.strip() or name
        if provider == "opencode-zen" and name.lower().startswith("claude-"):
            return _dots_to_hyphens(name)
        return name

    # --- Anthropic: strip matching provider prefix, dots -> hyphens ---
    if provider in _DOT_TO_HYPHEN_PROVIDERS:
        bare = _strip_matching_provider_prefix(name, provider)
        if "/" in bare:
            return bare
        return _dots_to_hyphens(bare)

    # --- Copilot / Copilot ACP: delegate to the Copilot-specific
    #     normalizer.  It knows about the alias table (vendor-prefix
    #     stripping for Anthropic/OpenAI, dash-to-dot repair for Claude)
    #     and live-catalog lookups.  Without this, vendor-prefixed or
    #     dash-notation Claude IDs survive to the Copilot API and hit
    #     HTTP 400 "model_not_supported".  See issue #6879.
    if provider in {"copilot", "copilot-acp"}:
        try:
            from intellect_cli.models import normalize_copilot_model_id

            normalized = normalize_copilot_model_id(name)
            if normalized:
                return normalized
        except Exception:
            # Fall through to the generic strip-vendor behaviour below
            # if the Copilot-specific path is unavailable for any reason.
            pass

    # --- Copilot / Copilot ACP / openai-codex fallback:
    #     strip matching provider prefix, keep dots ---
    if provider in _STRIP_VENDOR_ONLY_PROVIDERS:
        stripped = _strip_matching_provider_prefix(name, provider)
        if stripped == name and name.startswith("openai/"):
            # openai-codex maps openai/gpt-5.4 -> gpt-5.4
            return name.split("/", 1)[1]
        return stripped

    # --- DeepSeek: map to one of two canonical names ---
    if provider == "deepseek":
        bare = _strip_matching_provider_prefix(name, provider)
        if "/" in bare:
            return bare
        return _normalize_for_deepseek(bare)

    # --- Direct providers: repair matching provider prefixes only ---
    if provider in _MATCHING_PREFIX_STRIP_PROVIDERS:
        result = _strip_matching_provider_prefix(name, provider)
        # Some providers require lowercase model IDs (e.g. Xiaomi's API
        # rejects "MiMo-V2.5-Pro" but accepts "mimo-v2.5-pro").
        if provider in _LOWERCASE_MODEL_PROVIDERS:
            result = result.lower()
        return result

    # --- Authoritative native providers: preserve user-facing slugs as-is ---
    if provider in _AUTHORITATIVE_NATIVE_PROVIDERS:
        return name

    # --- Custom & all others: pass through as-is ---
    return name


# ---------------------------------------------------------------------------
# Batch / convenience helpers
# ---------------------------------------------------------------------------

