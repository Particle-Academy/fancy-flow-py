"""The engine's rules, each pinned by the failure it prevents."""

from __future__ import annotations

import asyncio

import pytest

from fancy_flow import (
    AbortController,
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    FlowRunner,
    NodeKind,
    NodeKindRegistry,
    NodeStatus,
    Port,
    PortDescriptor,
    RunAborted,
    RunEvent,
    RunOptions,
)


def graph(nodes, edges=()):
    return FlowGraph(tuple(nodes), tuple(edges))


def test_runs_nodes_in_topological_order() -> None:
    order: list[str] = []
    executors = ExecutorRegistry().bind("*", lambda ctx: order.append(ctx.node.id))

    result = FlowRunner().run(
        graph(
            [FlowNode("c"), FlowNode("a"), FlowNode("b")],
            [FlowEdge("e1", "a", "b"), FlowEdge("e2", "b", "c")],
        ),
        executors,
    )

    assert result.ok
    assert order == ["a", "b", "c"]


def test_a_cycle_aborts_before_anything_runs() -> None:
    ran: list[str] = []
    executors = ExecutorRegistry().bind("*", lambda ctx: ran.append(ctx.node.id))

    result = FlowRunner().run(
        graph(
            [FlowNode("a"), FlowNode("b")],
            [FlowEdge("e1", "a", "b"), FlowEdge("e2", "b", "a")],
        ),
        executors,
    )

    assert result.ok is False
    assert "Cycle detected" in (result.error or "")
    assert ran == [], "a cycle must be caught up front, not discovered mid-run"


def test_a_merge_point_runs_on_one_active_edge() -> None:
    """The #1 bug. Requiring ALL incoming edges active skipped every merge."""
    executors = (
        ExecutorRegistry()
        .bind("decide", lambda ctx: Port.branch("true", {"v": 1}))
        .bind("side", lambda ctx: ctx.input("in"))
        .bind("join", lambda ctx: dict(ctx.inputs))
    )

    result = FlowRunner().run(
        graph(
            [
                FlowNode("d", "decide", outputs=(PortDescriptor("true"), PortDescriptor("false"))),
                FlowNode("a", "side"),
                FlowNode("b", "side"),
                FlowNode("m", "join"),
            ],
            [
                FlowEdge("e1", "d", "a", source_handle="true"),
                FlowEdge("e2", "d", "b", source_handle="false"),
                FlowEdge("e3", "a", "m", target_handle="a"),
                FlowEdge("e4", "b", "m", target_handle="b"),
            ],
        ),
        executors,
    )

    assert result.ok
    assert result.outputs["m"] == {"a": {"v": 1}}, "the merge ran, and saw only the live side"


def test_a_dead_edge_cannot_clobber_a_live_one_on_the_same_handle() -> None:
    """The other half of #1, and the one that reported success while being wrong.

    Ordering the dead edge LAST is what makes it bite: an unconditional assign
    would overwrite the live value with nothing.
    """
    executors = (
        ExecutorRegistry()
        .bind("decide", lambda ctx: Port.branch("true", {"v": 1}))
        .bind("pass", lambda ctx: ctx.node.id)
        .bind("sink", lambda ctx: ctx.input("in"))
    )

    result = FlowRunner().run(
        graph(
            [
                FlowNode("d", "decide", outputs=(PortDescriptor("true"), PortDescriptor("false"))),
                FlowNode("a", "pass"),
                FlowNode("b", "pass"),
                FlowNode("o", "sink"),
            ],
            [
                FlowEdge("e1", "d", "a", source_handle="true"),
                FlowEdge("e2", "d", "b", source_handle="false"),
                FlowEdge("e3", "a", "o"),
                FlowEdge("e4", "b", "o"),
            ],
        ),
        executors,
    )

    assert result.outputs["o"] == "a"


