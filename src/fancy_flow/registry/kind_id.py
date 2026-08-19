"""The naming convention for node-kind ids, and the only place it is spelled out.

A kind's name is its CANONICAL id and is what gets written into saved
documents -- so a bare name two packages could both claim is unfixable after the
fact: the ambiguous string is already in the document. Canonical ids are
therefore namespaced (``@particle-academy/llm_router``), and every previous
spelling stays registered as an ALIAS so graphs saved before a rename keep
opening.

:func:`variants` is the structural fallback for lookups that have no registry
to consult; explicit aliases always take precedence over convention.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "LEGACY_NAMESPACE",
    "NAMESPACE",
    "bare",
    "builtin_aliases",
    "canonical",
    "is_namespaced",
    "matches",
    "variants",
]

NAMESPACE: Final = "@particle-academy/"

#: The namespace shipped before the package name was settled.
LEGACY_NAMESPACE: Final = "@fancy/"


def is_namespaced(kind_id: str) -> bool:
    return kind_id.startswith("@")


def canonical(name: str) -> str:
    """``manual_trigger`` -> ``@particle-academy/manual_trigger``. Idempotent."""
    return name if is_namespaced(name) else NAMESPACE + name


def bare(kind_id: str) -> str:
    """``@particle-academy/manual_trigger`` -> ``manual_trigger``."""
    if not is_namespaced(kind_id):
        return kind_id
    slash = kind_id.rfind("/")
    return kind_id if slash == -1 else kind_id[slash + 1 :]


def builtin_aliases(name: str) -> list[str]:
    """The aliases a built-in kind keeps: its bare name and the legacy namespace."""
    b = bare(name)
    return [b, LEGACY_NAMESPACE + b]


def matches(kind_id: str, bare_name: str) -> bool:
    """Does ``kind_id`` name the built-in ``bare_name`` under any of its spellings?

    Deliberately narrow: only the bare name and fancy-flow's own namespaces
    match, so a third party's ``@acme/note`` is NOT mistaken for the builtin.
    """
    return kind_id in (bare_name, NAMESPACE + bare_name, LEGACY_NAMESPACE + bare_name)


def variants(kind_id: str) -> list[str]:
    """Every id this one could also be written as, ``kind_id`` first.

    Order is preference order: an exact match wins, then the canonical form,
    then the older spellings.
    """
    b = bare(kind_id)
    ordered = [kind_id, NAMESPACE + b, LEGACY_NAMESPACE + b, b]
    seen: dict[str, None] = {}
    for item in ordered:
        seen.setdefault(item, None)
    return list(seen)
