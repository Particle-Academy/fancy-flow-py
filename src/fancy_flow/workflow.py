"""Parse, validate, import and export WorkflowSchema v1 documents.

A graph an agent or human authors in ``<FlowEditor>`` round-trips through here
unchanged. This answers "is this graph COHERENT?" -- unknown kinds, dangling
edges, missing required config. It does **not** answer "is it safe to accept?";
that is :mod:`fancy_flow.security`, and conflating the two is how a payload
gets treated as a document.
"""

from __future__ import annotations

import json
import time
from typing import Any, Final

from .registry.registry import NodeKindRegistry, default_registry
from .schema.graph import FlowEdge, FlowGraph, FlowNode, WorkflowMetadata
from .schema.issues import ERROR, WARNING, ImportIssue, ImportResult

__all__ = ["SCHEMA_URL", "SCHEMA_VERSION", "export_workflow", "import_workflow", "to_json"]

SCHEMA_VERSION: Final = 1
SCHEMA_URL: Final = "https://particle.academy/schemas/workflow/v1.json"


def import_workflow(
    schema: str | dict[str, Any],
    lenient: bool = False,
    registry: NodeKindRegistry | None = None,
) -> ImportResult:
    """Hydrate a WorkflowSchema into a :class:`FlowGraph`.

    Validates kinds and configs against the registry, reporting unknown kinds,
    missing required config, and dangling edges. In lenient mode, schema-level
    errors become warnings.
    """
    registry = registry if registry is not None else default_registry()
    issues: list[ImportIssue] = []

    if isinstance(schema, str):
        try:
            decoded = json.loads(schema)
        except ValueError:
            decoded = None
        schema = decoded if isinstance(decoded, dict) else None  # type: ignore[assignment]

    if not isinstance(schema, dict):
        return ImportResult(False, FlowGraph(), (ImportIssue.error("Schema is not an object."),))

    version = schema.get("version")
    if version != SCHEMA_VERSION:
        issues.append(
            ImportIssue(
                WARNING if lenient else ERROR,
                f"Unsupported workflow schema version: {version!r} (expected {SCHEMA_VERSION})",
            )
        )
        if not lenient:
            return ImportResult(False, FlowGraph(), tuple(issues))

    graph_raw = schema.get("graph") or {}
    raw_nodes = graph_raw.get("nodes") or []
    raw_edges = graph_raw.get("edges") or []

    nodes: list[FlowNode] = []
    node_ids: set[str] = set()

    for raw in raw_nodes:
        kind_name = str(raw.get("kind", ""))
        kind = registry.get(kind_name)

        if kind is None:
            issues.append(
                ImportIssue(
                    WARNING if lenient else ERROR,
                    f'Unknown kind "{kind_name}" - register it before importing.',
                    node_id=raw.get("id"),
                )
            )

        config = raw.get("config")
        if config is None:
            config = registry.default_config_for(kind) if kind is not None else {}

        if kind is not None:
            for issue in registry.validate_config(kind, config):
                issues.append(
                    ImportIssue.warning(
                        f"{issue['key']}: {issue['message']}", node_id=raw.get("id")
                    )
                )

        position = raw.get("position") or {}
        node = FlowNode(
            id=str(raw["id"]),
            type=kind_name,
            x=float(position.get("x", 0)),
            y=float(position.get("y", 0)),
            label=raw.get("label") or (kind.label if kind is not None else kind_name),
            description=str(raw["description"]) if raw.get("description") is not None else None,
            starting_msg=str(raw["startingMsg"]) if raw.get("startingMsg") is not None else None,
            stopping_msg=str(raw["stoppingMsg"]) if raw.get("stoppingMsg") is not None else None,
            config=config,
            # inputs/outputs intentionally left None on import - the engine then
            # falls back to the kind's ports, or a single `out`, matching the
            # TypeScript import.
        )
        nodes.append(node)
        node_ids.add(node.id)

    edges: list[FlowEdge] = []
    for raw in raw_edges:
        edge_id = str(raw.get("id", ""))
        source = str(raw.get("source", ""))
        target = str(raw.get("target", ""))

        if source not in node_ids:
            issues.append(
                ImportIssue.warning(f'Edge source "{source}" not found.', edge_id=edge_id)
            )
            continue
        if target not in node_ids:
            issues.append(
                ImportIssue.warning(f'Edge target "{target}" not found.', edge_id=edge_id)
            )
            continue

        label = raw.get("label")
        edges.append(
            FlowEdge(
                id=edge_id,
                source=source,
                target=target,
                source_handle=_opt(raw.get("sourceHandle")),
                target_handle=_opt(raw.get("targetHandle")),
                label=label if isinstance(label, str) else None,
            )
        )

    ok = not any(issue.is_error for issue in issues)
    # `graph.inputs` is what the workflow ACCEPTS -- the declaration
    # `resolve_workflow_props` validates against. Dropping it meant every
    # imported graph declared nothing, so every prop was rejected with "this
    # workflow declares no inputs". The PHP twin had the identical gap, for the
    # identical reason: both ports transcribed the node/edge loop and neither
    # carried the declaration beside it.
    #
    # Only well-formed entries survive, and a malformed one is dropped rather
    # than aborting the import: a bad declaration should not cost a consumer
    # their whole graph, and `resolve_workflow_props` judges values anyway.
    declared_inputs = tuple(
        i
        for i in (graph_raw.get("inputs") or [])
        if isinstance(i, dict) and isinstance(i.get("name"), str) and i["name"]
    )

    return ImportResult(
        ok, FlowGraph(tuple(nodes), tuple(edges), declared_inputs), tuple(issues)
    )


