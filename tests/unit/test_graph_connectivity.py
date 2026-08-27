"""A graph must not contain a node that cannot take part in it.

The Python twin of ``FancyFlow\\Analysis\\GraphConnectivity`` (PHP 0.48) and
``checkGraphConnectivity`` (TypeScript 0.64).

Both refused shapes were MEASURED against the engine before any runtime
implemented the rule, and NEITHER of them fails today:

- a floating ``log`` in a three-node graph ran (``t,lonely,o``), disconnected;
- ``t -> output -> log`` imported clean and the ``log`` ran, with
  ``{{ input }}`` resolving to ``""``.

So these are not "does the validator notice", they are "does the validator
notice something the runtime never will".
"""

from __future__ import annotations

from fancy_flow import SCHEMA_VERSION, NodeKindRegistry, builtin, import_workflow
from fancy_flow.registry.node_kind import NodeKind


def registry() -> NodeKindRegistry:
    return builtin.register(NodeKindRegistry(), with_structural=True)


def schema(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "graph": {"nodes": nodes, "edges": edges or []},
    }


def node(node_id: str, kind: str, **extra) -> dict:
    return {"id": node_id, "kind": kind, "position": {"x": 0, "y": 0}, **extra}


def errors(nodes: list[dict], edges: list[dict] | None = None, reg=None):
    return import_workflow(schema(nodes, edges), registry=reg or registry()).errors()


def messages(nodes: list[dict], edges: list[dict] | None = None, reg=None) -> str:
    return "\n".join(i.message for i in errors(nodes, edges, reg))


# -- floating nodes ------------------------------------------------------


def test_a_node_with_no_inbound_and_no_outbound_edge_is_refused() -> None:
    result = import_workflow(
        schema(
            [node("t", "manual_trigger"), node("o", "output"), node("lonely", "log")],
            [{"id": "e1", "source": "t", "target": "o"}],
        ),
        registry=registry(),
    )

    assert result.ok is False
    assert '"lonely" is connected to nothing' in result.errors()[0].message


def test_the_floating_node_is_named_so_an_editor_can_highlight_it() -> None:
    found = errors(
        [node("t", "manual_trigger"), node("o", "output"), node("lonely", "log")],
        [{"id": "e1", "source": "t", "target": "o"}],
    )

    assert len(found) == 1
    assert found[0].node_id == "lonely"


def test_a_trigger_that_reaches_nobody_is_refused() -> None:
    # Outbound-only is the direction people forget: the node fires and the
    # graph never hears it.
    found = errors(
        [node("t1", "manual_trigger"), node("o", "output"), node("orphan", "webhook_trigger")],
        [{"id": "e1", "source": "t1", "target": "o"}],
    )

    assert len(found) == 1
    assert found[0].node_id == "orphan"


def test_every_disconnected_node_is_reported_not_just_the_first() -> None:
    # Stopping at the first would make fixing a graph an N-round trip, and an
    # agent authoring one would burn a call per stray node.
    assert (
        len(
            errors(
                [
                    node("t", "manual_trigger"),
                    node("o", "output"),
                    node("a", "log"),
                    node("b", "log"),
                ],
                [{"id": "e1", "source": "t", "target": "o"}],
            )
        )
        == 2
    )


def test_a_disconnected_island_is_allowed_being_two_workflows_in_one_document() -> None:
    # Each node has an edge, so none of them floats by the letter of the rule.
    # Recorded deliberately: an island is a defensible thing to author, unlike
    # a node nobody wired up.
    assert (
        errors(
            [
                node("t1", "manual_trigger"),
                node("o1", "output"),
                node("t2", "manual_trigger"),
                node("o2", "output"),
            ],
            [
                {"id": "e1", "source": "t1", "target": "o1"},
                {"id": "e2", "source": "t2", "target": "o2"},
            ],
        )
        == []
    )


# -- what may float ------------------------------------------------------


