"""Which nodes can run RIGHT NOW, given what has already settled.

Why this is not a second engine
-------------------------------

:class:`~fancy_flow.engine.runner.FlowRunner` walks a Kahn topological order
and, at each node, runs it when at least one incoming edge is active. That is a
total order because one process executes every node. Split the graph across jobs
and the same rule has to be asked the other way round -- not "what is next" but
"what is unblocked" -- which is this module.

The rule is the engine's, restated:

- every direct predecessor has SETTLED (in topological order the engine has
  already settled all of them by the time it reaches a node);
- and either the node has no incoming edges, or at least one incoming edge is
  ACTIVE -- its source completed and published on that edge's source handle.

A node whose predecessors have all settled with no active edge is what the
engine reports as ``idle/skipped``. Skipping SETTLES it, which can in turn
unblock -- or skip -- its own successors, so the pass repeats until nothing
changes. That cascade is how a dead branch collapses without leaving the run
stuck.

The one thing it does NOT decide
--------------------------------

Which ports a result activated. Those rules (``__port``, ``branch``, declared
outputs, the kind's ports, the ``out`` fallback) live in the engine and stay
there: this reads the ports back off the ``node-output`` events the engine
emitted when the node ran, stored on the claim row.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..registry import kind_id as kid
from ..schema.graph import FlowEdge, FlowGraph
from .state import NodeClaimStore, NodeRunStatus, NodeState

__all__ = ["Frontier", "FrontierResult"]


@dataclass(frozen=True, slots=True)
class FrontierResult:
    ready: tuple[str, ...]
    skipped: tuple[str, ...]


class Frontier:
    @staticmethod
    def compute(graph: FlowGraph, state: dict[str, NodeState]) -> FrontierResult:
        incoming: dict[str, list[FlowEdge]] = {}
        for edge in graph.edges:
            incoming.setdefault(edge.target, []).append(edge)

        # Settled nodes and the ports they lit. A skipped node is settled with
        # NO ports, which is precisely what makes its successors skip too.
        settled: dict[str, tuple[str, ...]] = {}
        held: set[str] = set()
        for node_id, entry in state.items():
            if entry.status == NodeRunStatus.COMPLETED:
                settled[node_id] = entry.ports
            elif entry.status in (NodeRunStatus.SKIPPED, NodeRunStatus.FAILED):
                settled[node_id] = ()
            else:
                held.add(node_id)

        ready: dict[str, None] = {}
        skipped: list[str] = []

        changed = True
        while changed:
            changed = False

            for node in graph.nodes:
                node_id = node.id
                if node_id in settled or node_id in held or node_id in ready:
                    continue

                edges = incoming.get(node_id, [])
                blocked = False
                active = False

                for edge in edges:
                    if edge.source not in settled:
                        blocked = True
                        break
                    # The engine's port key: an edge with no source handle reads
                    # the source's `out` port.
                    if (edge.source_handle or "out") in settled[edge.source]:
                        active = True

                if blocked:
                    continue

                # Reached, but down a branch that never lit.
                if edges and not active:
                    settled[node_id] = ()
                    skipped.append(node_id)
                    changed = True
                    continue

                # Annotations are never executed. Settling them here rather than
                # dispatching a job saves a queue round trip per sticky note --
                # and a graph can carry a lot of sticky notes.
                if node.type is not None and kid.matches(node.type, "note"):
                    settled[node_id] = ()
                    skipped.append(node_id)
                    changed = True
                    continue

                ready[node_id] = None

        return FrontierResult(tuple(ready), tuple(skipped))

    @staticmethod
    def is_complete(graph: FlowGraph, state: dict[str, NodeState]) -> bool:
        """Has every node settled? The run is finished when it has."""
        return all(
            node.id in state and state[node.id].status in NodeRunStatus.SETTLED
            for node in graph.nodes
        )

    @staticmethod
    def has_work_in_flight(state: dict[str, NodeState]) -> bool:
        """Is any node still held by a worker, or parked for a person?

        An empty frontier means something different depending on this: with work
        in flight the run is simply waiting, and whichever job finishes will
        advance it. With nothing in flight and nodes still unsettled, the graph
        cannot progress at all -- which is a stuck run, and must be reported
        rather than waited on.
        """
        return any(
            entry.status in (NodeRunStatus.CLAIMED, NodeRunStatus.PAUSED)
            for entry in state.values()
        )

    @staticmethod
    def settle_skips(store: NodeClaimStore, run_key: str, skipped: tuple[str, ...]) -> None:
        """Persist the skip cascade so the next pass does not recompute it."""
        for node_id in skipped:
            store.skip(run_key, node_id)
