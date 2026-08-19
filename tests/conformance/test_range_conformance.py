"""The semver-range parity table, run against this side.

``particle-academy/fancy-flow-php`` runs the identical rows from the identical
file. The TypeScript side does NOT yet -- ``fancy-flow``'s only conformance test
is ``conformance-expr.test.ts`` -- which is recorded in the plan as a gap on
that side rather than worked around here.

The rule this table is really about: below 1.0.0 a minor bump is breaking, so
``^0.5`` means ``0.5.x``. Every node manifest in the marketplace is pre-1.0, so
a runtime that used npm's post-1.0 caret semantics would accept nodes against
engines that cannot run them.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow.marketplace import satisfies_range

from .loader import cases, format_summary, run_table, version

SUITE = "shared/satisfies-range"


def _run_case(case: dict[str, Any]) -> Any:
    return satisfies_range(case["input"]["version"], case["input"]["range"])


def test_loads_the_shared_suite() -> None:
    # The vacuity guard: zero rows would report parity.
    assert len(cases(SUITE)) > 15


def test_matches_every_row(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _run_case)

    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r["id"] for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(failures)
    assert summary["passed"] > 15
    assert summary["version"] == version()


def test_pre_1_0_caret_locks_the_minor() -> None:
    # The discrimination check. A range implementation that used npm's post-1.0
    # caret everywhere would pass most of the table and fail only here, so this
    # pins that the difference is real and exercised.
    assert satisfies_range("0.15.1", "^0.15") is True
    assert satisfies_range("0.16.0", "^0.15") is False
    assert satisfies_range("1.2.0", "^1.0") is True
