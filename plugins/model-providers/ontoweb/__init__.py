"""ONTOWEB Portal provider profile."""

from typing import Any

from agent.portal_tags import ontoweb_portal_tags
from providers import register_provider
from providers.base import ProviderProfile


class OntowebProfile(ProviderProfile):
    """ONTOWEB Portal — product tags, reasoning with ONTOWEB-specific omission."""

    def build_extra_body(
        self, *, session_id: str | None = None, **context
    ) -> dict[str, Any]:
        return {"tags": ontoweb_portal_tags()}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        supports_reasoning: bool = False,
        **context,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """ONTOWEB: passes full reasoning_config, but OMITS when disabled."""
        extra_body = {}
        if supports_reasoning:
            if reasoning_config is not None:
                rc = dict(reasoning_config)
                if rc.get("enabled") is False:
                    pass  # ONTOWEB omits reasoning when disabled
                else:
                    extra_body["reasoning"] = rc
            else:
                extra_body["reasoning"] = {"enabled": True, "effort": "medium"}
        return extra_body, {}


ontoweb = OntowebProfile(
    name="ontoweb",
    aliases=("ontoweb-portal", "ontoweb"),
    env_vars=("ONTOWEB_API_KEY",),
    display_name="ONTOWEB",
    description="ONTOWEB — Intellect model family",
    signup_url="https://ontoweb.cn/",
    fallback_models=(),
    base_url="https://inference.ontoweb.cn/v1",
    auth_type="oauth_device_code",
)

register_provider(ontoweb)
