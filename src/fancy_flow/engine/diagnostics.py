"""Warnings about a graph that runs and delivers nothing -- the one implementation.

:func:`undelivered_edge_warnings` is called by BOTH drivers, and that is the
point of it living here rather than inside the walk:

- :class:`~fancy_flow.engine.runner.FlowRunner` calls it for every node it
  reaches, just before deciding whether the node runs;
- the durable :class:`~fancy_flow.durable.coordinator.Coordinator` calls it for
  each node its frontier SKIPS. A skipped node never gets a job, so the warning
  its replay emitted inside other jobs was filtered out there, and a host
  driving the run durably never saw it (the 0.21.0 known gap).

Both hand it the same four facts -- the target, its inbound edges, the ports
published so far, and which nodes completed -- and emit what comes back. A
second copy of the rule in the durable layer would agree for a year and then
disagree on one config-derived port.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from typing import Any

from ..registry.port_resolution import possible_ports
from ..registry.registry import NodeKindRegistry
from ..runtime.events import RunEvent
from ..schema.graph import FlowEdge, FlowNode

__all__ = ["undelivered_edge_warnings"]


def undelivered_edge_warnings(
    target: FlowNode,
    incoming: Iterable[FlowEdge],
    port_values: Mapping[str, Any],
    completed: AbstractSet[str],
    nodes_by_id: Mapping[str, FlowNode],
    registry: NodeKindRegistry,
) -> list[RunEvent]:
    """The ``log``/``warn`` events for ``target``'s edges that deliver nothing.

    Returns them and emits nothing; the caller decides where they go.

    ``port_values`` is keyed ``"<node_id>:<port>"``, in the order the ports were
    published. Only the KEYS are read -- they are what "Available" lists -- so a
    driver that stores ports without their values passes any value it likes.

    AN EDGE THAT DELIVERS NOTHING MUST SAY SO. Asked before the activity gate,
    because the two outcomes are both silent and only one reaches input
    collection. If the bad edge is a node's only inbound one the node is SKIPPED
    and never collects inputs at all; if the node has another live edge it RUNS
    with that port simply missing -- and then the downstream template is
    completely correct and renders empty, because the payload never arrived to
    have a field in it.

    Keyed on the source having COMPLETED, and on the handle not being a port the
    source could POSSIBLY publish. A branch that was not taken is ordinary and
    must never warn; a source that finished and cannot publish this port is a
    misconfiguration that will never work on any run. "Did it publish?" cannot
    tell those apart -- both are absent. A warning that fires on ordinary
    branching is noise, and noise is how a real warning stops being read.
    """
    warnings: list[RunEvent] = []

    for edge in incoming:
        handle = edge.source_handle or "out"
        # The engine's port key: an edge with no source handle reads `out`.
        if (
            f"{edge.source}:{handle}" not in port_values
            and edge.source in completed
            and handle not in _possible_port_ids(nodes_by_id.get(edge.source), registry)
        ):
            warnings.append(
                RunEvent.log(
                    "warn",
                    _undelivered_edge_message(edge, target, port_values, nodes_by_id, registry),
                    target.id,
                    {"edge": edge.id, "source": edge.source, "sourceHandle": handle},
                )
            )

    return warnings


def _undelivered_edge_message(
    edge: FlowEdge,
    target: FlowNode,
    port_values: Mapping[str, Any],
    nodes_by_id: Mapping[str, FlowNode],
    registry: NodeKindRegistry,
) -> str:
    """The message for an edge whose source port publishes nothing.

    Shape agreed with the consumer who reported the defect against the PHP
    twin, in their order, and each part earns its place:

    1. THE EDGE ID FIRST. The author is looking at a graph, and the edge is
       the thing they can act on.
    2. THE CONSEQUENCE, IN RUNTIME TERMS. Without "nothing would reach X"
       this reads as a schema nit, and a handle string feels cosmetic.
    3. THE AVAILABLE PORTS -- what the source ACTUALLY published on this
       run, in publication order, not the kind's declaration, so a
       config-driven kind reports its real ports.
    4. THE REMEDY FOR THE COMMON CASE. Nearly every occurrence is an agent
       ADDING a handle that should not be there, so "leave sourceHandle
       off" is the fix more often than picking from the list -- and it is
       offered only when there IS a handle to leave off.

    Plus the part only the engine can supply: when the handle names a FIELD
    of the source's output shape, say so. That is the actual confusion, and
    naming it turns a correction into an explanation.
    """
    handle = edge.source_handle or "out"

    prefix = f"{edge.source}:"
    available = [key[len(prefix) :] for key in port_values if key.startswith(prefix)]

    message = (
        f'Edge {edge.id} reads port "{handle}" from node {edge.source}, which never '
        f"publishes it \u2014 nothing would reach {target.id} at run time."
    )

    if available:
        message += " Available: " + ", ".join(available) + "."

    # The near-miss: a FIELD of that name, where a PORT was expected.
    source = nodes_by_id.get(edge.source)
    kind = registry.get(source.type) if source is not None and source.type else None
    fields = kind.output_shape_for(source.config) if kind is not None and source else None

    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict) and field.get("path") == handle:
                message += (
                    f' Note: "{handle}" is a FIELD this node emits, not a port \u2014 read '
                    f"it downstream as {{{{ in.{handle} }}}} rather than naming it as a "
                    "source handle."
                )
                break

    if edge.source_handle is not None:
        message += " Leave sourceHandle off to read the node's output."

    return message


def _possible_port_ids(node: FlowNode | None, registry: NodeKindRegistry) -> list[str]:
    """Every port this node COULD publish -- not the ones it did.

    Delegated to :func:`possible_ports` so the config-derived ports of
    ``switch_case``, ``llm_router`` and ``subflow`` count. Reading only the
    kind's static declaration would call a third configured case impossible,
    which is exactly what the PHP twin once did to a graph its own authoring
    API had invited.
    """
    if node is None:
        return ["out"]

    kind = registry.get(node.type) if node.type else None
    return possible_ports(node, kind, node.config)
