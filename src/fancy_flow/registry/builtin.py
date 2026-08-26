"""The built-in node library.

The kinds ``@particle-academy/fancy-flow`` ships, ported kind for kind, plus
batteries-included framework-free executors::

    register(registry)          # install the kind definitions
    executors = executors()     # default executors (offline fake clients)

On the TypeScript side the built-in kinds ship *without* executors -- each host
wires where memory / HTTP / AI actually go. Both server twins ship default
executors so a flow runs out of the box, while every one stays overridable
through the same kind + executor path a custom node uses. Inject real clients
via :class:`ExecutorDeps`.

The literals below are written with BARE names because that reads better and
there are two dozen of them; namespacing is applied by :func:`_canonicalize`, so
no kind can drift out of the convention by hand.
"""

from __future__ import annotations

from typing import Any

from ..executors import ExecutorRegistry
from ..nodes import ai, data, human, io_, logic, output, structural, trigger
from ..nodes.support.deps import ExecutorDeps
from . import kind_id as kid
from .node_kind import NodeKind
from .registry import NodeKindRegistry, default_registry

__all__ = ["agent_kind", "executors", "kind_id_index", "kinds", "register", "structural_kinds"]


def register(
    registry: NodeKindRegistry | None = None, with_structural: bool = False
) -> NodeKindRegistry:
    """Install every built-in kind definition into a registry (default: the shared one)."""
    registry = registry if registry is not None else default_registry()
    for raw in kinds():
        registry.register(NodeKind.from_dict(raw))
    if with_structural:
        for raw in structural_kinds():
            registry.register(NodeKind.from_dict(raw))
    return registry


def executors(deps: ExecutorDeps | None = None, resolver: Any = None) -> ExecutorRegistry:
    """A registry pre-bound with the default executor for every built-in kind.

    Bindings are made under EVERY id each kind answers to, not just the
    canonical one. Convention-derived variants (bare <-> ``@particle-academy/``)
    are not enough: ``llm_router`` was renamed from ``llm_branch``, and no
    amount of prefix arithmetic gets you from one to the other -- only the
    kind's declared alias list does.
    """
    deps = deps or ExecutorDeps()

    bindings: dict[str, Any] = {
        # triggers
        "manual_trigger": trigger.manual_trigger,
        "webhook_trigger": trigger.webhook_trigger,
        "schedule_trigger": trigger.schedule_trigger,
        # human
        "user_input": human.user_input,
        "human_approval": human.human_approval,
        "notify": human.Notify(deps.notifier),
        # logic
        "branch": logic.branch,
        "switch_case": logic.switch_case,
        "for_each": logic.for_each,
        "merge": logic.merge,
        "wait": logic.wait,
        "transform": logic.transform,
        "subflow": structural.Subflow(deps),
        # data
        "memory_store": data.MemoryStore(deps.memory),
        "data_store": data.DataStore(deps.data),
        "variable": data.variable,
        # ai
        "llm_call": ai.LlmCall(deps.llm),
        "llm_router": ai.LlmRouter(),
        "tool_use": ai.ToolUse(deps.tools),
        "embed_search": ai.EmbedSearch(deps.vectors),
        # io
        "api_request": io_.ApiRequest(deps.http),
        "webhook_out": io_.WebhookOut(deps.http),
        # output
        "output": output.output,
        "log": output.log,
        # structural
        "subgraph": structural.Subgraph(deps),
    }

    index = kind_id_index()
    expanded: dict[str, Any] = {}
    for kind, executor in bindings.items():
        for kind_id in index.get(kind, [kid.canonical(kind)]):
            expanded[kind_id] = executor

    return ExecutorRegistry(resolver).bind_many(expanded)


