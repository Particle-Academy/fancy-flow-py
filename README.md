# fancy-flow (Python)

[![Fancified](art/fancified.svg)](https://particle.academy)

The Python runtime for [`fancy-flow`](https://github.com/Particle-Academy/fancy-flow)
workflow graphs — the third twin of its headless TypeScript engine, alongside
[`fancy-flow-php`](https://github.com/Particle-Academy/fancy-flow-php).

> A graph an agent or human authors in `<FlowEditor>` runs **unchanged** on
> Python. Same JSON in, same outputs out. The editor stays the one authoring
> surface; Python becomes a peer runtime alongside Node and PHP.

**Zero runtime dependencies.** Everything the built-in nodes reach for — HTTP,
an LLM, a vector store, a queue — is an injected protocol with a deterministic
offline default, so a workflow app that never calls a model does not inherit a
provider SDK, and every test runs without a network.

```python
from fancy_flow import FlowRunner, RunOptions, builtin, import_workflow

builtin.register()  # install the built-in kinds
result = import_workflow(schema_json)  # WorkflowSchema v1

run = FlowRunner().run(
    result.graph,
    builtin.executors(),  # or your own bindings
    options=RunOptions(initial_inputs={"trigger-1": {"payload": body}}),
)

run.ok  # bool
run.outputs  # {node_id: value}
run.error  # str | None
```

`import_workflow(..., lenient=True)` softens an unknown kind to a warning. It
never softens the schema version: a document without `version: 1` is refused in
every mode, with an empty graph and `result.refused` true, exactly as the
TypeScript and PHP runtimes refuse it. Check `result.refused` before running,
or an empty graph runs and reports success.

## Async, without two engines

Executors may be synchronous or `async`. The graph walk is written once and
driven two ways, so branching, skipping and port routing cannot drift between
them.

```python
run = await FlowRunner().arun(graph, executors)  # awaits awaitable executors
```

The synchronous runner **refuses** an awaitable rather than storing it: a
coroutine object in `outputs` looks like success and reaches every downstream
node as a value nothing can read.

## Custom nodes

Two halves, kept in sync — exactly the path every built-in takes.

```python
from fancy_flow import ConfigField, NodeKind, ExecutorRegistry, default_registry

default_registry().register(
    NodeKind(
        name="@acme/send_invoice",
        category="io",
        label="Send invoice",
        aliases=("send_invoice",),
        config_schema=(ConfigField(type="text", key="to", label="To", required=True),),
        side_effects="unsafe-to-replay",  # a durable run gives this ONE attempt
    )
)


def send_invoice(ctx):
    return {"sent": ctx.option("to")}


executors = builtin.executors().bind("@acme/send_invoice", send_invoice)
```

An executor may be a callable, an object with `.execute(ctx)`, or a class
resolved through your container.

## Durable runs, with no queue library

Durability is **checkpoint-per-node, keyed by node id**. The core owns the hard
part — which node may run, and with what inputs — and a queue supplies transport
and nothing else.

```python
from fancy_flow.durable import Coordinator

flow = Coordinator(graph=graph, executors=executors, run=run_id, store=store)

ready = flow.advance()  # what may be dispatched right now -> one job per id
flow.run_node(node_id)  # claim, run through the real engine, checkpoint
flow.run_to_completion()  # or drive both, here, in this process
```

`advance()` and `run_node()` are the two operations a Celery / Dramatiq / Taskiq
job wraps: call `advance()` again whenever a job settles. `Coordinator.run_to_completion()`
over a persistent `NodeClaimStore` is already a real durable runner: a crash
resumes from the same place a crashed worker would, because the resume behaviour
lives in the checkpoints rather than in the loop.

**A queued run dispatches one node at a time by default.** A node goes on the
queue only after the node before it has settled, in the graph's declaration
order, and a node paused for a person keeps its slot, so nothing is handed out
beside a gate while the person decides. `advance()` returns nothing while a node
of the run is held. To hand out the whole ready frontier instead, opt in:

```python
from fancy_flow.durable import UNLIMITED_CONCURRENCY

Coordinator(..., max_concurrent=UNLIMITED_CONCURRENCY)  # every ready node at once
Coordinator(..., max_concurrent=4)  # at most four of this run's nodes held at once
```

A negative, `bool` or non-`int` `max_concurrent` is refused at construction.

Human gates **fail closed**: `user_input` and `human_approval` pause because
they *are* human nodes, not because their input port happens to be empty. Only a
recorded answer for that node resumes the run.

## Accepting a graph you did not write

`import_workflow` answers *is this graph coherent?* `GraphPolicy` answers *is it
safe to accept?* — kind allowlists (resolved across every id a kind answers to),
size caps, byte hygiene, structure, and host rules.

```python
from fancy_flow.security import GraphPolicy

GraphPolicy.untrusted(allow=["manual_trigger", "transform", "output"]).assert_safe(schema)
```

## Parity

The guarantee is asserted, not asserted-to. The suite runs the shared
tables from
[`fancy-conformance`](https://github.com/Particle-Academy/fancy-conformance)
(among them `shared/expr`, `shared/satisfies-range` and `flow/durable-dispatch`),
the 23 golden `WorkflowSchema` fixtures, and — because a queued run derives
readiness from the opposite end — every one of those fixtures a second time
through the per-node durable driver.

```bash
python -m pip install -e . --group dev
pytest
```

## Status

Pre-1.0: breaking changes land in minor releases. See
[`CHANGELOG.md`](CHANGELOG.md) and, for how the package is built and what is
staged next, `AGENTS.md`.

MIT.
