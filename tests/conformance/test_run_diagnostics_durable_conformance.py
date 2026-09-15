"""The run-diagnostics table again -- through the durable ``Coordinator``.

``test_run_diagnostics_conformance.py`` runs ``flow/run-diagnostics`` through
``FlowRunner.run``, which is one process walking one order and emitting every
warning it reaches. The durable driver splits the same walk into one replay per
node and forwards only the events of the node each job runs. A warning whose
target never gets a job -- an undelivered edge that was the target's ONLY
inbound one, so the frontier SKIPS it -- was emitted inside other jobs' replays
and filtered out there, so a host driving the run durably never saw it.

That was the known gap in 0.21.0. Running every row through the Coordinator and
requiring the same answer is what turns "the drivers agree" into a test result:
a warning can be lost, or delivered twice, and both show up as a row failing.
"""

from __future__ import annotations

from typing import Any

import pytest
from fancy_conformance import format_summary, run_table

from fancy_flow import NodeKindRegistry, RunEvent, builtin, import_workflow
from fancy_flow.durable import Coordinator

SUITE = "flow/run-diagnostics"


def _run_case_durably(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Import leniently, drive the graph node by node, report every ``warn`` log.

    Set up exactly as the in-process test sets up, and the registry is handed to
    the Coordinator as ``kinds``: the default one is empty here, and a replay
    resolving ports against it would publish ``for_each`` on ``out`` rather than
    ``item`` / ``done`` -- so 0012 would pass or fail for a reason unrelated to
    what it tests.
    """
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(case["input"]["schema"], lenient=True, registry=registry).graph

    warnings: list[dict[str, Any]] = []

    def collect(event: RunEvent) -> None:
        if event.type == RunEvent.LOG and event.level == "warn":
            warnings.append(
                {"nodeId": event.node_id, "message": event.message, "detail": event.detail}
            )

    Coordinator(
        graph=graph,
        executors=builtin.executors(),
        run=case["id"],
        initial_inputs=case["input"].get("initialInputs", {}),
        kinds=registry,
        on_event=collect,
    ).run_to_completion()

    return sorted(warnings, key=lambda warning: str(warning["message"]))


def test_the_durable_driver_matches_the_run_diagnostics_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = run_table(SUITE, _run_case_durably)

    with capsys.disabled():
        print("\n[durable] " + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "The durable Coordinator disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The same vacuity floor as the in-process run: a table that loaded no rows
    # has no failures either.
    assert summary["passed"] > 12, f"only {summary['passed']} rows ran; discovery is broken"
