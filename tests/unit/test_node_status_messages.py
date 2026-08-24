"""Per-node status messages, and subflow registry inheritance.

Both ported from ``@particle-academy/fancy-flow`` 0.49.0 /
``particle-academy/fancy-flow-php`` 0.21.0, so all three runtimes agree.

STATUS MESSAGES. A node may carry ``starting_msg`` / ``stopping_msg``; the
engine announces them around that node as ``node-message`` events. Opt-in per
node -- most nodes in a graph are plumbing, and narrating all of them buries
the two or three steps a person actually follows.

The sharpest rule: ``stopping_msg`` does NOT fire when the node raises.
"Analysis complete" printed after a crash tells a human the opposite of what
happened, in the part of the UI they trust most.

SUBFLOW REGISTRY (fancy-flow-php#7). A subflow ran its child against
``self._executors or builtin_executors(...)`` -- the BARE builtins -- so a
host-registered kind resolved at top level and vanished one level down, and a
host that had REPLACED a builtin got the package's version inside the child.
Nothing warned, because an unregistered kind fails closed with no outputs.
"""

from __future__ import annotations

from fancy_flow import (
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    FlowRunner,
    RunEvent,
)
from fancy_flow.capabilities import set_workflow_resolver
from fancy_flow.registry.builtin import executors as builtin_executors


def graph(nodes, edges=()):
    return FlowGraph(tuple(nodes), tuple(edges))


def narration_of(g, registry):
    """Run and return the narration, in order, as ``phase:message``."""
    said = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_MESSAGE:
            said.append(f"{event.phase}:{event.message}")

    FlowRunner().run(g, registry, sink)
    return said


def step_registry(fn):
    return builtin_executors().bind("step", fn)


def test_announces_start_before_the_node_and_end_after_it() -> None:
    order = []

    def step(ctx):
        order.append("executed")
        return 1

    g = graph([FlowNode("a", "step", starting_msg="Starting the deep analysis", stopping_msg="Analysis complete")])

    assert narration_of(g, step_registry(step)) == [
        "start:Starting the deep analysis",
        "end:Analysis complete",
    ]
    assert order == ["executed"]


def test_a_node_with_no_messages_says_nothing() -> None:
    # Opt-in is the feature.
    g = graph([FlowNode("quiet", "step")])
    assert narration_of(g, step_registry(lambda ctx: 1)) == []


def test_does_not_announce_completion_when_the_node_raises() -> None:
    # The one that matters: a completion message after a failure is the run
    # telling a person the opposite of what happened.
    def boom(ctx):
        raise RuntimeError("model refused")

    g = graph([FlowNode("a", "step", starting_msg="Starting the deep analysis", stopping_msg="Analysis complete")])

    assert narration_of(g, step_registry(boom)) == ["start:Starting the deep analysis"]


def test_ignores_a_message_that_is_blank_after_stripping() -> None:
    # A blank field is the shape a cleared editor input takes, and a blank line
    # in a progress feed cannot be told apart from a real message.
    g = graph([FlowNode("a", "step", starting_msg="", stopping_msg="   ")])
    assert narration_of(g, step_registry(lambda ctx: 1)) == []


def test_narration_stays_out_of_node_status_text() -> None:
    g = graph([FlowNode("a", "step", starting_msg="Hello", stopping_msg="Bye")])
    seen: list[str] = []

    def sink(event: RunEvent) -> None:
        if event.type == RunEvent.NODE_STATUS and event.text:
            seen.append(event.text)

    FlowRunner().run(g, step_registry(lambda ctx: 1), sink)

    assert "Hello" not in seen
    assert "Bye" not in seen


def test_narrates_a_two_node_run_in_execution_order() -> None:
    # The example this was built for: analyse, then save.
    g = graph(
        [
            FlowNode("a", "step", starting_msg="Starting the deep analysis", stopping_msg="Analysis complete"),
            FlowNode("b", "step", starting_msg="Saving report"),
        ],
        [FlowEdge("e1", "a", "b")],
    )

    assert narration_of(g, step_registry(lambda ctx: 1)) == [
        "start:Starting the deep analysis",
        "end:Analysis complete",
        "start:Saving report",
    ]


