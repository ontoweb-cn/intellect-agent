"""CLI handler for /moa command — Mixture of Agents preset management (HP-302f+g)."""

from __future__ import annotations

import argparse


def register_cli(parent: argparse.ArgumentParser) -> None:
    """Register /moa subcommands on the parent parser."""
    parent.set_defaults(func=cmd_moa)
    sub = parent.add_subparsers(dest="moa_action")

    p_list = sub.add_parser("list", help="List available MoA presets")
    p_list.set_defaults(func=cmd_moa)


def cmd_moa(args: argparse.Namespace) -> int:
    """Handle ``intellect moa`` — list presets with cost information."""
    try:
        from intellect_cli.moa_config import list_presets, preset_summary
    except ImportError:
        print("MoA config not available.")
        return 1

    presets = list_presets()
    if not presets:
        print("No MoA presets configured. Add presets in ~/.intellect/moa/presets.yaml")
        return 0

    print("MoA Presets (Mixture of Agents)")
    print("─" * 50)
    for name in presets:
        summary = preset_summary(name)
        if summary:
            n_refs = summary["reference_count"]
            print(f"  {name}")
            print(f"    References: {n_refs} models")
            print(f"    Aggregator: {summary['aggregator']}")
            print(f"    ⚠️  Cost: {n_refs + 1} LLM calls per request")
            print()
    print("Usage: /model moa/<preset>  — switch to a MoA preset as main model")
    return 0
