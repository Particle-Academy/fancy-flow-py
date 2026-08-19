"""The durable layer: frontier, retries, and human gates that fail closed."""

from __future__ import annotations

import pytest

from fancy_flow import (
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    NodeKind,
    NodeKindRegistry,
    Pause,
    PauseSignal,
    PortDescriptor,
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

    coordinator = Coordinator(graph=graph, executors=executors, run_key="gate")
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

    first = Coordinator(graph=graph, executors=executors, run_key="gate", store=store)
    assert first.run_to_completion().paused

    submissions.record("g", {"answer": "yes"})
    store.release("gate", "g")

    second = Coordinator(graph=graph, executors=executors, run_key="gate", store=store)
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

    result = Coordinator(graph=graph, executors=executors, run_key="auto").run_to_completion()

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

    result = Coordinator(graph=graph, executors=executors, run_key="approve").run_to_completion()
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