def test_port_conventions() -> None:
    published: list[tuple[str, object]] = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_OUTPUT:
            published.append((f"{event.node_id}:{event.port_id}", event.value))

    executors = (
        ExecutorRegistry()
        .bind("only", lambda ctx: Port.only("x", 1))
        .bind("branch", lambda ctx: Port.branch("y", 2))
        .bind("branch_bare", lambda ctx: {"branch": "z"})
        .bind("plain", lambda ctx: "v")
    )

    FlowRunner().run(
        graph(
            [
                FlowNode("a", "only"),
                FlowNode("b", "branch"),
                FlowNode("c", "branch_bare"),
                FlowNode("d", "plain", outputs=(PortDescriptor("p"), PortDescriptor("q"))),
            ]
        ),
        executors,
        sink,
    )

    assert ("a:x", 1) in published
    assert ("b:y", 2) in published
    # `r.value ?? r` -- an omitted value carries the whole result object.
    assert ("c:z", {"branch": "z"}) in published
    # A declared multi-port node publishes on every one of them.
    assert ("d:p", "v") in published
    assert ("d:q", "v") in published


def test_an_explicitly_empty_output_list_publishes_nothing() -> None:
    """The three-state port field. `None` is not `()`.

    A terminal node declaring no ports must publish nothing; collapsing the two
    states would make it publish on `out` and quietly reactivate dead chains.
    """
    published: list[str] = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_OUTPUT:
            published.append(str(event.node_id))

    FlowRunner().run(
        graph([FlowNode("terminal", "x", outputs=()), FlowNode("normal", "x")]),
        ExecutorRegistry().bind("x", lambda ctx: 1),
        sink,
    )

    assert published == ["normal"]


def test_the_kind_ports_fallback_applies_when_a_node_declares_none() -> None:
    """A hand-written schema omits ports; the kind still knows them.

    Without this a branch node collapses to a single `out` here while routing
    correctly on Node -- breaking the same-JSON-same-outputs guarantee.
    """
    registry = NodeKindRegistry().register(
        NodeKind(
            name="two_ports",
            category="logic",
            label="Two",
            outputs=(PortDescriptor("left"), PortDescriptor("right")),
        )
    )
    published: list[str] = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_OUTPUT:
            published.append(str(event.port_id))

    FlowRunner(kinds=registry).run(
        graph([FlowNode("n", "two_ports")]),
        ExecutorRegistry().bind("two_ports", lambda ctx: 1),
        sink,
    )

    assert published == ["left", "right"]


def test_an_empty_kind_port_list_is_not_adopted() -> None:
    """A terminal KIND must not cut every chain through it.

    The kind declares `outputs: []`, meaning "terminal on the canvas". Consuming
    that literally as the fallback would publish zero ports where the historical
    fallback published `out`.
    """
    registry = NodeKindRegistry().register(
        NodeKind(name="terminal_kind", category="output", label="T", outputs=())
    )
    published: list[str] = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_OUTPUT:
            published.append(str(event.port_id))

    FlowRunner(kinds=registry).run(
        graph([FlowNode("n", "terminal_kind")]),
        ExecutorRegistry().bind("terminal_kind", lambda ctx: 1),
        sink,
    )

    assert published == ["out"]


@pytest.mark.parametrize("kind_id", ["note", "@particle-academy/note", "@fancy/note"])
def test_a_note_never_executes_under_any_of_its_ids(kind_id: str) -> None:
    ran: list[str] = []
    result = FlowRunner().run(
        graph([FlowNode("n", kind_id)]),
        ExecutorRegistry().bind("*", lambda ctx: ran.append(ctx.node.id)),
    )
    assert result.ok
    assert ran == []


def test_a_third_party_note_is_not_mistaken_for_the_builtin() -> None:
    """Kind matching is deliberately narrow.

    `@acme/note` is somebody else's node and must run.
    """
    ran: list[str] = []
    FlowRunner().run(
        graph([FlowNode("n", "@acme/note")]),
        ExecutorRegistry().bind("*", lambda ctx: ran.append(ctx.node.id)),
    )
    assert ran == ["n"]


def test_a_missing_executor_fails_the_run_by_name() -> None:
    result = FlowRunner().run(graph([FlowNode("n", "nope")]), ExecutorRegistry())
    assert result.ok is False
    assert "No executor registered for kind=nope" in (result.error or "")


