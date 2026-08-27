"""Refuse a graph whose nodes cannot take part in the workflow's dataflow.

Two shapes, both of which import cleanly and then quietly do nothing. Neither
*fails* — which is what makes them worth refusing at authoring time, because a
run that reports success is the worst way for a workflow to be wrong. Both were
measured against the engine before any runtime implemented this:

1. **A floating node** — no inbound edge and no outbound edge. It is not
   skipped: a node with no incoming edge is a root, so the topological sort
   runs it. A three-node graph with one stray ``log`` executed ``t,lonely,o``.
   It runs disconnected, receiving nothing from the graph and reaching nobody
   in it, which is precisely the state an author cannot see on a canvas.

2. **An edge leaving a terminator.** A terminal kind — ``output``, ``log`` —
   declares an *empty* output port list; it ends a chain. Measured:
   ``t -> output -> log`` imported clean and the ``log`` ran, with
   ``{{ input }}`` resolving to ``""``. ``collect_inputs`` binds a payload only
   when ``"<source_id>:<handle>"`` exists, and a node publishing no ports never
   creates that key — so the edge does not fail, it delivers nothing, and the
   node downstream operates on a hole.

That second one is the same silent-nothing the undelivered-edge diagnostic
reports at run time, except this is decidable **from the document alone**, so it
should never reach a run to be diagnosed.

Both are errors rather than warnings because both are unambiguous: no data at
run time makes a floating node participate, and none makes an edge out of a
terminator deliver. That is the test for refusing at authoring time instead of
warning about it.

The twin of ``FancyFlow\\Analysis\\GraphConnectivity`` (PHP) and
``checkGraphConnectivity`` (TypeScript).
"""

from __future__ import annotations

from ..registry import kind_id
from ..registry.registry import NodeKindRegistry, default_registry
from ..schema.graph import FlowEdge, FlowNode
from ..schema.issues import ImportIssue

__all__ = ["check_graph_connectivity", "may_float"]


def check_graph_connectivity(
    nodes: list[FlowNode],
    edges: list[FlowEdge],
    registry: NodeKindRegistry | None = None,
) -> list[ImportIssue]:
    """Every connectivity problem in the graph, as import issues."""
    if registry is None:
        registry = default_registry()

    has_incoming: set[str] = set()
    has_outgoing: set[str] = set()
    for edge in edges:
        has_incoming.add(edge.target)
        has_outgoing.add(edge.source)

    issues: list[ImportIssue] = []

    # A single-node graph is not "floating" -- it is a graph with one step,
    # which is a legitimate (if small) workflow and what every graph looks like
    # on the way to a bigger one. Refusing it would make an editor unusable
    # from the first node placed.
    single = len(nodes) == 1

    for node in nodes:
        if single or may_float(node, registry):
            continue

        if node.id not in has_incoming and node.id not in has_outgoing:
            issues.append(
                ImportIssue.error(
                    f'Node "{node.id}" is connected to nothing - no inbound edge and no '
                    "outbound edge. It still RUNS (a node with no inbound edge is a root), "
                    "but it receives nothing from the graph and reaches nobody in it, so it "
                    "is either unwired or left behind by a deletion. Only a note, an "
                    "annotation or a lane may float.",
                    node_id=node.id,
                )
            )

    by_id = {node.id: node for node in nodes}

    for edge in edges:
        source = by_id.get(edge.source)
        if source is None or not _is_terminator(source, registry):
            continue

        issues.append(
            ImportIssue.error(
                f'Edge "{edge.id}" reads from "{edge.source}", which is a TERMINAL node and '
                "publishes no output ports at all. Nothing can ever travel this edge: it does "
                "not fail at run time, it delivers nothing, and "
                f'"{edge.target}" runs anyway with an empty input.',
                edge_id=edge.id,
            )
        )

    return issues


def may_float(node: FlowNode, registry: NodeKindRegistry | None = None) -> bool:
    """Whether this node is allowed to sit unconnected.

    Three answers, and the third is the one that took a second pass — it was
    missed in the PHP twin's first release and shipped as 0.48.1:

    1. ``note``, matched across every id the kind answers to, so a graph saved
       with the canonical ``@particle-academy/note`` stays an annotation rather
       than becoming an unwireable node.
    2. Any kind categorised ``annotation`` or ``layout``. A host may register
       its own note, and the TypeScript runtime ships ``@particle-academy/lane``
       — a swimlane its engine walks straight past. Neither is a step, and
       neither is ever wired to anything.
    3. A kind this registry has never heard of. Not a loophole, the honest
       answer: an unknown kind already produces its own issue, and we cannot
       know whether it is a step, an annotation or a lane. Claiming it must be
       wired would assert something unverifiable — and it lands hardest on the
       graphs that deserve it least, since a laned graph loaded by a runtime
       without ``lane`` registered would report every swimlane twice, the
       second time wrongly.
    """
    if registry is None:
        registry = default_registry()

    if not node.type:
        return False

    if kind_id.matches(node.type, "note"):
        return True

    kind = registry.get(node.type)
    if kind is None:
        return True

    return kind.category in ("annotation", "layout")


def _is_terminator(node: FlowNode, registry: NodeKindRegistry) -> bool:
    """Whether this node ends a chain and can never be an edge's source.

    ``()`` and ``None`` are different answers and only the first means this.
    ``None`` is "nobody declared what this publishes", which resolves to ``out``
    and describes most nodes in most graphs; an empty tuple is an explicit claim
    that there is nothing to connect from. Reading them alike would refuse
    nearly every workflow ever written.
    """
    # A node declaring its own ports overrides its kind, so an author who has
    # said what this node publishes is believed -- the same way the engine
    # believes it.
    #
    # Reachable only for a hand-built graph here: `import_workflow` drops
    # node-level ports (as the PHP twin does, and unlike the TypeScript one,
    # which preserves them). A real divergence between the importers, recorded
    # rather than smoothed over.
    if node.outputs is not None:
        return len(node.outputs) == 0

    kind = registry.get(node.type) if node.type else None

    # An unregistered kind falls back to `out` in the engine, so it is not a
    # terminator. Refusing here would break a host mid-registration, and would
    # use "I do not know" as evidence.
    if kind is None:
        return False

    return kind.outputs is not None and len(kind.outputs) == 0
