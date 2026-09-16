"""Which output ports a node lights, and what each carries — the shared table, run here.

Until fancy-flow-php#18 the engine knew two answers: ``__port`` / ``branch`` lit
exactly one port, and anything else lit EVERY declared port. There was no way to
say "these two of five", so a router that matched two lanes had to drop the rest
of the work or wake lanes nobody asked for. ``__ports`` is the third answer, and
this table is what keeps all four runtimes giving it identically.

The rows assert the ``node-output`` EVENTS rather than ``_activated_ports``'
return value. That function is private in every runtime, and the events are what
a consumer -- and the durable layer, which reads activated ports straight off
them -- actually observes. Asserting the private function would also let this
file pass while the events it feeds were wrong.

Row 0303 (an explicitly empty ``outputs``) is skipped for **node**, not here:
this runtime honours the three states and publishes nothing, which is the whole
point of the row.
"""

from __future__ import annotations

from typing import Any

import pytest
from fancy_conformance import format_summary, run_table

from fancy_flow import (
    ExecutorRegistry,
    FlowGraph,
    FlowNode,
    FlowRunner,
    PortDescriptor,
    RunEvent,
)

SUITE = "flow/port-activation"


def _run_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a one-node graph and report what the node published, in order.

    The kind is ``hostRouter``, which no runtime ships, and no registry is
    handed to the runner. Both are deliberate: the declared-port fallback
    reaches for the KIND's ports before falling back to ``out``, so naming a
    builtin would quietly assert the builtin's ports instead of the rule under
    test.
    """
    declared = case["input"]["declaredOutputs"]
    result = case["input"]["result"]

    node = FlowNode(
        "r",
        type="hostRouter",
        outputs=None if declared is None else [PortDescriptor(p) for p in declared],
    )
    graph = FlowGraph((node,), ())

    executors = ExecutorRegistry().bind("hostRouter", lambda _ctx: result)
    run = FlowRunner().run(graph, executors)

    # Emission order, never sorted: a map-shaped `__ports` lights its ports in
    # the order the map declares them, and row 0105 is only an assertion at all
    # because this list stays in the order the engine produced it.
    return [
        {"port": event.port_id, "value": event.value}
        for event in run.events
        if event.type == RunEvent.NODE_OUTPUT and event.node_id == "r"
    ]


def test_matches_the_port_activation_table(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _run_case)

    # capsys.disabled(): pytest captures a passing test's stdout, so a bare
    # print() never reaches the CI log on exactly the green runs it explains --
    # and this table's summary is where a reader sees WHICH row node skips.
    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The vacuity floor, just under the twelve rows. One row expects NO ports at
    # all, so a table that loaded nothing has no failures either and would read
    # as green without this.
    assert summary["passed"] > 10, f"only {summary['passed']} rows ran; discovery is broken"
