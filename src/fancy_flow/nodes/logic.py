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
from .support.routing_diagnostics import warn_if_unresolved

__all__ = ["branch", "for_each", "merge", "switch_case", "transform", "wait"]


def branch(ctx: ExecutionContext) -> Any:
    """``branch`` -- two ports, exactly one taken.

    The condition resolves through :mod:`~fancy_flow.nodes.support.expr` against
    the node's inputs; :func:`~fancy_flow.nodes.support.expr.truthy` decides.
    The incoming value passes through unchanged down whichever side is taken,
    and the other edge stays dead for the rest of the run.
    """
    condition = ctx.option("condition")
    resolved = expr.evaluate(condition, ctx.inputs)
    port = "true" if expr.truthy(resolved) else "false"

    # A condition that did not RESOLVE is falsy, so the run takes `false`
    # silently and for the wrong reason. Routing is unchanged; the reason is
    # now visible.
    warn_if_unresolved(ctx, condition, port)

    return Port.branch(port, ctx.input("in", ctx.inputs))


def switch_case(ctx: ExecutionContext) -> Any:
    """``switch_case`` -- N ports, one taken.

    Routes on a key: ``value`` is resolved and looked up in the ``cases`` map
    (value -> port id), falling back to ``default``.
    """
    expression = ctx.option("value")
    value = expr.text(expr.evaluate(expression, ctx.inputs))
    cases = ctx.option("cases", {})
    port = "default"
    if isinstance(cases, dict) and cases.get(value) is not None:
        port = str(cases[value])

    # The same silent mis-route as `branch`, one step over: a `value` that does
    # not resolve becomes "", matches no case, and falls to `default` --
    # indistinguishable from a value that genuinely matched nothing.
    warn_if_unresolved(ctx, expression, port, "value")

    return Port.only(port, ctx.input("in", ctx.inputs))


_FOR_EACH_DEFAULT_MAX_ITEMS = 1000
_FOR_EACH_HARD_MAX_ITEMS = 10000


def _reachable(adjacency: dict[str, list[str]], starts: list[str]) -> set[str]:
    """Every node id reachable from ``starts``, following edges forwards."""
    seen: set[str] = set()
    queue = list(starts)
    cursor = 0
    while cursor < len(queue):
        node_id = queue[cursor]
        cursor += 1
        if node_id in seen:
            continue
        seen.add(node_id)
        queue.extend(adjacency.get(node_id, ()))
    return seen


def _for_each_lane(graph: Any, node_id: str) -> tuple[Any, list[Any]] | None:
    """The loop BODY: reachable from ``item``, stopping at anything ``done`` reaches.

    Derived from the graph rather than declared, so a graph says what the body
    is by being drawn -- there is no second list to keep in step with the edges.
    The ``done`` subtraction is what lets a node sit after the loop and still be
    reachable from inside it.

    ``None`` means "no ``item`` edge", which is the data-only case, not an error.
    """
    from ..schema.graph import FlowGraph

    def handle(edge: Any) -> str:
        return edge.source_handle or "out"

    item_edges = [e for e in graph.edges if e.source == node_id and handle(e) == "item"]
    if not item_edges:
        return None

    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)

    done = _reachable(
        adjacency,
        [e.target for e in graph.edges if e.source == node_id and handle(e) == "done"],
    )
    body = {
        nid
        for nid in _reachable(adjacency, [e.target for e in item_edges])
        if nid not in done and nid != node_id
    }

    lane = FlowGraph(
        nodes=tuple(n for n in graph.nodes if n.id in body),
        edges=tuple(e for e in graph.edges if e.source in body and e.target in body),
        inputs=graph.inputs,
    )
    return lane, [e for e in item_edges if e.target in body]


def for_each(ctx: ExecutionContext) -> Any:
    """``for_each`` -- the collection as DATA, or the lane run once per item.

    WITHOUT an ``item`` edge (or with ``mode: "collect"``) this publishes the
    resolved collection and its size and stops. That half is deliberate rather
    than unfinished: on a durable run a ``for_each`` over 10,000 rows is one
    node, one claim, one checkpoint -- not 10,000.

    WITH an ``item`` edge it runs the derived lane once per item and aggregates
    on ``done``. That half was missing until the ``item`` port had an
    implementation here at all: the schema accepted the edge, the editor drew
    it, and the engine ignored it -- so every downstream node ran ONCE against
    the whole collection, silently, with no error.

    Measured rather than reasoned about: a reference graph scoring five records
    produced five per-item scores on the PHP twin and one aggregate here, and
    the assertion node downstream failed with "the path names nothing" because
    ``results`` was never produced.

    ``concurrency`` is still carried rather than acted on: items run in order,
    and parity with the twin outranks throughput here.
    """
    from ..engine.runner import FlowRunner
    from ..runtime.options import RunOptions
    from ..runtime.pause import Pause

    source = expr.evaluate(ctx.option("source"), ctx.inputs)
    if isinstance(source, dict):
        items = list(source.values())
    elif isinstance(source, (list, tuple)):
        items = list(source)
    elif source is None:
        items = []
    else:
        items = [source]

    lane = _for_each_lane(ctx.graph, ctx.node.id) if ctx.graph is not None else None
    if lane is None or ctx.option("mode") == "collect":
        return {"items": items, "count": len(items)}

    lane_graph, entries = lane
    if not lane_graph.nodes:
        ctx.abort(f'for_each "{ctx.node.id}" has an item edge but its derived lane is empty')

    max_items = ctx.option("maxItems", _FOR_EACH_DEFAULT_MAX_ITEMS)
    try:
        max_items = int(max_items)
    except (TypeError, ValueError):
        max_items = -1
    if max_items < 1 or max_items > _FOR_EACH_HARD_MAX_ITEMS:
        ctx.abort(
            f'for_each "{ctx.node.id}" maxItems must be between 1 and {_FOR_EACH_HARD_MAX_ITEMS}'
        )
    if len(items) > max_items:
        ctx.abort(
            f'for_each "{ctx.node.id}" resolved {len(items)} items '
            f"exceeds its maxItems cap of {max_items}"
        )

    results: list[Any] = []
    failures: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        initial_inputs: dict[str, dict[str, Any]] = {}
        for edge in entries:
            initial_inputs.setdefault(edge.target, {})[edge.target_handle or "in"] = item

        nested = FlowRunner().run(
            lane_graph,
            ctx.executors,
            None,
            RunOptions(
                initial_inputs=initial_inputs,
                depth=ctx.depth + 1,
                # The index rides on the identity, so a node in iteration 3
                # cannot share an idempotency key with the same node in 4.
                run=ctx.run.descend(ctx.node.id, index) if ctx.run else None,
            ),
        )

        if not nested.ok:
            reason = nested.error or "unknown error"

            # A PAUSE IS NOT A FAILURE. It travels the error channel, and
            # recording it as a failed item strands whoever the run waits on.
            if Pause.decode(reason) is not None:
                ctx.abort(reason)

            results.append(None)
            failures.append({"index": index, "item": item, "error": reason})
            continue

        results.append(nested.outputs)

    return Port.only(
        "done",
        {
            "items": items,
            "results": results,
            "failures": failures,
            "count": len(items),
        },
    )


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