def export_workflow(
    graph: FlowGraph,
    metadata: WorkflowMetadata | None = None,
    view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Snapshot an in-memory graph as a portable WorkflowSchema.

    When ``metadata`` is supplied its ``updatedAt`` is stamped with the current
    time in milliseconds, mirroring ``exportWorkflow``.
    """
    schema: dict[str, Any] = {"$schema": SCHEMA_URL, "version": SCHEMA_VERSION}

    if metadata is not None:
        meta = metadata.to_dict()
        meta["updatedAt"] = round(time.time() * 1000)
        schema["metadata"] = meta

    schema["graph"] = {
        # Written only when there IS a declaration, matching the TypeScript
        # exporter. An always-present `"inputs": []` would change the bytes of
        # every graph ever saved, for nothing.
        **({"inputs": [dict(i) for i in graph.inputs]} if graph.inputs else {}),
        "nodes": [_node_to_schema(n) for n in graph.nodes],
        "edges": [_edge_to_schema(e) for e in graph.edges],
    }

    if view is not None:
        schema["view"] = view

    return schema


def to_json(
    graph: FlowGraph,
    metadata: WorkflowMetadata | None = None,
    view: dict[str, Any] | None = None,
    indent: int | None = 4,
) -> str:
    """Export and JSON-encode in one step."""
    return json.dumps(export_workflow(graph, metadata, view), indent=indent, ensure_ascii=False)


def _opt(value: Any) -> str | None:
    return None if value is None else str(value)


def _node_to_schema(node: FlowNode) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": node.id,
        "kind": node.type or "custom",
        "position": {"x": node.x, "y": node.y},
    }
    if node.label is not None:
        out["label"] = node.label
    if node.description is not None:
        out["description"] = node.description
    # Omitted entirely when unset, so a graph of ordinary plumbing nodes does
    # not carry a pair of empty keys per node and every diff of a saved graph
    # stays readable.
    if node.starting_msg and node.starting_msg.strip():
        out["startingMsg"] = node.starting_msg
    if node.stopping_msg and node.stopping_msg.strip():
        out["stoppingMsg"] = node.stopping_msg
    if node.config:
        out["config"] = node.config
    return out


def _edge_to_schema(edge: FlowEdge) -> dict[str, Any]:
    out: dict[str, Any] = {"id": edge.id, "source": edge.source, "target": edge.target}
    if edge.source_handle is not None:
        out["sourceHandle"] = edge.source_handle
    if edge.target_handle is not None:
        out["targetHandle"] = edge.target_handle
    if edge.label is not None:
        out["label"] = edge.label
    return out
