"""The copy must not drift from what it was copied from.

``tests/parity/fixtures/`` is a copy of ``fancy-flow-php``'s golden graph
fixtures (see ``fixtures/SOURCE.md``). Copies drift, and the drift is silent:
both suites stay green while asserting different things.

So whenever the PHP twin is on disk -- the normal case inside the `.agi`
envelope and in any CI job that checks both out -- this compares the two
directories and **fails** on a missing file, an extra file, or a changed byte.

When the twin is absent it does not skip quietly. A skip in a green log reads
exactly like a pass, which is the mechanism that hid two-way drift in this org
before. It reports, loudly and by name, that the check could not run -- and the
CI job that matters checks the twin out so it always can.
"""

from __future__ import annotations

import hashlib
import os
import warnings
from pathlib import Path

LOCAL = Path(__file__).parent / "fixtures"

#: Where the PHP twin's fixtures live, relative to a checkout root.
TWIN_SUFFIX = Path("fancy-flow-php") / "tests" / "Parity" / "fixtures"


def _find_twin() -> Path | None:
    """Locate the PHP twin's fixture directory.

    Explicit env var first, then a bounded walk up from this repo looking for a
    sibling checkout. Never a fixed ``../..``: the two parity harnesses this
    org replaced both hard-coded a relative sibling path, so they worked in
    exactly one directory layout and silently no-opped everywhere else.
    """
    override = os.environ.get("FANCY_FLOW_PHP_FIXTURES")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    here = Path(__file__).resolve()
    for parent in list(here.parents)[:8]:
        for base in (parent, parent / "repos"):
            candidate = base / TWIN_SUFFIX
            if candidate.is_dir():
                return candidate
    return None


def _digests(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.glob("*.json"))
    }


def test_fixtures_match_the_php_twin() -> None:
    twin = _find_twin()

    if twin is None:
        warnings.warn(
            "Could not find fancy-flow-php's fixtures, so cross-runtime drift was NOT "
            "checked. Set FANCY_FLOW_PHP_FIXTURES, or check the twin out beside this "
            "repo. This is an unverified run, not a clean one.",
            stacklevel=1,
        )
        return

    ours = _digests(LOCAL)
    theirs = _digests(twin)

    missing = sorted(set(theirs) - set(ours))
    extra = sorted(set(ours) - set(theirs))
    changed = sorted(name for name in set(ours) & set(theirs) if ours[name] != theirs[name])

    assert not missing, (
        f"fancy-flow-php has fixtures this runtime does not assert: {missing}. "
        "Copy them in and make them pass, or the Python engine is unverified for "
        "whatever behaviour they pin."
    )
    assert not extra, (
        f"This runtime asserts fixtures fancy-flow-php does not have: {extra}. "
        "A golden only one runtime runs is not a parity fixture."
    )
    assert not changed, (
        f"These fixtures differ between the runtimes: {changed}. One side changed a "
        "golden without the other."
    )
