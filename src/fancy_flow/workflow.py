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
from collections.abc import Callable
from typing import Any, Final

from .analysis.graph_connectivity import check_graph_connectivity
from .registry.registry import NodeKindRegistry, default_registry
from .schema.graph import FlowEdge, FlowGraph, FlowNode, WorkflowMetadata
from .schema.issues import ERROR, WARNING, ImportIssue, ImportResult

__all__ = [
    "SCHEMA_URL",
    "SCHEMA_VERSION",
    "export_workflow",
    "import_workflow",
    "migrate_schema",
    "to_json",
]

SCHEMA_VERSION: Final = 1
SCHEMA_URL: Final = "https://particle.academy/schemas/workflow/v1.json"


_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}
"""Every migration step, keyed by the version it upgrades FROM.

A step keyed ``N`` takes a version-N document to version N+1. Empty today
because v1 is current -- when a BREAKING bump lands, add the step here and every
stored document upgrades on read, in this runtime and its twins.
"""


def migrate_schema(
    schema: dict[str, Any],
    steps: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Upgrade a schema document to the current version, as far as it can go.

    Why this exists, and why it had to exist BEFORE it was needed
    ------------------------------------------------------------

    The version has always been on the document; only the TypeScript runtime
    acted on it. This runtime and the PHP one compared it and errored -- so the
    day schema v2 was cut, every stored Op would have hard-failed to import on
    both SERVER runtimes, which is where durable runs RESUME. A run parked on a
    human approval would have become unresumable, and the fix could not be
    applied afterwards: the graphs would already be unreadable by the very code
    meant to migrate them.

    The three rules, each with a reason
    -----------------------------------

    - A **past** version migrates forward, step by step, to the current one.
    - A **future** version is left ALONE. We cannot know what a later schema
      means, and migrating downward would be guessing; untouched hands it to the
      version check, which reports it honestly.
    - A **gap** in the table is left alone too. A missing step is not a licence
      to guess.

    Nothing here changes behaviour today -- with an empty table every document
    passes through untouched -- which is exactly what makes it safe to add now
    rather than under pressure later.

    ``steps`` is an argument rather than a hard-coded lookup because otherwise
    this seam could not be TESTED: with only v1 in existence there is no old
    document to migrate, and a test against the built-in table would pass
    identically against a function that did nothing at all.
    """
    table = _MIGRATIONS if steps is None else steps
    version = schema.get("version")

    if not isinstance(version, int) or isinstance(version, bool) or version >= SCHEMA_VERSION:
        return schema

    while version < SCHEMA_VERSION:
        step = table.get(version)
        if step is None:
            return schema

        schema = step(schema)
        version += 1
        schema["version"] = version

    return schema


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
    # Best-effort forward migration BEFORE the version check, so a document
    # written against an older schema is upgraded rather than rejected. The
    # check below is still the gate.
    schema = migrate_schema(schema)
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

    # WIRING, not merely dataflow: a node no edge reaches and that reaches no
    # edge, and an edge reading from a node that publishes nothing.
    #
    # Deliberately AFTER the edge loop, so it sees the same edges the engine
    # will -- a dangling edge is dropped with a warning above, and running this
    # first would let a dropped edge count as a connection.
    #
    # Deliberately NOT gated on `lenient`. That flag is about unknown
    # VOCABULARY (a kind this host has not registered), never about wiring; a
    # floating node floats in every registry.
    issues.extend(check_graph_connectivity(nodes, edges, registry))

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
    # `inputs` is a SIBLING of `graph`, not a member of it — that is where
    # `importWorkflow` reads it (`s.inputs`) and where `exportWorkflow` writes
    # it. Reading it from inside `graph` meant each runtime looked exactly
    # where the other had not put it, so a TS-authored workflow loaded here
    # with its declaration silently gone and every prop became
    # `Unknown workflow input "..."` — an error blaming the caller for a bug
    # in the loader.
    #
    # The nested spot is still accepted, because documents this exporter wrote
    # before the fix have it there; dropping those would be the same failure
    # aimed at our own users.
    raw_inputs = schema.get("inputs")
    if not isinstance(raw_inputs, list):
        raw_inputs = graph_raw.get("inputs") or []

    declared_inputs = tuple(
        i
        for i in raw_inputs
        if isinstance(i, dict) and isinstance(i.get("name"), str) and i["name"]
    )

    return ImportResult(ok, FlowGraph(tuple(nodes), tuple(edges), declared_inputs), tuple(issues))


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

    # Top level, beside `$schema`/`version`/`graph` — the level `exportWorkflow`
    # writes and `importWorkflow` reads. Written only when there IS a
    # declaration: an always-present `"inputs": []` would change the bytes of
    # every graph ever saved, and `[]` is the different, positive claim that
    # the workflow declares no inputs.
    if graph.inputs:
        schema["inputs"] = [dict(i) for i in graph.inputs]

    schema["graph"] = {
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
