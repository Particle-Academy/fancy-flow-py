"""The three-runtime table for run/step identity, run against this side.

``@particle-academy/fancy-flow`` and ``particle-academy/fancy-flow-php`` read
the identical rows from the identical file. Idempotency is exactly the kind of
contract prose cannot keep honest: every implementation looks right in
isolation, and the failure only ever appears as a duplicate charge in somebody's
ledger.

The rows that carry the weight
------------------------------

``0011`` and ``0012`` are a pair and mean nothing apart: the same step on
attempt 1 and attempt 5 must produce the SAME key. An implementation that folds
the attempt number into the key passes every other row in this table and creates
a second charge on the first timeout in production.

``0006`` and ``0007`` are the other pair -- a node literally named ``a/b`` at
the top level must not key the same as a node ``b`` inside an invocation of
``a``. Unescaped they spell the same string, so two unrelated writes share an
idempotency key and the provider deduplicates them into one.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow.runtime import RunIdentity

from .loader import cases, format_summary, run_table, version

SUITE = "shared/flow-run-identity"


def _run_case(case: dict[str, Any]) -> Any:
    fn = case["fn"]
    data = case["input"]

    if fn == "stepKey":
        return RunIdentity(
            data["runKey"],
            tuple(data.get("path", ())),
            data.get("attempt", 1),
        ).step_key(data["nodeId"], data.get("occurrence"))

    if fn == "isReplaySafe":
        return RunIdentity(
            "run_conformance",
            (),
            data["attempt"],
            data["firstAttemptAt"],
        ).is_replay_safe(data["windowSeconds"], data["now"])

    raise RuntimeError(f"case {case['id']} calls unimplemented fn {fn}")


def test_loads_the_shared_suite() -> None:
    # The vacuity guard. A suite that fails to load yields zero cases, and
    # "every case passed" over an empty list is indistinguishable from parity.
    rows = cases(SUITE)
    assert len(rows) > 20
    fns = {row.get("fn") for row in rows}
    assert "stepKey" in fns
    assert "isReplaySafe" in fns


def test_matches_every_row(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _run_case)

    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r["id"] for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(failures)
    assert summary["failed"] == 0
    assert summary["passed"] > 20
    assert summary["version"] == version()


def test_attempt_is_not_in_the_key() -> None:
    # The discrimination check for the pair above, stated directly. An
    # implementation that mixed `attempt` into the key would still pass every
    # individual row of the table read one at a time.
    first = RunIdentity("run_a", (), 1)
    retry = RunIdentity("run_a", (), 5)

    assert retry.step_key("pay") == first.step_key("pay")
    assert RunIdentity("run_b").step_key("pay") != first.step_key("pay")
    assert first.descend("billing").step_key("pay") != first.step_key("pay")
