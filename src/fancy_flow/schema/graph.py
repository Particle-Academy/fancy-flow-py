r"""WorkflowSchema v1 shapes — the Python twins of ``FancyFlow\Schema\*``.

These are plain frozen dataclasses. The graph is the wire format shared by
three runtimes, so nothing here may carry behaviour a peer runtime does not
also have: if a method decides anything, it belongs in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "FlowEdge",
    "FlowGraph",
    "FlowNode",
    "PortDescriptor",
    "WorkflowMetadata",
]

_UNSET = object()


@dataclass(frozen=True, slots=True)
class PortDescriptor:
    """A connection point on a node.

    ``id`` is what an edge references through ``source_handle`` /
    ``target_handle``. The default input port is ``in`` and the default output
    port is ``out``.
    """

    id: str
    label: str | None = None
    type: str | None = None

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> PortDescriptor:
        return PortDescriptor(
            id=str(raw.get("id", "out")),
            label=str(raw["label"]) if raw.get("label") is not None else None,
            type=str(raw["type"]) if raw.get("type") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.label is not None:
            out["label"] = self.label
        if self.type is not None:
            out["type"] = self.type
        return out


@dataclass(frozen=True, slots=True)
class FlowNode:
    """A runtime node.

    ``type`` is the registry kind id (``@particle-academy/branch``, or a bare
    alias) — the same value the TypeScript side stores as both the xyflow node
    ``type`` and ``data.kind``.

    ``inputs`` / ``outputs`` are deliberately three-state. ``None`` means "no
    ports declared" and the engine falls back; an EMPTY list means "explicitly
    no ports" (a terminal node). Collapsing those two is how a terminal node
    starts publishing on ``out`` — or a branch node stops branching.
    """

    id: str
    type: str | None = None
    x: float = 0.0
    y: float = 0.0
    label: str | None = None
    description: str | None = None
    #: Announced to a person just BEFORE this node runs -- "Starting the deep
    #: analysis". Optional on purpose: most nodes in a graph are plumbing, and
    #: narrating all of them buries the steps anyone actually follows.
    starting_msg: str | None = None
    #: Announced AFTER this node finishes -- "Analysis complete". Emitted only
    #: when the node SUCCEEDS: a completion message printed after a failure
    #: tells a human the opposite of what happened.
    stopping_msg: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    inputs: tuple[PortDescriptor, ...] | None = None
    outputs: tuple[PortDescriptor, ...] | None = None

    @property
    def kind(self) -> str | None:
        """The registry kind id — an alias for :attr:`type`."""
        return self.type

    def option(self, key: str, default: Any = None) -> Any:
        """Read one config key, with PHP ``??`` semantics (null means absent)."""
        value = self.config.get(key)
        return default if value is None else value


@dataclass(frozen=True, slots=True)
class FlowEdge:
    """A directed connection between two nodes' ports.

    With ``source_handle`` / ``target_handle`` omitted the engine reads ``out``
    on the source and writes ``in`` on the target.
    """

    id: str
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class FlowGraph:
    """Nodes plus edges — the unit a host persists and the engine executes."""

    nodes: tuple[FlowNode, ...] = ()
    edges: tuple[FlowEdge, ...] = ()
    #: What this workflow ACCEPTS at run start — ``{name, type?, required?,
    #: default?}`` per entry. Callers pass a flat mapping BY NAME rather than
    #: keyed by node id, so renaming a trigger no longer breaks every caller
    #: silently. Empty for a workflow that takes none, which is every graph
    #: saved before this existed.
    inputs: tuple[Mapping[str, Any], ...] = ()

    def node(self, node_id: str) -> FlowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None


@dataclass(frozen=True, slots=True)
class WorkflowMetadata:
    """The portable ``metadata`` block of a WorkflowSchema v1 document."""

    id: str | None = None
    name: str | None = None
    description: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    author: str | None = None
    tags: tuple[str, ...] | None = None

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> WorkflowMetadata:
        tags = raw.get("tags")
        return WorkflowMetadata(
            id=str(raw["id"]) if raw.get("id") is not None else None,
            name=str(raw["name"]) if raw.get("name") is not None else None,
            description=str(raw["description"]) if raw.get("description") is not None else None,
            created_at=int(raw["createdAt"]) if raw.get("createdAt") is not None else None,
            updated_at=int(raw["updatedAt"]) if raw.get("updatedAt") is not None else None,
            author=str(raw["author"]) if raw.get("author") is not None else None,
            tags=tuple(str(t) for t in tags) if isinstance(tags, list) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        pairs = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "author": self.author,
            "tags": list(self.tags) if self.tags is not None else None,
        }
        return {k: v for k, v in pairs.items() if v is not None}
