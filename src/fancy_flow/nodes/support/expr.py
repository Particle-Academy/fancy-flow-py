r"""``{{ }}`` resolution -- the Python twin of ``FancyFlow\\Nodes\\Support\\Expr``
and of ``@particle-academy/fancy-flow``'s ``evaluateExpression``.

Deliberately NOT a general expression language, and it must not grow into one:
it resolves a dot-path against a context and nothing else -- no arithmetic, no
comparisons, no calls. Hosts that want real expressions override the executor.

Divergence here is a correctness bug rather than a style difference: the same
graph is authored once and may run on any of the three runtimes.
``suites/shared/expr`` in ``particle-academy/fancy-conformance`` is the fixture
table all three run, so parity is a test result instead of a claim.

Two decisions are load-bearing and easy to get wrong in Python:

* **Scanning, not a regex.** The TypeScript twin was rewritten to
  ``indexOf`` after two CodeQL ``js/polynomial-redos`` alerts, and the second
  survived the obvious pattern fix -- a global lazy scan for a delimiter that
  never arrives is quadratic by construction. Python's ``re`` has the same
  backtracking engine, so the same scan is used here rather than a translated
  pattern. It also happens to be the only way to reproduce the peer runtimes'
  one odd corner exactly (see :func:`_whole_expression`).
* **Truthiness is PHP's, not Python's.** ``"0"``, ``"false"`` and ``[]`` are
  all truthy in JavaScript and falsy in PHP; a branch node reading a form value
  or a JSON body hits every one of them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Final

__all__ = ["evaluate", "resolve_path", "text", "truthy"]

#: Strings the peer runtimes treat as false.
_FALSY_STRINGS: Final = frozenset({"", "0", "false", "no", "off", "null"})


def _whole_expression(trimmed: str) -> str | None:
    """The inner text of a template that is exactly one expression, else ``None``.

    Note the deliberate corner: ``{{a}}{{b}}`` is a WHOLE expression whose path
    is ``a}}{{b`` (which resolves to ``None``), because the PHP pattern is
    end-anchored and its lazy capture has to grow to reach the end. Both peer
    runtimes do this; reproducing it is the point.
    """
    if len(trimmed) < 4:
        return None
    if not trimmed.startswith("{{") or not trimmed.endswith("}}"):
        return None
    return trimmed[2:-2]


def _interpolate(template: str, resolve: Callable[[str], str]) -> str:
    """Replace every ``{{ ... }}`` run, left to right, in a single pass.

    An unterminated ``{{`` is literal text -- what the regex did by simply not
    matching, and the case an author hits constantly while typing.
    """
    out: list[str] = []
    i = 0
    while True:
        open_at = template.find("{{", i)
        if open_at == -1:
            out.append(template[i:])
            return "".join(out)

        close_at = template.find("}}", open_at + 2)
        if close_at == -1:
            out.append(template[i:])
            return "".join(out)

        out.append(template[i:open_at])
        out.append(resolve(template[open_at + 2 : close_at]))
        i = close_at + 2


def resolve_path(path: str, context: dict[str, Any]) -> Any:
    """Resolve a dot-path against the context, honouring the ``$json`` alias.

    ``$json`` and ``$input`` both point at the ``in`` port value when the
    context has one, and at the whole context otherwise -- the fallback that
    makes ``{{ $json.x }}`` work on a trigger node with no upstream input.

    A path that does not resolve returns ``None``. Lists are addressed by
    numeric segment, matching PHP list access; nothing else is special-cased,
    which is on purpose. JavaScript resolves ``list.length`` because arrays
    carry a ``length`` property, and PHP does not -- so neither does this.
    """
    trimmed = path.strip()
    if trimmed == "":
        return None

    segments = trimmed.split(".")

    if segments[0] in ("$json", "$input"):
        cursor: Any = context["in"] if isinstance(context, dict) and "in" in context else context
        segments = segments[1:]
    else:
        cursor = context

    for segment in segments:
        if isinstance(cursor, dict):
            if segment not in cursor:
                return None
            cursor = cursor[segment]
        elif isinstance(cursor, (list, tuple)):
            index = _as_index(segment)
            if index is None or index >= len(cursor):
                return None
            cursor = cursor[index]
        else:
            # Attribute access on arbitrary objects, to match PHP reading
            # public properties. Dunders are refused: a dot-path in a config
            # field is author input, and `{{ x.__class__ }}` must not be a
            # doorway into the interpreter.
            if segment.startswith("_") or not hasattr(cursor, segment):
                return None
            cursor = getattr(cursor, segment)

    return cursor


def evaluate(template: Any, context: dict[str, Any] | None = None) -> Any:
    """Evaluate a template against a context.

    A string that is EXACTLY one expression returns the resolved value with its
    type intact -- ``{{ $json.count }}`` gives a number, not ``"3"``. Anything
    else interpolates each run as text. That distinction is load-bearing: it is
    what lets one config field carry either a value or a sentence.

    Non-string templates pass through untouched, so this is safe to map over a
    whole config object.
    """
    if not isinstance(template, str):
        return template

    context = context if context is not None else {}
    trimmed = template.strip()

    whole = _whole_expression(trimmed)
    if whole is not None:
        return resolve_path(whole, context)

    return _interpolate(template, lambda path: _stringify(resolve_path(path, context)))


def truthy(value: Any) -> bool:
    """Truthiness for branch / switch decisions -- PHP's rules, not Python's.

    Note ``bool`` is checked before ``int``: in Python ``True`` IS an ``int``,
    and falling through would compare it against zero.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_STRINGS
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def text(value: Any) -> str:
    """Coerce a value to text the way interpolation does."""
    return _stringify(value)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return _number_text(value)
    try:
        # Separator-free, matching JSON.stringify and json_encode: an
        # interpolated object must read the same in a Slack message whichever
        # runtime sent it.
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _number_text(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        # 3.0 prints as "3" on both peers; Python's str() would say "3.0".
        return str(int(value))
    return str(value)


def _as_index(segment: str) -> int | None:
    try:
        index = int(segment)
    except ValueError:
        return None
    return index if index >= 0 else None
