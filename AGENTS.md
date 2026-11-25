# Using fancy-flow (Python) — guide for agents

How an agent installs and uses this package in a consumer app. What the package
is, is in [README.md](./README.md). Notes for working ON this repo's code are in
[CONTRIBUTING.md](./CONTRIBUTING.md).

## Install

```bash
pip install fancy-flow
```

- Python 3.11+. Zero runtime dependencies.
- The repo is `fancy-flow-py`; the distribution is `fancy-flow` and the import
  is `fancy_flow`. Twins: npm `@particle-academy/fancy-flow`, Composer
  `particle-academy/fancy-flow-php`.

## Find it on the Fancy MCP server

The catalog server is `https://ui.particle.academy/mcp` (streamable HTTP, no API
key; MCP id `fancy-ui`). It is listed as the registry entry `fancy-flow-py` (a
package entry, not vendorable source). Find it with `search-backend-packages`
(stack `python`).

Workflow nodes: fancy-flow's core builtins need no installation. Marketplace
nodes are found with `search-nodes`, `get-node` and `node-install-instructions`.

## API essentials

```python
from fancy_flow import FlowRunner, RunOptions, builtin, import_workflow

builtin.register()
result = import_workflow(schema_json)  # WorkflowSchema v1, as <FlowEditor> exports it

run = FlowRunner().run(
    result.graph,
    builtin.executors(),
    options=RunOptions(initial_inputs={"trigger-1": {"payload": body}}),
)
run.ok
run.outputs
run.error
```

- **Async:** `await FlowRunner().arun(graph, executors)` awaits awaitable
  executors. Same walk as `run()`.
- **Custom nodes:** `default_registry().register(NodeKind(name="@acme/...", category=..., label=..., aliases=(...), config_schema=(ConfigField(...),), side_effects=...))`,
  then `builtin.executors().bind("@acme/...", fn)`. An executor is a callable,
  an object with `.execute(ctx)`, or a class resolved through your container.
- **Durable runs:** `fancy_flow.durable.Coordinator(graph=..., executors=..., run=..., store=...)`.
  A queue job wraps `advance()` (what may be dispatched now) and
  `run_node(node_id)`; `run_to_completion()` drives both in-process.
  `max_concurrent` (default 1; `UNLIMITED_CONCURRENCY` for the whole ready
  frontier) caps how many of one run's nodes are held at once.
- **Untrusted graphs:** `fancy_flow.security.GraphPolicy.untrusted(allow=[...]).assert_safe(schema)`.
- **LLM and terminals are host capabilities.** Supply an `LlmClient`
  (`llm_router`), a `WorkflowResolver` (`subflow`) and a `TerminalHost`
  (terminal lanes). There is no LLM auto-detection.

## Traps

- **Check `result.refused` before running.** A document without `version: 1` is
  refused in every mode, even with `lenient=True`, and an empty graph runs and
  reports success.
- **`run()` refuses awaitables.** If any executor is `async`, use `arun()`.
  Terminal lane nodes (`terminal_run`, `terminal_send`, `terminal_await`) are
  async, so they only work through `arun()`.
- **Queued runs are serial by default**, and a node paused at a human gate keeps
  its slot. `advance()` returns nothing while a node of the run is held.
- **Human gates fail closed.** Only a recorded answer for that node resumes the
  run.
- `side_effects="unsafe-to-replay"` gives a node one attempt in a durable run.
- `GraphPolicy.untrusted()` starts with an empty allowlist; list every kind you
  accept.
- `{{ x.length }}` on a list is `None` here (it resolves in the TypeScript
  engine).
- A negative, `bool` or non-`int` `max_concurrent` is refused at construction.
- Not built yet: queue adapters (Celery, Dramatiq, Taskiq, Procrastinate), a
  persistent `NodeClaimStore`, a web-framework integration, the `agent`
  executor. Supply your own store and queue glue.
