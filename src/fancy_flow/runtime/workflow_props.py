"""Resolving what a caller passed against what a workflow declared.

The Python twin of ``@particle-academy/fancy-flow``'s
``src/runtime/workflow-props.ts`` and ``FancyFlow\\Runtime\\WorkflowProps``.
Deliberately a pure function over two plain mappings rather than a step inside
the runner: ``suites/flow/workflow-props`` in ``fancy-conformance`` is the table
all three runtimes run, and a rule that lives inside a runner is a rule each
runtime re-derives.

Every branch exists to make a mistake LOUD
------------------------------------------

The behaviour being replaced was silence. Run inputs were keyed BY NODE ID, so
a caller had to know the trigger happened to be called ``t``; and nothing
declared what a workflow accepted, so a misspelled key was not an error — the
value sat unread, the node saw nothing, and the run reported success with
output that was quietly wrong.

So an unknown key fails, a missing required value fails, and a wrong type
fails. None of them is a warning: a warning on a queue worker is a line in a
log nobody opens.

The CODE is the contract, not the message
-----------------------------------------

Each runtime words its errors idiomatically. The shared table asserts ``code``,
so parity is pinned on the decision rather than the phrasing — otherwise three
implementations would be held to a translation, and a wording improvement would
go red having changed nothing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

UNKNOWN_INPUT = "unknown_input"
MISSING_REQUIRED = "missing_required"
TYPE_MISMATCH = "type_mismatch"

__all__ = [
    "MISSING_REQUIRED",
    "TYPE_MISMATCH",
    "UNKNOWN_INPUT",
    "resolve_workflow_props",
]


def _type_of(value: Any) -> str:
    """The runtime type of a value, in the vocabulary a declaration uses.

    ``bool`` is checked BEFORE ``int`` because ``isinstance(True, int)`` is true
    in Python — a declaration saying ``number`` would otherwise accept ``True``,
    and a check that accepts a boolean where a count belongs is a check that
    passes while meaning nothing.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def resolve_workflow_props(
    declared: Sequence[Mapping[str, Any]] | None,
    passed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Check and fill a caller's props.

    Returns ``{"ok": True, "props": {...}}`` — supplied values plus declared
    defaults, and nothing else — or ``{"ok": False, "code": ..., "error": ...}``
    for the FIRST problem. One error rather than a list: a run stops at the
    first one anyway, and a caller fixing a call wants the thing to fix.
    """
    inputs = list(declared or [])
    given = dict(passed or {})

    names = [i["name"] for i in inputs if isinstance(i, Mapping) and isinstance(i.get("name"), str)]
    known = set(names)

    # UNKNOWN KEYS FIRST, and this is the check the whole feature is for.
    #
    # A caller who misspells `topic` as `topik` has configured nothing, and
    # before this the run went ahead and looked fine. Checking it before
    # anything else means the error names the word they TYPED rather than
    # complaining that a key they believe they supplied is missing.
    for name in given:
        if name not in known:
            suffix = (
                "this workflow declares no inputs"
                if not names
                else "known inputs: " + ", ".join(names)
            )
            return {
                "ok": False,
                "code": UNKNOWN_INPUT,
                "error": f'Unknown workflow input "{name}" — {suffix}.',
            }

    resolved: dict[str, Any] = {}

    for spec in inputs:
        if not isinstance(spec, Mapping) or not isinstance(spec.get("name"), str):
            continue

        name = spec["name"]
        declared_type = spec.get("type")
        declared_type = declared_type if isinstance(declared_type, str) else None

        # Membership, never truthiness. `0`, `False` and `""` are values a
        # caller MEANT to pass, and a default applied over them is a silent
        # override — a declared limit of 0 quietly becoming 10 is not an error
        # anybody observes. `"default" in spec`, not `spec.get("default")`, for
        # the same reason: a declared default OF `None` is still a default.
        supplied = name in given
        has_default = "default" in spec

        if not supplied:
            if has_default:
                resolved[name] = spec["default"]
                continue

            if spec.get("required") is True:
                suffix = "" if declared_type is None else f" ({declared_type})"
                return {
                    "ok": False,
                    "code": MISSING_REQUIRED,
                    "error": f'Missing required workflow input "{name}"{suffix}.',
                }

            # Absent stays ABSENT — not None, not "". PHP has one absent value
            # and JavaScript has two; writing a placeholder here would make
            # `{{ $props.note }}` resolve differently across runtimes for one
            # graph.
            continue

        value = given[name]

        # An undeclared type accepts anything. "I am not asserting a shape"
        # must not degrade into "nothing is allowed", which is how a
        # defensively-written validator rejects valid calls.
        if declared_type is not None:
            actual = _type_of(value)
            if actual != declared_type:
                return {
                    "ok": False,
                    "code": TYPE_MISMATCH,
                    "error": (
                        f'Workflow input "{name}" expects {declared_type}, got {actual}.'
                    ),
                }

        resolved[name] = value

    return {"ok": True, "props": resolved}
