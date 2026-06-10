"""Intellect Kanban bridge for the WebUI.

This module exposes a full CRUD API under ``/api/kanban/*`` while keeping
Intellect Agent's ``intellect_cli.kanban_db`` as the only source of truth.

Supported operations:
- Task CRUD (create, read, patch, bulk update, archive)
- Multi-board management (list, create, archive, switch)
- Task dependency links (create, delete)
- SSE live event stream for real-time updates
- Comments and worker dispatch integration
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from urllib.parse import parse_qs, unquote

from api.helpers import bad, j

BOARD_COLUMNS = ["triage", "todo", "ready", "running", "blocked", "done"]
_TASK_PREFIX = "/api/kanban/tasks/"


def _repo(board=None):
    from agent.storage.kanban_repository import KanbanRepository

    return KanbanRepository(board=board)


def _kb():
    """Legacy import surface — prefer :func:`_repo`."""
    from intellect_cli import kanban_db as kb

    return kb


def _notify_kanban_board(board=None) -> None:
    """Wake local SSE loops and publish Redis invalidation (P4b / W4b)."""
    try:
        from api.kanban_events import publish_kanban_changed, subscribe_kanban_board

        slug = str(board if board is not None else "default")
        subscribe_kanban_board(slug)
        publish_kanban_changed(slug)
    except Exception:
        pass


def _kanban_write_result(result, *, board=None):
    _notify_kanban_board(board)
    return result


def _resolve_board(parsed):
    """Validate and normalise a ?board=<slug> query param.

    Returns the normalised slug, or ``None`` when the caller omitted the
    param. Raises ValueError on a malformed slug so the bridge surfaces a
    clean 400 instead of a 500 from deeper in the library.
    """
    raw = (parse_qs(parsed.query or "").get("board") or [None])[0]
    return _normalise_board_or_raise(raw)


def _resolve_board_from_body(body):
    """Same contract as :func:`_resolve_board` but reads ``board`` from a
    parsed JSON body (POST / PATCH / DELETE handlers receive a dict, not
    a parsed URL). Returns ``None`` when the body did not specify a board.
    """
    if not isinstance(body, dict):
        return None
    raw = body.get("board")
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None
    return _normalise_board_or_raise(raw)


def _normalise_board_or_raise(raw):
    """Shared normalisation + existence check for board slugs."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None
    repo = _repo()
    try:
        normed = repo.normalize_board_slug(raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid board slug: {raw!r}") from exc
    if not normed:
        return None
    default_slug = repo.default_board
    if normed != default_slug and not repo.board_exists(normed):
        raise LookupError(f"board {normed!r} does not exist")
    return normed


from contextlib import contextmanager


@contextmanager
def _conn(board=None):
    with _repo(board).connection() as conn:
        yield conn


def _obj_dict(value):
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(getattr(value, "__dict__", {}))


def _task_dict(task, repo=None):
    data = _obj_dict(task)
    if not data:
        return data
    try:
        age = (repo or _repo()).task_age(task)
    except Exception:
        age = None
    data["age_seconds"] = age
    data["age"] = age
    data.setdefault("progress", None)
    return data


def _bool_query(parsed, name: str, default: bool = False) -> bool:
    raw = (parse_qs(parsed.query or "").get(name) or [None])[0]
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _str_query(parsed, name: str):
    raw = (parse_qs(parsed.query or "").get(name) or [None])[0]
    return str(raw).strip() or None if raw is not None else None


def _int_query(parsed, name: str, default=None, *, minimum=None, maximum=None):
    raw = _str_query(parsed, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _board_payload(parsed):
    board = _resolve_board(parsed)
    tenant = _str_query(parsed, "tenant")
    assignee = _str_query(parsed, "assignee")
    include_archived = _bool_query(parsed, "include_archived", False)
    only_mine = _bool_query(parsed, "only_mine", False)
    since = _int_query(parsed, "since", None, minimum=0)
    profile = None
    if only_mine and not assignee:
        try:
            from api.profiles import get_active_profile_name

            profile = get_active_profile_name() or "default"
        except Exception:
            profile = "default"
        assignee = profile

    repo = _repo(board)
    with repo.connection() as conn:
        try:
            latest_event_id = repo.latest_event_id(conn)
        except Exception:
            latest_event_id = 0
        if since is not None and since >= latest_event_id:
            return {"changed": False, "latest_event_id": latest_event_id, "read_only": False}

        try:
            tasks = repo.list_tasks(
                conn,
                tenant=tenant,
                assignee=assignee,
                include_archived=include_archived,
            )
        except Exception:
            import traceback
            traceback.print_exc()
            tasks = []
        try:
            link_counts = repo.link_counts(conn, tasks)
        except Exception:
            link_counts = {}
        try:
            comment_counts = repo.comment_counts(conn)
        except Exception:
            comment_counts = {}

        def row(task):
            data = _task_dict(task)
            if isinstance(link_counts, dict):
                data["link_counts"] = link_counts.get(task.id, {"parents": 0, "children": 0})
            else:
                data["link_counts"] = {"parents": 0, "children": 0}
            if isinstance(comment_counts, dict):
                data["comment_count"] = comment_counts.get(task.id, 0)
            else:
                data["comment_count"] = 0
            return data

        columns = [
            {"name": name, "tasks": [row(task) for task in tasks if task.status == name]}
            for name in BOARD_COLUMNS
        ]
        if include_archived:
            columns.append({
                "name": "archived",
                "tasks": [row(task) for task in tasks if task.status == "archived"],
            })
        return {
            "columns": columns,
            "tenants": sorted({task.tenant for task in tasks if getattr(task, "tenant", None)}),
            "assignees": sorted({task.assignee for task in tasks if getattr(task, "assignee", None)}),
            "latest_event_id": latest_event_id,
            "changed": True,
            "read_only": False,
            "filters": {
                "tenant": tenant,
                "assignee": assignee,
                "include_archived": include_archived,
                "only_mine": only_mine,
                "profile": profile,
            },
        }



def _validate_status(status: str) -> str:
    value = str(status or "").strip().lower()
    allowed = set(BOARD_COLUMNS) | {"archived"}
    if value not in allowed:
        raise ValueError(f"invalid status: {value}")
    return value


def _set_status_direct(conn, task_id: str, new_status: str, repo=None) -> bool:
    """Delegate to :meth:`KanbanRepository.transition_status_direct`."""
    return (repo or _repo()).transition_status_direct(conn, task_id, new_status)


def _create_task_payload(body: dict, *, board=None):
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    try:
        priority = int(body.get("priority") or 0)
    except (TypeError, ValueError):
        raise ValueError("priority must be an integer")
    repo = _repo(board)
    requested_status = body.get("status")
    with repo.connection() as conn:
        task_id = repo.create_task(
            conn,
            title=title,
            body=body.get("body") or None,
            assignee=body.get("assignee") or None,
            created_by=body.get("created_by") or "webui",
            tenant=body.get("tenant") or None,
            priority=priority,
            parents=body.get("parents") or (),
            triage=bool(body.get("triage") or False),
            workspace_kind=body.get("workspace_kind") or "scratch",
            workspace_path=body.get("workspace_path") or None,
            idempotency_key=body.get("idempotency_key") or None,
            max_runtime_seconds=body.get("max_runtime_seconds") or None,
            skills=body.get("skills") or None,
        )
        if requested_status:
            _patch_task(conn, task_id, {"status": requested_status}, repo=repo)
        return _kanban_write_result(
            {"task": _task_dict(repo.get_task(conn, task_id), repo=repo), "read_only": False},
            board=board,
        )


def _patch_task(conn, task_id: str, body: dict, repo=None):
    repo = repo or _repo()
    task = repo.get_task(conn, task_id)
    if not task:
        raise LookupError("task not found")

    updates = {}
    if "title" in body:
        title = str(body.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        updates["title"] = title
    if "body" in body:
        updates["body"] = body.get("body") or None
    if "tenant" in body:
        updates["tenant"] = body.get("tenant") or None
    if "priority" in body:
        try:
            updates["priority"] = int(body.get("priority") or 0)
        except (TypeError, ValueError):
            raise ValueError("priority must be an integer")

    for field, value in updates.items():
        if hasattr(task, field):
            try:
                setattr(task, field, value)
            except Exception:
                pass
    if updates:
        assignments = ", ".join(f"{field} = ?" for field in updates)
        conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", [*updates.values(), task_id])
        if hasattr(_kb(), "_append_event"):
            _kb()._append_event(conn, task_id, "updated", {"fields": list(updates), "source": "webui"})

    if "assignee" in body:
        if not repo.assign_task(conn, task_id, body.get("assignee") or None):
            raise LookupError("task not found")

    if "status" not in body or body.get("status") in (None, ""):
        return
    status = _validate_status(body.get("status"))
    if status == "done":
        if not repo.complete_task(conn, task_id, result=body.get("result"), summary=body.get("summary")):
            raise LookupError("task not found")
    elif status == "blocked":
        if not repo.block_task(conn, task_id, reason=body.get("block_reason") or body.get("reason")):
            raise LookupError("task not found")
    elif status == "archived":
        if not repo.archive_task(conn, task_id):
            raise LookupError("task not found")
    elif status == "running":
        # The 'running' state is owned by the kanban dispatcher / claim
        # protocol — entering it via raw UPDATE bypasses claim_lock,
        # claim_expires, started_at, and worker_pid, which leaves the task
        # in a state the dispatcher treats as "phantom claimed" and may
        # reclaim or hide. Match the agent dashboard plugin's contract
        # (plugins/kanban/dashboard/plugin_api.py update_task) by rejecting
        # this transition with HTTP 400. Workers enter 'running' via
        # kb.claim_task(); UI users should use the dispatcher nudge.
        raise ValueError(
            "Cannot set status to 'running' directly; use the dispatcher/claim path"
        )
    elif status == "ready":
        # If the task is currently 'blocked', use the structured unblock
        # verb so the unblocked event fires. Otherwise it's a legitimate
        # drag-drop or click move (e.g. todo → ready, running → ready when
        # the user yanks a stuck worker back to the queue) and we use the
        # claim-aware direct status write.
        current = repo.get_task(conn, task_id)
        if not current:
            raise LookupError("task not found")
        if current.status == "blocked":
            if not repo.unblock_task(conn, task_id):
                raise LookupError("task not found")
        else:
            if not _set_status_direct(conn, task_id, "ready", repo=repo):
                raise LookupError("task not found")
    elif status in ("triage", "todo"):
        # Direct status write for drag-drop moves between non-running,
        # non-terminal columns. Uses the claim-aware helper that nulls out
        # claim_lock / claim_expires / worker_pid when leaving 'running'
        # and ends any active run with outcome='reclaimed'.
        if not _set_status_direct(conn, task_id, status, repo=repo):
            raise LookupError("task not found")
    else:
        # _validate_status guarantees we never reach here, but be defensive.
        raise ValueError(f"unknown status: {status}")


def _patch_task_payload(task_id: str, body: dict, *, board=None):
    task_id = str(task_id or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    repo = _repo(board)
    with repo.connection() as conn:
        _patch_task(conn, task_id, body, repo=repo)
        return _kanban_write_result(
            {"task": _task_dict(repo.get_task(conn, task_id), repo=repo), "read_only": False},
            board=board,
        )


def _comment_payload(task_id: str, body: dict, *, board=None):
    task_id = str(task_id or "").strip()
    comment_body = str(body.get("body") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    if not comment_body:
        raise ValueError("body is required")
    repo = _repo(board)
    with repo.connection() as conn:
        if not repo.get_task(conn, task_id):
            raise LookupError("task not found")
        comment_id = repo.add_comment(conn, task_id, body.get("author") or "webui", comment_body)
        return _kanban_write_result(
            {"ok": True, "comment_id": comment_id, "read_only": False},
            board=board,
        )


def _link_tasks_payload(body: dict, *, unlink: bool = False, board=None):
    parent_id = str(body.get("parent_id") or "").strip()
    child_id = str(body.get("child_id") or "").strip()
    if not parent_id or not child_id:
        raise ValueError("parent_id and child_id are required")
    repo = _repo(board)
    with repo.connection() as conn:
        if not repo.get_task(conn, parent_id):
            raise LookupError("parent task not found")
        if not repo.get_task(conn, child_id):
            raise LookupError("child task not found")
        if unlink:
            changed = repo.unlink_tasks(conn, parent_id, child_id)
            return _kanban_write_result(
                {
                    "ok": True,
                    "changed": bool(changed),
                    "parent_id": parent_id,
                    "child_id": child_id,
                    "read_only": False,
                },
                board=board,
            )
        repo.link_tasks(conn, parent_id, child_id)
        return _kanban_write_result(
            {"ok": True, "parent_id": parent_id, "child_id": child_id, "read_only": False},
            board=board,
        )

def _links_for(conn, task_id: str, repo=None) -> dict:
    repo = repo or _repo()
    return {
        "parents": repo.parent_ids(conn, task_id),
        "children": repo.child_ids(conn, task_id),
    }


def _task_detail_payload(task_id: str, *, board=None):
    repo = _repo(board)
    with repo.connection() as conn:
        task = repo.get_task(conn, task_id)
        if not task:
            return None
        return {
            "task": _task_dict(task, repo=repo),
            "comments": [_obj_dict(c) for c in repo.list_comments(conn, task_id)],
            "events": [_obj_dict(e) for e in repo.list_events(conn, task_id)],
            "links": _links_for(conn, task_id, repo=repo),
            "runs": [_obj_dict(r) for r in repo.list_runs(conn, task_id)],
            "read_only": False,
        }


def _events_payload(parsed):
    board = _resolve_board(parsed)
    since = _int_query(parsed, "since", 0, minimum=0)
    limit = _int_query(parsed, "limit", 200, minimum=1, maximum=200)
    repo = _repo(board)
    with repo.connection() as conn:
        cursor, events = repo.list_events_since(conn, since, limit=limit)
        latest = repo.latest_event_id(conn)
        if not events:
            cursor = latest if since >= latest else since
        return {"events": events, "cursor": cursor, "latest_event_id": cursor, "read_only": False}


def _config_payload(*, board=None):
    repo = _repo(board)
    try:
        with repo.connection() as conn:
            try:
                assignees = list(repo.known_assignees(conn))
            except Exception:
                assignees = []
    except Exception:
        assignees = []
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
    except Exception:
        cfg = {}
    k_cfg = ((cfg.get("dashboard") or {}).get("kanban") or {})
    return {
        "columns": BOARD_COLUMNS,
        "assignees": assignees,
        "default_tenant": k_cfg.get("default_tenant") or "",
        "lane_by_profile": bool(k_cfg.get("lane_by_profile", True)),
        "include_archived_by_default": bool(k_cfg.get("include_archived_by_default", False)),
        "render_markdown": bool(k_cfg.get("render_markdown", True)),
        "read_only": False,
    }


def _stats_payload(*, board=None):
    repo = _repo(board)
    try:
        with repo.connection() as conn:
            try:
                result = repo.board_stats(conn)
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
            try:
                rows = conn.execute(
                    "SELECT status, assignee, COUNT(*) AS n FROM tasks WHERE status != 'archived' GROUP BY status, assignee"
                ).fetchall()
                by_status = {}
                by_assignee = {}
                for row in rows:
                    # Handle both tuple rows and dict-like rows
                    if isinstance(row, dict) or hasattr(row, "keys"):
                        n = int(row["n"] or 0)
                        st = row["status"]
                        an = row["assignee"] or "unassigned"
                    else:
                        n = int(row[2] or 0)
                        st = row[0]
                        an = row[1] or "unassigned"
                    by_status[st] = by_status.get(st, 0) + n
                    by_assignee[an] = by_assignee.get(an, 0) + n
                return {"by_status": by_status, "by_assignee": by_assignee}
            except Exception:
                return {"by_status": {}, "by_assignee": {}}
    except Exception:
        return {"by_status": {}, "by_assignee": {}}


def _assignees_payload(*, board=None):
    repo = _repo(board)
    assignees = []
    try:
        with repo.connection() as conn:
            try:
                assignees = list(repo.known_assignees(conn))
            except Exception:
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL AND assignee != '' ORDER BY assignee"
                    ).fetchall()
                    # Handle both tuple rows and dict-like rows
                    assignees = [row["assignee"] if hasattr(row, "keys") or isinstance(row, dict) else row[0] for row in rows]
                except Exception:
                    assignees = []
    except Exception:
        import traceback
        traceback.print_exc()
        assignees = []
    return {"assignees": assignees}


def _task_log_payload(parsed, task_id: str):
    board = _resolve_board(parsed)
    repo = _repo(board)
    tail = _int_query(parsed, "tail", None, minimum=1, maximum=2_000_000)
    with repo.connection() as conn:
        if not repo.get_task(conn, task_id):
            return None
    content = repo.read_worker_log(task_id, tail_bytes=tail)
    log_path = repo.worker_log_path(task_id)
    try:
        size = log_path.stat().st_size if log_path and log_path.exists() else 0
    except OSError:
        size = 0
    return {
        "task_id": task_id,
        "path": str(log_path or ""),
        "exists": content is not None,
        "size_bytes": size,
        "content": content or "",
        "truncated": bool(tail and size > tail),
    }


def _bulk_tasks_payload(body: dict, *, board=None):
    ids = [str(i).strip() for i in (body.get("ids") or []) if str(i).strip()]
    if not ids:
        raise ValueError("ids is required")
    results = []
    repo = _repo(board)
    with repo.connection() as conn:
        for task_id in ids:
            entry = {"id": task_id, "ok": True}
            try:
                if not repo.get_task(conn, task_id):
                    entry.update(ok=False, error="not found")
                    results.append(entry)
                    continue
                if body.get("archive"):
                    if not repo.archive_task(conn, task_id):
                        entry.update(ok=False, error="archive refused")
                elif body.get("status") is not None:
                    _patch_task(conn, task_id, {"status": body.get("status")}, repo=repo)
                if body.get("assignee") is not None:
                    if not repo.assign_task(conn, task_id, body.get("assignee") or None):
                        entry.update(ok=False, error="assign refused")
                if body.get("priority") is not None:
                    try:
                        priority = int(body.get("priority"))
                    except (TypeError, ValueError):
                        entry.update(ok=False, error="priority must be an integer")
                    else:
                        conn.execute("UPDATE tasks SET priority = ? WHERE id = ?", (priority, task_id))
                        if hasattr(_kb(), "_append_event"):
                            _kb()._append_event(conn, task_id, "reprioritized", {"priority": priority, "source": "webui"})
            except Exception as exc:
                entry.update(ok=False, error=str(exc))
            results.append(entry)
    return _kanban_write_result({"results": results, "read_only": False}, board=board)


def _dispatch_payload(parsed):
    board = _resolve_board(parsed)
    repo = _repo(board)
    dry_run = _bool_query(parsed, "dry_run", False)
    max_spawn = _int_query(parsed, "max", 8, minimum=1, maximum=100)
    try:
        _kb().dispatch_once
    except AttributeError:
        raise ValueError("dispatcher is unavailable")
    try:
        with repo.connection() as conn:
            result = repo.dispatch_once(conn, dry_run=dry_run, max_spawn=max_spawn)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return _kanban_write_result({"ok": False, "error": str(exc), "read_only": False}, board=board)
    if isinstance(result, dict):
        return _kanban_write_result(result, board=board)
    try:
        return _kanban_write_result(asdict(result), board=board)
    except TypeError:
        return _kanban_write_result({"result": str(result)}, board=board)


def _task_action_payload(task_id: str, body: dict, action: str, *, board=None):
    repo = _repo(board)
    task_id = str(task_id or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    with repo.connection() as conn:
        if not repo.get_task(conn, task_id):
            raise LookupError("task not found")
        if action == "block":
            ok = repo.block_task(conn, task_id, reason=body.get("reason") or body.get("block_reason"))
        elif action == "unblock":
            ok = repo.unblock_task(conn, task_id)
        else:
            raise ValueError(f"invalid action: {action}")
        if not ok:
            raise RuntimeError(f"{action} refused")
        return _kanban_write_result(
            {"task": _task_dict(repo.get_task(conn, task_id), repo=repo), "read_only": False},
            board=board,
        )


# ---------------------------------------------------------------------------
# Multi-board management
# ---------------------------------------------------------------------------
# These endpoints operate on the on-disk board collection itself rather than
# on the tasks of a single board. They mirror the agent dashboard plugin's
# /boards surface (plugins/kanban/dashboard/plugin_api.py) so that the
# CLI / gateway / dashboard / WebUI all share the same active-board pointer.

def _board_meta_dict(meta):
    """Coerce the library's board metadata dict into a JSON-serialisable
    form. ``list_boards`` returns dicts with Path values for ``directory``;
    json.dumps would refuse those without help."""
    if not isinstance(meta, dict):
        return meta
    out = dict(meta)
    for key in ("directory", "db_path", "path"):
        if key in out and out[key] is not None:
            out[key] = str(out[key])
    return out


def _board_counts_for_slug(slug):
    try:
        repo = _repo(slug)
        if not repo.board_exists(slug):
            return {}
        try:
            with repo.connection() as conn:
                result = repo.status_counts(conn)
                return result if isinstance(result, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


def _list_boards_payload(parsed):
    """GET /api/kanban/boards — return all boards on disk + active slug.

    Each entry includes per-status counts and an ``is_current`` flag so the
    UI can render the switcher in a single round-trip.
    """
    repo = _repo()
    include_archived = _bool_query(parsed, "include_archived", False)
    try:
        boards = repo.list_boards(include_archived=include_archived)
    except Exception:
        import traceback
        traceback.print_exc()
        boards = []
    try:
        current = repo.get_current_board()
    except Exception:
        current = "default"
    try:
        visible_slugs = set()
        for meta in boards:
            md = _board_meta_dict(meta)
            if isinstance(md, dict):
                slug = md.get("slug")
                if slug:
                    visible_slugs.add(slug)
    except Exception:
        visible_slugs = set()
    try:
        default_slug = repo.default_board
    except Exception:
        default_slug = "default"
    if current not in visible_slugs:
        try:
            repo.clear_current_board()
        except Exception:
            pass
        current = default_slug
    out = []
    for raw_meta in boards:
        try:
            meta = _board_meta_dict(raw_meta)
            if not isinstance(meta, dict):
                continue
            slug = meta.get("slug")
            if slug is None:
                continue
            meta["is_current"] = (slug == current)
            meta["counts"] = _board_counts_for_slug(slug)
            if isinstance(meta["counts"], dict):
                meta["total"] = sum(meta["counts"].values()) if meta["counts"] else 0
            else:
                meta["total"] = 0
            out.append(meta)
        except Exception:
            import traceback
            traceback.print_exc()
            continue
    return {"boards": out, "current": current, "read_only": False}


def _create_board_payload(body):
    repo = _repo()
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    slug = str(body.get("slug") or "").strip()
    if not slug:
        raise ValueError("slug is required")
    try:
        meta = repo.create_board(
            slug,
            name=body.get("name") or None,
            description=body.get("description") or None,
            icon=body.get("icon") or None,
            color=body.get("color") or None,
        )
    except (ValueError, AttributeError) as exc:
        raise ValueError(str(exc)) from exc
    if body.get("switch"):
        try:
            repo.set_current_board(meta["slug"])
        except (ValueError, AttributeError) as exc:
            raise ValueError(str(exc)) from exc
    try:
        current = repo.get_current_board()
    except Exception:
        current = "default"
    return _kanban_write_result(
        {"board": _board_meta_dict(meta), "current": current, "read_only": False},
        board=slug,
    )


def _update_board_payload(slug, body):
    repo = _repo()
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    try:
        normed = repo.normalize_board_slug(slug)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid board slug: {slug!r}") from exc
    if not normed or not repo.board_exists(normed):
        raise LookupError(f"board {slug!r} does not exist")
    archived = body.get("archived")
    if isinstance(archived, str):
        archived = archived.strip().lower() in {"1", "true", "yes", "on"}
    meta = repo.write_board_metadata(
        normed,
        name=body.get("name"),
        description=body.get("description"),
        icon=body.get("icon"),
        color=body.get("color"),
        archived=archived if isinstance(archived, bool) else None,
    )
    return _kanban_write_result(
        {"board": _board_meta_dict(meta), "read_only": False},
        board=normed,
    )


def _delete_board_payload(slug, parsed):
    repo = _repo()
    hard_delete = _bool_query(parsed, "delete", False)
    try:
        normed = repo.normalize_board_slug(slug)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid board slug: {slug!r}") from exc
    if not normed or not repo.board_exists(normed):
        raise LookupError(f"board {slug!r} does not exist")
    default_slug = repo.default_board
    if normed == default_slug:
        raise ValueError("cannot remove the default board")
    res = repo.remove_board(normed, archive=not hard_delete)
    try:
        current = repo.get_current_board()
    except Exception:
        current = "default"
    return _kanban_write_result(
        {
            "result": _board_meta_dict(res) if isinstance(res, dict) else res,
            "current": current,
            "read_only": False,
        },
        board=normed,
    )


def _switch_board_payload(slug):
    repo = _repo()
    try:
        normed = repo.normalize_board_slug(slug)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid board slug: {slug!r}") from exc
    if not normed or not repo.board_exists(normed):
        raise LookupError(f"board {slug!r} does not exist")
    repo.set_current_board(normed)
    return _kanban_write_result({"current": normed, "read_only": False}, board=normed)


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------
# Server-Sent Events let the UI react to task transitions in real time
# without the 30s HTTP polling tax. The agent dashboard uses WebSockets
# for the same purpose; we use SSE because the WebUI's existing transport
# is a synchronous BaseHTTPServer and SSE is the right tool for
# unidirectional server-pushed event streams. The wire-level UX is
# identical from the client's perspective: events arrive within ~300ms
# of being committed to task_events.

# Polling interval matches the agent dashboard's _EVENT_POLL_SECONDS so
# write-to-receive latency is identical between the two surfaces.
_KANBAN_SSE_POLL_SECONDS = 0.3
# Heartbeat keeps proxies/CDNs from reaping the connection on idle boards.
# Identical to the approval/clarify SSE heartbeat.
_KANBAN_SSE_HEARTBEAT_SECONDS = 15.0
# Hard cap on a single SSE batch so a board with thousands of historical
# events doesn't ship them all in one frame. Same as the dashboard.
_KANBAN_SSE_BATCH_LIMIT = 200


def _kanban_sse_fetch_new(board, cursor):
    repo = _repo(board)
    if board is not None:
        default_slug = repo.default_board
        if board != default_slug and not repo.board_exists(board):
            return cursor, []
    try:
        with repo.connection() as conn:
            return repo.list_events_since(conn, int(cursor), limit=_KANBAN_SSE_BATCH_LIMIT)
    except Exception:
        return cursor, []


def _handle_events_sse_stream(handler, parsed):
    """GET /api/kanban/events/stream — long-lived SSE feed of task events.

    Query params:
      since=<int>   Resume from this event id. Defaults to 0 (full backlog
                    on first connect — the client should pass the latest
                    id it knows about so it does not re-receive historical
                    events.) Capped to the most recent _KANBAN_SSE_BATCH_LIMIT.
      board=<slug>  Pin the stream to a specific board. Switching boards
                    requires the client to close and re-open the stream.

    Header (set automatically by EventSource on reconnect):
      Last-Event-ID  Fallback resume cursor when ?since= is absent. The
                     server emits ``id: <event_id>`` on every events frame
                     so the browser can resume cleanly across drops without
                     re-receiving up to _KANBAN_SSE_BATCH_LIMIT events the
                     client already has.

    Mirrors the agent dashboard's WebSocket /events contract event-for-event
    so a client that handles one can handle the other with only the
    transport swapped.
    """
    try:
        board = _resolve_board(parsed)
    except (ValueError, LookupError) as exc:
        return bad(handler, str(exc), status=400 if isinstance(exc, ValueError) else 404)

    qs = parse_qs(parsed.query or "")
    # Resolution chain: ?since= query param → Last-Event-ID header → 0.
    # The Last-Event-ID header is what EventSource sends automatically on
    # reconnect; honouring it lets the browser resume cleanly without the
    # client needing to track the cursor in JS.
    since_raw = (qs.get("since") or [None])[0]
    if since_raw is None:
        try:
            since_raw = handler.headers.get("Last-Event-ID")
        except Exception:
            since_raw = None
    try:
        cursor = int(since_raw) if since_raw is not None else 0
    except (TypeError, ValueError):
        cursor = 0
    if cursor < 0:
        cursor = 0

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    handler.end_headers()

    # Send an initial frame so the client knows the connection is open
    # and learns the current cursor (in case the server already had a
    # backlog when the client first connected).
    try:
        handler.wfile.write(
            f"event: hello\ndata: {json.dumps({'cursor': cursor, 'board': board})}\n\n".encode("utf-8")
        )
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
        return True

    from api.kanban_events import subscribe_kanban_board, wake_event

    board_slug = str(board if board is not None else "default")
    subscribe_kanban_board(board_slug)
    wake = wake_event(board_slug)

    last_heartbeat = time.monotonic()
    try:
        while True:
            cursor, events = _kanban_sse_fetch_new(board, cursor)
            if events:
                # Emit `id: <last_event_id>` on every events frame so the
                # browser sets Last-Event-ID on auto-reconnect, letting us
                # resume from there without re-streaming the backlog.
                payload = json.dumps({"events": events, "cursor": cursor})
                frame = (
                    f"id: {cursor}\nevent: events\ndata: {payload}\n\n"
                ).encode("utf-8")
                try:
                    handler.wfile.write(frame)
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
                    return True
                last_heartbeat = time.monotonic()
            else:
                # Heartbeat keeps reverse proxies and the browser from
                # closing an idle stream. SSE comments (lines starting
                # with `:`) are ignored by EventSource.
                if (time.monotonic() - last_heartbeat) >= _KANBAN_SSE_HEARTBEAT_SECONDS:
                    try:
                        handler.wfile.write(b": keepalive\n\n")
                        handler.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
                        return True
                    last_heartbeat = time.monotonic()
            wake.wait(timeout=_KANBAN_SSE_POLL_SECONDS)
            if wake.is_set():
                wake.clear()
    except Exception:
        # Any other unexpected exception in the SSE loop should not bubble
        # up to the request handler (which would 500 a long-lived stream).
        return True


def handle_kanban_get(handler, parsed) -> bool | None:
    """Dispatch a Kanban GET. Three-valued return:

    - ``False`` — no Kanban path matched; caller should emit a 404
      (``_kanban_unknown_endpoint``) for genuinely stale-bundle requests.
    - ``None`` — a path matched and the inner handler already sent a
      response via ``bad(...)`` / ``j(...)`` (which both return ``None``).
      The caller MUST NOT emit another response.
    - ``True`` — a path matched and the inner handler succeeded.

    Treat any falsy-but-not-False return (``0``, ``''``, etc.) as a bug and
    audit the new return path; the caller uses ``is False`` identity check
    to distinguish unmatched paths from already-responded paths (#1843).
    """
    path = parsed.path
    try:
        # Multi-board management endpoints — these do NOT take a board arg
        # because they operate on the on-disk board collection itself, not
        # on a single board's tasks.
        if path == "/api/kanban/boards":
            return j(handler, _list_boards_payload(parsed)) or True
        if path == "/api/kanban/board":
            return j(handler, _board_payload(parsed)) or True
        if path == "/api/kanban/config":
            return j(handler, _config_payload(board=_resolve_board(parsed))) or True
        if path == "/api/kanban/stats":
            return j(handler, _stats_payload(board=_resolve_board(parsed))) or True
        if path == "/api/kanban/assignees":
            return j(handler, _assignees_payload(board=_resolve_board(parsed))) or True
        if path == "/api/kanban/events":
            return j(handler, _events_payload(parsed)) or True
        if path == "/api/kanban/events/stream":
            return _handle_events_sse_stream(handler, parsed)
        if path.startswith(_TASK_PREFIX) and path.endswith("/log"):
            task_id = unquote(path[len(_TASK_PREFIX):-len("/log")]).strip("/")
            if not task_id or "/" in task_id:
                return False
            payload = _task_log_payload(parsed, task_id)
            if payload is None:
                return bad(handler, "task not found", status=404)
            return j(handler, payload) or True
        if path.startswith(_TASK_PREFIX):
            task_id = unquote(path[len(_TASK_PREFIX):]).strip("/")
            if not task_id or "/" in task_id:
                return False
            payload = _task_detail_payload(task_id, board=_resolve_board(parsed))
            if payload is None:
                return bad(handler, "task not found", status=404)
            return j(handler, payload) or True
        return False
    except ImportError as exc:
        # intellect_cli not installed (webui-only deploy). Return a clean 503
        # "kanban unavailable" rather than a 500 so the frontend's existing
        # try/catch surfaces a useful toast.
        return bad(handler, f"[{path}] kanban unavailable: {exc}", status=503)
    except LookupError as exc:
        return bad(handler, f"[{path}] {exc}", status=404)
    except ValueError as exc:
        return bad(handler, f"[{path}] {exc}", status=400)
    except RuntimeError as exc:
        return bad(handler, f"[{path}] {exc}", status=409)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return bad(handler, f"[{path}] {exc}", status=500)


def handle_kanban_post(handler, parsed, body) -> bool | None:
    """Dispatch a Kanban POST. See ``handle_kanban_get`` for the
    three-valued ``True | None | False`` contract (#1843)."""
    path = parsed.path
    try:
        # Multi-board management endpoints — `_create_board_payload` and
        # `_switch_board_payload` operate on the on-disk board collection,
        # not on a single board's tasks.
        if path == "/api/kanban/boards":
            return j(handler, _create_board_payload(body)) or True
        # POST /api/kanban/boards/<slug>/switch — set active board
        _BOARDS_PREFIX = "/api/kanban/boards/"
        if path.startswith(_BOARDS_PREFIX) and path.endswith("/switch"):
            slug = unquote(path[len(_BOARDS_PREFIX):-len("/switch")]).strip("/")
            if not slug or "/" in slug:
                return False
            return j(handler, _switch_board_payload(slug)) or True
        # All board-scoped writes accept a ?board=<slug> query param OR a
        # `board` field in the JSON body. Query takes precedence.
        board_q = _resolve_board(parsed)
        board_b = _resolve_board_from_body(body)
        board = board_q if board_q is not None else board_b
        if path == "/api/kanban/dispatch":
            return j(handler, _dispatch_payload(parsed)) or True
        if path == "/api/kanban/tasks/bulk":
            return j(handler, _bulk_tasks_payload(body, board=board)) or True
        if path == "/api/kanban/tasks":
            return j(handler, _create_task_payload(body, board=board)) or True
        if path == "/api/kanban/links":
            return j(handler, _link_tasks_payload(body, board=board)) or True
        if path == "/api/kanban/links/delete":
            return j(handler, _link_tasks_payload(body, unlink=True, board=board)) or True
        if path.startswith(_TASK_PREFIX) and path.endswith("/comments"):
            task_id = path[len(_TASK_PREFIX):-len("/comments")].strip("/")
            return j(handler, _comment_payload(task_id, body, board=board)) or True
        for suffix, action in (("/block", "block"), ("/unblock", "unblock")):
            if path.startswith(_TASK_PREFIX) and path.endswith(suffix):
                task_id = path[len(_TASK_PREFIX):-len(suffix)].strip("/")
                return j(handler, _task_action_payload(task_id, body, action, board=board)) or True
        if path.startswith(_TASK_PREFIX) and path.endswith("/patch"):
            task_id = path[len(_TASK_PREFIX):-len("/patch")].strip("/")
            return j(handler, _patch_task_payload(task_id, body, board=board)) or True
    except ImportError as exc:
        return bad(handler, f"[POST {path}] kanban unavailable: {exc}", status=503)
    except LookupError as exc:
        return bad(handler, f"[POST {path}] {exc}", status=404)
    except ValueError as exc:
        return bad(handler, f"[POST {path}] {exc}", status=400)
    except RuntimeError as exc:
        return bad(handler, f"[POST {path}] {exc}", status=409)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return bad(handler, f"[POST {path}] {exc}", status=500)
    return False


def handle_kanban_patch(handler, parsed, body) -> bool | None:
    """Dispatch a Kanban PATCH. See ``handle_kanban_get`` for the
    three-valued ``True | None | False`` contract (#1843)."""
    path = parsed.path
    try:
        # /boards/<slug> routes operate on the on-disk board collection
        # itself — the slug travels in the URL path, not via ?board=. Match
        # them BEFORE resolving the board param so a stray ?board=ghost in
        # the query string doesn't 404 the legitimate `experiments` rename.
        # (Mirrors handle_kanban_post's structure — fixes asymmetry caught
        # by Opus advisor.)
        _BOARDS_PREFIX = "/api/kanban/boards/"
        if path.startswith(_BOARDS_PREFIX):
            slug = unquote(path[len(_BOARDS_PREFIX):]).strip("/")
            if not slug or "/" in slug:
                return False
            return j(handler, _update_board_payload(slug, body)) or True
        # Task-scoped writes accept ?board=<slug> (or body.board) to pin the
        # write to a specific board. Query takes precedence over body.
        board_q = _resolve_board(parsed)
        board_b = _resolve_board_from_body(body)
        board = board_q if board_q is not None else board_b
        if path.startswith(_TASK_PREFIX):
            task_id = unquote(path[len(_TASK_PREFIX):]).strip("/")
            if not task_id or "/" in task_id:
                return False
            return j(handler, _patch_task_payload(task_id, body, board=board)) or True
    except ImportError as exc:
        return bad(handler, f"[PATCH {path}] kanban unavailable: {exc}", status=503)
    except LookupError as exc:
        return bad(handler, f"[PATCH {path}] {exc}", status=404)
    except ValueError as exc:
        return bad(handler, f"[PATCH {path}] {exc}", status=400)
    except RuntimeError as exc:
        return bad(handler, f"[PATCH {path}] {exc}", status=409)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return bad(handler, f"[PATCH {path}] {exc}", status=500)
    return False


def handle_kanban_delete(handler, parsed, body) -> bool | None:
    """Dispatch a Kanban DELETE. See ``handle_kanban_get`` for the
    three-valued ``True | None | False`` contract (#1843)."""
    path = parsed.path
    try:
        # Same routing reorder as PATCH: /boards/<slug> path-routed first,
        # so a stray ?board=ghost can't 404 a legitimate board archive.
        _BOARDS_PREFIX = "/api/kanban/boards/"
        if path.startswith(_BOARDS_PREFIX):
            slug = unquote(path[len(_BOARDS_PREFIX):]).strip("/")
            if not slug or "/" in slug:
                return False
            return j(handler, _delete_board_payload(slug, parsed)) or True
        board_q = _resolve_board(parsed)
        board_b = _resolve_board_from_body(body)
        board = board_q if board_q is not None else board_b
        if path == "/api/kanban/links":
            return j(handler, _link_tasks_payload(body, unlink=True, board=board)) or True
    except ImportError as exc:
        return bad(handler, f"[DELETE {path}] kanban unavailable: {exc}", status=503)
    except LookupError as exc:
        return bad(handler, f"[DELETE {path}] {exc}", status=404)
    except ValueError as exc:
        return bad(handler, f"[DELETE {path}] {exc}", status=400)
    except RuntimeError as exc:
        return bad(handler, f"[DELETE {path}] {exc}", status=409)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return bad(handler, f"[DELETE {path}] {exc}", status=500)
    return False
