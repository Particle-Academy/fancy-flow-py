"""The durable layer: frontier, retries, and human gates that fail closed."""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow import (
    ExecutionContext,
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    NodeKind,
    NodeKindRegistry,
    Pause,
    PauseSignal,
    PortDescriptor,
    RunEvent,
)
from fancy_flow.durable import (
    Coordinator,
    DurableApproval,
    DurableUserInput,
    Frontier,
    InMemoryClaimStore,
    NodeRunStatus,
    NodeState,
    NotAwaitingHuman,
    RetryPolicy,
    Submissions,
)

# -- the frontier --------------------------------------------------------


def chain() -> FlowGraph:
    return FlowGraph(
        nodes=(FlowNode("a", "k"), FlowNode("b", "k"), FlowNode("c", "k")),
        edges=(FlowEdge("e1", "a", "b"), FlowEdge("e2", "b", "c")),
    )


def test_only_entry_nodes_are_ready_at_the_start() -> None:
    assert Frontier.compute(chain(), {}).ready == ("a",)


def test_a_successor_unblocks_when_its_predecessor_publishes() -> None:
    state = {"a": NodeState(NodeRunStatus.COMPLETED, ports=("out",))}
    assert Frontier.compute(chain(), state).ready == ("b",)


def test_a_claimed_node_blocks_its_successors() -> None:
    """Held, not settled. Dispatching past a node still being worked would run
    a successor with inputs that do not exist yet."""
    state = {"a": NodeState(NodeRunStatus.CLAIMED)}
    assert Frontier.compute(chain(), state).ready == ()


def test_a_skip_cascades_through_the_whole_tail() -> None:
    """Skipping SETTLES a node, which can skip its own successors.

    Without the cascade a dead branch leaves the run stuck on a node no value
    will ever reach.
    """
    state = {"a": NodeState(NodeRunStatus.COMPLETED, ports=("other",))}
    result = Frontier.compute(chain(), state)

    assert result.ready == ()
    assert set(result.skipped) == {"b", "c"}


def test_a_failed_node_settles_so_the_run_does_not_hang() -> None:
    state = {"a": NodeState(NodeRunStatus.FAILED, error="boom")}
    result = Frontier.compute(chain(), state)
    assert set(result.skipped) == {"b", "c"}


def test_a_parallel_join_waits_for_both_sides() -> None:
    """The "all settled" half of the rule.

    It is what distinguishes a genuine parallel join from a merge after a
    decision -- the join must not run on the first arrival.
    """
    graph = FlowGraph(
        nodes=(FlowNode("t", "k"), FlowNode("p1", "k"), FlowNode("p2", "k"), FlowNode("m", "k")),
        edges=(
            FlowEdge("e1", "t", "p1"),
            FlowEdge("e2", "t", "p2"),
            FlowEdge("e3", "p1", "m"),
            FlowEdge("e4", "p2", "m"),
        ),
    )

    half = {
        "t": NodeState(NodeRunStatus.COMPLETED, ports=("out",)),
        "p1": NodeState(NodeRunStatus.COMPLETED, ports=("out",)),
        "p2": NodeState(NodeRunStatus.CLAIMED),
    }
    assert "m" not in Frontier.compute(graph, half).ready

    both = dict(half, p2=NodeState(NodeRunStatus.COMPLETED, ports=("out",)))
    assert Frontier.compute(graph, both).ready == ("m",)


def test_fan_out_returns_every_live_successor() -> None:
    """Nothing says only one branch may be active."""
    graph = FlowGraph(
        nodes=(FlowNode("t", "k"), FlowNode("a", "k"), FlowNode("b", "k")),
        edges=(FlowEdge("e1", "t", "a"), FlowEdge("e2", "t", "b")),
    )
    state = {"t": NodeState(NodeRunStatus.COMPLETED, ports=("out",))}
    assert set(Frontier.compute(graph, state).ready) == {"a", "b"}


def test_a_note_is_settled_by_the_frontier_not_dispatched() -> None:
    """A graph can carry a lot of sticky notes, and each one would otherwise
    cost a queue round trip."""
    graph = FlowGraph(nodes=(FlowNode("n", "@particle-academy/note"),))
    result = Frontier.compute(graph, {})
    assert result.ready == ()
    assert result.skipped == ("n",)