def kind_id_index() -> dict[str, list[str]]:
    """Bare kind name -> every id that kind answers to, canonical first.

    PUBLIC because an override has to agree with the bindings it is overriding.
    :meth:`ExecutorRegistry.bind` consults this so that replacing
    ``user_input`` replaces it under all three ids, the way the base bindings
    were made -- and the kind registry is not necessarily populated at bind
    time, so it cannot be the only source.
    """
    index: dict[str, list[str]] = {}
    for raw in [*kinds(), *structural_kinds(), agent_kind()]:
        name = str(raw["name"])
        ids = [name, *(raw.get("aliases") or ())]
        seen: dict[str, None] = {}
        for kind_id in ids:
            seen.setdefault(kind_id, None)
        index[kid.bare(name)] = list(seen)
    return index


def _canonicalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Give a built-in kind its CANONICAL namespaced id, keeping every previous
    spelling as an alias."""
    bare = kid.bare(str(raw["name"]))
    out = dict(raw)
    out["name"] = kid.NAMESPACE + bare
    seen: dict[str, None] = {}
    for alias in [*kid.builtin_aliases(bare), *(raw.get("aliases") or ())]:
        seen.setdefault(alias, None)
    out["aliases"] = list(seen)
    return out


def kinds() -> list[dict[str, Any]]:
    """The raw kind literals, with canonical namespaced ids and bare aliases."""
    return [_canonicalize(raw) for raw in _KIND_LITERALS()]


def structural_kinds() -> list[dict[str, Any]]:
    """Kinds the engine handles specially.

    ``note`` is never executed; ``subgraph`` runs a nested flow. Neither is part
    of the TypeScript ``builtin.ts`` registration, so they are opt-in.
    """
    return [_canonicalize(raw) for raw in _STRUCTURAL_LITERALS()]


def agent_kind() -> dict[str, Any]:
    """The ``agent`` kind -- an LLM agent with tools and bounded multi-step reasoning.

    Not part of the TypeScript ``builtin.ts`` mirror, so it is opt-in. Declared
    here (rather than omitted) so :func:`kind_id_index` knows its aliases: an
    executor bound for it must expand the same way every other builtin does.
    """
    return _canonicalize(
        {
            "name": "agent",
            "category": "ai",
            "label": "Agent",
            "icon": "*",
            "description": "LLM agent with tools + multi-step reasoning.",
            "configSchema": [
                {
                    "type": "text",
                    "key": "model",
                    "label": "Model",
                    "required": True,
                    "placeholder": "claude-sonnet-4-5",
                },
                {"type": "textarea", "key": "system", "label": "System prompt", "rows": 4},
                {
                    "type": "expression",
                    "key": "prompt",
                    "label": "Task",
                    "required": True,
                    "example": "{{ $json.task }}",
                },
                {
                    "type": "json",
                    "key": "tools",
                    "label": "Tools (JSON)",
                    "description": "Tool definitions the agent may call.",
                },
                {
                    "type": "number",
                    "key": "max_steps",
                    "label": "Max steps",
                    "default": 3,
                    "min": 1,
                    "max": 20,
                },
                {
                    "type": "number",
                    "key": "temperature",
                    "label": "Temperature",
                    "min": 0,
                    "max": 2,
                    "step": 0.1,
                    "default": 0.7,
                },
            ],
        }
    )


_HTTP_METHOD = {
    "type": "select",
    "key": "method",
    "label": "Method",
    "default": "GET",
    "required": True,
    "options": [
        {"value": "GET", "label": "GET"},
        {"value": "POST", "label": "POST"},
        {"value": "PUT", "label": "PUT"},
        {"value": "PATCH", "label": "PATCH"},
        {"value": "DELETE", "label": "DELETE"},
    ],
}


def _KIND_LITERALS() -> list[dict[str, Any]]:  # noqa: N802 - reads as a constant table
    return [
        # ---------------- Triggers ----------------
        {
            "name": "manual_trigger",
            "category": "trigger",
            "label": "Manual",
            "description": "Entry point fired when the user clicks Run.",
            "icon": "⚡",
            "inputs": [],
            "outputs": [{"id": "out"}],
        },
        {
            "name": "webhook_trigger",
            "category": "trigger",
            "label": "Webhook",
            "description": "Triggered by an inbound HTTP request to a host-provided URL.",
            "icon": "\U0001f4e1",
            "inputs": [],
            "outputs": [{"id": "out", "label": "payload"}],
            "configSchema": [
                {
                    "type": "text",
                    "key": "path",
                    "label": "Path",
                    "placeholder": "/hooks/my-flow",
                    "required": True,
                },
                {
                    "type": "select",
                    "key": "method",
                    "label": "Method",
                    "default": "POST",
                    "options": [
                        {"value": "POST", "label": "POST"},
                        {"value": "GET", "label": "GET"},
                    ],
                },
                {
                    "type": "credential",
                    "key": "secret",
                    "label": "Verifying secret",
                    "credentialType": "webhook_secret",
                },
            ],
        },
        {
            "name": "schedule_trigger",
            "category": "trigger",
            "label": "Schedule",
            "description": "Fires on a cron schedule (host-implemented).",
            "icon": "⏱",
            "inputs": [],
            "outputs": [{"id": "out"}],
            "configSchema": [
                {
                    "type": "text",
                    "key": "cron",
                    "label": "Cron",
                    "placeholder": "*/5 * * * *",
                    "required": True,
                    "description": "Standard 5-field cron expression.",
                },
                {
                    "type": "text",
                    "key": "timezone",
                    "label": "Timezone",
                    "placeholder": "UTC",
                    "default": "UTC",
                },
            ],
        },
        {
            "name": "user_input",
            "category": "human",
            "label": "User Input",
            "description": "Pause the flow until the user submits the configured form.",
            "icon": "✎",
            "pausesForHuman": "input",
            "inputs": [{"id": "in"}],
            "outputs": [{"id": "out", "label": "values"}],
            "configSchema": [
                {
                    "type": "text",
                    "key": "title",
                    "label": "Form title",
                    "default": "Need your input",
                },
                {
                    "type": "json",
                    "key": "fields",
                    "label": "Fields (JSON)",
                    "language": "json",
                    "rows": 6,
                    "default": [{"key": "answer", "label": "Your answer", "type": "textarea"}],
                },
                {
                    "type": "switch",
                    "key": "autoAnswerFromInput",
                    "label": "Let an incoming value answer this",
                    "default": False,
                    "description": (
                        "Off by default: this node pauses for a person even when something "
                        "already put a value on its input. Turn it on for a step that is a "
                        "form when a human is present and a pass-through when an upstream "
                        "node already produced the answer."
                    ),
                },
            ],
        },
        # ---------------- Logic ----------------
        {
            "name": "branch",
            "category": "logic",
            "label": "Branch",
            "description": "Multi-way branch on a condition or value.",
            "icon": "◇",
            "inputs": [{"id": "in"}],
            "outputs": [{"id": "true", "label": "true"}, {"id": "false", "label": "false"}],
            "configSchema": [
                {
                    "type": "expression",
                    "key": "condition",
                    "label": "Condition",
                    "example": "{{ $json.active }}",
                    "required": True,
                }
            ],
        },
        {
            "name": "switch_case",
            "category": "logic",
            "label": "Switch",
            "description": "Route to one of N labelled outputs based on a key.",
            "icon": "⤳",
            "inputs": [{"id": "in"}],
            "outputs": [
                {"id": "case_a", "label": "a"},
                {"id": "case_b", "label": "b"},
                {"id": "default", "label": "default"},
            ],
            "configSchema": [
                {
                    "type": "expression",
                    "key": "value",
                    "label": "Switch on",
                    "example": "{{ $json.kind }}",
                    "required": True,
                },
                {
                    "type": "json",
                    "key": "cases",
                    "label": "Cases (JSON)",
                    "default": {"a": "case_a", "b": "case_b"},
                },
            ],
        },
        {
            "name": "for_each",
            # Read from logic.py:63.
            "outputShape": [
                {"path": "items", "type": "array", "description": "The list that was iterated."},
                {"path": "count", "type": "number", "description": "How many items it held."},
            ],
            "category": "logic",
            "label": "For Each",
            "description": "Iterate over a list, emitting each item on `item`.",
            "icon": "↻",
            "inputs": [{"id": "in"}],
            "outputs": [{"id": "item", "label": "item"}, {"id": "done", "label": "done"}],
            "configSchema": [
                {
                    "type": "expression",
                    "key": "source",
                    "label": "List",
                    "example": "{{ $json.users }}",
                    "required": True,
                },
                {
                    "type": "number",
                    "key": "concurrency",
                    "label": "Concurrency",
                    "default": 1,
                    "min": 1,
                    "max": 50,
                },
            ],
        },
        {
            "name": "subflow",
            "category": "logic",
            "label": "SubFlow",
            "description": (
                "Run another workflow and bring its result - or its live progress - "
                "back into this one."
            ),
            "icon": "⧉",
            "inputs": [{"id": "in"}],
            # The `stream` port only exists when something streams; see
            # Subflow.ports() for the config-derived set.
            "outputs": [{"id": "out", "label": "result"}],
            "configSchema": [
                {
                    "type": "text",
                    "key": "workflow",
                    "label": "Workflow",
                    "required": True,
                    "placeholder": "onboarding-v2",
                    "description": "Reference resolved by the host's WorkflowResolver.",
                },
                {
                    "type": "number",
                    "key": "version",
                    "label": "Pin to version",
                    "description": (
                        "Optional. Leave blank to always run the child current version. "
                        "Pinning fails the run loudly if the child has moved on. Without "
                        "it, someone edits the child and this flow silently runs different "
                        "logic."
                    ),
                },
                {
                    "type": "select",
                    "key": "mode",
                    "label": "Return",
                    "default": "output",
                    "options": [
                        {"value": "output", "label": "Output when it finishes"},
                        {"value": "stream", "label": "Stream progress as it runs"},
                        {"value": "both", "label": "Both - stream, then output"},
                    ],
                    "description": (
                        "Streaming adds a second port so a parent can show progress "
                        "instead of a spinner."
                    ),
                },
                {
                    "type": "json",
                    "key": "inputs",
                    "label": "Input mapping",
                    "description": (
                        "Entry-point inputs for the child run. Omit to pass this node's "
                        "inputs straight through."
                    ),
                },
                {
                    "type": "number",
                    "key": "maxDepth",
                    "label": "Max nesting depth",
                    "default": structural.DEFAULT_MAX_DEPTH,
                    "min": 1,
                    "max": 32,
                    "description": "Guards against a workflow referencing itself.",
                },
            ],
        },
        {
            "name": "merge",
            "category": "logic",
            "label": "Merge",
            "description": "Combine multiple inputs into one object or array.",
            "icon": "⊕",
            "inputs": [{"id": "a"}, {"id": "b"}],
            "outputs": [{"id": "out"}],
            "configSchema": [
                {
                    "type": "select",
                    "key": "mode",
                    "label": "Mode",
                    "default": "merge",
                    "options": [
                        {"value": "merge", "label": "Object merge"},
                        {"value": "concat", "label": "Array concat"},
                    ],
                }
            ],
        },
        {
            "name": "wait",
            # Read from logic.py:118.
            "outputShape": [
                {"path": "waited", "type": "string", "description": "Which wait mode ran."},
                {"path": "duration", "type": "number", "description": "How long it waited."},
                {
                    "path": "input",
                    "type": "unknown",
                    "description": "The value that arrived, carried forward.",
                },
            ],
            "category": "logic",
            "label": "Wait",
            "description": "Sleep or wait for an external event.",
            "icon": "⏸",
            "configSchema": [
                {
                    "type": "select",
                    "key": "mode",
                    "label": "Mode",
                    "default": "duration",
                    "options": [
                        {"value": "duration", "label": "Duration"},
                        {"value": "until", "label": "Until timestamp"},
                        {"value": "event", "label": "External event"},
                    ],
                },
                {
                    "type": "text",
                    "key": "duration",
                    "label": "Duration",
                    "placeholder": "5s, 10m, 1h",
                    "description": "Used when mode = duration.",
                },
            ],
        },
        {
            "name": "transform",
            "category": "logic",
            "label": "Transform",
            "description": "Reshape data with an expression.",
            "icon": "ƒ",
            "configSchema": [
                {
                    "type": "expression",
                    "key": "expression",
                    "label": "Expression",
                    "example": "{{ { id: $json.id, name: $json.first + ' ' + $json.last } }}",
                    "required": True,
                }
            ],
        },
        # ---------------- Data ----------------
        {
            "name": "memory_store",
            "category": "data",
            "label": "Memory Store",
            "description": "Read or write per-conversation memory.",
            "icon": "\U0001f9e0",
            "configSchema": [
                {
                    "type": "select",
                    "key": "operation",
                    "label": "Operation",
                    "required": True,
                    "default": "read",
                    "options": [
                        {"value": "read", "label": "Read"},
                        {"value": "write", "label": "Write"},
                        {"value": "append", "label": "Append"},
                    ],
                },
                {
                    "type": "text",
                    "key": "key",
                    "label": "Key",
                    "placeholder": "user.preferences",
                    "required": True,
                },
                {
                    "type": "expression",
                    "key": "value",
                    "label": "Value (write/append only)",
                    "example": "{{ $json }}",
                },
                {
                    "type": "credential",
                    "key": "store",
                    "label": "Memory store",
                    "credentialType": "memory_store",
                },
            ],
        },
        {
            "name": "data_store",
            "category": "data",
            "label": "Data Store",
            "description": "Key-value or table read/write against a host store.",
            "icon": "\U0001f5c3",
            "configSchema": [
                {
                    "type": "select",
                    "key": "operation",
                    "label": "Operation",
                    "required": True,
                    "default": "get",
                    "options": [
                        {"value": "get", "label": "Get"},
                        {"value": "set", "label": "Set"},
                        {"value": "delete", "label": "Delete"},
                        {"value": "query", "label": "Query"},
                        {"value": "list", "label": "List"},
                    ],
                },
                {"type": "text", "key": "table", "label": "Table / collection", "required": True},
                {"type": "text", "key": "key", "label": "Key"},
                {
                    "type": "json",
                    "key": "where",
                    "label": "Where (JSON)",
                    "description": "For query/list operations.",
                },
                {
                    "type": "expression",
                    "key": "value",
                    "label": "Value (set only)",
                    "example": "{{ $json }}",
                },
                {
                    "type": "credential",
                    "key": "store",
                    "label": "Data store",
                    "credentialType": "data_store",
                },
            ],
        },
        {
            "name": "variable",
            "category": "data",
            "label": "Variable",
            "description": "Workflow-scoped value used by other nodes.",
            "icon": "\U0001d4cd",
            "configSchema": [
                {"type": "text", "key": "name", "label": "Name", "required": True},
                {"type": "expression", "key": "value", "label": "Value", "required": True},
            ],
        },
        # ---------------- AI ----------------
        {
            "name": "llm_call",
            "category": "ai",
            "label": "LLM Call",
            "description": "Send a prompt + context to a model and receive a response.",
            "icon": "✦",
            "configSchema": [
                {
                    "type": "select",
                    "key": "provider",
                    "label": "Provider",
                    "default": "anthropic",
                    "options": [
                        {"value": "anthropic", "label": "Anthropic"},
                        {"value": "openai", "label": "OpenAI"},
                        {"value": "custom", "label": "Custom"},
                    ],
                },
                {
                    "type": "text",
                    "key": "model",
                    "label": "Model",
                    "placeholder": "claude-sonnet-4-5",
                    "required": True,
                },
                {"type": "textarea", "key": "system", "label": "System prompt", "rows": 4},
                {
                    "type": "expression",
                    "key": "prompt",
                    "label": "User prompt",
                    "example": "{{ $json.question }}",
                    "required": True,
                },
                {
                    "type": "number",
                    "key": "temperature",
                    "label": "Temperature",
                    "min": 0,
                    "max": 2,
                    "step": 0.1,
                    "default": 0.7,
                },
                {
                    "type": "number",
                    "key": "max_tokens",
                    "label": "Max tokens",
                    "min": 1,
                    "max": 8192,
                    "default": 1024,
                },
                {
                    "type": "json",
                    "key": "tools",
                    "label": "Tools (JSON)",
                    "description": "Optional Anthropic-style tool definitions.",
                },
                {
                    "type": "credential",
                    "key": "credential",
                    "label": "API credential",
                    "credentialType": "llm_credential",
                },
            ],
        },
        {
            # Renamed from `llm_branch`: the node picks one of N NAMED ROUTES,
            # it is not a two-way branch, and the id now matches the label and
            # the `routes[]` config. Every previously-shipped id stays an alias,
            # so graphs already carrying `llm_branch` keep resolving. Config
            # keys are unchanged.
            "name": "llm_router",
            # Read from ai.py:229.
            "outputShape": [
                {"path": "route", "type": "string", "description": "The port the model chose."},
                {"path": "reason", "type": "string", "description": "Why the model chose it."},
                {
                    "path": "input",
                    "type": "unknown",
                    "description": "The value that arrived, carried forward.",
                },
            ],
            "category": "ai",
            "label": "LLM Router",
            "aliases": ["llm_branch", "@fancy/llm_branch"],
            "description": "Let a model choose which route the flow takes.",
            "icon": "✧",
            "inputs": [{"id": "in"}],
            # The static ports here are the DEFAULT-config shape; real ports
            # come from the node's own `routes` via LlmRouter.ports().
            "outputs": [
                {"id": "a", "label": "a"},
                {"id": "b", "label": "b"},
                {"id": "fallback", "label": "fallback"},
            ],
            "configSchema": [
                {
                    "type": "textarea",
                    "key": "system",
                    "label": "System prompt",
                    "rows": 3,
                    "description": "Optional framing for the routing decision.",
                },
                {
                    "type": "expression",
                    "key": "prompt",
                    "label": "What to route on",
                    "required": True,
                    "example": "{{ $json.message }}",
                },
                {
                    "type": "json",
                    "key": "routes",
                    "label": "Routes",
                    "description": (
                        "The model picks exactly one. Descriptions are what it chooses "
                        "between - make them distinct."
                    ),
                    "default": [
                        {
                            "port": "a",
                            "description": "Describe when the model should pick this route.",
                        },
                        {
                            "port": "b",
                            "description": "Describe when the model should pick this route.",
                        },
                    ],
                },
                {
                    "type": "select",
                    "key": "provider",
                    "label": "Provider",
                    "default": "anthropic",
                    "options": [
                        {"value": "anthropic", "label": "Anthropic"},
                        {"value": "openai", "label": "OpenAI"},
                        {"value": "custom", "label": "Custom"},
                    ],
                },
                {
                    "type": "text",
                    "key": "model",
                    "label": "Model",
                    "placeholder": "claude-sonnet-4-5",
                },
                {
                    "type": "switch",
                    "key": "fallback",
                    "label": "Add a `fallback` port",
                    "default": True,
                    "description": ("Where the flow goes if the model returns no usable route."),
                },
                {
                    "type": "credential",
                    "key": "credential",
                    "label": "API credential",
                    "credentialType": "llm_credential",
                },
            ],
        },
        {
            "name": "tool_use",
            "category": "ai",
            "label": "Tool Use",
            "description": "Hand control to a host-registered tool by name.",
            "icon": "\U0001f6e0",
            "configSchema": [
                {
                    "type": "text",
                    "key": "tool",
                    "label": "Tool name",
                    "placeholder": "search_index",
                    "required": True,
                },
                {
                    "type": "expression",
                    "key": "args",
                    "label": "Arguments",
                    "example": "{{ { query: $json.q } }}",
                },
            ],
        },
        {
            "name": "embed_search",
            # Read from ai.py:266.
            "outputShape": [
                {"path": "query", "type": "string", "description": "The query that was embedded."},
                {
                    "path": "matches",
                    "type": "array",
                    "description": "Vector-store hits for the query.",
                },
            ],
            "category": "ai",
            "label": "Embed & Search",
            "description": "Embed a query and search a vector store.",
            "icon": "✺",
            "configSchema": [
                {
                    "type": "expression",
                    "key": "query",
                    "label": "Query",
                    "required": True,
                    "example": "{{ $json.question }}",
                },
                {
                    "type": "number",
                    "key": "topK",
                    "label": "Top K",
                    "default": 5,
                    "min": 1,
                    "max": 50,
                },
                {
                    "type": "credential",
                    "key": "vectorStore",
                    "label": "Vector store",
                    "credentialType": "vector_store",
                },
            ],
        },
        # ---------------- IO ----------------
        {
            "name": "api_request",
            # Read from io_.py:39 -- the HttpClient result, which webhook_out reads as status/body.
            "outputShape": [
                {"path": "status", "type": "number", "description": "HTTP status code."},
                {"path": "headers", "type": "object", "description": "Response headers."},
                {"path": "body", "type": "unknown", "description": "Parsed response body."},
            ],
            "category": "io",
            "label": "API Request",
            "description": "HTTP request to any URL.",
            "icon": "↔",
            "configSchema": [
                _HTTP_METHOD,
                {
                    "type": "text",
                    "key": "url",
                    "label": "URL",
                    "placeholder": "https://api.example.com/...",
                    "required": True,
                },
                {
                    "type": "json",
                    "key": "headers",
                    "label": "Headers",
                    "default": {"content-type": "application/json"},
                },
                {"type": "json", "key": "body", "label": "Body"},
                {
                    "type": "credential",
                    "key": "auth",
                    "label": "Auth",
                    "credentialType": "api_credential",
                },
            ],
        },
        {
            "name": "webhook_out",
            # Read from io_.py:57-61.
            "outputShape": [
                {
                    "path": "sent",
                    "type": "boolean",
                    "description": "True once the request was made.",
                },
                {
                    "path": "status",
                    "type": "number",
                    "description": "HTTP status, when the transport reported one.",
                },
                {
                    "path": "response",
                    "type": "unknown",
                    "description": "The response body, when there was one.",
                },
            ],
            "category": "io",
            "label": "Send Webhook",
            "description": "POST a payload to a configured URL.",
            "icon": "↗",
            "configSchema": [
                {"type": "text", "key": "url", "label": "URL", "required": True},
                {"type": "json", "key": "headers", "label": "Headers"},
                {
                    "type": "expression",
                    "key": "payload",
                    "label": "Payload",
                    "required": True,
                    "example": "{{ $json }}",
                },
            ],
        },
        # ---------------- Human ----------------
        {
            "name": "human_approval",
            "category": "human",
            "label": "Human Approval",
            "description": "Pause until a human approves or denies.",
            "icon": "✓",
            "pausesForHuman": "approval",
            "inputs": [{"id": "in"}],
            "outputs": [
                {"id": "approved", "label": "approved"},
                {"id": "denied", "label": "denied"},
            ],
            "configSchema": [
                {
                    "type": "text",
                    "key": "title",
                    "label": "Approval title",
                    "default": "Approve action",
                },
                {
                    "type": "textarea",
                    "key": "description",
                    "label": "Description for approver",
                    "rows": 3,
                },
                {
                    "type": "credential",
                    "key": "channel",
                    "label": "Notify channel",
                    "credentialType": "notify_channel",
                },
                {
                    "type": "switch",
                    "key": "autoAnswerFromInput",
                    "label": "Let an incoming value approve this",
                    "default": False,
                    "description": (
                        "Off by default. Turning it on means the graph, not a person, can "
                        "approve - an upstream value on the approved port decides and the "
                        "gate never pauses. Weigh this harder than on a form."
                    ),
                },
            ],
        },
        {
            "name": "notify",
            # Read from human.py:56.
            "outputShape": [
                {
                    "path": "sent",
                    "type": "boolean",
                    "description": "True once the message was handed to the channel.",
                },
                {"path": "channel", "type": "string", "description": "The channel it went to."},
                {"path": "to", "type": "string", "description": "The recipient."},
                {"path": "message", "type": "string", "description": "The rendered message."},
            ],
            "category": "human",
            "label": "Notify",
            "description": "Send a message via Slack / email / SMS / etc.",
            "icon": "\U0001f514",
            "configSchema": [
                {
                    "type": "select",
                    "key": "channel",
                    "label": "Channel",
                    "default": "slack",
                    "options": [
                        {"value": "slack", "label": "Slack"},
                        {"value": "email", "label": "Email"},
                        {"value": "sms", "label": "SMS"},
                        {"value": "discord", "label": "Discord"},
                    ],
                },
                {"type": "text", "key": "to", "label": "To", "required": True},
                {
                    "type": "expression",
                    "key": "message",
                    "label": "Message",
                    "required": True,
                    "example": "{{ $json.summary }}",
                },
            ],
        },
        # ---------------- Output ----------------
        {
            "name": "output",
            "category": "output",
            "label": "Output",
            "description": "Terminal node - captures the workflow's result.",
            "icon": "●",
            "inputs": [{"id": "in"}],
            "outputs": [],
        },
        {
            "name": "log",
            # Read from output.py:24.
            "outputShape": [
                {
                    "path": "logged",
                    "type": "string",
                    "description": "The message that was written.",
                },
                {"path": "level", "type": "string", "description": "The level it was written at."},
            ],
            "category": "output",
            "label": "Log",
            "description": "Send to the run feed.",
            "icon": "≡",
            "inputs": [{"id": "in"}],
            "outputs": [],
            "configSchema": [
                {
                    "type": "select",
                    "key": "level",
                    "label": "Level",
                    "default": "info",
                    "options": [
                        {"value": "info", "label": "info"},
                        {"value": "warn", "label": "warn"},
                        {"value": "error", "label": "error"},
                    ],
                },
                {
                    "type": "expression",
                    "key": "message",
                    "label": "Message",
                    "required": True,
                    "example": "{{ $json }}",
                },
            ],
        },
    ]


def _STRUCTURAL_LITERALS() -> list[dict[str, Any]]:  # noqa: N802
    return [
        {
            "name": "note",
            "category": "custom",
            "label": "Note",
            "description": "A canvas annotation. Never executed.",
            "icon": "\U0001f5d2",
            "inputs": [],
            "outputs": [],
        },
        {
            "name": "subgraph",
            "category": "custom",
            "label": "Subgraph",
            "description": "Runs a nested workflow.",
            "icon": "▣",
            "inputs": [{"id": "in"}],
            "outputs": [{"id": "out"}],
            "configSchema": [
                {
                    "type": "json",
                    "key": "graph",
                    "label": "Nested workflow (WorkflowSchema)",
                }
            ],
        },
    ]
