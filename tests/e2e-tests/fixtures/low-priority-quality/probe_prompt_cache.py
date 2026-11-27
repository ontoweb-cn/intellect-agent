import sys
from pathlib import Path as _RepoPath
sys.path.insert(0, str(_RepoPath(__file__).resolve().parents[4]))
"""Diagnostic probe for public prompt-cache tool-message behavior."""
from agent.prompt_caching import apply_anthropic_cache_control


def main() -> None:
    msgs = [{"role": "system", "content": "sys"}, {"role": "tool", "content": "result"}]
    native = apply_anthropic_cache_control(msgs, native_anthropic=True)
    openrouter = apply_anthropic_cache_control(msgs, native_anthropic=False)
    print("native_tool_cache", native[1].get("cache_control"))
    print("openrouter_tool_cache", openrouter[1].get("cache_control"))
    assert native[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in openrouter[1]


if __name__ == "__main__":
    main()