def test_an_edge_reads_the_source_handle_it_names() -> None:
    graph = FlowGraph(
        nodes=(FlowNode("a", "k"), FlowNode("b", "k")),
        edges=(FlowEdge("e", "a", "b", source_handle="true"),),
    )
    assert (
        Frontier.compute(graph, {"a": NodeState(NodeRunStatus.COMPLETED, ports=("false",))}).ready
        == ()
    )
    assert Frontier.compute(
        graph, {"a": NodeState(NodeRunStatus.COMPLETED, ports=("true",))}
    ).ready == ("b",)


def test_work_in_flight_distinguishes_waiting_from_stuck() -> None:
    """An empty frontier means two different things, and only one is a bug."""
    assert Frontier.has_work_in_flight({"a": NodeState(NodeRunStatus.CLAIMED)})
    assert Frontier.has_work_in_flight({"a": NodeState(NodeRunStatus.PAUSED)})
    assert not Frontier.has_work_in_flight({"a": NodeState(NodeRunStatus.COMPLETED)})


# -- the claim store -----------------------------------------------------


def test_a_claim_is_exclusive() -> None:
    store = InMemoryClaimStore()
    assert store.claim("run", "n", "worker-a") is True
    assert store.claim("run", "n", "worker-b") is False


def test_an_owner_can_re_enter_its_own_claim() -> None:
    """What lets a job's retry resume instead of deadlocking against the row it
    wrote itself."""
    store = InMemoryClaimStore()
    store.claim("run", "n", "worker-a")
    assert store.claim("run", "n", "worker-a") is True
    assert store.state("run")["n"].attempts == 2


def test_a_settled_node_cannot_be_reclaimed() -> None:
    store = InMemoryClaimStore()
    store.claim("run", "n", "worker-a")
    store.complete("run", "n", "value", ("out",))
    assert store.claim("run", "n", "worker-a") is False


def test_a_skip_reports_whether_it_settled_the_node() -> None:
    """What lets the Coordinator deliver a skipped node's warning exactly once.

    Two callers deciding from the same frontier both skip the same node. Only
    the first settled it, and only the first may speak for it. A settled row is
    never overwritten -- a stale skip landing on a COMPLETED node would erase
    its output.
    """
    store = InMemoryClaimStore()
    assert store.skip("run", "n") is True
    assert store.skip("run", "n") is False

    store.claim("run", "c", "worker-a")
    assert store.skip("run", "c") is True, "a held claim is not settled"

    store.claim("run", "done", "worker-a")
    store.complete("run", "done", "value", ("out",))
    assert store.skip("run", "done") is False
    assert store.state("run")["done"].status == NodeRunStatus.COMPLETED
    assert store.state("run")["done"].output == "value"


# -- retries -------------------------------------------------------------


def kinds_with(side_effects: str | None) -> NodeKindRegistry:
    return NodeKindRegistry().register(
        NodeKind(
            name="@particle-academy/git_pr_open",
            category="io",
            label="Open PR",
            aliases=("git_pr_open",),
            side_effects=side_effects,
        )
    )


def test_an_unsafe_to_replay_node_gets_one_attempt_regardless() -> None:
    """Retrying it repeats the effect rather than recovering from it --
    `git_pr_open` opens a second pull request."""
    policy = RetryPolicy(tries=5, backoff_seconds=30)
    node = FlowNode("n", "git_pr_open")
    registry = kinds_with("unsafe-to-replay")

    assert policy.tries_for(node, registry) == 1
    assert policy.backoff_for(node, registry) == 0


def test_undeclared_side_effects_take_the_configured_default() -> None:
    """Not assumed safe, and not assumed unsafe.

    Inventing a safety claim on a node author's behalf is how a retry loop ends
    up posting the same webhook twice.
    """
    policy = RetryPolicy(tries=3)
    assert policy.tries_for(FlowNode("n", "git_pr_open"), kinds_with(None)) == 3


def test_a_per_kind_override_matches_any_spelling() -> None:
    """Keying on the literal string makes the override silently stop applying
    the day a kind is renamed."""
    policy = RetryPolicy(tries=1, per_kind={"git_pr_open": 4})
    node = FlowNode("n", "@particle-academy/git_pr_open")
    assert policy.tries_for(node, kinds_with(None)) == 4


