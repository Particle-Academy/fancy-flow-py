"""Run ONE node of a graph -- through the real engine, not around it.

The problem this solves
-----------------------

A per-node driver has to hand a node exactly the inputs it would have received
mid-run: the right values, on the right target handles, from the right *active*
edges. Those rules are the engine's (``_collect_inputs``, ``_activated_ports``,
the merge-after-decision contract, the ``out`` fallbacks), and they are the
reason the three runtimes agree. Re-implementing them here would be a second
engine wearing a driver's clothes, and the two would drift.

What it does instead
--------------------

It replays the graph with :class:`FlowRunner` untouched:

- every node already completed is fed back as ``resume_outputs``, so the engine
  republishes it on the same ports and routes exactly as it did the first time;
- every node EXCEPT the target is bound, by node id, to a FENCE that runs
  nothing and publishes only a port no edge reads;
- so the engine walks its own topological order, skips its own dead branches,
  collects the target's inputs its own way, and runs the target.

Why the fence does not stop the walk
------------------------------------

It used to abort the run. The target's own inputs never depend on a fenced node:
the frontier dispatches a node only once every source is settled. A COMPLETED
source is resumed, not fenced. A SKIPPED or FAILED source lit no ports in the
frontier and lights none in the replay: the engine skips it again or fences it,
and a fence publishes only a port no edge reads.

But an UNRELATED node can precede the target in topological order, and two
siblings dispatched together are exactly that. When ``b``'s job started while
``a`` was still running, the replay aborted at ``a`` and never reached ``b``.
:meth:`Coordinator.run_node` read "the replay ended without running me" as "the
engine decided I am unreachable", so ``b`` was recorded skipped, never ran, and
the run completed as a success.

That needs no second worker. The frontier reports ready nodes in the order the
graph declares them, while the engine walks siblings in the order their edges
are listed, so :meth:`Coordinator.run_to_completion` started ``b`` first on any
graph whose two lists disagree.

Walking past fences makes that inference honest again: when the replay finishes
without an output for the target, it is because the engine found every inbound
edge dead.

The target's output is ``result.outputs[node_id]``, and the ports it activated
arrive as the engine's own ``node-output`` events. Nothing about routing is
recomputed here.

The cost, stated plainly
------------------------

Replaying the completed prefix is O(nodes) per node, so a run is O(nodes^2) in
bookkeeping. The republish executes nothing -- it re-publishes stored values --
so for the graph sizes workflows actually have this is noise next to a single
queue round trip. It buys exact fidelity to the engine, which is not negotiable,
and one implementation of the routing rules instead of two.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from ..engine.runner import FlowRunner
from ..executors import ExecutorRegistry
from ..registry.registry import NodeKindRegistry
from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.identity import RunIdentity
from ..runtime.options import RunOptions, RunResult
from ..runtime.ports import Port
from ..schema.graph import FlowGraph

__all__ = ["BOUNDARY", "FENCE_PORT", "ReplayResult", "is_boundary", "replay_up_to"]

#: The abort reason a boundary used to report. Nothing aborts with it any more
#: (see "Why the fence does not stop the walk"); :func:`is_boundary` still
#: recognises it so a caller that checks for it keeps working.
BOUNDARY: Final = "fancy-flow:node-boundary"

#: The port a fenced node publishes on. No edge reads it, so everything
#: downstream of a fenced node is dark in the replay -- which never matters to
#: the target, whose sources are all settled.
FENCE_PORT: Final = "fancy-flow:fenced"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    result: RunResult
    #: node id -> the ports its output activated, from the engine's own events.
    ports: dict[str, tuple[str, ...]]

    def output_of(self, node_id: str) -> Any:
        return self.result.outputs.get(node_id)

    def ports_of(self, node_id: str) -> tuple[str, ...]:
        return self.ports.get(node_id, ())


def _boundary(ctx: ExecutionContext) -> dict[str, Any]:
    return Port.only(FENCE_PORT)


def replay_up_to(
    graph: FlowGraph,
    node_id: str | None,
    executors: ExecutorRegistry,
    resume_outputs: dict[str, Any],
    initial_inputs: dict[str, dict[str, Any]] | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    depth: int = 0,
    run: RunIdentity | None = None,
    kinds: NodeKindRegistry | None = None,
) -> ReplayResult:
    """Replay ``graph`` up to and through ``node_id``.

    Pass ``node_id=None`` to PROBE: every node is a boundary, so nothing
    executes and the engine reports only what it can determine structurally --
    a cycle, and the ports each resumed output republishes on.

    ``kinds`` is the registry the engine resolves ports against, exactly as
    ``FlowRunner(kinds)`` takes it; ``None`` means the shared one. A driver must
    pass the registry its run was configured with. Replaying against a different
    one publishes a node with no declared outputs on THAT registry's idea of its
    kind -- ``for_each`` on ``out`` instead of ``item`` / ``done`` -- so the
    durable run routes differently from a single-process run of the same graph.
    """
    fork = executors.fork()
    for node in graph.nodes:
        if node.id != node_id:
            # bind_node outranks kind bindings AND the `*` fallback, so this
            # fences off the whole graph regardless of what a host bound.
            fork.bind_node(node.id, _boundary)

    ports: dict[str, list[str]] = {}

    def collect(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_OUTPUT and event.node_id is not None:
            ports.setdefault(event.node_id, []).append(str(event.port_id))
        if on_event is not None:
            on_event(event)

    result = FlowRunner(kinds).run(
        graph,
        fork,
        collect,
        RunOptions(
            initial_inputs=initial_inputs or {},
            resume_outputs=resume_outputs,
            depth=depth,
            run=run,
        ),
    )

    return ReplayResult(result, {k: tuple(v) for k, v in ports.items()})


def is_boundary(error: str | None) -> bool:
    """True when a run ended because the replay reached a node it does not own."""
    return error == BOUNDARY
