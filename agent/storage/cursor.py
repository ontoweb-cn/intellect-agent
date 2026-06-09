"""Unified row/cursor helpers for storage backends."""

from __future__ import annotations

from typing import Any, Iterator

Row = dict[str, Any]


def row_to_dict(row: Any) -> Row | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return None


class CursorProxy:
    """Normalize sqlite3 / DB-API cursors to dict rows."""

    def __init__(self, cursor: Any | None = None) -> None:
        self._cursor = cursor

    def execute(self, sql: str, params: tuple = ()) -> CursorProxy:
        if self._cursor is None:
            raise RuntimeError("CursorProxy has no underlying cursor")
        self._cursor.execute(sql, params)
        return self

    def fetchone(self) -> Row | None:
        if self._cursor is None:
            return None
        return row_to_dict(self._cursor.fetchone())

    def fetchall(self) -> list[Row]:
        if self._cursor is None:
            return []
        return [row for row in (row_to_dict(r) for r in self._cursor.fetchall()) if row is not None]

    def __iter__(self) -> Iterator[Row]:
        if self._cursor is None:
            return iter(())
        for raw in self._cursor:
            item = row_to_dict(raw)
            if item is not None:
                yield item

    @property
    def lastrowid(self) -> int | None:
        if self._cursor is None:
            return None
        value = getattr(self._cursor, "lastrowid", None)
        return int(value) if value is not None else None

    @property
    def rowcount(self) -> int:
        if self._cursor is None:
            return 0
        return int(getattr(self._cursor, "rowcount", 0) or 0)