# -- run diagnostics under the durable driver ----------------------------


class _Lagging:
    """A claim store whose READS lag its writes.

    Every ``state()`` returns the snapshot it was built with, which is what two
    ``advance()`` calls see when they decide from the same frontier before
    either has settled it. ``skip_returns_none`` makes it an older store, from
    before ``skip`` reported anything.
    """

    def __init__(
        self,
        inner: InMemoryClaimStore,
        run_key: str,
        skip_returns_none: bool = False,
    ) -> None:
        self.inner = inner
        self.snapshot = inner.state(run_key)
        self.skip_returns_none = skip_returns_none

    def claim(self, run_key: str, node_id: str, owner: str) -> bool:
        return self.inner.claim(run_key, node_id, owner)

    def state(self, run_key: str) -> dict[str, NodeState]:
        return dict(self.snapshot)

    def complete(self, run_key: str, node_id: str, output: Any, ports: tuple[str, ...]) -> None:
        self.inner.complete(run_key, node_id, output, ports)

    def skip(self, run_key: str, node_id: str) -> bool | None:
        settled = self.inner.skip(run_key, node_id)
        return None if self.skip_returns_none else settled

    def fail(self, run_key: str, node_id: str, error: str) -> None:
        self.inner.fail(run_key, node_id, error)

    def pause(self, run_key: str, node_id: str, reason: str) -> None:
        self.inner.pause(run_key, node_id, reason)


def undelivered_graph() -> FlowGraph:
    """``s`` publishes ``out``; the only edge into ``o`` reads ``result``."""
    return FlowGraph(
        nodes=(FlowNode("s", "src"), FlowNode("o", "sink")),
        edges=(FlowEdge("e", "s", "o", source_handle="result"),),
    )


def undelivered_executors() -> ExecutorRegistry:
    return ExecutorRegistry().bind("src", lambda ctx: "value").bind("sink", lambda ctx: "sunk")


def warn_logs(events: list[RunEvent]) -> list[RunEvent]:
    return [e for e in events if e.type == RunEvent.LOG and e.level == "warn"]


def test_a_skipped_targets_undelivered_edge_warning_reaches_the_host() -> None:
    """The 0.21.0 known gap: the target never gets a job, so nothing forwarded it."""
    events: list[RunEvent] = []
    result = Coordinator(
        graph=undelivered_graph(),
        executors=undelivered_executors(),
        run="gap",
        on_event=events.append,
    ).run_to_completion()

    assert result.ok
    warnings = warn_logs(events)
    assert [(w.node_id, w.detail) for w in warnings] == [
        ("o", {"edge": "e", "source": "s", "sourceHandle": "result"})
    ]
    assert warnings[0].message == (
        'Edge e reads port "result" from node s, which never publishes it — nothing '
        "would reach o at run time. Available: out. Leave sourceHandle off to read the "
        "node's output."
    )


def test_a_skip_another_caller_already_settled_does_not_warn_again() -> None:
    """Exactly once per skipped node, however many callers reach the decision."""
    graph = undelivered_graph()
    inner = InMemoryClaimStore()
    Coordinator(graph=graph, executors=undelivered_executors(), run="race", store=inner).run_node(
        "s"
    )

    events: list[RunEvent] = []
    coordinator = Coordinator(
        graph=graph,
        executors=undelivered_executors(),
        run="race",
        store=_Lagging(inner, "race"),
        on_event=events.append,
    )

    # Both read the same frontier, so both decide `o` is skipped.
    assert Frontier.compute(graph, coordinator.store.state("race")).skipped == ("o",)
    coordinator.advance()
    coordinator.advance()

    assert inner.state("race")["o"].status == NodeRunStatus.SKIPPED
    assert [w.node_id for w in warn_logs(events)] == ["o"]


