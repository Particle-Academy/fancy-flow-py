"""Parity of SURFACE -- the shared table, run against this side.

Every other conformance table pins what the engine DOES. This one pins what a
kind DECLARES, and nothing pinned that until four capabilities had been found
present in one runtime and absent in the others: ``graph.inputs`` dropped on
import, ``side_effects`` declared by nothing, this package's conformance loader
never published, and ``output_shape`` existing only in TypeScript.

In every one of those, **absent reads as a legitimate answer**, so nothing
reported the gap. This table is what makes a fifth loud.

TypeScript is the SPECIFICATION for the table, not a peer: it ships no
executors, so its declarations are the only ones that cannot be checked against
code. This runtime ships its own, so a disagreement here means THIS
implementation is wrong -- read the executor before touching the fixture.
"""

from typing import Any

from fancy_flow.registry import builtin

from .loader import format_summary, run_table

SUITE = "flow/kind-declaration-surface"


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    registry = builtin.register()
    kind_id = str(case["input"]["kind"])
    kind = registry.get(kind_id)
    assert kind is not None, f"builtin `{kind_id}` is not registered"

    config: dict[str, Any] = case["input"].get("config") or {}

    # A config-dependent shape reports the MARKER, never a resolved list: the
    # table asks what the kind DECLARES, and "depends on config" IS the
    # declaration. Resolving it would compare four runtimes' answers to a
    # question each was asked with different config.
    if kind.has_dynamic_output_shape():
        output_shape: Any = "dynamic"
    else:
        shape = kind.output_shape_for(config)
        # A SET, not an ordered list. These come out of maps, and the Rust twin
        # inserts `count` before `items` where the others do the reverse -- so
        # asserting order would report a divergence that is not one, and a
        # fixture that cries wolf is one nobody reads.
        output_shape = sorted(f["path"] for f in shape) if shape is not None else None

    return {"outputShape": output_shape, "emits": kind.emits_for(config)}


def test_matches_the_kind_declaration_surface_table() -> None:
    summary = run_table(SUITE, _run_case)

    print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The vacuity floor, and it is not filler here. Every other failure mode in
    # this file is loud; a table that silently loaded nothing is green.
    assert summary["passed"] >= 20, f"only {summary['passed']} rows ran; discovery is broken"
