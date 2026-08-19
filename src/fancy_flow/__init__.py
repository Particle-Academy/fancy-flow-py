"""fancy-flow for Python -- the third runtime for fancy-flow workflow graphs.

The guarantee, and the only thing that matters: **the same WorkflowSchema JSON
in produces the same RunResult.outputs out**, on Node, on PHP, and here. This
is a faithful PORT, not a redesign; behaviour questions are settled against
``@particle-academy/fancy-flow`` and ``particle-academy/fancy-flow-php``, and
parity is asserted by shared fixture tables rather than claimed.

Getting started::

    from fancy_flow import FlowRunner, RunOptions, builtin, import_workflow

    builtin.register()                       # install the built-in kinds
    result = import_workflow(schema_json)
    run = FlowRunner().run(
        result.graph,
        builtin.executors(),
        options=RunOptions(initial_inputs={"trigger-1": {"payload": body}}),
    )
"""

from __future__ import annotations

from . import capabilities
from .contracts import NativeResolver, NodeExecutor, Resolver, TriggerGuard
from .engine.runner import FlowRunner
from .exceptions import FlowError, RunAborted, UnsafeGraph
from .executors import ExecutorRegistry
from .registry import builtin
from .registry.node_kind import ConfigField, NodeKind
from .registry.registry import NodeKindRegistry, default_registry, reset_default_registry
from .runtime import (
    AbortController,
    AbortSignal,
    ExecutionContext,
    NodeStatus,
    Pause,
    PauseSignal,
    Port,
    RunEvent,
    RunOptions,
    RunResult,
)
from .schema import (
    FlowEdge,
    FlowGraph,
    FlowNode,
    ImportIssue,
    ImportResult,
    PortDescriptor,
    WorkflowMetadata,
)
from .workflow import SCHEMA_URL, SCHEMA_VERSION, export_workflow, import_workflow, to_json

__version__ = "0.1.0"

__all__ = [
    "SCHEMA_URL",
    "SCHEMA_VERSION",
    "AbortController",
    "AbortSignal",
    "ConfigField",
    "ExecutionContext",
    "ExecutorRegistry",
    "FlowEdge",
    "FlowError",
    "FlowGraph",
    "FlowNode",
    "FlowRunner",
    "ImportIssue",
    "ImportResult",
    "NativeResolver",
    "NodeExecutor",
    "NodeKind",
    "NodeKindRegistry",
    "NodeStatus",
    "Pause",
    "PauseSignal",
    "Port",
    "PortDescriptor",
    "Resolver",
    "RunAborted",
    "RunEvent",
    "RunOptions",
    "RunResult",
    "TriggerGuard",
    "UnsafeGraph",
    "WorkflowMetadata",
    "__version__",
    "builtin",
    "capabilities",
    "default_registry",
    "export_workflow",
    "import_workflow",
    "reset_default_registry",
    "to_json",
]
