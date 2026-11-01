"""The one definition of a blank KEY, shared by ``transform``'s executor and the
validator that admits its config.

A key is the string stripped by ``str.strip()`` -- every character for which
``str.isspace()`` is true: ASCII tab, LF, vertical tab, form feed, CR and space,
the separators U+001C to U+001F, U+0085, U+00A0, and every Unicode ``Zs``,
``Zl`` and ``Zp`` character. A non-string is no key at all. It is blank when
that leaves ``""``.

It lives in its own module because the two callers used to carry their own
reading of "blank": the validator compared with ``""``, the executor stripped,
so ``{"key": "   ", "value": "x"}`` was admitted and then dropped at run time.
"""

from __future__ import annotations

from typing import Any


def trimmed_key(key: Any) -> str:
    """``key`` stripped, or ``""`` when it is not a string."""
    return key.strip() if isinstance(key, str) else ""