# ── subflow registry inheritance ──────────────────────────────────────────


def host_kind_graph(node_id: str = "c1") -> FlowGraph:
    return graph([FlowNode(node_id, "host_kind")])


def subflow_parent(ref: str = "child") -> FlowGraph:
    return graph([FlowNode("sub", "subflow", config={"workflow": ref})])


class MapResolver:
    """A resolver over a fixed ref -> graph map; unknown refs resolve to None.

    The protocol wants an object with ``resolve()``, not a bare callable.
    """

    def __init__(self, graphs: dict) -> None:
        self._graphs = graphs

    def resolve(self, ref: str, version: int | None = None):
        return self._graphs.get(ref)


def test_a_host_kind_resolves_inside_the_child_not_only_at_top_level() -> None:
    ran: list[str] = []
    registry = builtin_executors().bind("host_kind", lambda ctx: ran.append("host") or "ok")

    set_workflow_resolver(MapResolver({"child": host_kind_graph()}))
    try:
        # Control: run the child DIRECTLY. If this fails the test is wrong
        # about the registry rather than about nesting.
        assert FlowRunner().run(host_kind_graph(), registry).ok is True
        assert ran == ["host"]

        ran.clear()
        nested = FlowRunner().run(subflow_parent(), registry)

        assert nested.ok is True, "a host kind must resolve inside a subflow"
        assert ran == ["host"]
    finally:
        set_workflow_resolver(None)


def test_the_child_uses_the_hosts_override_not_the_packages() -> None:
    # The expensive case: a host that replaces a builtin to add tenancy or
    # budgeting must not silently get the package's version inside a child.
    used: list[str] = []
    registry = builtin_executors().bind("host_kind", lambda ctx: used.append("host version") or 1)

    set_workflow_resolver(MapResolver({"child": host_kind_graph()}))
    try:
        FlowRunner().run(subflow_parent(), registry)
        assert used == ["host version"]
    finally:
        set_workflow_resolver(None)


def test_inheritance_reaches_a_grandchild() -> None:
    # Depth is where a "pass it down once" fix quietly stops working.
    ran: list[str] = []
    registry = builtin_executors().bind("host_kind", lambda ctx: ran.append("deep") or 1)

    middle = graph([FlowNode("m1", "subflow", config={"workflow": "grandchild"})])
    graphs = {"child": middle, "grandchild": host_kind_graph("g1")}

    set_workflow_resolver(MapResolver(graphs))
    try:
        result = FlowRunner().run(subflow_parent(), registry)
        assert result.ok is True
        assert ran == ["deep"]
    finally:
        set_workflow_resolver(None)


def test_status_messages_survive_the_document_round_trip() -> None:
    """The document has to CARRY them, or the feature is decorative.

    Import/export whitelists node fields by name rather than spreading a dict,
    which is the right call -- it is what keeps a saved graph from accumulating
    a host's private junk. It also means a new field nobody adds to BOTH
    directions is silently dropped the first time a graph is saved and
    reopened, and nothing anywhere reports it.
    """
    from fancy_flow import export_workflow, import_workflow

    original = graph([
        FlowNode(
            "a",
            "@particle-academy/manual_trigger",
            starting_msg="Starting the deep analysis",
            stopping_msg="Analysis complete",
        )
    ])

    doc = export_workflow(original)
    node = doc["graph"]["nodes"][0]
    assert node["startingMsg"] == "Starting the deep analysis"
    assert node["stoppingMsg"] == "Analysis complete"

    reopened = import_workflow(doc)
    assert reopened.graph.nodes[0].starting_msg == "Starting the deep analysis"
    assert reopened.graph.nodes[0].stopping_msg == "Analysis complete"


def test_a_node_with_no_messages_stays_clean_in_the_document() -> None:
    from fancy_flow import export_workflow

    doc = export_workflow(graph([FlowNode("a", "@particle-academy/manual_trigger")]))
    node = doc["graph"]["nodes"][0]

    assert "startingMsg" not in node
    assert "stoppingMsg" not in node
