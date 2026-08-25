"""The workflow-props table, run against this side.

``@particle-academy/fancy-flow`` and ``particle-academy/fancy-flow-php`` run the
identical rows from the identical file. One table, three runtimes: a divergence
is a red build in whichever drifted, rather than a consumer discovering it.

This suite arrived ON TIME, and that is the point
-------------------------------------------------

``flow/subflow-registry`` was written after all four runtimes had shipped the
same defect — a post-mortem in fixture form. This table existed before this
port was started, so it is a specification rather than a record.

It earned its keep on the PHP side immediately, failing exactly one row that
turned out to describe an input PHP cannot represent (``{"0": "a"}`` — the
numeric string key is coerced to an int, so the map becomes a list). That row is
skipped there with the reason attached; ``0109`` pins the same rule with a
non-numeric key, which every runtime agrees on. Python has no such problem: a
``dict`` keeps its keys, so both rows run here.

The row that carries the weight for Python
------------------------------------------

``0005``. ``isinstance(True, int)`` is true in Python, so a naive ``number``
check accepts ``True`` — and a boolean arriving where a count belongs is a
check that runs, passes, and asserts nothing. ``_type_of`` tests ``bool``
before ``int`` for exactly that reason, and the discrimination test below pins
that the difference is real rather than incidental.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow.runtime.workflow_props import resolve_workflow_props

from .loader import cases, format_summary, run_table

SUITE = "flow/workflow-props"


def _run_case(case: dict[str, Any]) -> Any:
    """Run one row, returning only the keys the table asserts.

    ``resolve_workflow_props`` also carries a human-readable ``error``,
    deliberately absent from the contract: each runtime words its failures
    idiomatically, and comparing the prose would hold three implementations to
    a translation.
    """
    result = resolve_workflow_props(
        case["input"].get("declared"),
        case["input"].get("passed"),
    )

    if result["ok"]:
        return {"ok": True, "props": result["props"]}
    return {"ok": False, "code": result["code"]}


def test_loads_the_suite() -> None:
    # The vacuity guard, and the one that matters most: a suite that fails to
    # load yields zero rows, and "every case passed" over an empty list is
    # indistinguishable from parity.
    rows = cases(SUITE)
    assert len(rows) > 15

    ids = {row["id"] for row in rows}
    # The row the whole feature exists for — a misspelled key must FAIL.
    assert "0101-an-unknown-key-fails" in ids


def test_matches_every_row(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _run_case)

    # Printed unconditionally so a green build still shows WHAT was compared,
    # including any skips and their reasons.
    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert failures == [], "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )
    assert summary["failed"] == 0
    assert summary["passed"] > 15


def test_a_boolean_does_not_satisfy_number() -> None:
    """The Python-specific discrimination check.

    ``isinstance(True, int)`` is true, so an implementation checking
    ``isinstance(value, int)`` for ``number`` would accept ``True`` and pass the
    table only because no row happened to exercise it. This pins that the
    difference is real and deliberate.
    """
    declared = [{"name": "limit", "type": "number"}]

    result = resolve_workflow_props(declared, {"limit": True})

    assert result["ok"] is False
    assert result["code"] == "type_mismatch"
    # The language quirk being defended against, asserted directly so the
    # ordering inside `_type_of` reads as necessary rather than arbitrary.
    assert isinstance(True, int)


def test_a_supplied_falsy_value_survives_a_default() -> None:
    """The falsy trap, checked directly as well as through the table.

    ``if not supplied`` on a truthiness test rather than a membership test
    replaces a real ``0`` with the default, and a declared limit of 0 quietly
    becoming 10 is not an error anybody observes.
    """
    declared = [{"name": "limit", "type": "number", "default": 10}]

    result = resolve_workflow_props(declared, {"limit": 0})

    assert result["ok"] is True
    assert result["props"]["limit"] == 0
