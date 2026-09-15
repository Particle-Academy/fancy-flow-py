"""The fixture set this port pins, and the CI checkout that must match it.

Rule 4 of fancy-conformance's ``runners/README.md``: print AND assert the pinned
suite version. Every suite in this directory prints the version it ran against,
but until this file nothing asserted it against a pin -- each file compared the
summary's version (``summary["suiteVersion"]``) with ``version()``, which is the
same number read twice. "We are on an old fixture set" should be visible in the
log, and a fixture set moving underneath the port should fail here rather than be
inferred later.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fancy_conformance import version

# Pinned at 0.22.0 on 2026-09-13, after re-running every table in this directory
# against a v0.22.0 checkout: flow/entry-points 7, flow/executor-resolution 8
# (+6 documented skips: FlowNode is flattened, so there is no data.kind),
# shared/expr 20, flow/kind-declaration-surface 20, shared/satisfies-range 17,
# shared/flow-run-identity 25, flow/workflow-props 21 -- nothing failed.
#
# Moved 0.22.0 -> 0.22.1 on 2026-09-13. That release changed no case and no
# golden (the Rust loader pins fancy-json by tag, plus docs); every table was
# re-run against a v0.22.1 checkout first all the same, and each printed the
# counts above, the same six skips (0201-0206) included -- nothing failed.
#
# Moved 0.22.1 -> 0.23.0 on 2026-09-14, in the same change as the
# fancy-flow-php#16 fix in `_whole_expression`. 0.23.0 adds shared/expr
# 0021-0026 (a whole-string expression is exactly one `{{ }}`), so that table is
# now 26; the code before the fix failed 0021, 0022, 0023, 0025 and 0026. Every
# other table was re-run against a v0.23.0 checkout and printed the counts above,
# the six skips included -- nothing failed.
#
# Moved 0.23.0 -> 0.24.0 on 2026-09-14, in the same change as the fancy-flow#17
# run diagnostics. 0.24.0 adds flow/run-diagnostics (14 rows: 6 warn, 8 silent),
# now run by test_run_diagnostics_conformance.py; the engine before the change
# failed its six warning rows. Every other table was re-run against a v0.24.0
# checkout and printed the counts above -- nothing failed.
#
# CI checks out `ref: v<this>` from .github/workflows/ci.yml. Move the two
# together, and only after re-running the tables;
# test_ci_checks_out_the_fixture_tag_this_suite_pins fails otherwise.
PINNED_SUITE_VERSION = "0.24.0"


def test_the_pinned_fixture_version_is_the_one_on_disk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with capsys.disabled():
        print(f"\nfancy-conformance on disk: {version()}, pinned: {PINNED_SUITE_VERSION}")

    assert version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {version()}, this port pins "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def _conformance_checkout_refs(workflow: str) -> list[str | None]:
    """The `ref:` of every workflow step that checks out fancy-conformance.

    Plain text on purpose: a YAML parser would be a dependency for one assertion.
    A step is a `- ` line plus everything indented deeper than it. `None` is a
    step with no `ref`, which checks out whatever `main` is at that moment.
    """
    lines = workflow.splitlines()
    refs: list[str | None] = []
    for index, line in enumerate(lines):
        start = re.match(r"(\s*)- ", line)
        if not start:
            continue
        step = [line]
        for following in lines[index + 1 :]:
            body = following.strip()
            indent = len(following) - len(following.lstrip())
            if body and not body.startswith("#") and indent <= len(start.group(1)):
                break
            step.append(following)
        text = "\n".join(step)
        if re.search(
            r"^\s*(- )?repository:\s*[\"']?Particle-Academy/fancy-conformance[\"']?\s*(#.*)?$",
            text,
            re.MULTILINE,
        ):
            ref = re.search(r"^\s*(- )?ref:\s*[\"']?([^\"'\s#]+)", text, re.MULTILINE)
            refs.append(ref.group(2) if ref else None)
    return refs


def test_the_checkout_ref_parser_sees_a_missing_ref() -> None:
    workflow = """
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/fancy-conformance
          path: .fancy-conformance
      - name: Pinned
        uses: actions/checkout@v4
        with:
          repository: "Particle-Academy/fancy-conformance"
          ref: 'v1.2.3'  # a comment
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/fancy-flow-php
          ref: v9.9.9
    """
    assert _conformance_checkout_refs(workflow) == [None, "v1.2.3"]


def test_ci_checks_out_the_fixture_tag_this_suite_pins() -> None:
    """The CI checkout `ref` and `PINNED_SUITE_VERSION` are one decision in two files.

    CI used to check fancy-conformance out with no `ref`, so every fixture release
    could turn this build red at once for a reason no commit here caused; four
    sibling ports sat red for weeks exactly that way. The pin is the contract:
    moving it is a deliberate commit in this repository, never a side effect of
    someone else's release.
    """
    here = Path(__file__).resolve()
    workflows = next(
        (p / ".github" / "workflows" for p in here.parents if (p / ".github/workflows").is_dir()),
        None,
    )
    assert workflows is not None, f"no .github/workflows above {here}"

    refs = {
        path.name: _conformance_checkout_refs(path.read_text(encoding="utf-8"))
        for path in sorted(workflows.glob("*.y*ml"))
    }
    refs = {name: found for name, found in refs.items() if found}
    # Vacuity guard: a parser that matched nothing would satisfy the loop below.
    assert refs, "no workflow checks out Particle-Academy/fancy-conformance"

    expected = f"v{PINNED_SUITE_VERSION}"
    for name, found in refs.items():
        assert found == [expected] * len(found), (
            f".github/workflows/{name} checks fancy-conformance out at {found}, but this "
            f"suite pins {PINNED_SUITE_VERSION}. Set `ref: {expected}` there, and move "
            "the pin and the ref together."
        )
