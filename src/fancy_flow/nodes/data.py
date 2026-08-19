"""Data executors -- memory, a keyed store, and workflow-scoped values."""

from __future__ import annotations

from typing import Any

from ..runtime.context import ExecutionContext
from .support import expr
from .support.clients import KeyValueStore

__all__ = ["DataStore", "MemoryStore", "variable"]


class MemoryStore:
    """``memory_store`` -- read / write / append per-conversation memory."""

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    def execute(self, ctx: ExecutionContext) -> Any:
        operation = str(ctx.option("operation", "read"))
        key = str(ctx.option("key", ""))

        if operation == "write":
            value = expr.evaluate(ctx.option("value"), ctx.inputs)
            self._store.set(key, value)
            return value

        if operation == "append":
            value = expr.evaluate(ctx.option("value"), ctx.inputs)
            current = self._store.get(key, [])
            items = list(current) if isinstance(current, (list, tuple)) else [current]
            items.append(value)
            self._store.set(key, items)
            return items

        return self._store.get(key)


class DataStore:
    """``data_store`` -- get / set / delete / query / list against a host store.

    Keys are namespaced by ``table`` as ``table/key``. ``query`` and ``list``
    scan the table; ``query`` additionally filters rows by the ``where`` map.
    """

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    def execute(self, ctx: ExecutionContext) -> Any:
        operation = str(ctx.option("operation", "get"))
        table = str(ctx.option("table", "default"))
        key = ctx.option("key")

        if operation == "set":
            value = expr.evaluate(ctx.option("value"), ctx.inputs)
            self._store.set(self._namespaced(table, str(key)), value)
            return value

        if operation == "delete":
            self._store.delete(self._namespaced(table, str(key)))
            return {"deleted": str(key)}

        if operation == "list":
            return self._rows(table)

        if operation == "query":
            where = ctx.option("where", {})
            where = where if isinstance(where, dict) else {}
            return [row for row in self._rows(table).values() if _matches(row, where)]

        return self._store.get(self._namespaced(table, str(key)))

    def _rows(self, table: str) -> dict[str, Any]:
        prefix = f"{table}/"
        return {
            store_key[len(prefix) :]: value
            for store_key, value in self._store.all().items()
            if store_key.startswith(prefix)
        }

    @staticmethod
    def _namespaced(table: str, key: str) -> str:
        return f"{table}/{key}"


def _matches(row: Any, where: dict[str, Any]) -> bool:
    if not where:
        return True
    if not isinstance(row, dict):
        return False
    return all(row.get(field) == expected for field, expected in where.items())


def variable(ctx: ExecutionContext) -> Any:
    """``variable`` -- a workflow-scoped value, resolved and emitted."""
    return expr.evaluate(ctx.option("value"), ctx.inputs)
