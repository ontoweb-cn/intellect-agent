"""CLI handler for /blueprint command (HP-304)."""

from __future__ import annotations

import argparse


def register_cli(parent: argparse.ArgumentParser) -> None:
    parent.set_defaults(func=_cmd_list)
    sub = parent.add_subparsers(dest="blueprint_action")

    p_list = sub.add_parser("list", help="List available blueprints")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="Show blueprint details")
    p_show.add_argument("blueprint_id", help="Blueprint id")
    p_show.set_defaults(func=_cmd_show)

    p_search = sub.add_parser("search", help="Search blueprints by keyword")
    p_search.add_argument("keyword", help="Search keyword")
    p_search.set_defaults(func=_cmd_search)


def cmd_blueprint(args: argparse.Namespace) -> int:
    return _cmd_list(args)


def _cmd_list(args: argparse.Namespace) -> int:
    from cron.blueprint_catalog import load_catalog

    catalog = load_catalog()
    if not catalog:
        print("No blueprints available. Create one in ~/.intellect/blueprints/")
        return 0
    for bp in catalog:
        print(f"  {bp['id']:<30} {bp.get('category', 'general'):<15} {bp['name']}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    import json

    from tools.blueprints import blueprint_detail

    detail = blueprint_detail(getattr(args, "blueprint_id", ""))
    print(json.dumps(detail, indent=2))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from cron.blueprint_catalog import load_catalog

    keyword = getattr(args, "keyword", "").lower()
    for bp in load_catalog():
        if keyword in bp["id"].lower() or keyword in bp.get("description", "").lower():
            print(f"  {bp['id']:<30} {bp['name']}")
    return 0
