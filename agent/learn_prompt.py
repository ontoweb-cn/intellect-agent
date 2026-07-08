"""Build prompts for /learn skill distillation (HP-204)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


LEARN_SYSTEM_PROMPT = """You distill a completed troubleshooting session into one Intellect SKILL.md.

Output ONLY valid SKILL.md content (YAML frontmatter + markdown body).

Frontmatter requirements:
- name: lowercase slug matching the skill directory name
- description: ONE sentence, <= 60 characters, ends with a period
- author: session contributor (use provided name or "User")
- metadata.intellect.category: pick a sensible category slug

Body section order:
# <Title> Skill
(short intro)
## When to Use
## Prerequisites
## How to Run
## Quick Reference
## Procedure
## Pitfalls
## Verification

Reference native Intellect tools in backticks (terminal, read_file, patch, etc.).
Do not invent tools. Keep under ~200 lines."""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _learn_redact_enabled() -> bool:
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
        learn_cfg = (cfg.get("auxiliary") or {}).get("learn") or {}
        return bool(learn_cfg.get("redact_secrets"))
    except Exception:
        return False


def build_learn_messages(
    *,
    skill_name: str,
    conversation_excerpt: List[Dict[str, Any]],
    author: str = "User",
) -> List[Dict[str, str]]:
    """Build auxiliary LLM messages for skill distillation."""
    slug = (skill_name or "learned-skill").strip().lower().replace(" ", "-")
    redact = _learn_redact_enabled()
    transcript_lines = []
    for msg in conversation_excerpt[-40:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)[:500]
        content = str(content)
        if redact:
            from agent.redact import redact_sensitive_text

            content = redact_sensitive_text(content, force=True)
        transcript_lines.append(f"{role}: {_truncate(content, 800)}")
    transcript = "\n\n".join(transcript_lines) or "(empty session)"

    user_prompt = (
        f"Skill slug: {slug}\n"
        f"Author line: {author}\n\n"
        f"Session transcript (recent messages):\n{transcript}\n\n"
        "Produce SKILL.md for this session's reusable procedure."
    )
    return [
        {"role": "system", "content": LEARN_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def extract_skill_name_from_args(args: str) -> Optional[str]:
    tokens = (args or "").strip().split()
    if not tokens:
        return None
    if tokens[0].lower() in {"save", "discard", "confirm"}:
        return None
    return tokens[0]
