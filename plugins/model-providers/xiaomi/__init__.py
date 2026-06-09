"""Xiaomi MiMo provider profile."""

from providers import register_provider
from providers.base import ProviderProfile


class XiaomiProviderProfile(ProviderProfile):
    """Xiaomi MiMo provider — /v1/models returns 401 even with valid key."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Return None to skip the always-failing /v1/models probe.

        Xiaomi's /v1/models endpoint returns HTTP 401 even with a valid API
        key.  The static _PROVIDER_MODELS list and models.dev merge serve as
        the model catalog instead.
        """
        return None


xiaomi = XiaomiProviderProfile(
    name="xiaomi",
    aliases=("mimo", "xiaomi-mimo"),
    env_vars=("XIAOMI_API_KEY",),
    base_url="https://api.xiaomimimo.com/v1",
    supports_health_check=False,  # /v1/models returns 401 even with valid key
)

register_provider(xiaomi)
