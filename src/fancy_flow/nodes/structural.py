"""Nesting executors -- a graph inside a node.

``subgraph`` carries its child graph inline in config; ``subflow`` NAMES one
the host resolves. They differ in where the graph comes from, not in how it
gets its inputs, so :func:`seed_entry_nodes` stays one implementation.
"""

from __future__ import annotations

from typing import Any

from .. import capabilities as caps
from ..engine.runner import FlowRunner
from ..executors import ExecutorRegistry
from ..registry.registry import NodeKindRegistry
from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.options import RunOptions
from ..runtime.ports import Port
from ..schema.graph import FlowGraph, PortDescriptor
from .support.deps import ExecutorDeps

__all__ = ["DEFAULT_MAX_DEPTH", "Subflow", "Subgraph", "seed_entry_nodes"]

DEFAULT_MAX_DEPTH = 8


def seed_entry_nodes(graph: FlowGraph, inputs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Hand a parent node's inputs to every entry point of a child graph."""
    has_incoming = {edge.target for edge in graph.edges}
    return {node.id: inputs for node in graph.nodes if node.id not in has_incoming}


class Subgraph:
    """``subgraph`` -- runs the nested WorkflowSchema held in the node's config.

    With no nested graph the input passes through, so a half-built node does
    not take a run down.
    """

    def __init__(self, deps: ExecutorDeps | None = None) -> None:
        self._deps = deps or ExecutorDeps()

    def execute(self, ctx: ExecutionContext) -> Any:
        from ..registry.builtin import executors as builtin_executors
        from ..registry.builtin import register as register_builtins
        from ..workflow import import_workflow

        graph = ctx.option("graph")
        if not isinstance(graph, dict):
            return ctx.input("in", ctx.inputs)

        registry = register_builtins(NodeKindRegistry(), with_structural=True)
        result = import_workflow(graph, lenient=True, registry=registry)

        run = FlowRunner().run(
            result.graph,
            builtin_executors(self._deps),
            options=RunOptions(
                initial_inputs=seed_entry_nodes(result.graph, ctx.inputs),
                depth=ctx.depth + 1,
            ),
        )

        return run.outputs


class Subflow:
    """``subflow`` -- run another workflow and bring its result home.

    Core, not marketplace: it introduces no third-party dependency. It runs a
    child graph through the very same :class:`FlowRunner`, so the only thing it
    needs from the host is WHERE workflows live -- a
    :class:`~fancy_flow.capabilities.WorkflowResolver`.

    Three output modes, because both halves are genuinely useful:

    - ``output`` -- the child's outputs arrive on ``out`` when it finishes.
    - ``stream`` -- the child's progress is forwarded live on the parent's feed.
    - ``both``   -- stream while running AND deliver the final outputs.

    Recursion is guarded by depth: a workflow referencing itself (directly or
    through a chain) would otherwise recurse until the interpreter gives up,
    surfacing as a ``RecursionError`` rather than "you built a loop".
    """

    def __init__(
        self,
        deps: ExecutorDeps | None = None,
        resolver: caps.WorkflowResolver | None = None,
        executors: ExecutorRegistry | None = None,
    ) -> None:
        self._deps = deps or ExecutorDeps()
        self._resolver = resolver
        self._executors = executors

    @staticmethod
    def mode(config: dict[str, Any]) -> str:
        """The mode, defaulting to ``output`` for anything unrecognised."""
        value = config.get("mode")
        return value if value in ("stream", "both") else "output"

    @staticmethod
    def ports(config: dict[str, Any]) -> list[PortDescriptor]:
        """Ports follow the mode -- ``stream`` only exists when something streams."""
        ports = [PortDescriptor("out", "result")]
        if Subflow.mode(config) != "output":
            ports.insert(0, PortDescriptor("stream", "stream"))
        return ports

    def execute(self, ctx: ExecutionContext) -> Any:
        from ..registry.builtin import executors as builtin_executors

        config = ctx.config()
        ref = str(config.get("workflow") or "").strip()
        if ref == "":
            ctx.abort("subflow has no workflow reference configured")

        resolver = self._resolver or caps.workflow_resolver()
        if resolver is None:
            ctx.abort(
                "subflow: no workflow resolver registered. Register one with "
                "fancy_flow.capabilities.set_workflow_resolver() so subflow can find "
                "the workflow it references."
            )

        max_depth = _max_depth(config)
        if ctx.depth + 1 > max_depth:
            # Name the cause. A bare RecursionError tells an author nothing
            # about the workflow they wired into itself.
            ctx.abort(
                f'subflow depth limit reached ({max_depth}) at "{ref}" - a workflow is '
                "referencing itself, directly or through a chain."
            )

        # An optional pin. A workflow another workflow depends on is an
        # interface: without a pin, someone edits the child and this flow
        # silently runs different logic while still reporting success.
        pin = config.get("version")
        version: int | None = None
        if pin is not None and pin != "":
            if isinstance(pin, bool) or not _is_intlike(pin):
                ctx.abort(f'subflow "{ref}" has a non-integer version pin ({pin}).')
            version = int(pin)

        child = resolver.resolve(ref, version)

        if isinstance(child, caps.WorkflowResolutionFailure):
            # A mismatch names BOTH versions. Reporting it as "not found" would
            # send an author looking for a workflow sitting right there.
            if child.message is not None:
                ctx.abort(child.message)
            if child.is_version_mismatch:
                have = "a different version" if child.available is None else str(child.available)
                ctx.abort(
                    f'subflow "{ref}" is pinned to version {version}, but the host has {have}.'
                )
            ctx.abort(f'subflow could not resolve workflow "{ref}"')

        if child is None:
            ctx.abort(f'subflow could not resolve workflow "{ref}"')

        mode = self.mode(config)
        streaming = mode != "output"

        # Surface the child's progress on the PARENT's feed as log lines
        # against THIS node. Re-emitting the child's raw events would collide
        # with the parent's node ids -- a child's node-status for its `output`
        # node is not a status for anything in the parent graph.
        forward = None
        if streaming:

            def forward(event: RunEvent) -> None:
                ctx.emit(RunEvent.log("info", f"[{ref}] {_describe(event)}", ctx.node.id))

        result = FlowRunner().run(
            child,
            self._executors or builtin_executors(self._deps),
            forward,
            RunOptions(
                initial_inputs=_child_inputs(config, child, ctx.inputs),
                depth=ctx.depth + 1,
            ),
        )

        if not result.ok:
            ctx.abort(f'subflow "{ref}" failed: {result.error or "unknown error"}')

        # `stream` alone still emits a final value on `stream` so downstream
        # nodes have something to run on; `both` publishes on every port.
        if mode == "stream":
            return Port.only("stream", result.outputs)
        if mode == "both":
            return result.outputs
        return Port.only("out", result.outputs)


def _describe(event: RunEvent) -> str:
    if event.type == RunEvent.NODE_STATUS:
        return f"{event.node_id} {event.status}".strip()
    if event.type == RunEvent.RUN_END:
        return "finished (" + ("ok" if event.ok else "failed") + ")"
    return event.type


def _child_inputs(
    config: dict[str, Any], child: FlowGraph, inputs: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The child's entry-point inputs.

    The node's explicit mapping, or -- with none -- the parent's inputs handed
    to every entry node, so the simple case needs no configuration at all.
    """
    mapping = config.get("inputs")
    if isinstance(mapping, dict) and mapping:
        return mapping
    return seed_entry_nodes(child, inputs)


def _max_depth(config: dict[str, Any]) -> int:
    raw = config.get("maxDepth")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_MAX_DEPTH
    return int(raw)


def _is_intlike(value: Any) -> bool:
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return value.isdigit()
    return False
