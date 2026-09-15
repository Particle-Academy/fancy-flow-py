r"""Warn when a routing decision was made on a path that DID NOT RESOLVE.

The twin of ``FancyFlow\\Nodes\\Support\\RoutingDiagnostics`` (PHP).

``branch`` resolves its ``condition`` and asks :func:`expr.truthy`. An
unresolvable path yields ``None``, ``None`` is falsy, and the run takes the
``false`` port -- **silently, and for the wrong reason.** ``switch_case`` has
the identical shape one step over: a ``value`` that does not resolve becomes
``""``, matches no case, and falls through to ``default``.

From the outside that is indistinguishable from a condition that was
legitimately false. It is the same collapse as an unresolvable path
interpolating to ``""``, except that here it changes the ROUTE rather than the
text, so half the graph never runs and the run reports success.

**Routing is deliberately unchanged.** An unresolved condition still takes
``false``; changing that would silently re-route graphs that have been running
for months. The warning supplies the part that was missing, which is the REASON.
A host that would rather fail has ``on_unresolved="throw"`` on
:func:`expr.evaluate`.

Found by ``flabs``: an agent built a correct triage graph whose urgency check
named a field that did not resolve, so every request -- including one reporting
total payment failure -- was routed as non-urgent. Pinned by fancy-conformance
``flow/run-diagnostics``.
"""

from __future__ import annotations

from typing import Any

from ...runtime.context import ExecutionContext
from ...runtime.events import RunEvent
from . import expr

__all__ = ["warn_if_unresolved"]


def warn_if_unresolved(
    ctx: ExecutionContext,
    condition: Any,
    took_port: str,
    config_key: str = "condition",
) -> None:
    """Emit a ``warn`` when ``condition`` is a single ``{{ path }}`` resolving to nothing.

    Only for a WHOLE expression. A condition mixing literal text with an
    expression is being used as a string, and an unresolved fragment there is
    the interpolation case rather than a routing one.

    Asks whether the path RESOLVED, never what it resolved to: a key holding
    ``None`` is an answer, and warning on it would fire on data that is simply
    empty.
    """
    if not isinstance(condition, str):
        return

    trimmed = condition.strip()
    if len(trimmed) < 4 or not trimmed.startswith("{{") or not trimmed.endswith("}}"):
        return

    path = trimmed[2:-2].strip()

    # `{{ a }}{{ b }}` would otherwise be read as ONE path spanning the inner
    # `}}{{`. That is two references rather than a missing field, and reporting
    # it as a missing field sends the reader somewhere useless.
    if path == "" or "}}" in path:
        return

    if expr.try_resolve_path(path, ctx.inputs).resolved:
        return

    node_id = ctx.node.id
    ctx.emit(
        RunEvent.log(
            "warn",
            f'Node {node_id} took the "{took_port}" port because `{config_key}` resolved to '
            f"NOTHING \u2014 the path {path} names no field on this node's inputs. That is not "
            "the same as a false condition: the route was decided by an absent value rather "
            "than by the data.",
            node_id,
            {"node": node_id, "configKey": config_key, "path": path, "tookPort": took_port},
        )
    )
