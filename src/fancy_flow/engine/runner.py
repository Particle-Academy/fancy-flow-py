"""Topological execution of a :class:`FlowGraph` -- the Python port of ``runFlow``.

Each node runs once, in a Kahn topological order. A node executes when **at
least one** incoming edge is active (its source port produced a value); that is
the fix for the merge-after-decision bug (#1) -- requiring *all* incoming edges
to be active wrongly skipped a shared continuation after a decision routed down
one branch. Cycles are detected and abort the run.

Port activation follows three conventions on an executor's result:

1. ``{"__port": "x", "value": ...}``  -> only port ``x`` emits.
2. ``{"branch": "x", "value": ...}``  -> only port ``x`` emits (decision sugar).
3. anything else                      -> the value is published on every
   declared output port.

**These rules live here and only here.** A queue driver must read the activated
ports back off the ``node-output`` events this module emits rather than
re-deriving them; a second copy of a routing table is the kind of duplicate
that agrees for a year and then disagrees on one branch.

Sync and async in one walk
--------------------------

TypeScript executors may be ``async``; PHP's are synchronous. Python has both,
and the usual answer -- write the loop twice -- would put two copies of the
routing rules in the file that exists to have exactly one.

So the graph walk is a **generator**: it yields a node to execute and is sent
the outcome back. :meth:`FlowRunner.run` drives it synchronously,
:meth:`FlowRunner.arun` drives the same generator while awaiting awaitable
results. The topology, branching, skipping and port rules are written once.
"""

from __future__ import annotations

import contextlib
import inspect
import time
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from typing import Any, cast

from ..exceptions import RunAborted
from ..executors import ExecutorRegistry
from ..registry import kind_id as kid
from ..registry.registry import NodeKindRegistry, default_registry
from ..runtime.context import ExecutionContext
from ..runtime.events import NodeStatus, RunEvent
from ..runtime.options import RunOptions, RunResult
from ..schema.graph import FlowEdge, FlowGraph, FlowNode

__all__ = ["FlowRunner"]

EventSink = Callable[[RunEvent], None]


@dataclass(slots=True)
class _Step:
    """One unit of work the walk hands back to whichever driver is running it."""

    ctx: ExecutionContext
    executor: Callable[[ExecutionContext], Any]