def test_an_executor_abort_ends_the_run_with_its_reason() -> None:
    result = FlowRunner().run(
        graph([FlowNode("n", "x")]),
        ExecutorRegistry().bind("x", lambda ctx: ctx.abort("nope")),
    )
    assert result.ok is False
    assert result.error == "nope"


def test_a_host_abort_signal_propagates_out_of_the_run() -> None:
    """Distinct from an executor's abort.

    A cancelled run has no result to report; a failed one does. Returning
    ok=False for a cancellation would make "the user pressed stop" look like
    "the workflow is broken".
    """
    controller = AbortController()
    controller.abort("user cancelled")

    with pytest.raises(RunAborted):
        FlowRunner().run(
            graph([FlowNode("n", "x")]),
            ExecutorRegistry().bind("x", lambda ctx: 1),
            options=RunOptions(signal=controller.signal),
        )


def test_a_timeout_stops_the_run_between_nodes() -> None:
    import time

    def slow(ctx):  # type: ignore[no-untyped-def]
        time.sleep(0.02)
        return 1

    result = FlowRunner().run(
        graph(
            [FlowNode("a", "slow"), FlowNode("b", "slow")],
            [FlowEdge("e", "a", "b")],
        ),
        ExecutorRegistry().bind("slow", slow),
        options=RunOptions(timeout_ms=1),
    )

    assert result.ok is False
    assert "timed out" in (result.error or "")
    assert "b" not in result.outputs


def test_resume_republishes_without_re_executing() -> None:
    calls: list[str] = []
    executors = (
        ExecutorRegistry()
        .bind("first", lambda ctx: calls.append("first") or {"v": 1})
        .bind("second", lambda ctx: ctx.input("in"))
    )

    result = FlowRunner().run(
        graph(
            [FlowNode("a", "first"), FlowNode("b", "second")],
            [FlowEdge("e", "a", "b")],
        ),
        executors,
        options=RunOptions(resume_outputs={"a": {"v": 9}}),
    )

    assert calls == []
    assert result.outputs["b"] == {"v": 9}


def test_a_resumed_node_reports_itself_as_resumed() -> None:
    texts: list[str | None] = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_STATUS and event.status == NodeStatus.DONE:
            texts.append(event.text)

    FlowRunner().run(
        graph([FlowNode("a", "x"), FlowNode("b", "x")]),
        ExecutorRegistry().bind("x", lambda ctx: 1),
        sink,
        RunOptions(resume_outputs={"a": 1}),
    )

    assert texts == ["resumed", None]


def test_the_async_runner_awaits_awaitable_executors() -> None:
    async def slow(ctx):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return "async"

    result = asyncio.run(
        FlowRunner().arun(
            graph([FlowNode("n", "slow")]),
            ExecutorRegistry().bind("slow", slow),
        )
    )

    assert result.ok
    assert result.outputs["n"] == "async"


def test_the_async_runner_still_runs_synchronous_executors() -> None:
    """A graph mixing both is the normal case, not the exception."""

    async def a(ctx):  # type: ignore[no-untyped-def]
        return 1

    result = asyncio.run(
        FlowRunner().arun(
            graph(
                [FlowNode("a", "async_kind"), FlowNode("b", "sync_kind")],
                [FlowEdge("e", "a", "b")],
            ),
            ExecutorRegistry().bind("async_kind", a).bind("sync_kind", lambda ctx: ctx.input("in")),
        )
    )

    assert result.outputs["b"] == 1


def test_the_sync_runner_refuses_an_awaitable_instead_of_storing_it() -> None:
    """A coroutine object in `outputs` looks like success.

    It would reach every downstream node as a value nothing can read, and the
    run would still report ok.
    """

    async def a(ctx):  # type: ignore[no-untyped-def]
        return 1

    result = FlowRunner().run(
        graph([FlowNode("n", "async_kind")]),
        ExecutorRegistry().bind("async_kind", a),
    )

    assert result.ok is False
    assert "arun()" in (result.error or "")