def test_a_store_whose_skip_returns_nothing_still_delivers_the_warning() -> None:
    """A store written before ``skip`` reported anything counts as "settled now"."""
    graph = undelivered_graph()
    inner = InMemoryClaimStore()
    Coordinator(graph=graph, executors=undelivered_executors(), run="old", store=inner).run_node(
        "s"
    )

    events: list[RunEvent] = []
    Coordinator(
        graph=graph,
        executors=undelivered_executors(),
        run="old",
        store=_Lagging(inner, "old", skip_returns_none=True),
        on_event=events.append,
    ).advance()

    assert [w.node_id for w in warn_logs(events)] == ["o"]


def test_the_replay_resolves_ports_against_the_coordinators_registry() -> None:
    """``kinds`` was consulted for retries and ignored by the replay.

    So a node with no declared outputs published on the SHARED registry's idea
    of its kind -- here, nothing, so ``out`` -- and the edge reading the port its
    own registry declares never lit. A different run from the same graph, with
    the registry the host passed sitting right there.
    """
    registry = NodeKindRegistry().register(
        NodeKind(
            name="two_port_under_test",
            category="logic",
            label="Two ports",
            outputs=(PortDescriptor("yes"), PortDescriptor("no")),
        )
    )
    graph = FlowGraph(
        nodes=(FlowNode("t", "two_port_under_test"), FlowNode("n", "k")),
        edges=(FlowEdge("e", "t", "n", source_handle="yes"),),
    )
    executors = (
        ExecutorRegistry()
        .bind("two_port_under_test", lambda ctx: "v")
        .bind("k", lambda ctx: ctx.inputs.get("in"))
    )
    coordinator = Coordinator(graph=graph, executors=executors, run="kinds", kinds=registry)

    result = coordinator.run_to_completion()

    assert coordinator.store.state("kinds")["t"].ports == ("yes", "no")
    assert result.ok
    assert result.outputs == {"t": "v", "n": "v"}


# -- sibling jobs out of order -------------------------------------------


def siblings_graph() -> FlowGraph:
    """``t`` fans out to ``a`` and ``b``; ``a`` comes first in topological order."""
    return FlowGraph(
        nodes=(FlowNode("t", "rec"), FlowNode("a", "rec"), FlowNode("b", "rec")),
        edges=(FlowEdge("e1", "t", "a"), FlowEdge("e2", "t", "b")),
    )


def test_a_node_whose_earlier_sibling_has_not_finished_runs_instead_of_skipping() -> None:
    """Siblings that become ready together are dispatched together.

    On real workers nothing orders their jobs: ``b``'s job can start while
    ``a`` is still running. ``b``'s replay walks the engine's topological order,
    and ``a`` -- unfinished, so not resumed -- came first. The fence used to
    ABORT the replay there, and ``run_node`` read "the replay ended without
    running me" as "the engine decided I am unreachable": ``b`` was recorded
    SKIPPED, never ran, and the run completed as a success with half its work
    missing.

    This runs ``b``'s job first, on purpose. The replay reads only completed
    outputs, so an ``a`` that is claimed and still running looks exactly like
    this unclaimed one.
    """
    ran: list[tuple[str, Any]] = []

    def record(ctx: ExecutionContext) -> str:
        ran.append((ctx.node.id, ctx.inputs.get("in")))
        return ctx.node.id

    coordinator = Coordinator(
        graph=siblings_graph(), executors=ExecutorRegistry().bind("rec", record), run="siblings"
    )

    assert coordinator.advance() == ("t",)
    assert coordinator.run_node("t").status == NodeRunStatus.COMPLETED
    assert coordinator.advance() == ("a", "b")

    outcome = coordinator.run_node("b")

    assert (outcome.status, outcome.error) == (NodeRunStatus.COMPLETED, None)
    assert coordinator.store.state("siblings")["b"].status == NodeRunStatus.COMPLETED
    # Its input is its own settled source's, never a fenced sibling's.
    assert ran == [("t", None), ("b", "t")]

    # The rest drains normally, and every node ran exactly once.
    result = coordinator.run_to_completion()

    assert result.ok
    assert result.outputs == {"t": "t", "a": "a", "b": "b"}
    assert ran == [("t", None), ("b", "t"), ("a", "t")]


