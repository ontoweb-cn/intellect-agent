"""Shell completion script generation for intellect CLI.

Walks the live argparse parser tree to generate accurate, always-up-to-date
completion scripts — no hardcoded subcommand lists, no extra dependencies.

Supports bash, zsh, and fish.
"""

from __future__ import annotations

import argparse
from typing import Any


def _walk(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Recursively extract subcommands and flags from a parser.

    Uses _SubParsersAction._choices_actions to get canonical names (no aliases)
    along with their help text.
    """
    flags: list[str] = []
    subcommands: dict[str, Any] = {}

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            # _choices_actions has one entry per canonical name; aliases are
            # omitted, which keeps completion lists clean.
            seen: set[str] = set()
            for pseudo in action._choices_actions:
                name = pseudo.dest
                if name in seen:
                    continue
                seen.add(name)
                subparser = action.choices.get(name)
                if subparser is None:
                    continue
                info = _walk(subparser)
                info["help"] = _clean(pseudo.help or "")
                subcommands[name] = info
        elif action.option_strings:
            flags.extend(o for o in action.option_strings if o.startswith("-"))

    return {"flags": flags, "subcommands": subcommands}


def _clean(text: str, maxlen: int = 60) -> str:
    """Strip shell-unsafe characters and truncate."""
    return text.replace("'", "").replace('"', "").replace("\\", "")[:maxlen]


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------

def generate_bash(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)
    top_cmds = " ".join(sorted(tree["subcommands"]))

    cases: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        info = tree["subcommands"][cmd]
        if cmd in ("agent", "profile") and info["subcommands"]:
            # Agent (canonical) / profile (legacy) subcommand: complete
            # actions, then agent-home names for actions that take a name.
            subcmds = " ".join(sorted(info["subcommands"]))
            profile_actions = "use delete show alias rename export"
            cases.append(
                f"        {cmd})\n"
                f"            case \"$prev\" in\n"
                f"                {cmd})\n"
                f"                    COMPREPLY=($(compgen -W \"{subcmds}\" -- \"$cur\"))\n"
                f"                    return\n"
                f"                    ;;\n"
                f"                {profile_actions.replace(' ', '|')})\n"
                f"                    COMPREPLY=($(compgen -W \"$(_intellect_profiles)\" -- \"$cur\"))\n"
                f"                    return\n"
                f"                    ;;\n"
                f"            esac\n"
                f"            ;;"
            )
        elif info["subcommands"]:
            subcmds = " ".join(sorted(info["subcommands"]))
            cases.append(
                f"        {cmd})\n"
                f"            COMPREPLY=($(compgen -W \"{subcmds}\" -- \"$cur\"))\n"
                f"            return\n"
                f"            ;;"
            )
        elif info["flags"]:
            flags = " ".join(info["flags"])
            cases.append(
                f"        {cmd})\n"
                f"            COMPREPLY=($(compgen -W \"{flags}\" -- \"$cur\"))\n"
                f"            return\n"
                f"            ;;"
            )

    cases_str = "\n".join(cases)

    return f"""# Intellect Agent bash completion
# Add to ~/.bashrc:
#   eval "$(intellect completion bash)"

_intellect_profiles() {{
    local names="default"
    local d
    for d in "$HOME/.intellect/agents" "$HOME/.intellect/profiles"; do
        if [ -d "$d" ]; then
            names="$names $(ls "$d" 2>/dev/null)"
        fi
    done
    echo "$names"
}}

_intellect_completion() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # Complete agent names after -a/--agent or legacy -p/--profile
    if [[ "$prev" == "-a" || "$prev" == "--agent" || "$prev" == "-p" || "$prev" == "--profile" ]]; then
        COMPREPLY=($(compgen -W "$(_intellect_profiles)" -- "$cur"))
        return
    fi

    if [[ $COMP_CWORD -ge 2 ]]; then
        case "${{COMP_WORDS[1]}}" in
{cases_str}
        esac
    fi

    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "{top_cmds}" -- "$cur"))
    fi
}}

