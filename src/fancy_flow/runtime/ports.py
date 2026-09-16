"""Branching sugar for executor return values.

The engine inspects a result and decides which output ports fire:

1. ``Port.only("true", value)``   -> ``{"__port": "true", "value": ...}``
   Only the named port emits, carrying ``value``.
2. ``Port.branch("true", value)`` -> ``{"branch": "true", "value": ...}``
   Decision sugar. With ``value`` omitted the whole result object is carried,
   matching the peer runtimes' ``r.value ?? r`` rule.
3. ``Port.many(["a", "c"], value)`` -> ``{"__ports": ["a", "c"], "value": ...}``
   Exactly those ports emit, each carrying ``value``.
   ``Port.many({"a": x, "c": y})`` -> ``{"__ports": {"a": x, "c": y}}``
   Exactly those ports emit, each carrying its OWN payload.
4. Anything else — published on every declared output port.

These mirror fancy-flow's ``__port`` / ``branch`` conventions exactly, so an
identical graph branches identically on Node, PHP and Python.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["Port"]


class Port:
    @staticmethod
    def only(port_id: str, value: Any = None) -> dict[str, Any]:
        return {"__port": port_id, "value": value}

    @staticmethod
    def branch(port_id: str, value: Any = None) -> dict[str, Any]:
        return {"branch": port_id, "value": value}

    @staticmethod
    def many(ports: Sequence[str] | Mapping[str, Any], value: Any = None) -> dict[str, Any]:
        """A CHOSEN SUBSET of the ports (#18).

        A SEQUENCE is port ids, each carrying ``value``; a MAPPING is port id ->
        its own payload, and ``value`` is ignored because each port has one. An
        empty collection lights nothing, deliberately -- the same answer an
        explicitly empty ``outputs`` gives, and the honest one for a router that
        matched no rule.

        Before this a node could light one port or all of them, so a router that
        matched two of five had to drop work or wake lanes nobody asked for.
        """
        if isinstance(ports, Mapping):
            return {"__ports": dict(ports)}

        return {"__ports": list(ports), "value": value}
