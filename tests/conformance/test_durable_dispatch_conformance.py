"""A queued run hands out one node at a time unless asked -- the shared table, run here.

fancy-flow-php#17 made serial the default in every coordinator: a node goes on
the queue only once the node before it has settled, in declaration order, and a
paused gate keeps its slot. ``flow/durable-dispatch`` pins that as a TRACE of
dispatches, completions, pauses and skips, because a list of batches cannot tell
a node handed out the moment a gate paused from one handed out after a sibling
settled -- and that is the difference between counting a paused node as held
and not.

The simulation is the manifest's, step for step, and it calls the SAME two
functions :meth:`Coordinator.advance` does: :meth:`Frontier.compute` and
:func:`select_dispatch`. No engine runs, no store is written. What is under test
is the decision, not the transport.

The rows that carry the weight:

- ``0001`` -- the default. The whole frontier went out at once before this.
- ``0007`` -- declaration order among what is ready NOW; breadth-first fails it.
- ``0008`` / ``0010`` -- a paused gate holds its slot, alone and under a cap.
  This coordinator does not park the run on a pause, so these are the rows that
  stop a gate's siblings being handed out while a person decides.
- ``0014`` -- a cap is measured against held work, not the size of one batch.
"""

from __future__ import annotations

from typing import Any

import pytest
from fancy_conformance import format_summary, run_table

from fancy_flow import NodeKindRegistry, builtin, import_workflow
from fancy_flow.durable import Frontier, NodeRunStatus, NodeState, select_dispatch

SUITE = "flow/durable-dispatch"


def _dispatch_trace(case: dict[str, Any]) -> dict[str, list[str]]:
    """Import leniently against a local built-in registry, then simulate the run.

    ``max_concurrent`` is handed to :func:`select_dispatch` as the table gives
    it: ``0`` is ``UNLIMITED_CONCURRENCY`` in this runtime too, so there is no
    ``None`` translation to get wrong.
    """
    data = case["input"]
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(data["schema"], lenient=True, registry=registry).graph
    max_concurrent: int = data["maxConcurrent"]
    pauses: list[str] = data.get("pauses") or []
    publishes: dict[str, list[str]] = data.get("publishes") or {}

    state: dict[str, NodeState] = {}
    in_flight: list[str] = []
    trace: list[str] = []

    for _ in range(1000):
        frontier = Frontier.compute(graph, state)
        for node_id in frontier.skipped:
            state[node_id] = NodeState(NodeRunStatus.SKIPPED)
            trace.append(f"skip {node_id}")

        for node_id in select_dispatch(frontier.ready, state, max_concurrent):
            state[node_id] = NodeState(NodeRunStatus.CLAIMED)
            in_flight.append(node_id)
            trace.append(f"dispatch {node_id}")

        if not in_flight:
            break

        node_id = in_flight.pop(0)
        if node_id in pauses:
            state[node_id] = NodeState(NodeRunStatus.PAUSED)
            trace.append(f"pause {node_id}")
        else:
            ports = tuple(publishes.get(node_id, ["out"]))
            state[node_id] = NodeState(NodeRunStatus.COMPLETED, ports=ports)
            trace.append(f"complete {node_id}")

    return {
        "trace": trace,
        "neverDispatched": [node.id for node in graph.nodes if node.id not in state],
    }


def test_matches_the_durable_dispatch_table(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _dispatch_trace)

    # capsys.disabled(): pytest captures a passing test's stdout, so a bare
    # print() never reaches the CI log on exactly the green runs it explains.
    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The vacuity floor: every one of the fourteen rows, and none skipped. A
    # table that loaded no rows has no failures either, and would read as green.
    assert summary["skipped"] == 0, "a row was skipped for Python"
    assert summary["passed"] == 14, f"only {summary['passed']} rows ran; discovery is broken"
