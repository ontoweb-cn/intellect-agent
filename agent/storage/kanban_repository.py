"""Repository over ``intellect_cli.kanban_db`` for WebUI and services (W3)."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional


def _kb():
    from intellect_cli import kanban_db as kb

    return kb


class KanbanRepository:
    """Board-scoped kanban access; single entry point for WebUI bridge."""

    def __init__(self, board: Optional[str] = None) -> None:
        self.board = board

    def _with_board(self, kwargs: dict) -> dict:
        if kwargs.get("board") is None and self.board is not None:
            out = dict(kwargs)
            out["board"] = self.board
            return out
        return kwargs

    @property
    def default_board(self) -> str:
        return _kb().DEFAULT_BOARD

    def init(self) -> None:
        _kb().init_db(board=self.board)

    def connect(self):
        return _kb().connect(board=self.board)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        self.init()
        with _kb().connect_closing(board=self.board) as conn:
            yield conn

    @contextmanager
    def write_txn(self, conn) -> Iterator[None]:
        with _kb().write_txn(conn):
            yield

    # --- board filesystem (no conn) ---

    def normalize_board_slug(self, raw: str | None) -> str | None:
        return _kb()._normalize_board_slug(raw)

    def board_exists(self, slug: str | None = None) -> bool:
        return _kb().board_exists(slug or self.board or _kb().DEFAULT_BOARD)

    def list_boards(self, *, include_archived: bool = True) -> list:
        return _kb().list_boards(include_archived=include_archived)

    def get_current_board(self) -> str:
        return _kb().get_current_board()

    def set_current_board(self, slug: str) -> None:
        _kb().set_current_board(slug)

    def clear_current_board(self) -> None:
        _kb().clear_current_board()

    def create_board(self, slug: str, **kwargs) -> dict:
        return _kb().create_board(slug, **kwargs)

    def write_board_metadata(self, slug: str, **kwargs) -> dict:
        return _kb().write_board_metadata(slug, **kwargs)

    def remove_board(self, slug: str, *, archive: bool = True):
        return _kb().remove_board(slug, archive=archive)

    # --- tasks ---

    def list_tasks(self, conn, **kwargs) -> list:
        return _kb().list_tasks(conn, **self._with_board(kwargs))

    def get_task(self, conn, task_id: str):
        return _kb().get_task(conn, task_id)

    def create_task(self, conn, **kwargs) -> str:
        return _kb().create_task(conn, **self._with_board(kwargs))

    def claim_task(self, conn, task_id: str, **kwargs):
        return _kb().claim_task(conn, task_id, **kwargs)

    def assign_task(self, conn, task_id: str, assignee: str | None) -> bool:
        return _kb().assign_task(conn, task_id, assignee)

    def complete_task(self, conn, task_id: str, **kwargs) -> bool:
        return _kb().complete_task(conn, task_id, **kwargs)

    def block_task(self, conn, task_id: str, **kwargs) -> bool:
        return _kb().block_task(conn, task_id, **kwargs)

    def unblock_task(self, conn, task_id: str) -> bool:
        return _kb().unblock_task(conn, task_id)

    def archive_task(self, conn, task_id: str) -> bool:
        return _kb().archive_task(conn, task_id)

    def task_age(self, task) -> Any:
        return _kb().task_age(task)

    def link_tasks(self, conn, parent_id: str, child_id: str) -> None:
        _kb().link_tasks(conn, parent_id, child_id)

    def unlink_tasks(self, conn, parent_id: str, child_id: str) -> bool:
        return _kb().unlink_tasks(conn, parent_id, child_id)

    def parent_ids(self, conn, task_id: str) -> list[str]:
        return _kb().parent_ids(conn, task_id)

    def child_ids(self, conn, task_id: str) -> list[str]:
        return _kb().child_ids(conn, task_id)

    def add_comment(self, conn, task_id: str, author: str, body: str) -> int:
        return _kb().add_comment(conn, task_id, author, body)

    def list_comments(self, conn, task_id: str) -> list:
        return _kb().list_comments(conn, task_id)

    def list_events(self, conn, task_id: str) -> list:
        return _kb().list_events(conn, task_id)

    def list_runs(self, conn, task_id: str) -> list:
        return _kb().list_runs(conn, task_id)

    def known_assignees(self, conn) -> list:
        return _kb().known_assignees(conn)

    def board_stats(self, conn) -> dict:
        return _kb().board_stats(conn)

    def recompute_ready(self, conn) -> int:
        return _kb().recompute_ready(conn)

    def dispatch_once(self, conn, **kwargs) -> dict:
        return _kb().dispatch_once(conn, **kwargs)

    def read_worker_log(self, task_id: str, **kwargs) -> str | None:
        return _kb().read_worker_log(task_id, **kwargs)

    def worker_log_path(self, task_id: str):
        return _kb().worker_log_path(task_id)

    # --- aggregations (bridge SQL lifted here) ---

    def latest_event_id(self, conn) -> int:
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest FROM task_events"
            ).fetchone()
            return int(row["latest"] or 0)
        except Exception:
            return 0

    def link_counts(self, conn, tasks) -> dict[str, dict[str, int]]:
        counts = {task.id: {"parents": 0, "children": 0} for task in tasks}
        try:
            rows = conn.execute("SELECT parent_id, child_id FROM task_links").fetchall()
        except Exception:
            return counts
        for row in rows:
            counts.setdefault(row["parent_id"], {"parents": 0, "children": 0})["children"] += 1
            counts.setdefault(row["child_id"], {"parents": 0, "children": 0})["parents"] += 1
        return counts

    def comment_counts(self, conn) -> dict[str, int]:
        try:
            rows = conn.execute(
                "SELECT task_id, COUNT(*) AS n FROM task_comments GROUP BY task_id"
            ).fetchall()
        except Exception:
            return {}
        return {row["task_id"]: int(row["n"] or 0) for row in rows}

    def status_counts(self, conn, *, exclude_archived: bool = True) -> dict[str, int]:
        try:
            sql = "SELECT status, COUNT(*) AS n FROM tasks"
            if exclude_archived:
                sql += " WHERE status != 'archived'"
            sql += " GROUP BY status"
            rows = conn.execute(sql).fetchall()
            return {row["status"]: int(row["n"] or 0) for row in rows}
        except Exception:
            return {}

    def list_events_since(self, conn, since: int, limit: int = 200) -> tuple[int, list[dict]]:
        try:
            rows = conn.execute(
                "SELECT id, task_id, run_id, kind, payload, created_at "
                "FROM task_events WHERE id > ? ORDER BY id ASC LIMIT ?",
                (int(since), int(limit)),
            ).fetchall()
        except Exception:
            return since, []
        out = []
        new_cursor = since
        for r in rows:
            payload = None
            try:
                raw = r["payload"]
                if raw:
                    payload = json.loads(raw)
            except Exception:
                payload = None
            out.append({
                "id": int(r["id"]),
                "task_id": r["task_id"],
                "run_id": r["run_id"],
                "kind": r["kind"],
                "payload": payload,
                "created_at": int(r["created_at"]) if r["created_at"] is not None else None,
            })
            new_cursor = int(r["id"])
        return new_cursor, out

    def transition_status_direct(self, conn, task_id: str, new_status: str) -> bool:
        """Direct status write for drag-drop moves (WebUI / dashboard parity)."""
        kb = _kb()
        with kb.write_txn(conn):
            prev = conn.execute(
                "SELECT status, current_run_id FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if prev is None:
                return False
            was_running = prev["status"] == "running"
            cur = conn.execute(
                "UPDATE tasks SET status = ?, "
                "  claim_lock = CASE WHEN ? = 'running' THEN claim_lock ELSE NULL END, "
                "  claim_expires = CASE WHEN ? = 'running' THEN claim_expires ELSE NULL END, "
                "  worker_pid = CASE WHEN ? = 'running' THEN worker_pid ELSE NULL END "
                "WHERE id = ?",
                (new_status, new_status, new_status, new_status, task_id),
            )
            if cur.rowcount != 1:
                return False
            run_id = None
            if was_running and new_status != "running" and prev["current_run_id"]:
                try:
                    run_id = kb._end_run(
                        conn,
                        task_id,
                        outcome="reclaimed",
                        status="reclaimed",
                        summary=f"status changed to {new_status} (webui/direct)",
                    )
                except Exception:
                    run_id = None
            conn.execute(
                "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
                "VALUES (?, ?, 'status', ?, ?)",
                (
                    task_id,
                    run_id,
                    json.dumps({"status": new_status, "source": "webui"}),
                    int(time.time()),
                ),
            )
        if new_status in ("done", "ready"):
            try:
                kb.recompute_ready(conn)
            except Exception:
                pass
        return True
