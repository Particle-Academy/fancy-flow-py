"""The per-node durable driver must reach the same answer as a single run.

This is the test that actually pins "how a queued run branches". A durable
driver asks the question from the opposite end -- "what is unblocked?" rather
than "what is next?" -- and the two derivations must not be able to disagree.
Running every golden fixture through BOTH and comparing is what makes that a
test result instead of an argument.

It is also why the coordinator replays through the real engine. If it collected
inputs itself, this suite would be comparing the engine against a copy of the
engine, and both could be wrong together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fancy_flow import FlowRunner, NodeKindRegistry, RunOptions, builtin, import_workflow
from fancy_flow.durable import Coordinator

from .test_graph_fixtures import _normalize

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


def _graph(doc: dict[str, Any]):
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    return import_workflow(doc["schema"], lenient=True, registry=registry).graph


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_durable_driver_agrees_with_the_single_process_run(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = _graph(doc)
    initial = doc.get("initialInputs", {})

    single = FlowRunner().run(
        graph, builtin.executors(), options=RunOptions(initial_inputs=initial)
    )

    durable = Coordinator(
        graph=graph,
        executors=builtin.executors(),
        run=path.stem,
        initial_inputs=initial,
    ).run_to_completion()

    assert durable.ok is single.ok, (
        f"{path.stem}: the drivers disagree about whether the run succeeded "
        f"(single={single.error!r}, durable={durable.error!r})"
    )

    if not single.ok:
        # Failure MESSAGES legitimately differ: a single run reports the first
        # error it hit walking a total order, while the durable driver reports
        # what its frontier could not resolve. Only the verdict is contractual.
        return

    assert _normalize(durable.outputs) == _normalize(single.outputs)


def test_a_dead_branch_settles_instead_of_stalling() -> None:
    """The cascade the frontier exists for.

    A branch routes one way; the other side's node can never be unblocked, so it
    must be SKIPPED -- which settles it, which is what lets the merge point run.
    Without the cascade the run would sit forever waiting on a node no value
    will ever reach.
    """
    doc = json.loads(
        (Path(__file__).parent / "fixtures" / "05-merge-after-decision.json").read_text(
            encoding="utf-8"
        )
    )
    graph = _graph(doc)

    coordinator = Coordinator(
        graph=graph,
        executors=builtin.executors(),
        run="dead-branch",
        initial_inputs=doc["initialInputs"],
    )
    result = coordinator.run_to_completion()

    assert result.ok
    state = coordinator.store.state("dead-branch")
    assert state["b"].status == "skipped", "the untaken branch must settle, not hang"
    assert state["m"].status == "completed", "the merge point must still run"
    assert result.outputs["m"] == {"a": "A"}


def test_a_lost_claim_race_is_a_no_op() -> None:
    """Two workers, one node, one execution.

    The claim is a unique constraint rather than a check, so the loser learns it
    lost from the store -- not from a duplicate side effect.
    """
    doc = json.loads(
        (Path(__file__).parent / "fixtures" / "01-manual-transform-output.json").read_text(
            encoding="utf-8"
        )
    )
    graph = _graph(doc)
    coordinator = Coordinator(
        graph=graph,
        executors=builtin.executors(),
        run="race",
        initial_inputs=doc["initialInputs"],
    )

    ready = coordinator.advance()
    assert ready == ("t",)

    first = coordinator.run_node("t", owner="worker-a")
    second = coordinator.run_node("t", owner="worker-b")

    assert first.status == "completed"
    assert second.claimed is False
    assert second.status == "not-claimed"


def test_a_completed_node_is_republished_not_re_executed() -> None:
    """Resume must not repeat work, and must still route identically.

    The counter proves the second pass did not call the executor again; the
    inputs prove the downstream node still saw the same value, because the
    engine republished the checkpoint on the same ports.
    """
    from fancy_flow import ExecutorRegistry, FlowEdge, FlowGraph, FlowNode

    calls: list[str] = []

    def counted(ctx):  # type: ignore[no-untyped-def]
        calls.append(ctx.node.id)
        return {"n": 1}

    seen: list[Any] = []

    def downstream(ctx):  # type: ignore[no-untyped-def]
        seen.append(ctx.input("in"))
        return "done"

    graph = FlowGraph(
        nodes=(FlowNode("a", "counted"), FlowNode("b", "downstream")),
        edges=(FlowEdge("e", "a", "b"),),
    )
    executors = ExecutorRegistry().bind("counted", counted).bind("downstream", downstream)

    coordinator = Coordinator(graph=graph, executors=executors, run="resume")
    coordinator.run_node("a")
    coordinator.run_node("b")

    assert calls == ["a"], "the upstream node ran exactly once"
    assert seen == [{"n": 1}], "the downstream node saw the republished checkpoint"
