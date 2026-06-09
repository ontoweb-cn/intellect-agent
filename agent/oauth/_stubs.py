"""Minimal OAuth stubs for single-user mode (replaces deleted agent.members_oauth).

In single-user mode, OAuth is used for model-provider credentials (Anthropic,
GitHub Copilot, etc.) — not for member login. These stubs provide the
configuration-reading functions the agent/oauth package still needs.
"""

from __future__ import annotations

# ── Provider presets (minimal; real catalog is in agent/oauth/catalog/) ──

OAUTH_PROVIDER_PRESETS: dict[str, dict] = {}


# ── Config helpers ────────────────────────────────────────────────────────

def is_oauth_enabled(config: dict | None = None) -> bool:
    """OAuth is enabled by default for model-provider credential flows."""
    if not isinstance(config, dict):
        return True
    members = config.get("members")
    if isinstance(members, dict):
        oauth = members.get("oauth")
        if isinstance(oauth, dict):
            return bool(oauth.get("enabled", True))
    return True


def get_oauth_config(config: dict | None = None) -> dict:
    """Read OAuth settings from config (kept for backward compat)."""
    if not isinstance(config, dict):
        return {}
    members = config.get("members")
    if isinstance(members, dict):
        return dict(members.get("oauth") or {})
    return {}


def get_provider_secret(provider_cfg: dict) -> str:
    """Read client_secret from provider config dict."""
    return str(provider_cfg.get("client_secret", ""))


def provider_oauth_login_ready(provider_cfg: dict) -> bool:
    """Check if provider has the credentials needed for OAuth flow."""
    if not isinstance(provider_cfg, dict):
        return False
    return bool(
        provider_cfg.get("client_id") and provider_cfg.get("client_secret")
    )


def is_git_host_provider(provider_id: str) -> bool:
    """Check if provider is a known git host."""
    return provider_id in {"github", "gitlab", "gitee", "gitea", "azure-devops"}


def get_git_host_for_provider(provider_id: str) -> str | None:
    """Return git host FQDN for a provider id."""
    mapping = {
        "github": "github.com",
        "gitlab": "gitlab.com",
        "gitee": "gitee.com",
        "azure-devops": "dev.azure.com",
    }
    return mapping.get(provider_id)


def list_enabled_providers(config: dict | None = None) -> list[str]:
    """List enabled OAuth provider IDs (used by doctor)."""
    if not isinstance(config, dict):
        return []
    members = config.get("members")
    if isinstance(members, dict):
        oauth = members.get("oauth")
        if isinstance(oauth, dict):
            return [
                p.get("id", "")
                for p in oauth.get("providers", [])
                if isinstance(p, dict) and p.get("id")
            ]
    return []


def resolve_trusted_header_member_from_headers(
    headers: dict,
    config: dict | None = None,
    db: object = None,
) -> str | None:
    """Trusted header SSO — not supported in single-user mode."""
    return None
