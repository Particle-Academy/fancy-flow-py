"""Node grouping -- ``parentId`` and the visual fields beside it.

Two guarantees, and until terminal lanes were ported this runtime kept neither.

**It must survive a round trip.** The WorkflowSchema's own comment said these
fields exist "purely for the canvas", so "a runtime that only walks edges and
ports ignores all of these". This runtime read that literally: ``parentId`` was
neither imported nor exported, so a graph read here and written back lost every
grouping a person had drawn -- silently, and completely. A tool that
round-trips a workflow is the normal case, not an exotic one.

**A lane must not fail the run.** The TypeScript engine ships
``@particle-academy/lane`` and walks straight past it. This one had no lane kind
and skipped only ``note``, so the same WorkflowSchema that ran on Node failed
here with "No executor registered for kind=lane" -- breaking the one guarantee
this package makes.

``analysis/graph_connectivity.py`` already knew: ``may_float`` names the lane
explicitly as "a swimlane its engine walks straight past". The analysis knew and
the runner did not, and nothing compared them. ``runtime/events.py`` even
documents a ``"lane"`` status text that nothing had ever emitted.
"""

from __future__ import annotations

from fancy_flow.engine.runner import FlowRunner
from fancy_flow.executors import ExecutorRegistry
from fancy_flow.registry import builtin
from fancy_flow.registry.node_kind import NodeKind
from fancy_flow.registry.registry import NodeKindRegistry, never_executes
from fancy_flow.schema.graph import FlowGraph, FlowNode
from fancy_flow.workflow import export_workflow, import_workflow

LANED_DOCUMENT = {
    "$schema": "https://particle.academy/schemas/workflow/v1.json",
    "version": 1,
    "graph": {
        "nodes": [
            {
                "id": "lane",
                "kind": "lane",
                "position": {"x": 0, "y": 0},
                "width": 480,
                "height": 220,
                "style": {"background": "#eef"},
                "config": {"title": "Setup"},
            },
            {
                "id": "t",
                "kind": "manual_trigger",
                "position": {"x": 20, "y": 40},
                "parentId": "lane",
                "extent": "parent",
            },
        ],
        "edges": [],
    },
}


def test_parent_id_survives_import() -> None:
    imported = import_workflow(LANED_DOCUMENT)
    inside = next(n for n in imported.graph.nodes if n.id == "t")

    assert inside.parent_id == "lane"
    assert inside.extent == "parent"


def test_the_whole_visual_block_survives_a_round_trip() -> None:
    """Carried together, because half a restoration is harder to notice.

    A container whose child kept its parent but lost its containment rule, or a
    lane restored at the default size, reads as a canvas somebody nudged rather
    than as data a tool destroyed.
    """
    round_tripped = export_workflow(import_workflow(LANED_DOCUMENT).graph)

    by_id = {n["id"]: n for n in round_tripped["graph"]["nodes"]}

    assert by_id["t"]["parentId"] == "lane"
    assert by_id["t"]["extent"] == "parent"
    assert by_id["lane"]["width"] == 480
    assert by_id["lane"]["height"] == 220
    assert by_id["lane"]["style"] == {"background": "#eef"}


def test_a_plain_node_gains_no_empty_keys() -> None:
    """Omitted when unset, matching the TypeScript writer.

    A graph of ordinary plumbing nodes should not carry five empty keys each,
    or every saved diff becomes unreadable.
    """
    exported = export_workflow(FlowGraph(nodes=(FlowNode(id="n", type="log"),), edges=()))
    written = exported["graph"]["nodes"][0]

    assert set(written) & {"parentId", "extent", "width", "height", "style"} == set()


def test_a_graph_containing_a_lane_runs() -> None:
    """The parity failure, stated as the thing a consumer would hit."""
    imported = import_workflow(LANED_DOCUMENT)
    result = FlowRunner().run(imported.graph, builtin.executors())

    assert result.ok is True
    assert result.error is None


def test_a_lane_is_skipped_under_every_id_it_answers_to() -> None:
    """Including with an EMPTY registry.

    ``builtin.register()`` is opt-in, so a caller who took
    ``builtin.executors()`` and never installed the kinds has no categories to
    look up. ``note`` already worked that way; a fix that only reached people
    who called one extra function would not be one.
    """
    for kind in (
        "lane",
        "@particle-academy/lane",
        "@fancy/lane",
        "terminal_lane",
        "@particle-academy/terminal_lane",
    ):
        graph = FlowGraph(nodes=(FlowNode(id="l", type=kind),), edges=())
        result = FlowRunner().run(graph, ExecutorRegistry())

        assert result.ok is True, f"{kind} did not run clean"
        statuses = [e for e in result.events if getattr(e, "type", "") == "node-status"]
        assert [s.text for s in statuses] == ["lane"], kind


def test_a_host_registered_layout_kind_is_skipped_too() -> None:
    """By CATEGORY, so a host's own swimlane needs no executor either."""
    registry = builtin.register(NodeKindRegistry())
    registry.register(NodeKind(name="@acme/swimlane", category="layout", label="Swimlane"))

    graph = FlowGraph(nodes=(FlowNode(id="l", type="@acme/swimlane"),), edges=())
    result = FlowRunner(registry).run(graph, ExecutorRegistry())

    assert result.ok is True


def test_an_unknown_kind_is_not_skipped() -> None:
    """Running is the default.

    ``may_float`` lets an unknown kind float because it cannot know what the
    kind is -- the honest answer to a different question. Skipping one here
    would turn every typo in a kind id into a node that silently does nothing
    while the run reports success.
    """
    graph = FlowGraph(nodes=(FlowNode(id="n", type="@acme/never-heard-of-it"),), edges=())
    result = FlowRunner().run(graph, ExecutorRegistry())

    assert result.ok is False
    assert "No executor registered" in (result.error or "")


def test_the_skip_rule_has_exactly_one_definition() -> None:
    """Three places need this answer, and any two disagreeing is invisible.

    The runner decides what to skip, ``may_float`` decides what may float, and
    the registry test that forces every kind to have an executor decides what is
    exempt. A kind skippable in one and not the others is either an unrunnable
    node or a spurious missing-executor failure, and both look like something
    else.
    """
    registry = builtin.register(NodeKindRegistry(), with_structural=True)

    for kind in registry.all():
        skipped = never_executes(kind.name, registry) is not None
        needs_executor = builtin.executors().resolve_for(FlowNode("n", kind.name)) is not None

        assert skipped != needs_executor, (
            f"{kind.name} is {'skipped' if skipped else 'run'} but "
            f"{'has' if needs_executor else 'has no'} executor"
        )
