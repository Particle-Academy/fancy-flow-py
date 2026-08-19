"""Branching sugar for executor return values.

The engine inspects a result and decides which output ports fire:

1. ``Port.only("true", value)``   -> ``{"__port": "true", "value": ...}``
   Only the named port emits, carrying ``value``.
2. ``Port.branch("true", value)`` -> ``{"branch": "true", "value": ...}``
   Decision sugar. With ``value`` omitted the whole result object is carried,
   matching the peer runtimes' ``r.value ?? r`` rule.
3. Anything else — published on every declared output port.

These mirror fancy-flow's ``__port`` / ``branch`` conventions exactly, so an
identical graph branches identically on Node, PHP and Python.
"""

from __future__ import annotations

from typing import Any

__all__ = ["Port"]


class Port:
    @staticmethod
    def only(port_id: str, value: Any = None) -> dict[str, Any]:
        return {"__port": port_id, "value": value}

    @staticmethod
    def branch(port_id: str, value: Any = None) -> dict[str, Any]:
        return {"branch": port_id, "value": value}
