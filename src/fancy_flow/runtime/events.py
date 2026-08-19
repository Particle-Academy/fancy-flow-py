"""The run event stream.

One class with a ``type`` tag and the union of every arm's payload, matching
the PHP twin rather than Python's usual "a class per variant" — because the
serialized shape is the contract three runtimes share, and a union that
serializes differently per language is not a union.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Final

__all__ = ["NodeStatus", "RunEvent"]


class NodeStatus:
    """The lifecycle status a node reports through ``node-status`` events."""

    IDLE: Final = "idle"
    QUEUED: Final = "queued"
    RUNNING: Final = "running"
    DONE: Final = "done"
    ERROR: Final = "error"


@dataclass(frozen=True, slots=True)
class RunEvent:
    """A single event in a run's stream.

    Types: ``run-start``, ``node-status``, ``node-output``, ``log``,
    ``run-end``, ``run-error``. Build them with the classmethods.
    """

    # ClassVar, not fields: a dataclass would otherwise turn each of these
    # into a constructor argument -- and one with a default, which makes every
    # real field after it illegal.
    RUN_START: ClassVar[str] = "run-start"
    NODE_STATUS: ClassVar[str] = "node-status"
    NODE_OUTPUT: ClassVar[str] = "node-output"
    LOG: ClassVar[str] = "log"
    RUN_END: ClassVar[str] = "run-end"
    RUN_ERROR: ClassVar[str] = "run-error"

    type: str
    node_id: str | None = None
    status: str | None = None
    text: str | None = None
    port_id: str | None = None
    value: Any = None
    level: str | None = None
    message: str | None = None
    detail: Any = None
    ok: bool | None = None
    error: str | None = None

    @classmethod
    def run_start(cls) -> RunEvent:
        return cls(cls.RUN_START)

    @classmethod
    def node_status(cls, node_id: str, status: str, text: str | None = None) -> RunEvent:
        return cls(cls.NODE_STATUS, node_id=node_id, status=status, text=text)

    @classmethod
    def node_output(cls, node_id: str, port_id: str, value: Any) -> RunEvent:
        return cls(cls.NODE_OUTPUT, node_id=node_id, port_id=port_id, value=value)

    @classmethod
    def log(
        cls,
        level: str,
        message: str,
        node_id: str | None = None,
        detail: Any = None,
    ) -> RunEvent:
        return cls(cls.LOG, node_id=node_id, level=level, message=message, detail=detail)

    @classmethod
    def run_end(cls, ok: bool) -> RunEvent:
        return cls(cls.RUN_END, ok=ok)

    @classmethod
    def run_error(cls, error: str) -> RunEvent:
        return cls(cls.RUN_ERROR, error=error)

    def to_dict(self) -> dict[str, Any]:
        """Serialize only the active arm, in the peer runtimes' key casing."""
        if self.type == self.RUN_START:
            return {"type": self.type}
        if self.type == self.NODE_STATUS:
            pairs = {
                "type": self.type,
                "nodeId": self.node_id,
                "status": self.status,
                "text": self.text,
            }
            return {k: v for k, v in pairs.items() if v is not None}
        if self.type == self.NODE_OUTPUT:
            return {
                "type": self.type,
                "nodeId": self.node_id,
                "portId": self.port_id,
                "value": self.value,
            }
        if self.type == self.LOG:
            pairs = {
                "type": self.type,
                "nodeId": self.node_id,
                "level": self.level,
                "message": self.message,
                "detail": self.detail,
            }
            return {k: v for k, v in pairs.items() if v is not None}
        if self.type == self.RUN_END:
            return {"type": self.type, "ok": self.ok}
        if self.type == self.RUN_ERROR:
            return {"type": self.type, "error": self.error}
        return {"type": self.type}