def test_a_note_may_float_because_a_note_is_an_annotation_not_a_step() -> None:
    assert (
        errors(
            [
                node("t", "manual_trigger"),
                node("o", "output"),
                node("sticky", "note", config={"text": "why this branch exists"}),
            ],
            [{"id": "e1", "source": "t", "target": "o"}],
        )
        == []
    )


def test_a_note_may_float_under_its_canonical_namespaced_id_too() -> None:
    # A graph saved by a newer editor carries `@particle-academy/note`. Keying
    # the exemption on the bare spelling alone would turn every sticky note
    # into an error the moment it round-tripped.
    assert (
        errors(
            [
                node("t", "manual_trigger"),
                node("o", "output"),
                node("sticky", "@particle-academy/note"),
            ],
            [{"id": "e1", "source": "t", "target": "o"}],
        )
        == []
    )


def test_an_annotation_or_layout_host_kind_may_float_and_an_ordinary_one_may_not() -> None:
    # PAIRED WITH ITS CONTROL, and the control is the point.
    #
    # Alone, the first two assertions CANNOT FAIL: if registration silently did
    # nothing, `design_note` and `lane` would be UNKNOWN kinds -- and unknown
    # kinds float too. They would pass whether the category rule worked or not.
    #
    # The third registers an ordinary kind and asserts it IS refused, which is
    # what makes registration observable.
    reg = registry()
    reg.register(NodeKind(name="design_note", category="annotation", label="Design Note"))
    reg.register(NodeKind(name="lane", category="layout", label="Lane"))
    reg.register(NodeKind(name="design_step", category="data", label="Design Step"))

    wired = [{"id": "e1", "source": "t", "target": "o"}]
    base = [node("t", "manual_trigger"), node("o", "output")]

    assert errors([*base, node("d", "design_note")], wired, reg) == []
    assert errors([*base, node("l", "lane")], wired, reg) == []
    assert "connected to nothing" in messages([*base, node("s", "design_step")], wired, reg)


def test_the_exemption_does_not_extend_to_an_ordinary_kind() -> None:
    assert "connected to nothing" in messages(
        [node("t", "manual_trigger"), node("o", "output"), node("x", "transform")],
        [{"id": "e1", "source": "t", "target": "o"}],
    )


def test_an_unknown_kind_is_not_also_called_floating_on_top_of_its_own_error() -> None:
    # We cannot know whether an unknown kind is a step, an annotation or a
    # lane, so claiming it must be wired asserts something unverifiable -- and
    # it lands hardest on the graphs that deserve it least. A laned graph
    # authored in the TS editor carries `lane` nodes this registry does not
    # have; before the exemption every swimlane collected a second, misleading
    # error underneath the real one.
    text = messages(
        [node("t", "manual_trigger"), node("o", "output"), node("c", "no_such_kind")],
        [{"id": "e1", "source": "t", "target": "o"}],
    )

    assert "Unknown kind" in text
    assert "connected to nothing" not in text


# -- edges out of a terminator -------------------------------------------


def test_an_edge_whose_source_is_a_terminal_node_is_refused() -> None:
    assert "is a TERMINAL node" in messages(
        [node("t", "manual_trigger"), node("out", "output"), node("after", "log")],
        [
            {"id": "e1", "source": "t", "target": "out"},
            {"id": "e2", "source": "out", "target": "after"},
        ],
    )


def test_the_offending_edge_is_named_not_the_node() -> None:
    found = errors(
        [node("t", "manual_trigger"), node("out", "output"), node("after", "log")],
        [
            {"id": "e1", "source": "t", "target": "out"},
            {"id": "e2", "source": "out", "target": "after"},
        ],
    )

    assert len(found) == 1
    assert found[0].edge_id == "e2"
    assert found[0].node_id is None


def test_an_edge_out_of_log_is_refused_because_log_is_terminal_too() -> None:
    assert "TERMINAL" in messages(
        [node("t", "manual_trigger"), node("l", "log"), node("after", "output")],
        [
            {"id": "e1", "source": "t", "target": "l"},
            {"id": "e2", "source": "l", "target": "after"},
        ],
    )