class FlowRunner:
    """Runs a graph against an :class:`ExecutorRegistry`."""

    def __init__(self, kinds: NodeKindRegistry | None = None) -> None:
        #: Consulted only for the declared-output-port fallback. ``None`` means
        #: the shared registry, matching the PHP twin, which reads the global.
        self._kinds = kinds

    # -- drivers ---------------------------------------------------------

    def run(
        self,
        graph: FlowGraph,
        executors: ExecutorRegistry,
        on_event: EventSink | None = None,
        options: RunOptions | None = None,
    ) -> RunResult:
        """Execute the graph synchronously.

        An executor that returns an awaitable is a programming error here and
        is reported as one, rather than being silently stored as a coroutine
        object that every downstream node then receives instead of a value.
        """
        walk = self._walk(graph, executors, on_event, options)
        outcome: tuple[bool, Any] | None = None
        try:
            while True:
                step = walk.send(outcome) if outcome is not None else next(walk)
                outcome = _sync_outcome(step)
        except StopIteration as stop:
            return cast("RunResult", stop.value)

    async def arun(
        self,
        graph: FlowGraph,
        executors: ExecutorRegistry,
        on_event: EventSink | None = None,
        options: RunOptions | None = None,
    ) -> RunResult:
        """Execute the graph, awaiting any executor that returns an awaitable.

        Synchronous executors run unchanged, so a graph mixing both works --
        which is the normal case when a host adds one async HTTP node to an
        otherwise plain workflow.
        """
        walk = self._walk(graph, executors, on_event, options)
        outcome: tuple[bool, Any] | None = None
        try:
            while True:
                step = walk.send(outcome) if outcome is not None else next(walk)
                outcome = await _async_outcome(step)
        except StopIteration as stop:
            return cast("RunResult", stop.value)

    # -- the one walk ----------------------------------------------------

    def _walk(
        self,
        graph: FlowGraph,
        executors: ExecutorRegistry,
        on_event: EventSink | None,
        options: RunOptions | None,
    ) -> Generator[_Step, tuple[bool, Any], RunResult]:
        options = options or RunOptions()
        initial_inputs = options.initial_inputs
        resume_outputs = options.resume_outputs
        signal = options.signal
        timeout_ms = options.timeout_ms

        outputs: dict[str, Any] = {}
        #: key: "{node_id}:{port_id}"
        port_values: dict[str, Any] = {}
        errors: list[str] = []
        events: list[RunEvent] = []

        def emit(event: RunEvent) -> None:
            events.append(event)
            if on_event is not None:
                on_event(event)

        # Deterministic topological order; also the cycle check.
        order = _topo_sort(graph)
        if order is None:
            msg = "Cycle detected in flow graph - aborting."
            emit(RunEvent.run_error(msg))
            return RunResult(False, outputs, msg, tuple(events))

        incoming_by_node = _index_incoming(graph.edges)
        start = time.monotonic()

        emit(RunEvent.run_start())

        for node in order:
            # Host cancellation propagates out of the run. That is distinct
            # from an executor's abort(), which ends the run with ok=False --
            # a cancelled run has no result to report, a failed one does.
            if signal is not None and signal.aborted:
                raise RunAborted(signal.reason or "aborted")

            # A timeout is recorded as an error and observed between nodes,
            # mirroring the TypeScript timer that pushes an error the loop then
            # sees. Nothing interrupts an executor mid-call on any runtime.
            timed_out = (
                timeout_ms is not None
                and not errors
                and (time.monotonic() - start) * 1000 > timeout_ms
            )
            if timed_out:
                errors.append(f"Run timed out after {timeout_ms}ms")
            if errors:
                break

            # Resume: a node completed in a prior run is not re-executed. Its
            # stored output is republished on its ports, reproducing the same
            # routing, so downstream nodes see identical inputs. This is the
            # primitive every durable driver is built on.
            if node.id in resume_outputs:
                self._publish(
                    node, resume_outputs[node.id], outputs, port_values, emit, resumed=True
                )
                continue

            incoming = incoming_by_node.get(node.id, ())

            # Run once any upstream branch reaches this node. In topological
            # order every upstream node is already settled, so each incoming
            # edge is active or dead -- never pending. Requiring ALL active
            # wrongly skipped merge points (#1); _collect_inputs reads only the
            # active ones.
            if incoming:
                any_active = any(
                    _port_key(e.source, e.source_handle) in port_values for e in incoming
                )
                if not any_active:
                    emit(RunEvent.node_status(node.id, NodeStatus.IDLE, "skipped"))
                    continue

            # Notes are annotations -- never executed. Matched across every id
            # the kind answers to: a graph saved with the canonical
            # `@particle-academy/note` must stay an annotation, not become an
            # unrunnable node.
            if node.type is not None and kid.matches(node.type, "note"):
                emit(RunEvent.node_status(node.id, NodeStatus.IDLE, "annotation"))
                continue

            emit(RunEvent.node_status(node.id, NodeStatus.RUNNING))

            inputs = _collect_inputs(node, incoming, port_values, initial_inputs)
            executor = executors.resolve_for(node)
            if executor is None:
                msg = f"No executor registered for kind={node.type}"
                errors.append(msg)
                emit(RunEvent.node_status(node.id, NodeStatus.ERROR, msg))
                emit(RunEvent.log("error", msg, node.id))
                break

            _announce(emit, node, "start")
            ctx = ExecutionContext(node, inputs, emit, options.depth, options.run, executors)
            ok, payload = yield _Step(ctx, executor)

            if not ok:
                errors.append(payload)
                emit(RunEvent.node_status(node.id, NodeStatus.ERROR, payload))
                emit(RunEvent.log("error", payload, node.id))
                break

            self._publish(node, payload, outputs, port_values, emit)
            # Success path only, and deliberately so: a stopping message after a
            # failure tells a human the opposite of what happened, in the part
            # of the UI they trust most.
            _announce(emit, node, "end")

        ok = not errors
        emit(RunEvent.run_end(ok))
        return RunResult(ok, outputs, None if ok else errors[0], tuple(events))

    # -- publishing ------------------------------------------------------

    def _publish(
        self,
        node: FlowNode,
        result: Any,
        outputs: dict[str, Any],
        port_values: dict[str, Any],
        emit: EventSink,
        resumed: bool = False,
    ) -> None:
        """Record a result, publish it on the activated ports, mark the node done."""
        outputs[node.id] = result

        ports, value = self._activated_ports(node, result)
        for port_id in ports:
            port_values[_port_key(node.id, port_id)] = value
            emit(RunEvent.node_output(node.id, port_id, value))

        emit(RunEvent.node_status(node.id, NodeStatus.DONE, "resumed" if resumed else None))

    def _activated_ports(self, node: FlowNode, result: Any) -> tuple[list[str], Any]:
        """Which output ports a result activates, and the value carried."""
        if isinstance(result, dict):
            if isinstance(result.get("__port"), str):
                return [result["__port"]], result.get("value")
            if isinstance(result.get("branch"), str):
                # `r.value ?? r` on the peer runtimes: an omitted value carries
                # the whole result object.
                value = result.get("value")
                return [result["branch"]], result if value is None else value

        declared = node.outputs

        # When the node declares none, fall back to the KIND's ports before
        # falling back to `out`. The TypeScript side resolves ports through its
        # kind -- including config-driven kinds like `switch_case`, whose ports
        # come from its `cases` map -- and serializes the resolved ports into
        # the document. This covers hand-written schemas that omit them.
        if declared is None and node.type is not None:
            registry = self._kinds if self._kinds is not None else default_registry()
            kind = registry.get(node.type)
            kind_ports = kind.outputs if kind is not None else None
            # Only adopt NON-EMPTY kind ports. A terminal kind (category
            # "output") declares an empty list, and consuming that literally
            # would publish zero ports where the historical fallback published
            # `out` -- silently cutting every chain through such a node.
            if kind_ports:
                declared = kind_ports

        if declared is None:
            return ["out"], result
        return [p.id for p in declared], result


