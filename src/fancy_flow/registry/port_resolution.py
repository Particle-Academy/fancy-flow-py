r"""Which ports a node CAN publish -- the one answer, for everybody.

The twin of ``FancyFlow\\Registry\\PortResolution`` (PHP).

Three kinds decide their ports from their own CONFIG rather than from a fixed
declaration: ``switch_case`` publishes one port per entry in its ``cases`` map,
``llm_router`` one per declared route, and ``subflow`` gains ``stream`` in the
streaming modes. Their :class:`NodeKind` can only carry a representative
default.

In the PHP twin that answer was computed in two places that did not agree: the
authoring API derived it from config and offered an agent a third case, while
the engine read only the kind's static declaration and called that same port
impossible. **The authoring API invited an edge and the runtime then reported
it as a mistake.** This engine never consulted config-derived ports at run time
until the undelivered-edge warning needed them, so it starts from the fixed
version. (``LlmRouter.ports()`` and ``Subflow.ports()`` are older host-facing
helpers returning descriptors for an editor; they are not this rule and differ
from it on an unconfigured node.)

Deliberately NOT what :meth:`FlowRunner._activated_ports` computes. That answers
"which ports did this RESULT light up"; this answers "which ports could this
node light up on SOME run". A ``branch`` that took ``true`` activated no
``false``, and ``false`` is still possible.
"""

from __future__ import annotations

from typing import Any

from ..schema.graph import FlowNode
from . import kind_id as kid
from .node_kind import NodeKind

__all__ = ["possible_ports"]


def possible_ports(
    node: FlowNode | None, kind: NodeKind | None, config: dict[str, Any] | None = None
) -> list[str]:
    """Every port this node could publish, given its kind and its config.

    Precedence, and it is deliberate:

    1. the NODE's own declared ``outputs`` -- the document is more specific
       than the kind;
    2. the kind's CONFIG-DERIVED ports, for the three kinds that have them;
    3. the kind's declared ports;
    4. ``out``.
    """
    if node is not None and node.outputs is not None:
        return [port.id for port in node.outputs]

    if kind is None:
        # An unregistered kind is NOT ambiguous, though it looks as though it
        # should be: `_activated_ports` falls back to exactly `out` for a kind
        # it cannot resolve, so that is what such a node publishes.
        return ["out"]

    declared = [port.id for port in (kind.outputs or ())]
    derived = _config_derived(kid.bare(kind.name), config or {}, declared)

    if derived:
        return derived

    return declared or ["out"]


def _config_derived(bare_kind: str, config: dict[str, Any], declared: list[str]) -> list[str]:
    """The ports a kind derives from its own config, or ``[]`` for none.

    ``[]`` also when the config does not declare any yet: an unconfigured
    ``switch_case`` falls back to the kind's representative defaults rather
    than to nothing, so a half-built node does not make every edge leaving it
    look impossible while its author is still typing.
    """
    if bare_kind == "switch_case":
        return _switch_case_ports(config)
    if bare_kind == "llm_router":
        return _llm_router_ports(config)
    if bare_kind == "subflow":
        return _subflow_ports(config, declared)
    return []


def _switch_case_ports(config: dict[str, Any]) -> list[str]:
    """``cases`` maps VALUE -> PORT ID, so the ports are its values.

    ``default`` is always added: the executor falls back to it for any value
    with no matching entry, so it can publish even when no case names it.

    A MAPPING only. The PHP twin also iterates a list here, because PHP's
    ``isset($cases[$value])`` can index one; this engine's ``switch_case`` routes
    only through a dict, so a list's elements could never be published and
    calling them possible would silence a warning that is true.
    """
    cases = config.get("cases")
    if not isinstance(cases, dict) or not cases:
        return []

    ports = [port for port in cases.values() if isinstance(port, str) and port != ""]
    if not ports:
        return []

    ports.append("default")
    return _unique(ports)


def _llm_router_ports(config: dict[str, Any]) -> list[str]:
    """One port per declared route, plus ``fallback`` unless it is switched off.

    ``fallback`` defaults ON -- it is where a run goes when the model returns a
    port that was never offered.
    """
    routes = config.get("routes")
    if not isinstance(routes, (list, tuple)) or not routes:
        return []

    ports = [
        str(route["port"])
        for route in routes
        if isinstance(route, dict) and route.get("port") is not None and route["port"] != ""
    ]
    if not ports:
        return []

    if config.get("fallback", True) is not False:
        ports.append("fallback")
    return _unique(ports)


def _subflow_ports(config: dict[str, Any], declared: list[str]) -> list[str]:
    """``subflow`` gains ``stream`` in the streaming modes."""
    mode = config.get("mode")
    if mode not in ("stream", "both"):
        return []

    return _unique([*(declared or ["out"]), "stream"])


def _unique(ports: list[str]) -> list[str]:
    """First occurrence wins, order kept -- ``array_values(array_unique())``."""
    return list(dict.fromkeys(ports))
