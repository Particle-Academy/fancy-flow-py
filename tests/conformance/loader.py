"""A minimal Python loader for the shared ``fancy-conformance`` fixtures.

``particle-academy/fancy-conformance`` ships two loaders -- ``src/index.ts`` for
Node and ``php/src/Conformance.php`` for PHP -- and no Python one, because until
now there was no Python implementation to hold to the tables. This is the third
loader, deliberately the same shape as the other two so a reviewer comparing
three CI logs is comparing like with like.

**It belongs in that repository, not this one.** Living here means Python reads
the tables through code the fixture package does not own, which is exactly the
arrangement its own README warns about. The plan records this as work for
`fancy-conformance`; until it lands, this file is the bridge.

Four rules the runners README lays down, all honoured below:

1. Run on every push and PR -- not nightly, not at release.
2. **A missing toolchain is a FAILURE, not a skip.** ``skipIf(!HAS_X)``
   returning green is the exact mechanism that hid two-way drift for months, so
   :func:`suites_root` raises rather than returning ``None``.
3. Print the summary unconditionally, including every skip and its reason.
4. Print and assert the pinned suite version.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["cases", "format_summary", "list_suites", "run_table", "suites_root", "version"]

_ENV = "FANCY_CONFORMANCE_ROOT"


def suites_root() -> Path:
    """The conformance repository root -- the directory holding ``suites/``.

    Resolution order: the ``FANCY_CONFORMANCE_ROOT`` environment variable, then
    a bounded walk up from this file looking for a sibling checkout (directly
    or under ``repos/``).

    Never a fixed ``../..`` and never a hard-coded sibling path: the two parity
    harnesses this package replaced both did that, which is why they ran in
    exactly one directory layout and silently no-opped everywhere else.

    Raises rather than returning ``None`` so the failure is a red build.
    """
    override = os.environ.get(_ENV)
    if override:
        candidate = Path(override)
        if (candidate / "suites").is_dir():
            return candidate
        raise RuntimeError(
            f"{_ENV} is set to {override!r} but there is no suites/ directory there."
        )

    here = Path(__file__).resolve()
    for parent in list(here.parents)[:8]:
        for base in (parent, parent / "repos"):
            candidate = base / "fancy-conformance"
            if (candidate / "suites").is_dir():
                return candidate

    raise RuntimeError(
        "Could not locate particle-academy/fancy-conformance. Check it out beside this "
        f"repository or set {_ENV} to its root. This is deliberately an error and not a "
        "skip: a conformance suite that silently does not run is worse than no suite, "
        "because the log reads identically to full coverage."
    )


def version() -> str:
    """The fixture collection's own version. A runner must print this."""
    return (suites_root() / "VERSION").read_text(encoding="utf-8").strip()


def list_suites() -> list[str]:
    root = suites_root() / "suites"
    return sorted(
        str(manifest.parent.relative_to(root)).replace("\\", "/")
        for manifest in root.rglob("manifest.json")
    )


def cases(suite: str) -> list[dict[str, Any]]:
    """Load one table suite's rows, enforcing the repository's own invariants.

    A skip without a reason and a duplicate case id are LOAD errors in both
    shipped loaders, with matching messages. That is the repository's entire
    thesis; a third loader that relaxed either would make the package
    decoration.
    """
    directory = suites_root() / "suites" / suite
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))

    if manifest.get("caseFormat") != "table":
        raise RuntimeError(
            f"Suite {suite} is {manifest.get('caseFormat')!r}, not a table. Directory "
            "suites are driven through the subprocess CLI, not this loader."
        )

    payload = json.loads(
        (directory / manifest.get("cases", "cases.json")).read_text(encoding="utf-8")
    )
    rows = payload.get("cases") or []

    seen: set[str] = set()
    for row in rows:
        case_id = row.get("id")
        if case_id in seen:
            raise RuntimeError(f"Duplicate case id {case_id!r} in {suite}.")
        seen.add(case_id)

        skip = row.get("skip")
        if skip is not None and not str(skip).strip():
            raise RuntimeError(
                f"Case {case_id} in {suite} is skipped with no reason. Every skip must "
                "say why, and show up in the log until it is gone."
            )

    return rows


def run_table(suite: str, run_case: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Run every row of a table suite against one implementation."""
    results: list[dict[str, Any]] = []
    passed = failed = skipped = 0

    for row in cases(suite):
        if row.get("skip"):
            skipped += 1
            results.append({"id": row["id"], "status": "skip", "reason": row["skip"]})
            continue
        try:
            actual = run_case(row)
        except Exception as exc:
            failed += 1
            results.append({"id": row["id"], "status": "fail", "error": repr(exc)})
            continue

        expected = row.get("expected")
        if actual == expected and _same_shape(actual, expected):
            passed += 1
            results.append({"id": row["id"], "status": "pass"})
        else:
            failed += 1
            results.append(
                {"id": row["id"], "status": "fail", "expected": expected, "actual": actual}
            )

    return {
        "suite": suite,
        "version": version(),
        "language": "python",
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }


def _same_shape(actual: Any, expected: Any) -> bool:
    """Refuse ``True == 1`` and ``False == 0``.

    Python's ``==`` treats booleans as integers, so a ``truthy`` row expecting
    ``False`` would be satisfied by an implementation returning ``0``. The
    peers compare with ``===``; this restores that.
    """
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    if isinstance(actual, dict) and isinstance(expected, dict):
        return all(_same_shape(actual[k], expected[k]) for k in actual.keys() & expected.keys())
    if isinstance(actual, list) and isinstance(expected, list):
        return all(_same_shape(a, e) for a, e in zip(actual, expected, strict=False))
    return True


def format_summary(summary: dict[str, Any]) -> str:
    """A summary that always names the skips.

    A bare "3 skipped" in a log reads identically to full coverage at a glance,
    so every skipped row is printed with its reason.
    """
    lines = [
        f"conformance {summary['suite']} @ {summary['version']} ({summary['language']}): "
        f"{summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped"
    ]
    for row in summary["results"]:
        if row["status"] == "skip":
            lines.append(f"  SKIP {row['id']}: {row['reason']}")
        elif row["status"] == "fail":
            detail = row.get("error") or (
                f"expected {row.get('expected')!r}, got {row.get('actual')!r}"
            )
            lines.append(f"  FAIL {row['id']}: {detail}")
    return "\n".join(lines)