def test_run_to_completion_runs_siblings_declared_out_of_topological_order() -> None:
    """The same defect, with no second worker anywhere.

    The frontier reports ready nodes in the order the graph DECLARES them. The
    engine walks siblings in the order their EDGES are listed. Here ``b`` is
    declared first and ``a``'s edge is listed first, so ``run_to_completion``
    started ``b`` while ``a`` had not run. The aborting fence skipped ``b``, and
    the run returned ``ok`` without it. A graph whose edges were drawn in a
    different order from its nodes is the ordinary case, not a contrived one.
    """
    ran: list[str] = []

    def record(ctx: ExecutionContext) -> str:
        ran.append(ctx.node.id)
        return ctx.node.id

    graph = FlowGraph(
        nodes=(FlowNode("t", "rec"), FlowNode("b", "rec"), FlowNode("a", "rec")),
        edges=(FlowEdge("e1", "t", "a"), FlowEdge("e2", "t", "b")),
    )
    coordinator = Coordinator(
        graph=graph, executors=ExecutorRegistry().bind("rec", record), run="declared"
    )

    result = coordinator.run_to_completion()

    assert result.ok
    assert result.outputs == {"t": "t", "b": "b", "a": "a"}
    assert sorted(ran) == ["a", "b", "t"]


def test_a_target_the_engine_skips_is_recorded_skipped_not_failed() -> None:
    """The engine's own verdict, whichever node happens to follow the target.

    A replay that finishes without the target's output means the engine found
    every inbound edge dead. That is a skip, and it was one only by accident:
    the old fence aborted at the NEXT node, so a dead target with a later node
    in topological order read as a boundary and was skipped, while a dead
    target that came LAST finished the replay cleanly and was recorded FAILED.
    The frontier would not dispatch either; the verdict must not depend on it.
    """
    for trailing in (True, False):
        nodes = [FlowNode("s", "rec"), FlowNode("dead", "rec")]
        edges = [FlowEdge("e1", "s", "dead", source_handle="never")]
        if trailing:
            nodes.append(FlowNode("later", "rec"))
            edges.append(FlowEdge("e2", "s", "later"))

        coordinator = Coordinator(
            graph=FlowGraph(nodes=tuple(nodes), edges=tuple(edges)),
            executors=ExecutorRegistry().bind("rec", lambda ctx: "v"),
            run=f"dead-{trailing}",
        )
        coordinator.run_node("s")

        outcome = coordinator.run_node("dead")

        assert outcome.status == NodeRunStatus.SKIPPED, f"trailing={trailing}"
        assert (
            coordinator.store.state(f"dead-{trailing}")["dead"].status == NodeRunStatus.SKIPPED
        ), f"trailing={trailing}"


# -- human gates ---------------------------------------------------------


def gate_graph(kind: str) -> FlowGraph:
    outputs = (
        (PortDescriptor("approved"), PortDescriptor("denied"))
        if kind == "human_approval"
        else (PortDescriptor("out"),)
    )
    return FlowGraph(
        nodes=(FlowNode("t", "seed"), FlowNode("g", kind, outputs=outputs)),
        edges=(FlowEdge("e", "t", "g"),),
    )


def test_a_pre_filled_input_does_not_satisfy_a_user_input_gate() -> None:
    """The fail-closed rule, and the bug it fixes.

    A gate pauses because it IS a human node, not because its input port
    happens to be empty. Deciding from the input ran the flow straight past the
    person it was waiting for, silently, with the run reporting success.
    """
    submissions = Submissions()
    graph = gate_graph("user_input")
    executors = (
        ExecutorRegistry()
        .bind("seed", lambda ctx: {"values": {"answer": "already here"}})
        .bind("user_input", DurableUserInput(submissions))
    )

    coordinator = Coordinator(graph=graph, executors=executors, run="gate")
    result = coordinator.run_to_completion()

    assert result.paused
    assert result.pause == PauseSignal("g", "input", {"title": "Need your input", "fields": []})
    assert "g" not in result.outputs


def test_a_recorded_answer_resumes_the_gate() -> None:
    submissions = Submissions()
    graph = gate_graph("user_input")
    executors = (
        ExecutorRegistry()
        .bind("seed", lambda ctx: {})
        .bind("user_input", DurableUserInput(submissions))
    )
    store = InMemoryClaimStore()

    first = Coordinator(graph=graph, executors=executors, run="gate", store=store)
    assert first.run_to_completion().paused

    submissions.record("g", {"answer": "yes"})
    store.release("gate", "g")

    second = Coordinator(graph=graph, executors=executors, run="gate", store=store)
    resumed = second.run_to_completion()

    assert resumed.ok
    assert resumed.outputs["g"] == {"answer": "yes"}


