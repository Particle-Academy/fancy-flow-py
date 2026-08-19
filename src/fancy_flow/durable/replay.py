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
- every node EXCEPT the target is bound, by node id, to a boundary executor
  that aborts;
- so the engine walks its own topological order, skips its own dead branches,
  collects the target's inputs its own way, runs the target -- and stops at the
  next thing it would have run.

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
from typing import Any, Final, NoReturn

from ..engine.runner import FlowRunner
from ..executors import ExecutorRegistry
from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.options import RunOptions, RunResult
from ..schema.graph import FlowGraph

__all__ = ["BOUNDARY", "ReplayResult", "is_boundary", "replay_up_to"]

#: The abort reason the boundary executor uses. Not a failure: it is the engine
#: telling us it reached a node this job is not responsible for.
BOUNDARY: Final = "fancy-flow:node-boundary"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    result: RunResult
    #: node id -> the ports its output activated, from the engine's own events.
    ports: dict[str, tuple[str, ...]]

    def output_of(self, node_id: str) -> Any:
        return self.result.outputs.get(node_id)

    def ports_of(self, node_id: str) -> tuple[str, ...]:
        return self.ports.get(node_id, ())


def _boundary(ctx: ExecutionContext) -> NoReturn:
    ctx.abort(BOUNDARY)


def replay_up_to(
    graph: FlowGraph,
    node_id: str | None,
    executors: ExecutorRegistry,
    resume_outputs: dict[str, Any],
    initial_inputs: dict[str, dict[str, Any]] | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    depth: int = 0,
) -> ReplayResult:
    """Replay ``graph`` up to and through ``node_id``.

    Pass ``node_id=None`` to PROBE: every node is a boundary, so nothing
    executes and the engine reports only what it can determine structurally --
    a cycle, and the ports each resumed output republishes on.
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

    result = FlowRunner().run(
        graph,
        fork,
        collect,
        RunOptions(
            initial_inputs=initial_inputs or {},
            resume_outputs=resume_outputs,
            depth=depth,
        ),
    )

    return ReplayResult(result, {k: tuple(v) for k, v in ports.items()})


def is_boundary(error: str | None) -> bool:
    """True when a replay ended because it reached a node it does not own."""
    return error == BOUNDARY