def test_an_edge_out_of_a_node_declaring_no_outputs_at_all_is_allowed() -> None:
    # THE DISTINCTION THIS TURNS ON. An empty tuple is an explicit "there is
    # nothing to connect from"; `None` is "nobody declared it", which resolves
    # to `out` and describes most nodes in most graphs. Reading them alike
    # would refuse nearly every workflow ever written.
    assert (
        errors(
            [node("t", "manual_trigger"), node("w", "wait"), node("o", "output")],
            [
                {"id": "e1", "source": "t", "target": "w"},
                {"id": "e2", "source": "w", "target": "o"},
            ],
        )
        == []
    )


def test_an_edge_from_an_unknown_kind_is_not_refused() -> None:
    # An unregistered kind falls back to `out` in the engine, so it is not a
    # terminator. Using "I do not know" as evidence is the failure this suite
    # keeps finding elsewhere.
    assert "TERMINAL" not in messages(
        [node("t", "manual_trigger"), node("x", "some_host_kind"), node("o", "output")],
        [
            {"id": "e1", "source": "t", "target": "x"},
            {"id": "e2", "source": "x", "target": "o"},
        ],
    )


# -- the graphs people actually write still pass -------------------------


def test_an_ordinary_linear_workflow_passes() -> None:
    assert (
        errors(
            [
                node("t", "manual_trigger"),
                node("h", "api_request", config={"url": "https://example.test"}),
                node("x", "transform"),
                node("o", "output"),
            ],
            [
                {"id": "e1", "source": "t", "target": "h"},
                {"id": "e2", "source": "h", "target": "x"},
                {"id": "e3", "source": "x", "target": "o"},
            ],
        )
        == []
    )


def test_a_branch_with_two_terminal_ends_passes() -> None:
    assert (
        errors(
            [
                node("t", "manual_trigger"),
                node("b", "branch", config={"condition": "input.ok"}),
                node("yes", "output"),
                node("no", "log"),
            ],
            [
                {"id": "e1", "source": "t", "target": "b"},
                {"id": "e2", "source": "b", "target": "yes", "sourceHandle": "true"},
                {"id": "e3", "source": "b", "target": "no", "sourceHandle": "false"},
            ],
        )
        == []
    )


def test_a_single_node_graph_passes_being_a_small_workflow_not_a_floating_node() -> None:
    # Refusing this would make an editor unusable from the first node placed,
    # and the node genuinely runs -- there is no second node it fails to reach.
    assert errors([node("t", "manual_trigger")]) == []


def test_an_empty_graph_passes() -> None:
    assert errors([]) == []


def test_a_dangling_edge_still_only_warns_and_does_not_strand_its_source() -> None:
    # A dangling edge is DROPPED with a warning by the importer. Running
    # connectivity on the surviving edges alone would strand its source and
    # turn one warning into an error -- changing an existing, documented
    # behaviour as a side effect.
    result = import_workflow(
        schema(
            [node("t", "manual_trigger"), node("o", "output")],
            [
                {"id": "e1", "source": "t", "target": "o"},
                {"id": "e2", "source": "t", "target": "ghost"},
            ],
        ),
        registry=registry(),
    )

    assert result.ok is True
    assert len(result.warnings()) == 1
    assert result.errors() == []


def test_the_rule_is_lenient_mode_independent() -> None:
    # `lenient` exists so a host can load a graph containing a kind IT has not
    # registered yet. It is about unknown vocabulary, never about wiring: a
    # floating node floats in every registry.
    result = import_workflow(
        schema(
            [node("t", "manual_trigger"), node("o", "output"), node("lonely", "log")],
            [{"id": "e1", "source": "t", "target": "o"}],
        ),
        lenient=True,
        registry=registry(),
    )

    assert result.ok is False
