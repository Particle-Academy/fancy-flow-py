"""Run/step identity, and what the durable driver does with it.

The property under test is one sentence and both halves matter: an idempotency
key must be the SAME on every retry of one logical step and DIFFERENT for every
other execution of the same node. A test that only asserts the first half is
satisfied by a constant, and a constant is the version that silently
deduplicates two legitimate payments into one.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow import (
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    NodeKind,
    NodeKindRegistry,
    PortDescriptor,
)
from fancy_flow.durable import Coordinator, InMemoryClaimStore, RetryPolicy
from fancy_flow.engine.runner import FlowRunner
from fancy_flow.runtime import ExecutionContext, RunIdentity, RunOptions

# -- composition ---------------------------------------------------------


def test_a_top_level_node_keys_on_the_run_and_its_own_id() -> None:
    assert RunIdentity("run_a").step_key("pay") == "run_a:pay"


def test_the_key_does_not_move_with_the_attempt() -> None:
    # The whole point. A key that moves with the attempt creates a second charge
    # on the first timeout, which is the failure it exists to prevent.
    assert RunIdentity("run_a", (), 5).step_key("pay") == RunIdentity("run_a", (), 1).step_key(
        "pay"
    )


def test_a_different_occurrence_is_a_different_step() -> None:
    identity = RunIdentity("run_a")

    assert identity.step_key("pay", 0) != identity.step_key("pay", 1)
    # 0 is a real occurrence -- a truthiness check here collapses iteration 0
    # into the un-iterated key.
    assert identity.step_key("pay", 0) != identity.step_key("pay")


def test_a_different_run_is_a_different_step() -> None:
    assert RunIdentity("run_a").step_key("pay") != RunIdentity("run_b").step_key("pay")


def test_descending_does_not_collide_with_the_parent() -> None:
    parent = RunIdentity("run_a")
    child = parent.descend("billing")

    assert child.step_key("pay") == "run_a:billing/pay"
    assert child.step_key("pay") != parent.step_key("pay")


def test_a_subflow_inherits_the_attempt_and_the_clock() -> None:
    child = RunIdentity("run_a", (), 3, "2026-08-19T00:00:00Z").descend("billing")

    assert child.attempt == 3
    assert child.first_attempt_at == "2026-08-19T00:00:00Z"


def test_a_slash_in_a_node_id_cannot_impersonate_a_nesting_level() -> None:
    flat = RunIdentity("run_a").step_key("a/b")
    nested = RunIdentity("run_a").descend("a").step_key("b")

    assert flat == "run_a:a%2Fb"
    assert nested == "run_a:a/b"
    assert flat != nested


def test_the_escape_character_is_escaped_first() -> None:
    assert RunIdentity("run_a").step_key("a%2Fb") == "run_a:a%252Fb"
    assert RunIdentity("run_a").step_key("a%2Fb") != RunIdentity("run_a").step_key("a/b")


def test_descend_returns_a_new_identity() -> None:
    parent = RunIdentity("run_a")
    parent.descend("billing")

    assert parent.path == ()


def test_it_round_trips_through_a_queue_payload() -> None:
    identity = RunIdentity("run_a", ("billing",), 4, "2026-08-19T00:00:00Z")
    rebuilt = RunIdentity.from_value(identity.to_dict())

    assert rebuilt.step_key("pay") == identity.step_key("pay")
    assert rebuilt.attempt == 4


def test_an_empty_run_key_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        RunIdentity("  ")


# -- the provider's dedup window -----------------------------------------


def test_attempt_one_is_replay_safe_however_long_the_run_was_parked() -> None:
    parked = RunIdentity("run_a", (), 1, "2026-08-01T00:00:00Z")

    assert parked.is_replay_safe(86400, "2026-08-19T00:00:00Z") is True


def test_the_window_boundary_is_inclusive() -> None:
    identity = RunIdentity("run_a", (), 2, "2026-08-18T00:00:00Z")

    assert identity.is_replay_safe(86400, "2026-08-19T00:00:00Z") is True
    assert identity.is_replay_safe(86400, "2026-08-19T00:00:01Z") is False


def test_a_zero_window_is_a_window_not_an_absent_one() -> None:
    at = "2026-08-19T00:00:00Z"

    assert RunIdentity("run_a", (), 2, at).is_replay_safe(0, at) is False
    assert RunIdentity("run_a", (), 1, at).is_replay_safe(0, at) is True


def test_a_null_window_never_expires() -> None:
    identity = RunIdentity("run_a", (), 9, "2020-01-01T00:00:00Z")

    assert identity.is_replay_safe(None, "2026-08-19T00:00:00Z") is True


def test_clock_skew_clamps_to_zero() -> None:
    identity = RunIdentity("run_a", (), 2, "2026-08-19T00:00:10Z")

    assert identity.is_replay_safe(86400, "2026-08-19T00:00:00Z") is True


def test_an_unparseable_timestamp_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="parseable timestamp"):
        RunIdentity("run_a", (), 2, "not a date").is_replay_safe(86400, "2026-08-19T00:00:00Z")


# -- reaching the executor ------------------------------------------------


def test_the_identity_reaches_every_executor() -> None:
    seen: list[str | None] = []
    executors = ExecutorRegistry().bind(
        "*", lambda ctx: seen.append(ctx.run.step_key(ctx.node.id) if ctx.run else None)
    )
    graph = FlowGraph(
        nodes=(FlowNode("a", "k"), FlowNode("b", "k")), edges=(FlowEdge("e", "a", "b"),)
    )

    FlowRunner().run(graph, executors, None, RunOptions(run=RunIdentity("run_a")))

    assert seen == ["run_a:a", "run_a:b"]


def test_the_identity_is_none_when_the_host_supplied_none() -> None:
    # Deliberately NOT auto-minted. A random key per call changes on every
    # whole-run retry, which is the failure this exists to stop -- so a host that
    # has not thought about it gets an honest None and a connector that declines
    # to write blind.
    seen: list[Any] = []
    executors = ExecutorRegistry().bind("*", lambda ctx: seen.append(ctx.run))

    FlowRunner().run(FlowGraph(nodes=(FlowNode("a", "k"),)), executors, None)

    assert seen == [None]


# -- the durable driver ---------------------------------------------------


def _writer(log: list[dict[str, Any]], fail_for: dict[str, int]):
    def execute(ctx: ExecutionContext) -> Any:
        log.append(
            {
                "node": ctx.node.id,
                "key": ctx.run.step_key(ctx.node.id) if ctx.run else None,
                "attempt": ctx.run.attempt if ctx.run else None,
                "first_attempt_at": ctx.run.first_attempt_at if ctx.run else None,
            }
        )
        if fail_for.get(ctx.node.id, 0) > 0:
            fail_for[ctx.node.id] -= 1
            raise RuntimeError("transient")
        return {"charged": True}

    return execute


def test_the_driver_hands_each_node_a_key_derived_from_the_run() -> None:
    log: list[dict[str, Any]] = []
    graph = FlowGraph(nodes=(FlowNode("pay", "writer"),))
    executors = ExecutorRegistry().bind("writer", _writer(log, {}))

    Coordinator(graph=graph, executors=executors, run="run_a").run_to_completion()

    assert [row["key"] for row in log] == ["run_a:pay"]


def test_a_retry_of_one_node_sends_the_same_key() -> None:
    # The money case, and the reason RetryPolicy had to be wired to something:
    # it was a declared field with no read site, so `tries=3` bought one attempt
    # and no error.
    log: list[dict[str, Any]] = []
    graph = FlowGraph(nodes=(FlowNode("pay", "writer"),))
    executors = ExecutorRegistry().bind("writer", _writer(log, {"pay": 1}))

    result = Coordinator(
        graph=graph, executors=executors, run="run_a", retry=RetryPolicy(tries=3)
    ).run_to_completion()

    assert result.ok
    assert len(log) == 2
    assert log[0]["key"] == log[1]["key"]


def test_a_retry_knows_it_is_one_and_keeps_the_first_attempt_clock() -> None:
    log: list[dict[str, Any]] = []
    graph = FlowGraph(nodes=(FlowNode("pay", "writer"),))
    executors = ExecutorRegistry().bind("writer", _writer(log, {"pay": 2}))

    Coordinator(
        graph=graph, executors=executors, run="run_a", retry=RetryPolicy(tries=3)
    ).run_to_completion()

    assert [row["attempt"] for row in log] == [1, 2, 3]
    # The clock is the FIRST claim and must never move: reading a column that is
    # refreshed per attempt would report a retry 25 hours late as seconds old.
    assert len({row["first_attempt_at"] for row in log}) == 1


def test_an_unsafe_to_replay_node_gets_one_attempt_whatever_tries_says() -> None:
    kinds = NodeKindRegistry().register(
        NodeKind(
            name="unsafe_write",
            category="io",
            label="Unsafe write",
            outputs=(PortDescriptor("out"),),
            side_effects="unsafe-to-replay",
        )
    )
    log: list[dict[str, Any]] = []
    graph = FlowGraph(nodes=(FlowNode("push", "unsafe_write"),))
    executors = ExecutorRegistry().bind("unsafe_write", _writer(log, {"push": 5}))

    result = Coordinator(
        graph=graph,
        executors=executors,
        run="run_a",
        retry=RetryPolicy(tries=5),
        kinds=kinds,
    ).run_to_completion()

    assert not result.ok
    assert len(log) == 1


def test_two_nodes_of_one_run_get_different_keys() -> None:
    log: list[dict[str, Any]] = []
    graph = FlowGraph(
        nodes=(FlowNode("a", "writer"), FlowNode("b", "writer")),
        edges=(FlowEdge("e", "a", "b"),),
    )
    executors = ExecutorRegistry().bind("writer", _writer(log, {}))

    Coordinator(graph=graph, executors=executors, run="run_a").run_to_completion()

    assert len({row["key"] for row in log}) == 2


def test_a_node_that_first_runs_after_a_park_is_on_its_own_attempt_one() -> None:
    # The human-gate case. The run was parked for however long; the writing node
    # executes for the FIRST time on resume, so nothing was sent for a provider
    # to have forgotten and the write is replay-safe by construction.
    log: list[dict[str, Any]] = []
    from fancy_flow.durable import DurableApproval, Submissions

    submissions = Submissions()
    graph = FlowGraph(
        nodes=(FlowNode("gate", "human_approval"), FlowNode("pay", "writer")),
        edges=(FlowEdge("e", "gate", "pay", source_handle="approved"),),
    )
    executors = (
        ExecutorRegistry()
        .bind("human_approval", DurableApproval(submissions))
        .bind("writer", _writer(log, {}))
    )
    store = InMemoryClaimStore()

    parked = Coordinator(
        graph=graph, executors=executors, run="run_a", store=store
    ).run_to_completion()

    assert parked.paused
    # Nothing downstream was even claimed: no worker is holding anything for the
    # person, and the call RETURNED rather than waiting.
    assert "pay" not in store.state("run_a")
    assert log == []

    submissions.record("gate", True)
    store.release("run_a", "gate")

    resumed = Coordinator(
        graph=graph, executors=executors, run="run_a", store=store
    ).run_to_completion()

    assert resumed.ok
    assert log[0]["attempt"] == 1
    assert (
        RunIdentity("run_a", (), log[0]["attempt"], log[0]["first_attempt_at"]).is_replay_safe(
            86400
        )
        is True
    )
