"""The node package manifest.

A node is not one artifact: it is a kind definition plus an executor for EACH
runtime the consumer runs. A node shipping only a TypeScript executor is
unusable to anyone executing on PHP or Python, and without a manifest that is
invisible until a run fails.

Validation must agree across the runtimes, field for field -- a node accepted by
one runtime's tooling and rejected by another's is worse than no check at all.

Why the engine range lives per runtime
--------------------------------------

The first cut carried ONE ``fancyFlow`` range and it was wrong: the engines
version independently, so a single range cannot say "needs ts >=0.15 AND php
>=0.7", let alone add a third. A node supporting several runtimes would install
cleanly against a host whose OTHER runtime was too old.

The runtime key is deliberately open. ``ts`` and ``php`` are what exists today
and ``py`` is what this package adds; nothing here enumerates them, so a fourth
runtime needs no change to any validator.
"""

from __future__ import annotations

import re
from typing import Any, Final

__all__ = [
    "SCHEMA_VERSION",
    "check_capabilities",
    "check_runtime_support",
    "is_valid",
    "satisfies_range",
    "validate",
]

#: Must match fancy-flow's ``NODE_MANIFEST_SCHEMA_VERSION``.
SCHEMA_VERSION: Final = 1

#: Reserved for first-party packages; the registry rejects other claimants.
_FIRST_PARTY_SCOPE: Final = "@particle-academy/"

