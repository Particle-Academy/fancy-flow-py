"""Import diagnostics — the twins of ``ImportIssue`` / ``ImportResult``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph import FlowGraph

__all__ = ["ERROR", "WARNING", "ImportIssue", "ImportResult"]

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """One problem found while importing a WorkflowSchema."""

    level: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    @staticmethod
    def error(message: str, node_id: str | None = None, edge_id: str | None = None) -> ImportIssue:
        return ImportIssue(ERROR, message, node_id, edge_id)

    @staticmethod
    def warning(
        message: str, node_id: str | None = None, edge_id: str | None = None
    ) -> ImportIssue:
        return ImportIssue(WARNING, message, node_id, edge_id)

    @property
    def is_error(self) -> bool:
        return self.level == ERROR

    def to_dict(self) -> dict[str, Any]:
        pairs = {
            "level": self.level,
            "message": self.message,
            "nodeId": self.node_id,
            "edgeId": self.edge_id,
        }
        return {k: v for k, v in pairs.items() if v is not None}


@dataclass(frozen=True, slots=True)
class ImportResult:
    """A hydrated graph plus the issues found.

    ``ok`` is true when no error-level issue was recorded. In lenient mode
    errors are downgraded to warnings, so ``ok`` stays true.
    """

    ok: bool
    graph: FlowGraph
    issues: tuple[ImportIssue, ...] = field(default_factory=tuple)

    def errors(self) -> list[ImportIssue]:
        return [i for i in self.issues if i.is_error]

    def warnings(self) -> list[ImportIssue]:
        return [i for i in self.issues if not i.is_error]
