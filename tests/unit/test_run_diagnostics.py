"""Run diagnostics the shared table does not reach.

``tests/conformance/test_run_diagnostics_conformance.py`` runs the fourteen rows
of fancy-conformance ``flow/run-diagnostics`` (fancy-flow#17). Those rows reach
the rule through ``switch_case``, ``branch``, ``for_each`` and ``transform``
only. These pin the rest of it: the other two config-derived kinds, the
precedence order, the handle-less edge, and the resume and async paths through
the same walk.

Mirrors ``tests/Unit/PortResolutionTest.php`` and
``tests/Unit/UndeliveredEdgeTest.php`` in fancy-flow-php where a row exists
there.
"""

from __future__ import annotations

import asyncio

from fancy_flow import (
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    FlowRunner,
    NodeKindRegistry,
    PortDescriptor,
    RunEvent,
    RunOptions,
    RunResult,
    builtin,
)
from fancy_flow.registry.port_resolution import possible_ports


def kinds() -> NodeKindRegistry:
    return builtin.register(NodeKindRegistry())


def possible(node: FlowNode, registry: NodeKindRegistry | None = None) -> list[str]:
    registry = registry or kinds()
    return possible_ports(node, registry.get(node.type or ""), node.config)


def warnings(result: RunResult) -> list[RunEvent]:
    return [e for e in result.events if e.type == RunEvent.LOG and e.level == "warn"]


# -- possible ports ----------------------------------------------------------


def test_switch_case_ports_come_from_the_cases_map_plus_default() -> None:
    node = FlowNode(
        "s",
        "switch_case",
        config={"cases": {"billing": "case_a", "technical": "case_b", "other": "case_c"}},
    )

    # `case_c` is NOT in the kind's declaration, and it is genuinely publishable.
    assert possible(node) == ["case_a", "case_b", "case_c", "default"]


def test_an_unconfigured_switch_case_falls_back_to_the_kind_declaration() -> None:
    # A half-built node must not resolve to NOTHING, or every edge leaving it
    # looks impossible while its author is still typing.
    assert possible(FlowNode("s", "switch_case")) == ["case_a", "case_b", "default"]


def test_a_list_of_cases_derives_no_ports_here() -> None:
    # This engine's switch_case routes only through a dict, so a list's
    # elements can never be published. (The PHP twin iterates a list, because
    # PHP can index one by a numeric-string value.)
    node = FlowNode("s", "switch_case", config={"cases": ["case_z"]})

    assert possible(node) == ["case_a", "case_b", "default"]


def test_llm_router_ports_are_its_routes_plus_fallback() -> None:
    node = FlowNode(
        "r", "llm_router", config={"routes": [{"port": "billing"}, {"port": "technical"}]}
    )

    assert possible(node) == ["billing", "technical", "fallback"]


def test_llm_router_drops_fallback_only_when_it_is_explicitly_off() -> None:
    off = FlowNode("r", "llm_router", config={"routes": [{"port": "a"}], "fallback": False})
    unset = FlowNode("r", "llm_router", config={"routes": [{"port": "a"}], "fallback": None})

    assert possible(off) == ["a"]
    # `None` is not `False`: fallback defaults ON, and only an explicit off
    # removes it.
    assert possible(unset) == ["a", "fallback"]


def test_subflow_gains_stream_in_the_streaming_modes_only() -> None:
    assert "stream" not in possible(FlowNode("f", "subflow", config={"workflow": "w"}))

    for mode in ("stream", "both"):
        node = FlowNode("f", "subflow", config={"workflow": "w", "mode": mode})
        assert possible(node) == ["out", "stream"]


def test_a_node_own_declared_outputs_win_over_everything() -> None:
    node = FlowNode(
        "s",
        "switch_case",
        config={"cases": {"a": "x"}},
        outputs=(PortDescriptor("only"),),
    )

    assert possible(node) == ["only"]


def test_an_unregistered_kind_publishes_exactly_out() -> None:
    assert possible_ports(FlowNode("u", "nope"), None, {}) == ["out"]


# -- the undelivered-edge warning, past the table ----------------------------


def test_a_handle_less_edge_from_named_ports_warns_without_the_remedy() -> None:
    # `for_each` declares `item` / `done` and publishes no `out`, so a
    # handle-less edge -- which reads `out` -- delivers nothing. There is no
    # handle to leave off, so suggesting it would be advice that cannot be
    # followed.
    registry = kinds()
    result = FlowRunner(registry).run(
        FlowGraph(
            (FlowNode("f", "for_each", config={"source": "{{ items }}"}), FlowNode("n", "sink")),
            (FlowEdge("e1", "f", "n"),),
        ),
        builtin.executors().bind("sink", lambda ctx: "ok"),
        options=RunOptions(initial_inputs={"f": {"items": [1]}}),
    )

    found = warnings(result)
    assert len(found) == 1
    assert found[0].node_id == "n"
    assert found[0].message is not None
    assert '"out"' in found[0].message
    assert "Available: item, done." in found[0].message
    assert "Leave sourceHandle off" not in found[0].message
    assert found[0].detail == {"edge": "e1", "source": "f", "sourceHandle": "out"}


def test_a_source_that_published_nothing_lists_no_available_ports() -> None:
    # A node declaring an explicitly EMPTY output list publishes no port at all,
    # so there is nothing to offer, and an empty "Available: ." would be noise.
    result = FlowRunner(NodeKindRegistry()).run(
        FlowGraph(
            (FlowNode("a", "plain", outputs=()), FlowNode("b", "sink")),
            (FlowEdge("e1", "a", "b", source_handle="text"),),
        ),
        ExecutorRegistry().bind("plain", lambda ctx: {"text": "hi"}).bind("sink", lambda c: 1),
    )

    found = warnings(result)
    assert len(found) == 1
    assert found[0].message == (
        'Edge e1 reads port "text" from node a, which never publishes it — nothing '
        "would reach b at run time. Leave sourceHandle off to read the node's output."
    )


def test_a_resumed_source_counts_as_completed() -> None:
    # Resume republishes a stored output, and the node is as finished as one
    # that ran -- so a handle it can never publish is still worth saying.
    result = FlowRunner(NodeKindRegistry()).run(
        FlowGraph(
            (FlowNode("a", "plain"), FlowNode("b", "sink")),
            (FlowEdge("e1", "a", "b", source_handle="nope"),),
        ),
        ExecutorRegistry().bind("sink", lambda ctx: "ok"),
        options=RunOptions(resume_outputs={"a": {"x": 1}}),
    )

    assert [e.detail for e in warnings(result)] == [
        {"edge": "e1", "source": "a", "sourceHandle": "nope"}
    ]


def test_arun_emits_the_same_warnings_as_run() -> None:
    # One walk, two drivers. A diagnostic added to a driver instead of the walk
    # would show up on one of them only.
    registry = kinds()
    graph = FlowGraph(
        (
            FlowNode("b", "branch", config={"condition": "{{ $json.urgent }}"}),
            FlowNode("n", "sink"),
        ),
        (FlowEdge("e1", "b", "n", source_handle="maybe"),),
    )
    executors = builtin.executors().bind("sink", lambda ctx: "ok")
    options = RunOptions(initial_inputs={"b": {"other": True}})

    sync = [
        (e.node_id, e.message)
        for e in warnings(FlowRunner(registry).run(graph, executors, options=options))
    ]
    async_ = [
        (e.node_id, e.message)
        for e in warnings(asyncio.run(FlowRunner(registry).arun(graph, executors, options=options)))
    ]

    assert len(sync) == 2
    assert {node_id for node_id, _ in sync} == {"b", "n"}
    assert async_ == sync
