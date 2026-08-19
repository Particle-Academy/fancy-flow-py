"""The ``{{ }}`` parity table, run against this side.

``@particle-academy/fancy-flow`` and ``particle-academy/fancy-flow-php`` run the
identical rows from the identical file. That is the whole mechanism: every
runtime reads one table, so a divergence is a red build in whichever one
drifted rather than a support ticket months later.

The rows that carry the weight are the truthiness ones. ``"0"``, ``"false"``
and ``[]`` are all truthy in JavaScript and falsy in PHP, and a branch node
reading a form value or a JSON body hits every one of them. Python agrees with
JavaScript on ``"0"`` and ``"false"`` and with PHP on ``[]``, so an
implementation that forwarded to ``bool()`` would fail exactly 0013 and 0014 --
which is the signal this table exists to produce.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow.nodes.support import expr

from .loader import cases, format_summary, run_table, version

SUITE = "shared/expr"


def _run_case(case: dict[str, Any]) -> Any:
    fn = case["fn"]
    if fn == "evaluateExpression":
        return expr.evaluate(case["input"]["template"], case["input"].get("context", {}))
    if fn == "truthy":
        return expr.truthy(case["input"]["value"])
    raise RuntimeError(f"case {case['id']} calls unimplemented fn {fn}")


def test_loads_the_shared_suite() -> None:
    # The vacuity guard, and the one that matters most. A suite that fails to
    # load yields zero cases, and "every case passed" over an empty list is
    # indistinguishable from parity.
    rows = cases(SUITE)
    assert len(rows) > 15
    fns = {row.get("fn") for row in rows}
    assert "evaluateExpression" in fns
    assert "truthy" in fns


def test_matches_every_row(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _run_case)

    # Printed unconditionally -- the conformance runner README asks for it, so a
    # green build still shows WHAT was compared rather than only that nothing
    # failed.
    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r["id"] for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(failures)
    assert summary["failed"] == 0
    # A suite that skipped everything would report zero failures too.
    assert summary["passed"] > 15
    assert summary["version"] == version()


def test_disagrees_with_native_truthiness_where_the_table_says_it_should() -> None:
    # The discrimination check. Without it, a `truthy` that simply forwarded to
    # bool() would pass the table only because the table happened not to
    # exercise the difference -- so this pins that the difference is real and
    # exercised.
    assert expr.truthy("false") is False
    assert bool("false") is True

    assert expr.truthy("0") is False
    assert bool("0") is True

    assert expr.truthy([]) is False
    assert expr.truthy("no thanks") is True
