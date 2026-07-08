"""External secret source integrations.

A secret source is anything that can supply environment-variable-shaped
credentials at process startup, _after_ ~/.intellect/.env has loaded.  By
default sources are non-destructive: they only set values for env vars
that aren't already present, so .env and shell exports continue to win.

Currently shipped:

  - ``bitwarden`` — Bitwarden Secrets Manager (`bws` CLI).  See
    ``agent.secret_sources.bitwarden`` for the integration and
    ``intellect_cli.secrets_cli`` for the user-facing setup wizard.
  - ``onepassword`` — 1Password (`op` CLI v2+).  See
    ``agent.secret_sources.onepassword``.  Requires a signed-in
    ``op`` account.  Items with label "credential" are mapped to
    env vars by their title.
"""
