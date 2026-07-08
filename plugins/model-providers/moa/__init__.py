"""MoA (Mixture of Agents) virtual provider plugin.

Registers a ``ProviderProfile`` with ``api_mode="moa"`` so the agent
initialization path recognizes ``moa/<preset>`` model names and routes
them to the MoA orchestration loop instead of a real API endpoint.
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile

moa = ProviderProfile(
    name="moa",
    aliases=("mixture-of-agents",),
    api_mode="moa",
    display_name="MoA (Mixture of Agents)",
    description=(
        "Virtual provider that aggregates multiple reference models "
        "through a synthesizer for higher-quality responses. "
        "Use with model name 'moa/default' or 'moa/<preset>'."
    ),
    signup_url="",
    env_vars=(),  # no API key — sub-models handle their own credentials
    base_url="",  # no real endpoint
    fallback_models=("moa/default",),
    default_aux_model="moa/default",
)

register_provider(moa)
