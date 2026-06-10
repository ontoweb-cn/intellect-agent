"""HTTP handlers for wiki contribution review (member → global)."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from api.members import (
    _load_config,
    _member_authorize,
    _store,
    resolve_member_id,
)


def _intellect_home() -> Path:
    from api.routes import _llm_wiki_active_intellect_home

    return _llm_wiki_active_intellect_home()


def _json(handler, payload, *, status=200):
    from api.routes import j

    return j(handler, payload, status=status)


def _bad(handler, message, *, status=400):
    return _json(handler, {"ok": False, "error": message}, status=status)


def _is_admin(store, actor: str | None) -> bool:
    if not actor:
        return False
    try:
        from agent.membership import Action

        return _member_authorize(store, actor, Action.ADMIN)
    except Exception:
        return False


def handle_wiki_contributions_list(handler, parsed) -> bool:
    from urllib.parse import parse_qs

    actor = resolve_member_id(handler, parsed)
    if not actor:
        return _bad(handler, "login required", status=401)
    qs = parse_qs(parsed.query or "")
    status = qs.get("status", [None])[0]
    store = _store()
    try:
        admin = _is_admin(store, actor)
        if admin:
            rows = store.list_wiki_contributions(status=status, limit=100)
        else:
            rows = store.list_wiki_contributions(
                submitter_id=actor, status=status, limit=100
            )
        pending = store.count_pending_wiki_contributions() if admin else 0
    finally:
        store.close()
    return _json(handler, {
        "ok": True,
        "contributions": rows,
        "pending_count": pending,
        "is_admin": admin,
    })


def handle_wiki_contributions_create(handler, parsed, body) -> bool:
    actor = resolve_member_id(handler, parsed)
    if not actor:
        return _bad(handler, "login required", status=401)
    if not body or not isinstance(body, dict):
        return _bad(handler, "JSON body required")
    page_paths = body.get("page_paths")
    if not isinstance(page_paths, list):
        return _bad(handler, "page_paths must be a list")
    store = _store()
    try:
        row, err = store.create_wiki_contribution(
            submitter_id=actor,
            intellect_home=_intellect_home(),
            config=_load_config(),
            page_paths=page_paths,
            title=str(body.get("title") or "").strip(),
            summary=str(body.get("summary") or "").strip(),
            note=str(body.get("note") or "").strip(),
        )
    finally:
        store.close()
    if err:
        return _bad(handler, err, status=403 if "personal wiki" in err else 400)
    return _json(handler, {"ok": True, "contribution": row}, status=201)


def handle_wiki_contribution_get(handler, parsed, contrib_id: str) -> bool:
    actor = resolve_member_id(handler, parsed)
    if not actor:
        return _bad(handler, "login required", status=401)
    store = _store()
    try:
        row = store.get_wiki_contribution(contrib_id)
        if not row:
            return _bad(handler, "not found", status=404)
        admin = _is_admin(store, actor)
        if row.get("submitter_id") != actor and not admin:
            return _bad(handler, "forbidden", status=403)
    finally:
        store.close()
    return _json(handler, {"ok": True, "contribution": row})


def handle_wiki_contribution_diff(handler, parsed, contrib_id: str) -> bool:
    actor = resolve_member_id(handler, parsed)
    if not actor:
        return _bad(handler, "login required", status=401)
    store = _store()
    try:
        if not _is_admin(store, actor):
            return _bad(handler, "admin required", status=403)
        row = store.get_wiki_contribution(contrib_id)
    finally:
        store.close()
    if not row:
        return _bad(handler, "not found", status=404)
    from intellect_cli.wiki_scaffold import contributions_staging_root, global_wiki_dir

    home = _intellect_home()
    staging = contributions_staging_root(home) / contrib_id / "snapshot"
    global_root = global_wiki_dir(home)
    diffs: dict[str, str] = {}
    for rel in row.get("page_paths") or []:
        src = staging / rel
        if not src.is_file():
            continue
        new_text = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        dest = global_root / rel
        old_text = (
            dest.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if dest.is_file()
            else []
        )
        diffs[rel] = "".join(
            difflib.unified_diff(old_text, new_text, fromfile=f"global/{rel}", tofile=f"staging/{rel}")
        )
    return _json(handler, {"ok": True, "diffs": diffs})


def handle_wiki_contribution_review(handler, parsed, contrib_id: str, body) -> bool:
    actor = resolve_member_id(handler, parsed)
    if not actor:
        return _bad(handler, "login required", status=401)
    store = _store()
    try:
        if not _is_admin(store, actor):
            return _bad(handler, "admin required", status=403)
        action = str((body or {}).get("action") or "").strip().lower()
        note = str((body or {}).get("note") or "").strip()
        merge_strategy = str((body or {}).get("merge_strategy") or "add_new").strip()
        row, err = store.review_wiki_contribution(
            contrib_id,
            reviewer_id=actor,
            action=action,
            note=note,
            merge_strategy=merge_strategy,
            intellect_home=_intellect_home(),
        )
    finally:
        store.close()
    if err:
        return _bad(handler, err, status=400)
    if action == "approve" and row:
        try:
            from api.routes import _queue_vault_build

            _queue_vault_build(handler, {"scope": "global", "scope_id": None})
        except Exception:
            pass
    return _json(handler, {"ok": True, "contribution": row})


def handle_wiki_contribution_withdraw(handler, parsed, contrib_id: str) -> bool:
    actor = resolve_member_id(handler, parsed)
    if not actor:
        return _bad(handler, "login required", status=401)
    store = _store()
    try:
        ok, err = store.withdraw_wiki_contribution(contrib_id, submitter_id=actor)
    finally:
        store.close()
    if err:
        status = 403 if err == "forbidden" else 404 if err == "not found" else 400
        return _bad(handler, err, status=status)
    return _json(handler, {"ok": ok})
