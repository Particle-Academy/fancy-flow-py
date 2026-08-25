"""Import/export, and the expression corners the shared table does not cover."""

from __future__ import annotations

import json

import pytest

from fancy_flow import (
    FlowEdge,
    FlowGraph,
    FlowNode,
    NodeKindRegistry,
    WorkflowMetadata,
    builtin,
    export_workflow,
    import_workflow,
    to_json,
)
from fancy_flow.nodes.support import expr


def registry() -> NodeKindRegistry:
    return builtin.register(NodeKindRegistry(), with_structural=True)


# -- import --------------------------------------------------------------


def test_a_wrong_schema_version_is_an_error_and_lenient_downgrades_it() -> None:
    doc = {"version": 99, "graph": {"nodes": [], "edges": []}}

    strict = import_workflow(doc, registry=registry())
    assert strict.ok is False
    assert strict.graph.nodes == ()

    lenient = import_workflow(doc, lenient=True, registry=registry())
    assert lenient.ok is True
    assert lenient.warnings() != []


def test_a_json_string_is_accepted() -> None:
    doc = json.dumps(
        {
            "version": 1,
            "graph": {
                "nodes": [{"id": "a", "kind": "manual_trigger", "position": {"x": 0, "y": 0}}],
                "edges": [],
            },
        }
    )
    assert import_workflow(doc, registry=registry()).ok


def test_unparseable_input_is_reported_not_raised() -> None:
    result = import_workflow("{not json", registry=registry())
    assert result.ok is False
    assert "not an object" in result.issues[0].message


