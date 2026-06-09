"""Single-user stub — replaced member login OAuth engine."""

from agent.oauth._stubs import (
    OAUTH_PROVIDER_PRESETS,
    is_oauth_enabled,
    get_oauth_config,
    get_provider_secret,
    provider_oauth_login_ready,
    is_git_host_provider,
    get_git_host_for_provider,
    list_enabled_providers,
)

# Additional stub functions


def build_authorization_url(*a, **kw):
    return ""


def exchange_code_for_tokens(*a, **kw):
    return {}


def extract_claims(*a, **kw):
    return {}


def generate_pkce_pair(*a, **kw):
    return ("", "")


def resolve_oauth_member(*a, **kw):
    return None


class OAuthMemberNotLinkedError(Exception):
    """Raised when an OAuth identity is not linked to any member."""
    pass


class OAuthIdentityConflictError(Exception):
    """Raised when an OAuth identity conflicts with an existing member."""
    pass


def resolve_trusted_header_member_from_headers(*a, **kw):
    return None
