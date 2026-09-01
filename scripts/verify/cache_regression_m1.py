#!/usr/bin/env python3
"""M1 gate-1 cache regression: old vs new sanitize_api_messages byte-diff.

Extracts the pre-A1 sanitize_api_messages from main (8511c09) into an
isolated namespace and byte-compares its output against the current
implementation across representative histories. Exit 1 on any divergence
— any byte difference means a prompt-cache invalidation regression.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIN_COMMIT = "8511c09"
OLD_PATH = "agent/agent_runtime_helpers.py"


def _fetch_old_source() -> str:
    out = subprocess.run(
        ["git", "show", f"{MAIN_COMMIT}:{OLD_PATH}"],
        capture_output=True, text=True, cwd=REPO, check=True,
    )
    return out.stdout


def _extract_function(src: str, name: str) -> str:
    lines = src.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        raise RuntimeError(f"{name} not found in old source")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("def ") or lines[j].startswith("class "):
            end = j
            break
    return "".join(lines[start:end])


def _real_valid_roles():
    """The stub MUST mirror the real run_agent role set — a divergent stub
    fabricates DIFFs that have nothing to do with the sanitize change."""
    try:
        import run_agent

        return set(run_agent.AIAgent._VALID_API_ROLES)
    except Exception:
        return {"system", "user", "assistant", "tool", "function", "developer"}


class _Static:
    _VALID_API_ROLES = _real_valid_roles()

    @staticmethod
    def _get_tool_call_id_static(tc):
        if isinstance(tc, dict):
            return tc.get("call_id", "") or tc.get("id", "") or ""
        return getattr(tc, "call_id", "") or getattr(tc, "id", "") or ""

    @staticmethod
    def _get_tool_call_name_static(tc):
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            return fn.get("name") or tc.get("name") or ""
        return ""

    logger = __import__("logging").getLogger("cache-regression")


class _RA:
    AIAgent = _Static
    logger = _Static.logger


def _load_old_impl():
    import logging
    import re
    from typing import Any, Dict, List, Optional

    ns = {
        "_ra": lambda: _RA,
        "logging": logging, "re": re,
        "List": list, "Dict": dict, "Any": Any, "Optional": Optional,
    }
    src = _fetch_old_source()
    fn_src = _extract_function(src, "sanitize_api_messages")
    exec(compile(fn_src, "old_sanitize", "exec"), ns)
    return ns["sanitize_api_messages"]


def _histories():
    exact_pair = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "run tests"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "terminal", "arguments": "{\"cmd\":\"pytest\"}"}},
            {"id": "call_2", "type": "function",
             "function": {"name": "read_file", "arguments": "{\"path\":\"/a.py\"}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "3 passed"},
        {"role": "tool", "tool_call_id": "call_2", "content": "def x(): pass"},
        {"role": "assistant", "content": "All green."},
    ]
    orphan = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "done"},
        {"role": "tool", "tool_call_id": "call_ghost", "content": "ghost"},
    ]
    missing = [
        {"role": "user", "content": "run"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_9", "type": "function",
             "function": {"name": "terminal", "arguments": "{}"}},
        ]},
    ]
    invalid_role = [
        {"role": "developer", "content": "legacy role"},
        {"role": "user", "content": "hello"},
    ]
    plain = [{"role": "user", "content": "no tools at all"}]
    mixed = [
        {"role": "user", "content": "multi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_a", "type": "function",
             "function": {"name": "web_search", "arguments": "{\"q\":\"x\"}"}},
            {"id": "call_b", "type": "function",
             "function": {"name": "terminal", "arguments": "{\"cmd\":\"ls\"}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_a", "content": "results"},
        {"role": "tool", "tool_call_id": "call_ghost", "content": "orphan"},
        {"role": "assistant", "content": "partial done, moving on"},
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_c", "type": "function",
             "function": {"name": "terminal", "arguments": "{\"cmd\":\"ls -la\"}"}},
        ]},
    ]
    return {
        "exact_pair": exact_pair,
        "orphan": orphan,
        "missing": missing,
        "invalid_role": invalid_role,
        "plain": plain,
        "mixed": mixed,
    }


def main() -> int:
    old_impl = _load_old_impl()
    from agent.agent_runtime_helpers import sanitize_api_messages as new_impl

    failures = 0
    for name, history in _histories().items():
        old_out = json.dumps(old_impl(json.loads(json.dumps(history))),
                             ensure_ascii=False, sort_keys=True)
        new_out = json.dumps(new_impl(json.loads(json.dumps(history))),
                             ensure_ascii=False, sort_keys=True)
        status = "OK " if old_out == new_out else "DIFF"
        if old_out != new_out:
            failures += 1
            Path(REPO / "scripts" / "verify" / f"_diff_{name}.old.json").write_text(old_out)
            Path(REPO / "scripts" / "verify" / f"_diff_{name}.new.json").write_text(new_out)
        print(f"{status} {name}")

    if failures:
        print(f"\nCACHE REGRESSION: {failures} history(ies) diverge (diffs saved)")
        return 1
    print("\nCache regression clean: old and new outputs byte-identical "
          "across all histories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