def test_an_unknown_kind_is_an_error_naming_the_kind() -> None:
    doc = {
        "version": 1,
        "graph": {
            "nodes": [{"id": "a", "kind": "nope", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
    }
    result = import_workflow(doc, registry=registry())
    assert result.ok is False
    assert 'Unknown kind "nope"' in result.errors()[0].message


def test_a_dangling_edge_is_dropped_with_a_warning_not_kept() -> None:
    """A kept dangling edge is an edge the engine would index and never resolve."""
    doc = {
        "version": 1,
        "graph": {
            "nodes": [{"id": "a", "kind": "manual_trigger", "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e", "source": "a", "target": "ghost"}],
        },
    }
    result = import_workflow(doc, lenient=True, registry=registry())
    assert result.graph.edges == ()
    assert any("not found" in i.message for i in result.warnings())


def test_import_leaves_node_ports_undeclared() -> None:
    """`None`, not `()`.

    The engine's fallback -- kind ports, then a lone `out` -- depends on the
    distinction, and baking `()` in at import would make every imported node
    terminal.
    """
    doc = {
        "version": 1,
        "graph": {
            "nodes": [
                {
                    "id": "a",
                    "kind": "branch",
                    "position": {"x": 0, "y": 0},
                    "config": {"condition": "x"},
                }
            ],
            "edges": [],
        },
    }
    node = import_workflow(doc, registry=registry()).graph.nodes[0]
    assert node.outputs is None


def test_defaults_are_applied_only_when_config_is_absent() -> None:
    doc = {
        "version": 1,
        "graph": {
            "nodes": [
                {"id": "a", "kind": "schedule_trigger", "position": {"x": 0, "y": 0}},
                {
                    "id": "b",
                    "kind": "schedule_trigger",
                    "position": {"x": 0, "y": 0},
                    "config": {"cron": "* * * * *"},
                },
            ],
            "edges": [],
        },
    }
    graph = import_workflow(doc, lenient=True, registry=registry()).graph
    assert graph.nodes[0].config["timezone"] == "UTC"
    assert "timezone" not in graph.nodes[1].config


# -- export --------------------------------------------------------------


def test_export_round_trips_a_graph() -> None:
    graph = FlowGraph(
        nodes=(
            FlowNode("a", "manual_trigger", label="Start"),
            FlowNode("b", "transform", config={"expression": "{{ $json }}"}),
        ),
        edges=(FlowEdge("e", "a", "b", target_handle="in"),),
    )

    doc = export_workflow(graph, WorkflowMetadata(name="demo"))
    assert doc["version"] == 1
    assert doc["metadata"]["name"] == "demo"
    assert "updatedAt" in doc["metadata"]

    back = import_workflow(doc, registry=registry())
    assert back.ok
    assert [n.id for n in back.graph.nodes] == ["a", "b"]
    assert back.graph.edges[0].target_handle == "in"


def test_to_json_is_parseable() -> None:
    text = to_json(FlowGraph(nodes=(FlowNode("a", "manual_trigger"),)))
    assert json.loads(text)["graph"]["nodes"][0]["kind"] == "manual_trigger"


# -- expression corners --------------------------------------------------


def test_two_adjacent_expressions_are_one_whole_expression() -> None:
    """The odd corner all three runtimes share.

    `{{a}}{{b}}` is a WHOLE expression whose path is `a}}{{b`, because PHP's
    pattern is end-anchored and its lazy capture has to grow to reach the end.
    A "sensible" implementation that interpolated both would diverge.
    """
    assert expr.evaluate("{{a}}{{b}}", {"a": 1, "b": 2}) is None


def test_an_unterminated_expression_is_literal_text() -> None:
    """The case an author hits constantly while typing."""
    assert expr.evaluate("hello {{ name", {"name": "Ada"}) == "hello {{ name"


def test_lists_are_indexed_numerically() -> None:
    assert expr.evaluate("{{ $json.items.1 }}", {"in": {"items": ["a", "b"]}}) == "b"
    assert expr.evaluate("{{ $json.items.9 }}", {"in": {"items": ["a"]}}) is None


def test_a_list_has_no_length_property() -> None:
    """JavaScript resolves `list.length`; PHP does not.

    Following JavaScript here would make `{{ $json.items.length }}` work on Node
    and silently return nothing on the server runtimes -- exactly the kind of
    divergence that is discovered in production.
    """
    assert expr.evaluate("{{ $json.items.length }}", {"in": {"items": [1, 2]}}) is None


def test_a_dot_path_cannot_reach_into_the_interpreter() -> None:
    """Config fields are author input. `{{ x.__class__ }}` is not a feature."""

    class Thing:
        public = "ok"

    assert expr.evaluate("{{ $json.__class__ }}", {"in": Thing()}) is None
    assert expr.evaluate("{{ $json.public }}", {"in": Thing()}) == "ok"


def test_an_object_interpolates_without_spaces() -> None:
    """Matching JSON.stringify and json_encode.

    A message sent to Slack must read identically whichever runtime sent it,
    and Python's default separators add a space after every colon.
    """
    assert expr.evaluate("x: {{ $json.o }}", {"in": {"o": {"a": 1, "b": 2}}}) == 'x: {"a":1,"b":2}'


def test_an_integral_float_interpolates_without_a_trailing_zero() -> None:
    assert expr.text(3.0) == "3"
    assert expr.text(3.5) == "3.5"


@pytest.mark.parametrize(("value", "expected"), [({}, False), ({"a": 1}, True)])
def test_an_empty_mapping_is_falsy_like_php(value: object, expected: bool) -> None:
    """A documented divergence from JavaScript, chosen to match PHP.

    PHP has one array type, so `{}` and `[]` are the same falsy empty array.
    JavaScript says `Boolean({})` is true. The shared table has no row for this,
    so it is pinned here and recorded in the plan as a gap to promote.
    """
    assert expr.truthy(value) is expected


def test_import_keeps_a_graphs_declared_inputs() -> None:
    """``graph.inputs`` is what ``RunOptions.props`` is validated against.

    The importer dropped it, so every imported graph declared nothing and
    ``resolve_workflow_props`` rejected every prop with "this workflow declares
    no inputs". The PHP twin had the identical gap, found the same day and for
    the same reason -- both ports transcribed the node/edge loop and neither
    carried the declaration beside it.
    """
    result = import_workflow(
        {
            "$schema": "https://particle.academy/schemas/workflow/v1.json",
            "version": 1,
            "graph": {
                "inputs": [{"name": "content", "type": "string", "required": True}],
                "nodes": [{"id": "t", "kind": "manual_trigger", "position": {"x": 0, "y": 0}}],
                "edges": [],
            },
        },
        lenient=True,
    )

    assert list(result.graph.inputs) == [
        {"name": "content", "type": "string", "required": True}
    ]


def test_export_writes_declared_inputs_back() -> None:
    """The other half of the round trip.

    Import alone still loses the declaration the moment a graph designed in the
    TypeScript editor -- which DOES emit ``graph.inputs`` -- passes through this
    runtime and is re-exported.
    """
    graph = FlowGraph(nodes=(), edges=(), inputs=({"name": "topic", "type": "string"},))

    schema = export_workflow(graph)

    assert schema["graph"]["inputs"] == [{"name": "topic", "type": "string"}]


def test_export_omits_inputs_for_a_graph_that_declares_none() -> None:
    """Matches the TypeScript exporter: the key appears only when there is one.

    An always-present ``"inputs": []`` would change the bytes of every graph ever
    saved, for nothing.
    """
    assert "inputs" not in export_workflow(FlowGraph())["graph"]
