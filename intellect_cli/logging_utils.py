"""
Logging utilities: sensitive-data filter for the Python logging framework.

Apply :class:`SensitiveDataFilter` to the root logger (or any logger) to
automatically redact API keys, tokens, passwords, and authorization headers
from log output before handlers write them to disk or stdout.

Usage (once, during startup)::

    import logging
    from intellect_cli.logging_utils import install_sensitive_data_filter
    install_sensitive_data_filter()

The filter uses regex substitution and is O(len(msg)) per log call.
"""

from __future__ import annotations

import logging
import re

# Patterns that match sensitive credential-bearing strings appearing in
# log messages — typically inside repr() dumps, f-strings, or JSON.
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer / Basic auth headers
    (re.compile(r"(Authorization:\s*)(Bearer\s+[\w\-._~+/]+=*)", re.IGNORECASE),
     r"\1Bearer ***"),
    (re.compile(r"(Authorization:\s*)(Basic\s+[\w+/=]+)", re.IGNORECASE),
     r"\1Basic ***"),
    # API key query params and key=value pairs
    (re.compile(r"([&?](?:api_?key|token|access_token|secret|apikey|key|auth|password|passwd)=)([^&\s'\"]+)",
               re.IGNORECASE),
     r"\1***"),
    # Key-value pairs in dict repr / JSON / f-strings
    (re.compile(
        r"('(?:api_?key|token|access_token|secret|apikey|api_secret|password|passwd)':\s*)['\"][^'\"]+['\"]",
        re.IGNORECASE,
    ),
     r"\1'***'"),
    # Double-quoted variant
    (re.compile(
        r"(\"(?:api_?key|token|access_token|secret|apikey|api_secret|password|passwd)\":\s*)\"[^\"]+\"",
        re.IGNORECASE,
    ),
     r'\1"***"'),
    # os.environ dumps: individual key masking
    (re.compile(r"('[A-Za-z_]*?(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z_]*?':\s*)'[^']+'",
               re.IGNORECASE),
     r"\1'***'"),
    # API key patterns like sk-..., anthropic-key-..., etc.
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,})\b"), r"sk-***"),
    (re.compile(r"\b(anthropic-[A-Za-z0-9_\-]{20,})\b"), r"anthropic-***"),
    # GitHub tokens
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,})\b"), r"gh*_***"),
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"), r"github_pat_***"),
]

# Compiled once at module load for the fast path.
_CLEAN_PATTERNS: list[tuple[re.Pattern[str], str]] = _SENSITIVE_PATTERNS


def sanitize_for_logging(text: str) -> str:
    """Return *text* with known sensitive patterns replaced by ``***``."""
    for pattern, replacement in _CLEAN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts sensitive data from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact the formatted message
        if isinstance(record.msg, str):
            record.msg = sanitize_for_logging(record.msg)
        # Redact args if they are strings (before %-formatting)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_for_logging(a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: sanitize_for_logging(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, str):
                record.args = sanitize_for_logging(record.args)
        return True  # always allow the record through (sanitized)


def install_sensitive_data_filter() -> None:
    """Install the sensitive-data filter on the root logger.

    Safe to call multiple times — duplicate filters are not added.
    """
    root = logging.getLogger()
    # Avoid duplicate installation
    for f in root.filters:
        if isinstance(f, SensitiveDataFilter):
            return
    root.addFilter(SensitiveDataFilter())
