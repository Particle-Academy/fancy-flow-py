"""``lenient`` never covers the schema version.

It used to. A lenient import turned an unsupported version into a warning and
carried on, in all three runtimes, and fancy-flow-php imports leniently on every
``run()``, so a versionless document ran in Laravel while a default import here
or in TypeScript, both strict, refused it. The fancy-conformance ``flow/connector-runs``
manifest recorded the split.

``lenient`` exists for unknown VOCABULARY: a kind this host has not registered.
A version is the format itself, and a runtime cannot honour a format it does
not know. The same rule holds in all three runtimes.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from fancy_flow import FlowRunner, NodeKindRegistry, RunOptions, builtin, import_workflow

_ABSENT = object()


def registry() -> NodeKindRegistry:
    return builtin.register(NodeKindRegistry(), with_structural=True)


def doc(version: Any = 1) -> dict[str, Any]:
    document: dict[str, Any] = {
        "$schema": "https://particle.academy/schemas/workflow/v1.json",
        "graph": {
            "nodes": [
                {"id": "t", "kind": "manual_trigger", "position": {"x": 0, "y": 0}, "config": {}},
                {"id": "o", "kind": "output", "position": {"x": 200, "y": 0}, "config": {}},
            ],
            "edges": [{"id": "e1", "source": "t", "target": "o"}],
        },
    }
    if version is not _ABSENT:
        document["version"] = version
    return document


@pytest.mark.parametrize(
    "version",
    [
        pytest.param(_ABSENT, id="no version at all"),
        pytest.param(2, id="a future version"),
        pytest.param("1", id="the version as a string"),
        # `True == 1` in Python, so a bare `!=` comparison let this through.
        pytest.param(True, id="a boolean"),
    ],
)
def test_a_lenient_import_refuses_a_document_it_cannot_read(version: Any) -> None:
    result = import_workflow(doc(version), lenient=True, registry=registry())

    assert result.ok is False
    assert result.graph.nodes == ()
    assert result.graph.edges == ()
    assert len(result.issues) == 1
    assert len(result.errors()) == 1
    assert re.fullmatch(
        r"Unsupported workflow schema version: .* \(expected 1\)", result.errors()[0].message
    )
    assert result.refused is True


def test_a_lenient_import_answers_exactly_as_a_strict_one() -> None:
    lenient = import_workflow(doc(_ABSENT), lenient=True, registry=registry())
    strict = import_workflow(doc(_ABSENT), registry=registry())

    assert lenient == strict


def test_version_1_written_as_1_0_is_read_as_the_other_runtimes_read_it() -> None:
    # JavaScript cannot tell 1.0 from 1 at all, so refusing it here alone would
    # be a split of its own.
    result = import_workflow(
        '{"version": 1.0, "graph": {"nodes": [], "edges": []}}', registry=registry()
    )

    assert result.ok is True


def test_lenient_still_softens_an_unknown_kind_in_a_version_1_document() -> None:
    document = doc(1)
    document["graph"]["nodes"].append(
        {"id": "x", "kind": "not_a_registered_kind", "position": {"x": 100, "y": 0}, "config": {}}
    )
    document["graph"]["edges"] = [
        {"id": "e1", "source": "t", "target": "x"},
        {"id": "e2", "source": "x", "target": "o"},
    ]

    result = import_workflow(document, lenient=True, registry=registry())

    assert result.ok is True
    assert len(result.graph.nodes) == 3
    assert result.errors() == []
    assert any("Unknown kind" in w.message for w in result.warnings())


def test_a_graph_that_was_read_but_carries_an_error_is_not_refused() -> None:
    # A `log` node publishes nothing, so an edge out of it is a connectivity
    # ERROR that `lenient` does not soften either. The graph was still READ;
    # only a document the importer refused outright comes back empty.
    document = doc(1)
    document["graph"]["nodes"].append(
        {
            "id": "lg",
            "kind": "log",
            "position": {"x": 100, "y": 0},
            "config": {"level": "info", "message": "hi"},
        }
    )
    document["graph"]["edges"] = [
        {"id": "e1", "source": "t", "target": "lg"},
        {"id": "e2", "source": "lg", "target": "o"},
    ]

    result = import_workflow(document, lenient=True, registry=registry())

    assert result.ok is False
    assert result.refused is False
    assert len(result.graph.nodes) == 3


def _outer(nested: dict[str, Any]) -> Any:
    outer = doc(1)
    outer["graph"]["nodes"] = [
        {"id": "t", "kind": "manual_trigger", "position": {"x": 0, "y": 0}, "config": {}},
        {
            "id": "sub",
            "kind": "subgraph",
            "position": {"x": 200, "y": 0},
            "config": {"graph": nested},
        },
    ]
    outer["graph"]["edges"] = [{"id": "e1", "source": "t", "target": "sub"}]
    return import_workflow(outer, registry=registry()).graph


def test_a_subgraph_node_fails_on_a_nested_graph_it_cannot_read() -> None:
    # Rather than running the empty graph a refused import returns, and passing.
    run = FlowRunner().run(
        _outer(doc(_ABSENT)),
        builtin.executors(),
        options=RunOptions(initial_inputs={"t": {"x": 1}}),
    )

    assert run.ok is False
    assert "Unsupported workflow schema version" in (run.error or "")


def test_a_subgraph_node_still_runs_a_version_1_nested_graph() -> None:
    run = FlowRunner().run(
        _outer(doc(1)),
        builtin.executors(),
        options=RunOptions(initial_inputs={"t": {"x": 1}}),
    )

    assert run.ok is True
    assert run.output("sub") not in (None, {})
