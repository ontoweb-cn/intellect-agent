"""
Vault Builder Plugin — auto-build Quartz static sites from LLM Wiki.

When ``vault.build_trigger`` is ``"auto"`` (the default), this plugin
hooks into ``on_session_end`` to detect WIKI file modifications and
trigger a background Quartz build.  Scheduled builds are handled by the
gateway cron ticker via ``intellect_cli.vault_build``.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _resolve_wiki_path(runtime_ctx=None, config=None) -> Path | None:
    env_path = os.getenv("WIKI_PATH")
    if env_path:
        return Path(os.path.expandvars(env_path)).expanduser()
    # Pass the real config through, otherwise resolve_wiki_target() cannot see
    # skills.config.wiki.path and silently falls back to the ~/wiki scaffold —
    # which would rebuild the vault from an empty wiki.
    cfg = config
    if cfg is None:
        try:
            from intellect_cli.config import load_config

            cfg = load_config()
        except Exception:
            cfg = None
    try:
        from agent.runtime_context import _resolve_wiki_path as _rw
        wiki = _rw(runtime_ctx, cfg)
        if wiki:
            return Path(wiki)
    except Exception:
        pass
    return Path(os.path.expanduser("~/wiki"))


class VaultBuilderPlugin:
    """Auto-builds Quartz vaults when the LLM Wiki changes."""

    def __init__(self):
        self._build_lock = threading.Lock()

    def on_session_end(
        self, messages: List[Dict[str, Any]] | None = None, **kwargs: Any
    ) -> None:
        # The core loop dispatches this hook as cb(**kwargs) with
        # session_id/completed/interrupted/model/platform — accept both shapes so
        # the callback is never rejected for a signature mismatch.
        from intellect_cli.vault_build import load_vault_config, maybe_build_single_wiki

        vcfg = load_vault_config()
        if vcfg.get("build_trigger") != "auto":
            return

        wiki_path = _resolve_wiki_path()
        if not wiki_path or not wiki_path.exists():
            logger.debug("vault-builder: wiki path not found, skipping")
            return

        if not self._build_lock.acquire(blocking=False):
            logger.debug("vault-builder: build already in progress, skipping")
            return

        def _run():
            try:
                result = maybe_build_single_wiki(wiki_path, vcfg=vcfg, trigger="auto")
                if result and result.ok:
                    logger.info("vault-builder: build succeeded for %s", wiki_path)
                elif result and not result.ok:
                    logger.error("vault-builder: build failed: %s", result.error)
            finally:
                self._build_lock.release()

        threading.Thread(target=_run, daemon=True).start()


_PLUGIN = VaultBuilderPlugin()


def register(ctx) -> None:
    # The host API is ctx.register_hook(name, callback) — there is no
    # ctx.register_plugin(), which is why this plugin previously failed to load
    # with "'PluginContext' object has no attribute 'register_plugin'".
    # A single module-level instance keeps one shared build lock per process.
    ctx.register_hook("on_session_end", _PLUGIN.on_session_end)