complete -F _intellect_completion intellect
"""


# ---------------------------------------------------------------------------
# Zsh
# ---------------------------------------------------------------------------

def generate_zsh(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)

    top_cmds_lines: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        help_text = _clean(tree["subcommands"][cmd].get("help", ""))
        top_cmds_lines.append(f"                '{cmd}:{help_text}'")
    top_cmds_str = "\n".join(top_cmds_lines)

    sub_cases: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        info = tree["subcommands"][cmd]
        if not info["subcommands"]:
            continue
        if cmd in ("agent", "profile"):
            # Agent (canonical) / profile (legacy) subcommand completion.
            sub_lines: list[str] = []
            for sc in sorted(info["subcommands"]):
                sh = _clean(info["subcommands"][sc].get("help", ""))
                sub_lines.append(f"                        '{sc}:{sh}'")
            sub_str = "\n".join(sub_lines)
            label = "agent" if cmd == "agent" else "profile"
            sub_cases.append(
                f"                {cmd})\n"
                f"                    case ${{line[2]}} in\n"
                f"                        use|delete|show|alias|rename|export)\n"
                f"                            _intellect_profiles\n"
                f"                            ;;\n"
                f"                        *)\n"
                f"                            local -a {label}_cmds\n"
                f"                            {label}_cmds=(\n"
                f"{sub_str}\n"
                f"                            )\n"
                f"                            _describe '{label} command' {label}_cmds\n"
                f"                            ;;\n"
                f"                    esac\n"
                f"                    ;;"
            )
        else:
            sub_lines = []
            for sc in sorted(info["subcommands"]):
                sh = _clean(info["subcommands"][sc].get("help", ""))
                sub_lines.append(f"                    '{sc}:{sh}'")
            sub_str = "\n".join(sub_lines)
            safe = cmd.replace("-", "_")
            sub_cases.append(
                f"                {cmd})\n"
                f"                    local -a {safe}_cmds\n"
                f"                    {safe}_cmds=(\n"
                f"{sub_str}\n"
                f"                    )\n"
                f"                    _describe '{cmd} command' {safe}_cmds\n"
                f"                    ;;"
            )
    sub_cases_str = "\n".join(sub_cases)

    return f"""#compdef intellect
# Intellect Agent zsh completion
# Add to ~/.zshrc:
#   eval "$(intellect completion zsh)"

_intellect_profiles() {{
    local -a profiles
    profiles=(default)
    local d
    for d in "$HOME/.intellect/agents" "$HOME/.intellect/profiles"; do
        if [[ -d "$d" ]]; then
            profiles+=("${{(@f)$(ls $d 2>/dev/null)}}")
        fi
    done
    _describe 'agent' profiles
}}

_intellect() {{
    local context state line
    typeset -A opt_args

    _arguments -C \\
        '(-)'{{-h,--help}}'[Show help and exit]' \\
        '(-)'{{-V,--version}}'[Show version and exit]' \\
        '(-)'{{-a,--agent}}'[Agent name]:agent:_intellect_profiles' \\
        '(-)'{{-p,--profile}}'[Agent name (legacy)]:agent:_intellect_profiles' \\
        '1:command:->commands' \\
        '*::arg:->args'

    case $state in
        commands)
            local -a subcmds
            subcmds=(
{top_cmds_str}
            )
            _describe 'intellect command' subcmds
            ;;
        args)
            case ${{line[1]}} in
{sub_cases_str}
            esac
            ;;
    esac
}}

compdef _intellect intellect
"""


# ---------------------------------------------------------------------------
# Fish
# ---------------------------------------------------------------------------

def generate_fish(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)
    top_cmds = sorted(tree["subcommands"])
    top_cmds_str = " ".join(top_cmds)

    lines: list[str] = [
        "# Intellect Agent fish completion",
        "# Add to your config:",
        "#   intellect completion fish | source",
        "",
        "# Helper: list available agents (agents/ + legacy profiles/)",
        "function __intellect_profiles",
        "    echo default",
        "    for d in $HOME/.intellect/agents $HOME/.intellect/profiles",
        "        if test -d $d",
        "            ls $d 2>/dev/null",
        "        end",
        "    end",
        "end",
        "",
        "# Disable file completion by default",
        "complete -c intellect -f",
        "",
        "# Complete agent names after -a/--agent or legacy -p/--profile",
        "complete -c intellect -f -s a -l agent"
        " -d 'Agent name' -xa '(__intellect_profiles)'",
        "complete -c intellect -f -s p -l profile"
        " -d 'Agent name (legacy)' -xa '(__intellect_profiles)'",
        "",
        "# Top-level subcommands",
    ]

    for cmd in top_cmds:
        info = tree["subcommands"][cmd]
        help_text = _clean(info.get("help", ""))
        lines.append(
            f"complete -c intellect -f "
            f"-n 'not __fish_seen_subcommand_from {top_cmds_str}' "
            f"-a {cmd} -d '{help_text}'"
        )

    lines.append("")
    lines.append("# Subcommand completions")

    profile_name_actions = {"use", "delete", "show", "alias", "rename", "export"}

    for cmd in top_cmds:
        info = tree["subcommands"][cmd]
        if not info["subcommands"]:
            continue
        lines.append(f"# {cmd}")
        for sc in sorted(info["subcommands"]):
            sinfo = info["subcommands"][sc]
            sh = _clean(sinfo.get("help", ""))
            lines.append(
                f"complete -c intellect -f "
                f"-n '__fish_seen_subcommand_from {cmd}' "
                f"-a {sc} -d '{sh}'"
            )
        # Agent (canonical) / profile (legacy): complete names for name-taking actions
        if cmd in ("agent", "profile"):
            for action in sorted(profile_name_actions):
                lines.append(
                    f"complete -c intellect -f "
                    f"-n '__fish_seen_subcommand_from {action}; "
                    f"and __fish_seen_subcommand_from {cmd}' "
                    f"-a '(__intellect_profiles)' -d 'Agent name'"
                )

    lines.append("")
    return "\n".join(lines)
