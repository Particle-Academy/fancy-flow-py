"""A graph that runs and delivers nothing must say so — the shared table, run here.

Two warnings, and both are the same silent failure: a graph that runs, reports
success, and delivers nothing down one path.

1. **An undelivered edge.** Its source completed, and its ``sourceHandle`` names
   a port that source could never publish. The target is skipped (its only edge
   was the bad one) or runs with that input missing, and a correct downstream
   template renders empty.
2. **A route taken on an unresolved path.** ``branch`` / ``switch_case`` routed
   on a whole ``{{ path }}`` that did not resolve, so the run took ``false`` or
   ``default`` for a reason that has nothing to do with the data.

fancy-flow-php emitted both; this engine, Node and Rust ran the identical graph
and said nothing (fancy-flow#17). Half the rows are silent on purpose: a warning
that fires on ordinary branching is noise, and noise is how a real warning stops
being read.
"""

from __future__ import annotations

from typing import Any

import pytest
from fancy_conformance import format_summary, run_table

from fancy_flow import FlowRunner, NodeKindRegistry, RunEvent, RunOptions, builtin, import_workflow

SUITE = "flow/run-diagnostics"


def _run_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Import leniently, run, and report every ``warn`` log, sorted by message.

    Sorted rather than in emission order because undelivered-edge warnings come
    out as each target is reached, and runtimes may break ties between
    same-depth nodes differently; ``flow/graph-runs`` owns ordering.

    The runner is given the SAME local registry as the import. The default one
    is empty here, and against it ``for_each`` would publish ``out`` instead of
    ``item`` / ``done`` -- so 0012 would pass or fail for a reason unrelated to
    what it tests.
    """
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(case["input"]["schema"], lenient=True, registry=registry).graph

    result = FlowRunner(registry).run(
        graph,
        builtin.executors(),
        options=RunOptions(initial_inputs=case["input"].get("initialInputs", {})),
    )

    warnings = [
        {"nodeId": event.node_id, "message": event.message, "detail": event.detail}
        for event in result.events
        if event.type == RunEvent.LOG and event.level == "warn"
    ]
    return sorted(warnings, key=lambda warning: str(warning["message"]))


def test_matches_the_run_diagnostics_table(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, _run_case)

    # capsys.disabled(): pytest captures a passing test's stdout, so a bare
    # print() never reaches the CI log on exactly the green runs it explains.
    with capsys.disabled():
        print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The vacuity floor, just under the fourteen rows. A table that loaded no
    # rows has no failures either, and would read as green without it.
    assert summary["passed"] > 12, f"only {summary['passed']} rows ran; discovery is broken"