def test_auto_answer_from_input_is_opt_in_and_works_when_opted_into() -> None:
    submissions = Submissions()
    graph = FlowGraph(
        nodes=(
            FlowNode("t", "seed"),
            FlowNode("g", "user_input", config={"autoAnswerFromInput": True}),
        ),
        # The upstream value has to arrive on the `values` handle, which is what
        # "an upstream node already produced the answer" means.
        edges=(FlowEdge("e", "t", "g", target_handle="values"),),
    )
    executors = (
        ExecutorRegistry()
        .bind("seed", lambda ctx: {"answer": "from upstream"})
        .bind("user_input", DurableUserInput(submissions))
    )

    result = Coordinator(graph=graph, executors=executors, run="auto").run_to_completion()

    assert result.ok
    assert result.outputs["g"] == {"answer": "from upstream"}


def test_an_approval_gate_pauses_even_with_an_approved_flag_on_its_input() -> None:
    """Weigh this one harder than a form: auto-answering means the graph, not a
    person, approves."""
    submissions = Submissions()
    graph = FlowGraph(
        nodes=(
            FlowNode("t", "seed"),
            FlowNode(
                "g",
                "human_approval",
                outputs=(PortDescriptor("approved"), PortDescriptor("denied")),
            ),
        ),
        edges=(FlowEdge("e", "t", "g", target_handle="approved"),),
    )
    executors = (
        ExecutorRegistry()
        .bind("seed", lambda ctx: True)
        .bind("human_approval", DurableApproval(submissions))
    )

    result = Coordinator(graph=graph, executors=executors, run="approve").run_to_completion()
    assert result.paused
    assert result.pause is not None
    assert result.pause.is_approval


def test_recording_an_answer_for_the_wrong_node_raises() -> None:
    """A queued answer for a node that never paused is a write nobody reads --
    and from the outside it looks exactly like a submission that worked."""
    submissions = Submissions()
    submissions.park("g")

    with pytest.raises(NotAwaitingHuman):
        submissions.record("somewhere-else", {"answer": 1})


def test_recording_an_answer_when_nothing_is_waiting_raises() -> None:
    with pytest.raises(NotAwaitingHuman):
        Submissions().record("g", {"answer": 1})


def test_an_empty_submission_is_a_real_answer() -> None:
    """A truthiness test pauses forever on an empty form."""
    submissions = Submissions()
    submissions.park("g")
    submissions.record("g", {})
    assert submissions.answered("g") is True
    assert submissions.answer("g") == {}


# -- the pause wire format ----------------------------------------------


def test_a_pause_round_trips_through_its_reason_string() -> None:
    signal = PauseSignal("node-1", "input", {"fields": ["a"]})
    decoded = Pause.decode(Pause.encode(signal))
    assert decoded == signal


def test_a_node_id_containing_a_colon_survives() -> None:
    """Why the payload is JSON and not delimited fields.

    A positional encoding that breaks on user data is the kind of bug that only
    ever shows up in someone else's graph.
    """
    signal = PauseSignal("group:node:7", "approval")
    assert Pause.decode(Pause.encode(signal)) == signal


def test_legacy_pause_prefixes_stay_decodable_forever() -> None:
    """These are sitting in the error column of every run that paused under an
    older version. A resume path that only works for new runs strands
    everything already in flight."""
    assert Pause.decode("awaiting-approval:node-1") == PauseSignal("node-1", "approval")
    assert Pause.decode("awaiting-input:node-1") == PauseSignal("node-1", "input")


def test_a_real_failure_is_not_a_pause() -> None:
    assert Pause.decode("Connection refused") is None
    assert Pause.is_pause(None) is False


def test_a_corrupt_pause_payload_is_not_given_an_invented_node_id() -> None:
    assert Pause.decode(Pause.PREFIX + "{not json") is None
    assert Pause.decode(Pause.PREFIX + '{"awaiting":"input"}') is None
