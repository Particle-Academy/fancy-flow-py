"""Turning a model's reply into schema-typed data.

A host adapter supporting provider-native structured output (Anthropic tool
results, OpenAI ``response_format: json_schema``) should return the parsed value
itself, and then none of this runs. This exists for the rest: an adapter that
ignores ``response_schema`` and returns prose would otherwise hand downstream
nodes a string where they expect data, silently.

That is the failure this module refuses to allow. **Every path here either
produces schema-valid data or raises.** There is deliberately no "return None
and let the next node deal with it" -- a truncated array that decodes to nothing
looks exactly like a model that found no results, and a workflow that silently
processes zero records is the expensive kind of wrong.

The validator is a SUBSET, and saying so is the point
-----------------------------------------------------

Enforced: ``type``, ``required``, ``properties`` (recursively), ``items``
(recursively), ``enum``. Everything else -- ``minLength``, ``pattern``,
``format``, ``additionalProperties``, ``oneOf`` -- is **ignored, not
enforced**.

The subset is what a workflow author actually leans on to keep a downstream
``{{ $json.data[0].title }}`` from resolving to nothing, and it is
dependency-free, which matters more here than completeness: core takes no
runtime dependencies, and a half-integrated validator that silently skips the
keyword you relied on is worse than one that names what it checks.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...exceptions import FlowError

__all__ = ["extract", "validate"]

_FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)(?:\r?\n)?[ \t]*```", re.DOTALL)


def extract(text: str) -> Any:
    """Pull a JSON value out of whatever the model actually said.

    Handles the three things models reliably do despite instructions, each
    reported from real runs:

    - wrap the JSON in a ```json fence
    - open with a prose preamble ("Here are the results:")
    - truncate mid-array on a long answer

    The first two are recoverable and are recovered. The third is NOT: a
    truncated array is indistinguishable from a short one once it fails to
    parse, so it raises rather than guessing.
    """
    trimmed = text.strip()

    if trimmed == "":
        raise FlowError("The model returned an empty response, so there is no JSON to read.")

    decoded = _try_json(trimmed)
    if decoded is not _MISS:
        return decoded

    match = _FENCE.search(trimmed)
    if match is not None:
        inner = match.group(1).strip()
        decoded = _try_json(inner)
        if decoded is not _MISS:
            return decoded
        raise FlowError(
            "The model returned a fenced block that is not valid JSON. This is usually "
            "truncation - raise max_tokens, or narrow the schema so the answer fits."
        )

    sliced = _first_balanced_value(trimmed)
    if sliced is not None:
        decoded = _try_json(sliced)
        if decoded is not _MISS:
            return decoded

    raise FlowError(
        "The model did not return JSON that could be parsed. First 200 characters: " + trimmed[:200]
    )


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate against the supported subset. Empty list means it conforms."""
    errors: list[str] = []

    declared = schema.get("type")
    if isinstance(declared, str) and not _matches_type(value, declared):
        return [f"{path} should be {declared}, got {_describe(value)}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_same(value, allowed) for allowed in enum):
        errors.append(f"{path} is not one of the allowed values")

    if isinstance(value, dict):
        for key in schema.get("required") or ():
            if str(key) not in value:
                errors.append(f"{path}.{key} is required but missing")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub_schema in properties.items():
                if isinstance(sub_schema, dict) and key in value:
                    errors.extend(validate(value[key], sub_schema, f"{path}.{key}"))

    if isinstance(value, list) and declared == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(validate(item, items, f"{path}[{index}]"))

    return errors


_MISS = object()


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return _MISS


def _first_balanced_value(text: str) -> str | None:
    """The first balanced ``{...}`` or ``[...]`` in a string.

    Scanned rather than matched with a regex. Balanced delimiters are not a
    regular language, so a pattern either gets nesting wrong or becomes the
    kind of backtracking construct that has already cost this suite three ReDoS
    alerts. This is a single left-to-right pass, and it tracks string state so
    a brace inside ``"{"`` does not count.
    """
    start: int | None = None
    opener = closer = ""
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if start is None:
            if char in "{[":
                start = index
                opener = char
                closer = "}" if char == "{" else "]"
                depth = 1
            continue

        if escaped:
            escaped = False
            continue

        if char == "\\" and in_string:
            escaped = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    # Opened and never closed -- truncation. Returning the partial text would
    # decode to nothing and read as "no results".
    return None


def _matches_type(value: Any, declared: str) -> bool:
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "number":
        # JSON has one number type; a schema saying `number` must accept an
        # int, or {"type":"number"} rejects 3 and every author hits it.
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "null":
        return value is None
    return True


def _same(value: Any, allowed: Any) -> bool:
    """Strict equality, with ``True == 1`` refused.

    The peer runtimes compare with ``===``; Python's ``==`` would let a boolean
    satisfy an enum of integers.
    """
    if isinstance(value, bool) != isinstance(allowed, bool):
        return False
    return bool(value == allowed)


def _describe(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "bool"
    if value is None:
        return "null"
    return type(value).__name__
