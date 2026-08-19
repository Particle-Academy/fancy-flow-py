"""Node manifests, and what it takes for a node to claim a Python backend.

A node is VENDORED source, not a package: one directory carrying its React kind
and a backend per runtime, copied into a consumer's project. There is no node
package and there must never be one -- the whole point is that adding a node
costs a consumer no new dependency.

What that means here is narrow and important: the manifest's ``runtimes`` map is
OPEN. Nothing in this validator (or in the PHP and TypeScript twins) enumerates
``ts`` and ``php``, so a node declaring ``py`` validates today, with no change to
any runtime. The work to make ``py`` reach consumers is in the CLI and the
registry, and is written up in the plan.
"""

from __future__ import annotations

import pytest

from fancy_flow.marketplace import (
    check_capabilities,
    check_runtime_support,
    is_valid,
    satisfies_range,
    validate,
)


def manifest(**overrides):
    base = {
        "schemaVersion": 1,
        "name": "acme/flow-nodes",
        "kind": "@acme/send_invoice",
        "title": "Send invoice",
        "category": "io",
        "ui": ["ui"],
        "runtimes": {
            "ts": {"files": ["js"], "engine": ">=0.30.0"},
            "php": {"files": ["php"], "engine": ">=0.9.0"},
        },
        "fixtures": "nodes/send-invoice/fixtures/send-invoice.json",
        "sideEffects": "unsafe-to-replay",
    }
    base.update(overrides)
    return base


def errors(m) -> list[str]:
    return [p["field"] for p in validate(m) if p["level"] == "error"]


def test_a_well_formed_manifest_validates() -> None:
    assert is_valid(manifest())


def test_a_python_runtime_needs_no_change_to_this_validator() -> None:
    """The finding that makes a Python node backend cheap.

    The runtime key is open by construction on every runtime, so a node can
    declare `py` today and every validator accepts it.
    """
    m = manifest(
        runtimes={
            "ts": {"files": ["js"], "engine": ">=0.30.0"},
            "php": {"files": ["php"], "engine": ">=0.9.0"},
            "py": {"files": ["py"], "engine": ">=0.1.0"},
        }
    )
    assert is_valid(m)


def test_a_node_missing_the_hosts_runtime_is_an_error_not_a_warning() -> None:
    """It would install, appear in the palette, and then fail to run."""
    problems = check_runtime_support(manifest(), ["py"], {"py": "0.1.0"})
    assert [p["level"] for p in problems] == ["error"]
    assert "executes on py" in problems[0]["message"]


def test_an_engine_too_old_for_the_node_is_an_error() -> None:
    m = manifest(runtimes={"py": {"files": ["py"], "engine": ">=0.5.0"}})
    problems = check_runtime_support(m, ["py"], {"py": "0.1.0"})
    assert problems[0]["level"] == "error"
    assert "this host runs 0.1.0" in problems[0]["message"]


def test_an_unchecked_range_is_a_warning_not_silence() -> None:
    """ "We did not check" and "it is fine" must not look the same."""
    m = manifest(runtimes={"py": {"files": ["py"], "engine": ">=0.1.0"}})
    problems = check_runtime_support(m, ["py"], {})
    assert [p["level"] for p in problems] == ["warning"]


def test_a_bare_kind_id_is_refused() -> None:
    """The one mistake that cannot be repaired: the ambiguous string is already
    written into saved documents."""
    assert "kind" in errors(manifest(kind="send_invoice"))


def test_the_first_party_scope_is_reserved() -> None:
    problems = validate(manifest(kind="@particle-academy/send_invoice"))
    assert any(p["field"] == "kind" and p["level"] == "warning" for p in problems)


def test_a_node_implementing_no_runtime_is_refused() -> None:
    assert "runtimes" in errors(manifest(runtimes={}))


def test_the_pre_per_runtime_single_range_is_named_rather_than_ignored() -> None:
    """Silently, it means "no constraint"."""
    assert "fancyFlow" in errors(manifest(fancyFlow=">=0.3"))


def test_entry_and_package_are_refused_because_nodes_are_not_installed() -> None:
    m = manifest(runtimes={"py": {"files": ["py"], "engine": ">=0.1", "package": "acme-node"}})
    assert "runtimes.py" in errors(m)


def test_fixtures_are_required_because_parity_must_be_run_not_claimed() -> None:
    m = manifest()
    del m["fixtures"]
    assert "fixtures" in errors(m)


def test_a_package_cannot_vouch_for_itself() -> None:
    assert "verified" in errors(manifest(verified=True))


def test_an_unknown_schema_version_stops_the_rest_of_the_report() -> None:
    """Every check below would be guessing at a shape we do not know."""
    problems = validate(manifest(schemaVersion=99))
    assert len(problems) == 1
    assert problems[0]["field"] == "schemaVersion"


def test_a_missing_required_capability_is_an_error() -> None:
    """It is meant to surface at AUTHOR time so an editor can grey the node and
    name what the host never registered."""
    m = manifest(capabilities={"gitRepository": "required", "telemetry": "optional"})
    problems = check_capabilities(m, {"telemetry": False})
    levels = {p["field"]: p["level"] for p in problems}
    assert levels == {
        "capabilities.gitRepository": "error",
        "capabilities.telemetry": "warning",
    }


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ("0.1.0", ">=0.1.0", True),
        ("0.1.0", ">=0.2.0", False),
        ("2.0.0", "^1.0", False),
        ("1.9.9", "^1.0", True),
    ],
)
def test_ranges(version: str, spec: str, expected: bool) -> None:
    assert satisfies_range(version, spec) is expected
