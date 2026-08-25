"""``__version__`` must be the version that was actually installed.

This is not a style point. ``__version__`` was a literal ``"0.1.0"`` while the
distribution was ``0.4.0`` — three releases stale — and nothing compared them.
The runtime's first outside consumer installed 0.4.0, read 0.1.0, and reported
it. Anything gating on the version at runtime would have branched on a release
that no longer existed.

The fix removes the second copy rather than re-syncing it: ``__version__`` is
read from the installed distribution metadata, so there is no longer a number
that CAN drift. These tests pin that property, not the current value — asserting
the literal would recreate the very duplicate being deleted.
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

import fancy_flow


def test_version_matches_the_installed_distribution() -> None:
    assert fancy_flow.__version__ == distribution_version("fancy-flow")


def test_version_matches_pyproject() -> None:
    """The end-to-end claim, in the terms a release actually happens in.

    The test above compares the package to its own metadata, which agree by
    construction once the read is dynamic. This one compares it to the file a
    human edits when they cut a release — the only place the number is
    authored — so it fails if an editable install goes stale as well.
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert fancy_flow.__version__ == declared, (
        f"fancy_flow.__version__ is {fancy_flow.__version__!r} but pyproject.toml "
        f"declares {declared!r}. If these disagree, reinstall — and if they disagree "
        f"after a reinstall, the dynamic read has been replaced by a literal again."
    )


def test_version_is_not_a_hardcoded_literal() -> None:
    """Guard the FIX, not just its result.

    Someone re-introducing ``__version__ = "0.4.1"`` would make both tests above
    pass on the day they wrote it, and drift again on the next release. That is
    exactly how this bug happened the first time, so the shape is asserted
    directly.
    """
    source = (Path(fancy_flow.__file__)).read_text(encoding="utf-8")

    assert not re.search(r'^__version__\s*=\s*["\']', source, re.M), (
        "__version__ is assigned a string literal again. Read it from the "
        "installed distribution metadata instead — a literal is a second copy "
        "of pyproject.toml's number, and second copies drift silently."
    )