# -- module-level helpers ------------------------------------------------


def _sync_outcome(step: _Step) -> tuple[bool, Any]:
    try:
        result = step.executor(step.ctx)
    except RunAborted as exc:
        return False, exc.reason
    except Exception as exc:
        return False, str(exc)
    if inspect.isawaitable(result):
        # Reported rather than stored. A coroutine object sitting in
        # `outputs[node]` looks like success and reaches every downstream node
        # as a value nothing can read.
        _close_awaitable(result)
        return False, (
            f"Node {step.ctx.node.id} returned an awaitable to the synchronous "
            "runner. Use FlowRunner.arun() for async executors."
        )
    return True, result


async def _async_outcome(step: _Step) -> tuple[bool, Any]:
    try:
        result = step.executor(step.ctx)
        if inspect.isawaitable(result):
            result = await result
    except RunAborted as exc:
        return False, exc.reason
    except Exception as exc:
        return False, str(exc)
    return True, result


def _close_awaitable(awaitable: Any) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()


def _port_key(node_id: str, port_id: str | None) -> str:
    return f"{node_id}:{port_id or 'out'}"


def _index_incoming(edges: Iterable[FlowEdge]) -> dict[str, list[FlowEdge]]:
    index: dict[str, list[FlowEdge]] = {}
    for edge in edges:
        index.setdefault(edge.target, []).append(edge)
    return index


def _topo_sort(graph: FlowGraph) -> list[FlowNode] | None:
    """Kahn's algorithm. ``None`` when a cycle is present.

    Iteration order matches the peer engines so runs are comparable node for
    node, not merely equal at the end.
    """
    in_degree: dict[str, int] = {node.id: 0 for node in graph.nodes}
    for edge in graph.edges:
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    queue = [node_id for node_id, degree in in_degree.items() if degree == 0]

    ordered: list[str] = []
    while queue:
        node_id = queue.pop(0)
        ordered.append(node_id)
        for edge in graph.edges:
            if edge.source != node_id:
                continue
            nxt = in_degree.get(edge.target, 0) - 1
            in_degree[edge.target] = nxt
            if nxt == 0:
                queue.append(edge.target)

    if len(ordered) != len(graph.nodes):
        return None

    by_id = {node.id: node for node in graph.nodes}
    return [by_id[node_id] for node_id in ordered if node_id in by_id]


def _collect_inputs(
    node: FlowNode,
    incoming: Iterable[FlowEdge],
    port_values: dict[str, Any],
    initial: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Gather a node's inputs, keyed by target-port id (default ``in``).

    Only *active* incoming edges contribute. An edge whose source port never
    produced a value -- a dead branch -- is skipped, so it cannot clobber a
    live value arriving on the same handle.

    This was a REAL divergence once: TypeScript assigned unconditionally, so a
    trailing dead edge overwrote a live one with ``undefined`` whenever two
    branches rejoined on the same handle. PHP implemented the documented
    contract, TypeScript implemented the code, and the two disagreed silently
    since both still reported success. TypeScript was fixed to match in
    fancy-flow 0.27.1; the fixture ``23-merge-same-handle`` pins it on all
    three sides.
    """
    inputs: dict[str, Any] = dict(initial.get(node.id, {}))
    for edge in incoming:
        key = _port_key(edge.source, edge.source_handle)
        if key in port_values:
            inputs[edge.target_handle or "in"] = port_values[key]

            # ALSO addressable by the SOURCE NODE'S ID when the edge named no
            # handle. Authors write ``{{ n2.text }}`` first -- it is how every
            # graph tool addresses nodes, and it is what an assistant generating
            # a graph reaches for. That resolved to nothing while NOTHING
            # FAILED, because an unresolvable path yields ``''``: the node ran,
            # the run reported success, and the damage was output that was
            # quietly wrong (fancy-flow-php#8).
            #
            # Only for handle-less edges -- an edge that named one said what it
            # meant -- and never clobbering a key already present, whether from
            # the host's initial inputs or an earlier edge.
            if edge.target_handle is None and edge.source not in inputs:
                inputs[edge.source] = port_values[key]
    return inputs


def _announce(emit: Any, node: FlowNode, phase: str) -> None:
    """Emit a node's own status message for one phase, if it declared one.

    Opt-in by absence: a node with neither message says nothing, because most
    nodes in a graph are plumbing and narrating all of them buries the two or
    three steps a person actually follows.

    A message must be non-empty after stripping. A blank field is the shape a
    cleared editor input takes, and a blank line in a progress feed cannot be
    told apart from a real message that renders as nothing.
    """
    raw = node.starting_msg if phase == "start" else node.stopping_msg
    if not isinstance(raw, str):
        return

    message = raw.strip()
    if not message:
        return

    emit(RunEvent.node_message(node.id, phase, message))
