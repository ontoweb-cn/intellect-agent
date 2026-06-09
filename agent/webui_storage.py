"""Storage manager resolution for intellect-webui (W1 bridge).

WebUI should import this module instead of opening state.db directly so
storage.backend and profile overrides stay aligned with the agent.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from agent.storage import StorageManager, get_storage_manager, reset_storage_managers


@contextmanager
def webui_profile_scope(profile: str | None) -> Iterator[None]:
    """Scope a WebUI request to *profile* without mutating ``os.environ``.

    Wrap each HTTP handler (or background task) so concurrent workers do not
    race on a process-global ``INTELLECT_HOME``.
    """
    if not profile:
        yield
        return
    from intellect_cli.profiles import resolve_profile_env
    from intellect_constants import reset_intellect_home_override, set_intellect_home_override

    token = set_intellect_home_override(resolve_profile_env(profile))
    try:
        yield
    finally:
        reset_intellect_home_override(token)


def load_config_for_webui(profile: str | None = None) -> dict:
    """Load merged config for WebUI.

    When *profile* is set, the caller must wrap the whole request in
    ``webui_profile_scope(profile)`` so storage paths stay isolated across
    threads.  Passing *profile* here only validates that the scope is active.
    """
    if profile:
        from intellect_cli.profiles import resolve_profile_env
        from intellect_constants import get_intellect_home_override

        expected = resolve_profile_env(profile)
        actual = get_intellect_home_override()
        if actual != expected:
            raise RuntimeError(
                "load_config_for_webui(profile=...) requires webui_profile_scope(profile) "
                "around the entire request"
            )
    from intellect_cli.config import load_config

    return load_config()


def get_webui_storage_manager(
    profile: str | None = None,
    *,
    config: dict | None = None,
) -> StorageManager:
    """Return initialized StorageManager for WebUI DB/cache/event access.

    Prefer ``with webui_profile_scope(profile):`` around the handler and pass
    ``config=load_config_for_webui()`` (or omit *profile* here).
    """
    cfg = config if config is not None else load_config_for_webui(profile)
    return get_storage_manager(cfg)


__all__ = [
    "get_webui_storage_manager",
    "load_config_for_webui",
    "reset_storage_managers",
    "webui_profile_scope",
]
