"""Which entry point fired — the shared table, run against this side.

A graph may hold more than one trigger, and a trigger has no inbound edges,
which IS the readiness rule — so every trigger's branch ran on every run,
whichever one fired. The triggers themselves are harmless; everything
DOWNSTREAM of the ones that did not fire is not.

Reported against the PHP runtime with production measurements — a ``user_input``
on the manual branch executing during an event-triggered run, parking it to ask
a person for data the event had already supplied — but the defect was in all
three runtimes, because all three share the rule.

The table was written BEFORE any runtime implemented it, so it is a
specification rather than a post-mortem. ``0101`` is the row to read first: it
pins that UNSET behaves exactly as before, which is what keeps every
multi-trigger graph already in the field working.
"""

from __future__ import annotations

from typing import Any

from fancy_flow import FlowRunner, NodeKindRegistry, RunOptions, builtin, import_workflow

from .loader import format_summary, run_table

SUITE = "flow/entry-points"


def _run_case(case: dict[str, Any]) -> list[str]:
    """Import leniently, run, and report WHICH nodes executed.

    A node that ran has an entry in ``outputs``; a skipped one does not. Sorted
    because this suite asks which nodes ran, not in what order — ``flow/graph-runs``
    already pins ordering-sensitive behaviour.
    """
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(case["input"]["schema"], lenient=True, registry=registry).graph

    result = FlowRunner().run(
        graph,
        builtin.executors(),
        options=RunOptions(
            initial_inputs=case["input"].get("initialInputs", {}),
            # `None` in the fixture means UNSET and must not become `[]` -- the
            # two are different rules, and 0101 vs 0106 exist to pin that.
            entry_nodes=case["input"]["entryNodes"],
        ),
    )

    return sorted(result.outputs.keys())


def test_matches_the_entry_points_table() -> None:
    summary = run_table(SUITE, _run_case)

    print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The vacuity floor, just under the seven rows.
    assert summary["passed"] > 5, f"only {summary['passed']} rows ran; discovery is broken"
