"""``for_each`` forwards what its item lane EMITS, as fancy-flow-php does (#25).

Each iteration ran its lane with no event sink, so every status, message and
log a lane node emitted was dropped. A host collecting events (a lab snapshot,
a progress feed) saw nothing from inside the loop: the PHP twin recorded five
per-item events for five items, and this engine recorded none.

The rule is PHP's ``RunEvent::forParent``: node-scoped events reach the parent
with their node id re-addressed as ``<for_each id>/<index>/<node id>``, and the
lane's own run-start / run-end / run-error are NOT forwarded.
"""

from __future__ import annotations

from fancy_flow import ExecutorRegistry, FlowEdge, FlowGraph, FlowNode, FlowRunner, RunOptions
from fancy_flow.nodes import logic
from fancy_flow.runtime.events import RunEvent


def _recorder(ctx):
    ctx.emit(RunEvent.log("info", "recorded", ctx.node.id, {"value": ctx.input("in")}))
    return None


def test_events_emitted_inside_each_iteration_reach_the_host_re_addressed_per_item() -> None:
    graph = FlowGraph(
        (
            FlowNode("each", "@particle-academy/for_each", config={"source": "{{ in.rows }}"}),
            FlowNode("rec", "recorder"),
        ),
        (FlowEdge("e1", "each", "rec", source_handle="item"),),
    )
    executors = (
        ExecutorRegistry()
        .bind("@particle-academy/for_each", logic.for_each)
        .bind("recorder", _recorder)
    )

    seen: list[RunEvent] = []
    result = FlowRunner().run(
        graph,
        executors,
        seen.append,
        RunOptions(initial_inputs={"each": {"in": {"rows": ["a", "b", "c"]}}}),
    )

    assert result.ok, result.error
    logs = [e for e in seen if e.type == RunEvent.LOG and e.message == "recorded"]
    assert [e.node_id for e in logs] == ["each/0/rec", "each/1/rec", "each/2/rec"]
    assert [e.detail["value"] for e in logs] == ["a", "b", "c"]

    # The result's own event list carries them too: that is what a host reads
    # when it did not pass a sink.
    assert [
        e.node_id for e in result.events if e.type == RunEvent.LOG and e.message == "recorded"
    ] == [
        "each/0/rec",
        "each/1/rec",
        "each/2/rec",
    ]

    assert any(
        e.type == RunEvent.NODE_STATUS and e.node_id == "each/1/rec" and e.status == "done"
        for e in seen
    )

    # The lanes' own run boundaries are not the parent's.
    assert len([e for e in seen if e.type == RunEvent.RUN_START]) == 1
    assert len([e for e in seen if e.type == RunEvent.RUN_END]) == 1
