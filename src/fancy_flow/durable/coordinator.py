"""The per-node driver, with the queue left out.

This is the whole of "how a queued run branches", minus the transport. It owns
two operations and nothing else:

``advance()``
    Ask the frontier what is unblocked, settle the skip cascade, and report the
    ready node ids. A queue adapter dispatches one job per id.

``run_node()``
    Claim one node, replay the graph through the real engine fenced to that
    node, and checkpoint the output plus the ports the ENGINE said it activated.

Everything a queue library would add -- enqueue, retry scheduling, worker
lifecycle -- sits outside. That separation is the point: it makes the subtle
part (which node may run, and with what inputs) testable in-process, with no
broker, and identical under every adapter.

:class:`Coordinator.run_to_completion` drives both in one process. It is a real
durable runner, not a toy: with a persistent :class:`NodeClaimStore` it survives
a crash exactly as a queued run does, because the crash-resume behaviour lives
in the checkpoints rather than in the loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..executors import ExecutorRegistry
from ..registry.registry import NodeKindRegistry
from ..runtime.events import RunEvent
from ..runtime.options import RunResult
from ..runtime.pause import Pause, PauseSignal
from ..schema.graph import FlowGraph
from .frontier import Frontier
from .replay import is_boundary, replay_up_to
from .retry import RetryPolicy
from .state import InMemoryClaimStore, NodeClaimStore, NodeRunStatus

__all__ = ["Coordinator", "DurableRunResult", "NodeOutcome"]


@dataclass(frozen=True, slots=True)
class NodeOutcome:
    """What happened to one node."""

    node_id: str
    status: str
    output: Any = None
    ports: tuple[str, ...] = ()
    error: str | None = None
    pause: PauseSignal | None = None

    @property
    def claimed(self) -> bool:
        """False when another worker got there first. A lost race is a NO-OP."""
        return self.status != "not-claimed"


@dataclass(frozen=True, slots=True)
class DurableRunResult:
    ok: bool
    outputs: dict[str, Any]
    error: str | None = None
    pause: PauseSignal | None = None

    @property
    def paused(self) -> bool:
        return self.pause is not None


@dataclass(slots=True)
class Coordinator:
    """Drives a graph across per-node checkpoints."""

    graph: FlowGraph
    executors: ExecutorRegistry
    run_key: str
    store: NodeClaimStore = field(default_factory=InMemoryClaimStore)
    initial_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    kinds: NodeKindRegistry | None = None
    on_event: Callable[[RunEvent], None] | None = None

    # -- the two operations ----------------------------------------------

    def advance(self) -> tuple[str, ...]:
        """Which nodes may be dispatched right now.

        Also settles the skip cascade, because a skip is a decision the frontier
        just made and a second caller must not make it again.
        """
        frontier = Frontier.compute(self.graph, self.store.state(self.run_key))
        Frontier.settle_skips(self.store, self.run_key, frontier.skipped)
        return frontier.ready

    def run_node(self, node_id: str, owner: str | None = None) -> NodeOutcome:
        """Claim, execute and checkpoint one node.

        The claim is taken FIRST. Two workers racing for the same node produce
        one execution and one no-op, and the loser learns that from the store
        rather than from a duplicate side effect.
        """
        owner = owner or uuid4().hex
        if not self.store.claim(self.run_key, node_id, owner):
            return NodeOutcome(node_id, "not-claimed")

        replay = replay_up_to(
            self.graph,
            node_id,
            self.executors,
            resume_outputs=self._completed_outputs(),
            initial_inputs=self.initial_inputs,
            on_event=self._forward(node_id),
        )
        result = replay.result

        if node_id in result.outputs:
            output = result.outputs[node_id]
            ports = replay.ports_of(node_id)
            self.store.complete(self.run_key, node_id, output, ports)
            return NodeOutcome(node_id, NodeRunStatus.COMPLETED, output, ports)

        # The node did not produce an output. Three reasons, and they are NOT
        # interchangeable.
        pause = Pause.decode(result.error)
        if pause is not None and pause.node_id == node_id:
            self.store.pause(self.run_key, node_id, result.error or "")
            return NodeOutcome(node_id, NodeRunStatus.PAUSED, pause=pause)

        if is_boundary(result.error):
            # The engine stopped at a node this job does not own BEFORE reaching
            # the target -- so the target was never actually unblocked. That is a
            # frontier bug, not a node failure, and it must not be recorded as
            # one: a FAILED node settles, and settling it would silently skip
            # everything downstream.
            self.store.skip(self.run_key, node_id)
            return NodeOutcome(
                node_id,
                NodeRunStatus.SKIPPED,
                error="replay stopped before reaching this node",
            )

        error = result.error or f"node {node_id} produced no output and no error"
        self.store.fail(self.run_key, node_id, error)
        return NodeOutcome(node_id, NodeRunStatus.FAILED, error=error)

    # -- an in-process driver over the two ------------------------------

    def run_to_completion(self, max_passes: int = 10_000) -> DurableRunResult:
        """Drive the graph here, in this process, one node at a time.

        Every checkpoint is written exactly as a queued run writes it, so a
        crash mid-loop resumes from the same place a crashed worker would.
        """
        pause: PauseSignal | None = None

        for _ in range(max_passes):
            ready = self.advance()
            if not ready:
                break

            for node_id in ready:
                outcome = self.run_node(node_id)
                if outcome.status == NodeRunStatus.PAUSED:
                    # A pause parks the RUN, not just the node: continuing would
                    # run the human gate's siblings while a person is still
                    # deciding. The cohort waits.
                    return DurableRunResult(False, self.outputs(), None, outcome.pause)
                if outcome.status == NodeRunStatus.FAILED:
                    return DurableRunResult(False, self.outputs(), outcome.error)

        state = self.store.state(self.run_key)
        if not Frontier.is_complete(self.graph, state):
            if Frontier.has_work_in_flight(state):
                return DurableRunResult(
                    False,
                    self.outputs(),
                    "the run is waiting on work held elsewhere",
                    pause,
                )
            unsettled = [
                node.id
                for node in self.graph.nodes
                if (state[node.id].status if node.id in state else None)
                not in NodeRunStatus.SETTLED
            ]
            return DurableRunResult(
                False,
                self.outputs(),
                "the run cannot progress; unsettled nodes: " + ", ".join(unsettled),
            )

        failed = [e.error for e in state.values() if e.status == NodeRunStatus.FAILED]
        return DurableRunResult(not failed, self.outputs(), failed[0] if failed else None)

    def outputs(self) -> dict[str, Any]:
        """Checkpointed outputs, in the graph's own node order.

        Ordered by the graph rather than by completion so two runs of the same
        workflow produce comparable output maps even when nodes finished in a
        different order.
        """
        state = self.store.state(self.run_key)
        return {
            node.id: state[node.id].output
            for node in self.graph.nodes
            if node.id in state and state[node.id].status == NodeRunStatus.COMPLETED
        }

    def as_run_result(self) -> RunResult:
        """The checkpointed run, in the shape a single-process run returns."""
        outcome = self.run_to_completion()
        return RunResult(outcome.ok, outcome.outputs, outcome.error)

    # -- internals -------------------------------------------------------

    def _completed_outputs(self) -> dict[str, Any]:
        state = self.store.state(self.run_key)
        return {
            node_id: entry.output
            for node_id, entry in state.items()
            if entry.status == NodeRunStatus.COMPLETED
        }

    def _forward(self, node_id: str) -> Callable[[RunEvent], None] | None:
        """Forward only the events the target node produced.

        A replay re-emits the whole completed prefix. Passing that through would
        show a consumer every node running again on every job -- the run feed
        would report a 20-node workflow as 200 status changes.
        """
        if self.on_event is None:
            return None
        sink = self.on_event

        def forward(event: RunEvent) -> None:
            if event.node_id is None or event.node_id == node_id:
                sink(event)

        return forward
