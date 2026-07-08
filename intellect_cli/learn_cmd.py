"""Shared /learn slash command logic (HP-204)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from agent.learn_prompt import build_learn_messages, extract_skill_name_from_args


def _parse_frontmatter_name(content: str) -> Optional[str]:
    m = re.search(r"^name:\s*(\S+)\s*$", content, re.MULTILINE)
    return m.group(1).strip() if m else None


LEARN_PRIVACY_NOTICE = (
    "Note: /learn sends recent session messages to your auxiliary.learn "
    "provider. Review the draft before `/learn save`.\n\n"
)


def run_learn_generate(
    *,
    args: str,
    messages: List[Dict[str, Any]],
    author: str = "User",
) -> Tuple[str, Optional[str]]:
    """Generate SKILL.md draft via auxiliary LLM. Returns (status_text, draft_or_none)."""
    from agent.auxiliary_client import call_llm

    skill_name = extract_skill_name_from_args(args) or "learned-skill"
    llm_messages = build_learn_messages(
        skill_name=skill_name,
        conversation_excerpt=messages,
        author=author,
    )
    try:
        draft = call_llm(task="learn", messages=llm_messages)
    except Exception as exc:
        return (f"Learn failed: {exc}", None)
    if not draft or not str(draft).strip():
        return ("Learn produced empty output.", None)
    draft = str(draft).strip()
    if not draft.startswith("---"):
        return ("Learn output missing YAML frontmatter.", None)
    return (
        LEARN_PRIVACY_NOTICE
        + f"Draft generated for skill `{_parse_frontmatter_name(draft) or skill_name}`.\n"
        "Reply `/learn save` to write it, or `/learn discard` to cancel.\n\n"
        + draft,
        draft,
    )


def run_learn_save(
    draft: str,
    *,
    skill_name_override: Optional[str] = None,
) -> str:
    """Persist draft via skill_manage with learn_command provenance."""
    from tools.skill_manager_tool import skill_manage
    from tools.skill_usage import mark_agent_created
    from tools.skill_provenance import (
        LEARN_COMMAND,
        reset_current_write_origin,
        set_current_write_origin,
    )

    name = skill_name_override or _parse_frontmatter_name(draft) or "learned-skill"
    token = set_current_write_origin(LEARN_COMMAND)
    try:
        result_json = skill_manage(
            action="create",
            name=name,
            content=draft,
        )
    finally:
        reset_current_write_origin(token)

    import json

    data = json.loads(result_json)
    if not data.get("success"):
        return data.get("error") or data.get("message") or "Save failed."
    mark_agent_created(name)
    return f"Skill saved: {name}"
