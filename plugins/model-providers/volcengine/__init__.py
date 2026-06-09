"""Volcano Engine (火山引擎) provider profiles.

Three endpoints share the same base hostname (ark.cn-beijing.volces.com)
with different API path prefixes:

- volcengine            → /api/v3          (standard chat)
- volcengine-coding-plan → /api/coding/v3   (coding tier)
- volcengine-agent-plan  → /api/plan/v3     (agent tier)
"""

from providers import register_provider
from providers.base import ProviderProfile

volcengine = ProviderProfile(
    name="volcengine",
    aliases=("doubao", "volc", "bytedance"),
    env_vars=("VOLCENGINE_API_KEY",),
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    display_name="Volcano Engine",
    description="Volcano Engine (火山引擎) — Doubao models direct API",
    signup_url="https://console.volcengine.com/ark",
)

volcengine_coding = ProviderProfile(
    name="volcengine-coding-plan",
    aliases=("volc-coding", "doubao-coding", "volcengine-coding"),
    env_vars=("VOLCENGINE_CODING_PLAN_API_KEY",),
    base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
    display_name="Volcano Engine Coding Plan",
    description="Volcano Engine Coding Plan — dedicated coding tier",
    signup_url="https://console.volcengine.com/ark",
)

volcengine_agent = ProviderProfile(
    name="volcengine-agent-plan",
    aliases=("volc-agent", "doubao-agent", "volcengine-agent"),
    env_vars=("VOLCENGINE_AGENT_PLAN_API_KEY",),
    base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    display_name="Volcano Engine Agent Plan",
    description="Volcano Engine Agent Plan — dedicated agent tier",
    signup_url="https://console.volcengine.com/ark",
)

register_provider(volcengine)
register_provider(volcengine_coding)
register_provider(volcengine_agent)
