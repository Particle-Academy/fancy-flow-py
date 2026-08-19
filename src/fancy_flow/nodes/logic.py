"""Logic executors -- the nodes that decide a graph's SHAPE.

These are worth precision because everything downstream of them depends on
which port lights up. See ``.ai/knowledge/flow-engine-spec.md`` section 4.
"""

from __future__ import annotations

from typing import Any

from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.ports import Port
from .support import expr

__all__ = ["branch", "for_each", "merge", "switch_case", "transform", "wait"]


def branch(ctx: ExecutionContext) -> Any:
    """``branch`` -- two ports, exactly one taken.

    The condition resolves through :mod:`~fancy_flow.nodes.support.expr` against
    the node's inputs; :func:`~fancy_flow.nodes.support.expr.truthy` decides.
    The incoming value passes through unchanged down whichever side is taken,
    and the other edge stays dead for the rest of the run.
    """
    resolved = expr.evaluate(ctx.option("condition"), ctx.inputs)
    port = "true" if expr.truthy(resolved) else "false"
    return Port.branch(port, ctx.input("in", ctx.inputs))


def switch_case(ctx: ExecutionContext) -> Any:
    """``switch_case`` -- N ports, one taken.

    Routes on a key: ``value`` is resolved and looked up in the ``cases`` map
    (value -> port id), falling back to ``default``.
    """
    value = expr.text(expr.evaluate(ctx.option("value"), ctx.inputs))
    cases = ctx.option("cases", {})
    port = "default"
    if isinstance(cases, dict) and cases.get(value) is not None:
        port = str(cases[value])
    return Port.only(port, ctx.input("in", ctx.inputs))


def for_each(ctx: ExecutionContext) -> Any:
    """``for_each`` -- fan-out as DATA, not as jobs.

    Publishes the resolved collection and its size. It does **not** spawn one
    job per item, and that is deliberate: on a durable run a ``for_each`` over
    10,000 rows is one node, one claim, one checkpoint -- not 10,000. Hosts
    that want true per-item iteration override this executor.
    """
    source = expr.evaluate(ctx.option("source"), ctx.inputs)
    if isinstance(source, dict):
        items = list(source.values())
    elif isinstance(source, (list, tuple)):
        items = list(source)
    elif source is None:
        items = []
    else:
        items = [source]
    return {"items": items, "count": len(items)}


def merge(ctx: ExecutionContext) -> Any:
    """``merge`` -- several inputs, one value.

    ``merge`` (default) combines inputs into one object: a mapping is merged in
    by key, anything else is keyed by its PORT id. ``concat`` flattens
    everything into one list.

    ``None`` inputs are skipped, and because dead edges never reach
    ``collect_inputs`` at all, a merge downstream of a branch receives only the
    side that actually ran.
    """
    mode = str(ctx.option("mode", "merge"))

    if mode == "concat":
        out: list[Any] = []
        for value in ctx.inputs.values():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                out.extend(value)
            else:
                out.append(value)
        return out

    merged: dict[str, Any] = {}
    for port, value in ctx.inputs.items():
        if value is None:
            continue
        if isinstance(value, dict):
            merged.update(value)
        else:
            merged[port] = value
    return merged


def wait(ctx: ExecutionContext) -> Any:
    """``wait`` -- a pause point.

    The framework-free default does NOT sleep: it records the requested wait
    and passes the input through, so tests stay fast and deterministic. A
    durable adapter overrides this to schedule the run's continuation rather
    than block a worker for an hour.
    """
    mode = str(ctx.option("mode", "duration"))
    duration = ctx.option("duration")
    ctx.emit(
        RunEvent.log(
            "info",
            f"wait ({mode}) - not sleeping in framework-free mode",
            ctx.node.id,
        )
    )
    return {"waited": mode, "duration": duration, "input": ctx.input("in", ctx.inputs)}


def transform(ctx: ExecutionContext) -> Any:
    """``transform`` -- reshape in place.

    With no expression the input passes through untouched. One ``out`` port,
    always active.
    """
    expression = ctx.option("expression")
    if expression is None or expression == "":
        return ctx.input("in", ctx.inputs)
    return expr.evaluate(expression, ctx.inputs)
