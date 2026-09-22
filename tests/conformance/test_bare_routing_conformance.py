"""Shared #24 refusal cases exercise the real importer and built-in executors."""

from copy import deepcopy
from typing import Any

import pytest
from fancy_conformance import cases

from fancy_flow import FlowRunner, NodeKindRegistry, RunOptions, builtin, import_workflow

ROWS = [
    case
    for case in cases("flow/graph-runs")
    if "-bare-" in case["id"] or "-unclosed-" in case["id"]
]


def test_loaded_refusal_rows() -> None:
    assert len(ROWS) == 8


@pytest.mark.parametrize("case", ROWS, ids=[case["id"] for case in ROWS])
def test_bare_routing_aborts_before_downstream(case: dict[str, Any]) -> None:
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(case["input"]["schema"], lenient=True, registry=registry).graph
    result = FlowRunner().run(
        graph,
        builtin.executors(),
        options=RunOptions(initial_inputs=case["input"]["initialInputs"]),
    )
    assert {"ok": result.ok, "error": result.error} == case["expected"]
    assert list(result.outputs) == ["t"]


@pytest.mark.parametrize(("kind", "key"), [("branch", "condition"), ("switch_case", "value")])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        "\n\t",
        True,
        False,
        1,
        0,
        None,
        "{{ in.data.fits }}",
        "prefix {{ in.kind }}",
        "{{ in.kind }}-{{ in.kind }}",
    ],
)
def test_other_routing_values_remain_accepted(kind: str, key: str, value: Any) -> None:
    case = deepcopy(ROWS[0 if kind == "branch" else 4])
    case["input"]["schema"]["graph"]["nodes"][1]["config"][key] = value
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(case["input"]["schema"], lenient=True, registry=registry).graph
    result = FlowRunner().run(
        graph,
        builtin.executors(),
        options=RunOptions(initial_inputs=case["input"]["initialInputs"]),
    )
    assert result.ok
    if kind == "branch" and value in ("", "  ", "\n\t", False, 0, None, "{{ in.data.fits }}"):
        assert "drop" in result.outputs
        assert "deal" not in result.outputs
    if kind == "branch" and value in (True, 1):
        assert "deal" in result.outputs
        assert "drop" not in result.outputs
