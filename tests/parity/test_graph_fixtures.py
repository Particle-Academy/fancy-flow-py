"""The parity suite.

Every fixture in ``fixtures/`` is a WorkflowSchema plus ``initialInputs`` with a
baked-in golden ``{ok, outputs}``. Running it through this engine and the
deterministic default executors must reproduce that result exactly.

Read ``fixtures/SOURCE.md`` before touching anything here: these goldens are a
COPY of the PHP twin's, not a shared table, and the provenance test next door is
the only thing stopping the two drifting apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fancy_flow import FlowRunner, NodeKindRegistry, RunOptions, builtin, import_workflow

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


def _run(doc: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None]:
    # A LOCAL kind registry, exactly as the PHP harness does, which leaves the
    # shared registry empty. That matters: the engine's declared-output-port
    # fallback reads the shared registry, so a populated one would give
    # `for_each` its `item`/`done` ports instead of the historical lone `out`.
    # Populating it here would make these goldens agree with a configuration
    # the PHP suite never runs. The fallback gets its own unit test instead.
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    result = import_workflow(doc["schema"], lenient=True, registry=registry)
    run = FlowRunner().run(
        result.graph,
        builtin.executors(),
        options=RunOptions(initial_inputs=doc.get("initialInputs", {})),
    )
    return run.ok, run.outputs, run.error


def _normalize(value: Any) -> Any:
    """Collapse the one difference PHP's JSON encoding forces on a golden.

    PHP has a single array type, so an empty map and an empty list both encode
    as ``[]``. Fixture ``14-api-request`` bakes ``"headers": []`` for what is
    semantically an empty header MAP — a Node runtime would produce ``{}``
    there too, so this is a defect in the golden rather than a Python quirk,
    and it is noted in ``SOURCE.md``.

    Deliberately narrow: only an EMPTY container becomes the shared sentinel. A
    non-empty list and a non-empty map are genuinely different values and must
    still fail.
    """
    if isinstance(value, dict):
        return _EMPTY if not value else {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return _EMPTY if not value else [_normalize(v) for v in value]
    return value


#: What both `[]` and `{}` compare as. A string rather than `object()` so a
#: failure message names the reason instead of printing an address.
_EMPTY = "<empty container: [] and {} are the same value in PHP>"


def test_fixture_set_is_complete() -> None:
    # The vacuity guard. "Every fixture passed" over an empty glob is
    # indistinguishable from parity.
    assert len(FIXTURES) >= 23


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_reproduces_the_golden_result(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    ok, outputs, error = _run(doc)
    expected = doc["expected"]

    assert ok is expected["ok"], f"{path.stem}: run ok mismatch (error={error!r})"

    if "errorContains" in expected:
        assert error is not None
        assert expected["errorContains"] in error

    if "outputs" in expected:
        assert _normalize(outputs) == _normalize(expected["outputs"])