#: ``@scope/name`` -- the shape namespaced kind ids take.
_NAMESPACED_KIND: Final = re.compile(r"^@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)

#: Durable runs RETRY, so a node that writes must say it is unsafe to replay.
_SIDE_EFFECTS: Final = ("none", "idempotent", "unsafe-to-replay")

_REQUIREMENTS: Final = ("required", "optional")

Problem = dict[str, str]


def _error(field: str, message: str) -> Problem:
    return {"level": "error", "field": field, "message": message}


def _warning(field: str, message: str) -> Problem:
    return {"level": "warning", "field": field, "message": message}


def validate(manifest: Any) -> list[Problem]:
    """Every problem with a manifest.

    Returns all of them rather than raising on the first: an author fixing a
    package wants the whole list, and a validator that reveals one error per run
    turns a five-minute fix into five round trips.
    """
    if not isinstance(manifest, dict):
        return [_error("", "Manifest must be a JSON object.")]

    problems: list[Problem] = []

    # Version first: an unknown version means every check below is guessing at
    # a shape we do not know, so say so instead of reporting confident nonsense
    # about the rest.
    schema_version = manifest.get("schemaVersion")
    if schema_version != SCHEMA_VERSION:
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            problems.append(_error("schemaVersion", f"Required, and must be {SCHEMA_VERSION}."))
        else:
            return [
                _error(
                    "schemaVersion",
                    f"Unsupported manifest version {schema_version}; this fancy-flow "
                    f"understands {SCHEMA_VERSION}. Upgrade fancy-flow to install this node.",
                )
            ]

    if not isinstance(manifest.get("name"), str) or not str(manifest.get("name")).strip():
        problems.append(_error("name", "Required - the package name as installed."))

    _validate_kind(manifest.get("kind"), problems)

    # A leftover single range from the pre-per-runtime shape. Named explicitly
    # rather than ignored - silently, it means "no constraint".
    if "fancyFlow" in manifest:
        problems.append(
            _error(
                "fancyFlow",
                "A single engine range cannot express the split - it cannot say "
                '"needs ts >=0.15 AND php >=0.7". Move the range into each entry of '
                "`runtimes` as `engine`.",
            )
        )

    _validate_aliases(manifest, problems)

    config_version = manifest.get("configVersion")
    if "configVersion" in manifest and (
        not isinstance(config_version, int) or isinstance(config_version, bool)
    ):
        problems.append(_error("configVersion", "Must be an integer."))

    if "sideEffects" in manifest and manifest["sideEffects"] not in _SIDE_EFFECTS:
        problems.append(_error("sideEffects", "Must be one of: " + ", ".join(_SIDE_EFFECTS) + "."))

    _validate_runtimes(manifest.get("runtimes"), problems)

    # The publish gate. Cross-runtime drift does not fail loudly - it completes,
    # down one path, with no error - so it has to be caught by something that
    # runs, not by review.
    if not isinstance(manifest.get("fixtures"), str) or not str(manifest["fixtures"]).strip():
        problems.append(
            _error(
                "fixtures",
                "Required - path to the node's golden fixtures. Every claimed runtime "
                "runs them, which is what makes cross-runtime parity verified rather "
                "than claimed.",
            )
        )

    _validate_capabilities(manifest, problems)

    if "verified" in manifest:
        problems.append(
            _error(
                "verified",
                "Assigned by the registry, not the package. Remove it - a package "
                "cannot vouch for itself.",
            )
        )

    return problems


def is_valid(manifest: Any) -> bool:
    """True when nothing in :func:`validate` was error-level. Warnings do not block."""
    return not any(p["level"] == "error" for p in validate(manifest))


def check_runtime_support(
    manifest: dict[str, Any],
    host_runtimes: list[str],
    engine_versions: dict[str, str] | None = None,
) -> list[Problem]:
    """Check a node against the runtimes a host executes on, and their versions.

    Two failures live here, both errors because the node genuinely cannot run: a
    runtime the package does not implement, and one it implements against an
    engine newer than the host's.

    An unchecked range is a WARNING rather than silence -- "we did not check"
    and "it is fine" must not look the same.
    """
    engine_versions = engine_versions or {}
    runtimes = manifest.get("runtimes")
    runtimes = runtimes if isinstance(runtimes, dict) else {}
    provided = list(runtimes)
    problems: list[Problem] = []
    kind = str(manifest.get("kind", "this node"))

    missing = [r for r in host_runtimes if r not in provided]
    if missing:
        problems.append(
            _error(
                "runtimes",
                f"{kind} implements {', '.join(provided) or 'no runtime'} but this "
                f"project executes on {', '.join(missing)}. The node would install, "
                "appear in the palette, and then fail to run.",
            )
        )

    for runtime in host_runtimes:
        spec = runtimes.get(runtime)
        if not isinstance(spec, dict):
            continue

        engine_range = str(spec.get("engine", ""))
        host_version = engine_versions.get(runtime)

        if host_version is None:
            problems.append(
                _warning(
                    f"runtimes.{runtime}.engine",
                    f"{kind} needs {runtime} engine {engine_range}; this host did not "
                    f"report its {runtime} version, so the range was not checked.",
                )
            )
            continue

        if not satisfies_range(host_version, engine_range):
            problems.append(
                _error(
                    f"runtimes.{runtime}.engine",
                    f"{kind} needs {runtime} engine {engine_range}, but this host runs "
                    f"{host_version}.",
                )
            )

    return problems


def check_capabilities(manifest: dict[str, Any], available: dict[str, bool]) -> list[Problem]:
    """Check that every capability a node needs is wired.

    A missing ``required`` capability is an ERROR -- that is the point of the
    requirement level. It is meant to surface at AUTHOR time so an editor can
    grey the node and name what the host never registered, rather than the node
    installing cleanly and silently no-opping during a run. An ``optional`` one
    is a warning: the node still works, with less.
    """
    needed = manifest.get("capabilities")
    needed = needed if isinstance(needed, dict) else {}
    kind = str(manifest.get("kind", "this node"))
    problems: list[Problem] = []

    for capability, requirement in needed.items():
        if available.get(capability) is True:
            continue
        message = f"{kind} needs the {capability} capability, which this host has not registered."
        problems.append(
            _error(f"capabilities.{capability}", message)
            if requirement == "required"
            else _warning(
                f"capabilities.{capability}",
                message + " It is optional, so the node runs with less.",
            )
        )

    return problems


_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")
_CLAUSE = re.compile(r"^(\^|~|>=|>|<=|<|=)?\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def satisfies_range(version: str, spec: str) -> bool:
    """Does ``version`` satisfy the range ``spec``?

    A deliberately small semver subset -- ``^ ~ >= > <= < =``, unions with
    ``||``, and ``*``. Asserted against ``suites/shared/satisfies-range`` in
    ``fancy-conformance``, so this and the PHP twin cannot drift.

    Note the pre-1.0 caret rule: below 1.0.0 a minor bump is breaking, so
    ``^0.5`` means ``0.5.x``. That is the range every pre-1.0 package in this
    suite actually needs.
    """
    spec = spec.strip()
    if spec in ("*", ""):
        return True

    parsed = _parse_version(version)
    if parsed is None:
        return False

    return any(_satisfies_clause(parsed, clause.strip()) for clause in spec.split("||"))


def _parse_version(version: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(version.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _satisfies_clause(actual: tuple[int, int, int], clause: str) -> bool:
    match = _CLAUSE.match(clause)
    if match is None:
        return False

    op = match.group(1) or "="
    target = (int(match.group(2)), int(match.group(3) or 0), int(match.group(4) or 0))
    cmp = (actual > target) - (actual < target)

    if op == ">=":
        return cmp >= 0
    if op == ">":
        return cmp > 0
    if op == "<=":
        return cmp <= 0
    if op == "<":
        return cmp < 0
    if op == "=":
        return cmp == 0
    if op == "~":
        # Same major+minor, patch may rise.
        return cmp >= 0 and actual[0] == target[0] and actual[1] == target[1]
    if op == "^":
        if target[0] == 0:
            return cmp >= 0 and actual[0] == 0 and actual[1] == target[1]
        return cmp >= 0 and actual[0] == target[0]
    return False


def _validate_kind(kind: Any, problems: list[Problem]) -> None:
    if not isinstance(kind, str) or not kind.strip():
        problems.append(_error("kind", "Required - the canonical kind id this package provides."))
        return

    if _NAMESPACED_KIND.match(kind) is None:
        # The one mistake that cannot be repaired: the ambiguous string is
        # already written into saved documents.
        problems.append(
            _error(
                "kind",
                f'"{kind}" must be namespaced as @scope/name - a bare id makes stored '
                "graphs ambiguous, and that is unfixable once documents carry it.",
            )
        )
        return

    if kind.startswith(_FIRST_PARTY_SCOPE):
        problems.append(
            _warning(
                "kind",
                _FIRST_PARTY_SCOPE + "* is reserved for first-party nodes; the registry "
                "will reject this unless the package is first-party.",
            )
        )


def _validate_aliases(manifest: dict[str, Any], problems: list[Problem]) -> None:
    if "aliases" not in manifest:
        return

    aliases = manifest["aliases"]
    bad = not isinstance(aliases, list)
    if not bad:
        bad = any(not isinstance(a, str) or not a.strip() for a in aliases)

    if bad:
        problems.append(_error("aliases", "Must be an array of non-empty id strings."))


def _validate_runtimes(runtimes: Any, problems: list[Problem]) -> None:
    if isinstance(runtimes, dict) and not runtimes:
        problems.append(
            _error("runtimes", "A node that implements no runtime cannot execute anywhere.")
        )
        return

    if not isinstance(runtimes, dict):
        problems.append(
            _error("runtimes", "Required - an object of runtime id to { files, engine }.")
        )
        return

    for runtime, spec in runtimes.items():
        if not isinstance(spec, dict):
            problems.append(
                _error(f"runtimes.{runtime}", "Must be an object of { files, engine }.")
            )
            continue

        files = spec.get("files")
        valid_files = (
            isinstance(files, list)
            and bool(files)
            and all(isinstance(f, str) and f.strip() for f in files)
        )
        if not valid_files:
            problems.append(
                _error(
                    f"runtimes.{runtime}",
                    "Needs `files` - the node source directories this runtime's backend "
                    "lives in, relative to the node.",
                )
            )

        if "entry" in spec or "package" in spec:
            # Nodes are VENDORED, not installed: the CLI copies a node's source
            # into the project the way it copies a component's. `entry` /
            # `package` described an npm/Composer install that no longer
            # happens, and leaving them readable would let a manifest claim an
            # install path nothing honours.
            problems.append(
                _error(
                    f"runtimes.{runtime}",
                    "`entry` / `package` are gone - nodes are copied into a project, not "
                    "installed from a registry. Declare `files` instead.",
                )
            )

        if not isinstance(spec.get("engine"), str) or not str(spec.get("engine")).strip():
            problems.append(
                _error(
                    f"runtimes.{runtime}.engine",
                    f"Required - the semver range of the {runtime} engine. Without it, "
                    f"this node installs against a {runtime} engine too old to run it.",
                )
            )


def _validate_capabilities(manifest: dict[str, Any], problems: list[Problem]) -> None:
    if "capabilities" not in manifest:
        return

    caps = manifest["capabilities"]
    if not isinstance(caps, dict):
        problems.append(
            _error(
                "capabilities",
                'Must be an object of capability id to "required" | "optional" - a bare '
                "list cannot say whether the node works without one.",
            )
        )
        return

    for capability, requirement in caps.items():
        if requirement not in _REQUIREMENTS:
            problems.append(
                _error(f"capabilities.{capability}", 'Must be "required" or "optional".')
            )
