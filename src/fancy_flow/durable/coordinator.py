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
from ..registry.registry import NodeKindRegistry, default_registry
from ..runtime.events import RunEvent
from ..runtime.identity import RunIdentity
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
    #: 1-based attempt this execution ran as. 0 when the claim was lost.
    attempt: int = 0
    #: True when this attempt failed and the policy still allows another.
    #:
    #: The claim row is left CLAIMED in that case, deliberately: a queue adapter
    #: re-dispatches the job with the SAME owner token and the retry re-enters
    #: the claim it already holds. Recording FAILED here instead would settle
    #: the node, which SKIPS everything downstream -- a run reporting a tidy
    #: finish having done half its work.
    retryable: bool = False

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
    #: The run's stable identity. A bare string is taken as the run key.
    #:
    #: Required, not defaulted: a durable run without a stable key cannot key an
    #: idempotent write, and minting one per construction would hand a retrying
    #: host a different key each time.
    run: RunIdentity | str
    store: NodeClaimStore = field(default_factory=InMemoryClaimStore)
    initial_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    kinds: NodeKindRegistry | None = None
    on_event: Callable[[RunEvent], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run", RunIdentity.from_value(self.run))

    @property
    def run_key(self) -> str:
        return self.identity.run_key

    @property
    def identity(self) -> RunIdentity:
        """The run identity, whatever spelling it was constructed with."""
        return self.run if isinstance(self.run, RunIdentity) else RunIdentity.from_value(self.run)

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

        row = self.store.state(self.run_key).get(node_id)
        # Per NODE, off the claim row -- not per run. This is the only place the
        # attempt and the first-attempt clock are EXACT rather than
        # conservative, and they are what a writing connector checks a provider's
        # idempotency window against.
        identity = (
            self.identity.with_attempt(row.attempts, row.first_attempt_at)
            if row is not None
            else self.identity
        )
        attempt = identity.attempt

        replay = replay_up_to(
            self.graph,
            node_id,
            self.executors,
            resume_outputs=self._completed_outputs(),
            initial_inputs=self.initial_inputs,
            on_event=self._forward(node_id),
            run=identity,
        )
        result = replay.result

        if node_id in result.outputs:
            output = result.outputs[node_id]
            ports = replay.ports_of(node_id)
            self.store.complete(self.run_key, node_id, output, ports)
            return NodeOutcome(node_id, NodeRunStatus.COMPLETED, output, ports, attempt=attempt)

        # The node did not produce an output. Three reasons, and they are NOT
        # interchangeable.
        pause = Pause.decode(result.error)
        if pause is not None and pause.node_id == node_id:
            self.store.pause(self.run_key, node_id, result.error or "")
            return NodeOutcome(node_id, NodeRunStatus.PAUSED, pause=pause, attempt=attempt)

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
                attempt=attempt,
            )

        error = result.error or f"node {node_id} produced no output and no error"

        if attempt < self._tries_for(node_id):
            # Leave the row CLAIMED so the same owner can re-enter it. This is
            # what `fancy-flow-php` does by only marking FAILED from the job's
            # `failed()` hook -- a row a worker still holds must not settle
            # mid-retry, because settling it skips everything downstream.
            return NodeOutcome(
                node_id, NodeRunStatus.FAILED, error=error, attempt=attempt, retryable=True
            )

        self.store.fail(self.run_key, node_id, error)
        return NodeOutcome(node_id, NodeRunStatus.FAILED, error=error, attempt=attempt)

    def _tries_for(self, node_id: str) -> int:
        """How many attempts this node gets, from the policy the host configured.

        Until now :attr:`retry` was a declared field with no read site anywhere
        in the package -- a policy object that looked wired and was not, so a
        host setting ``tries=3`` got one attempt and no error. ``unsafe-to-replay``
        was still honoured only because nothing retried at all.
        """
        node = next((n for n in self.graph.nodes if n.id == node_id), None)
        if node is None:
            return 1
        return self.retry.tries_for(node, self.kinds or default_registry())

    # -- an in-process driver over the two ------------------------------

    def run_to_completion(self, max_passes: int = 10_000) -> DurableRunResult:
        """Drive the graph here, in this process, one node at a time.

        Every checkpoint is written exactly as a queued run writes it, so a
        crash mid-loop resumes from the same place a crashed worker would.

        Retries honour :class:`~fancy_flow.durable.retry.RetryPolicy`: a node
        declaring ``unsafe-to-replay`` gets one attempt whatever the policy
        says, and a retry re-enters the SAME claim with the SAME owner token --
        so the step key it derives is unchanged, which is what makes the retry
        idempotent rather than duplicative.

        Nothing here sleeps, polls or waits on a person: a paused node RETURNS.
        """
        pause: PauseSignal | None = None

        for _ in range(max_passes):
            ready = self.advance()
            if not ready:
                break

            for node_id in ready:
                outcome = self._run_node_with_retries(node_id)
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

    def _run_node_with_retries(self, node_id: str) -> NodeOutcome:
        """One owner token for every attempt of this node.

        The token is what re-enters the claim rather than losing the race to
        itself -- and it is why the step key a retrying node derives is the same
        one its first attempt sent.
        """
        owner = uuid4().hex
        outcome = self.run_node(node_id, owner)
        while outcome.retryable:
            outcome = self.run_node(node_id, owner)
        return outcome

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
