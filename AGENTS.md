# AGENTS.md — fancy-flow-py

Python runtime for `fancy-flow` workflow graphs. The framework-free twin of
`@particle-academy/fancy-flow`'s TypeScript engine and of
`particle-academy/fancy-flow-php`. `CLAUDE.md` symlinks here.

This file describes **this package's code**. Process rules — publishing, kit
versioning, backports, the issue protocol — live in the envelope's `AGENTS.md`
and are deliberately not repeated.

## What this package is

A faithful **port**, not a redesign. Behaviour questions are settled against the
peers, in this order: `@particle-academy/fancy-flow`'s `src/runtime/run-flow.ts`
and `src/registry/*` for the contract, `particle-academy/fancy-flow-php` for how
a *server* twin realises it.

The guarantee: **same `WorkflowSchema` JSON in, same `RunResult.outputs` out**
on Node, PHP and Python. Don't break it.

Where this port deliberately differs from a peer, the difference is in a
docstring at the point of divergence and in the envelope plan. There are three
as of 0.1.0, and all three are listed under "Deliberate divergences" below.

## Architecture

Pure core, `src/` layout, **zero runtime dependencies**.

- `workflow.py` — import / export / validate WorkflowSchema v1.
- `engine/runner.py` — `FlowRunner`, the `runFlow` port. Kahn topo, ports,
  branching, cycles, timeout, resume. **A node runs when ≥1 incoming edge is
  active** (merge-after-decision, `#1`) and `_collect_inputs` reads only active
  edges. Don't regress either.
- `registry/` — `NodeKindRegistry`, `NodeKind`, `ConfigField`, `kind_id`, and
  `builtin` (the 24 authorable kinds, plus structural `note` and `subgraph`,
  and a default executor for each one that executes -- `note` never does).
- `executors.py` — `ExecutorRegistry`; resolves node id → kind → `*`.
- `runtime/` — `RunEvent`, `RunOptions`, `RunResult`, `ExecutionContext`,
  `Port`, `Pause`, `AbortSignal`.
- `nodes/` — the default executors by domain, plus `nodes/support/` (injectable
  client protocols, offline fakes, the `{{ }}` resolver, structured output).
- `capabilities/` — the HOST seam: `LlmClient` (`choose_route`, used by
  `llm_router`) and `WorkflowResolver` (used by `subflow`).
- `durable/` — resumable runs with **no queue library**: the claim contract, the
  frontier, per-node replay, retry policy, human gates, and the coordinator.
- `security/policy.py` — `GraphPolicy`, for a graph that arrived over the wire.
- `marketplace/manifest.py` — node-manifest validation and `satisfies_range`.

### The engine is one walk, driven two ways

TypeScript executors may be `async`; PHP's are synchronous. Python has both, and
writing the loop twice would put two copies of the routing rules in the file
that exists to have exactly one.

So `FlowRunner._walk` is a **generator**: it yields a node to execute and is sent
the outcome back. `run()` drives it synchronously; `arun()` drives the same
generator while awaiting awaitable results. Add behaviour to `_walk`, never to a
driver.

`run()` refuses an awaitable rather than storing it. A coroutine object sitting
in `outputs[node]` looks like success and reaches every downstream node as a
value nothing can read.

### The durable layer, and why it is not Temporal

Durability is **checkpoint-per-node, keyed by node id**, in the host's own
store — the same shape as `fancy-flow-php`'s `node_outputs`.

Event-sourced replay (Temporal, Restate) exists to police *arbitrary user code*
for non-determinism, and charges a sandbox, history-size limits and permanent
versioning for it. An interpreter over a declarative JSON graph is deterministic
by construction, so none of that is bought. Worse, checkpoint identity in those
systems is positional (DBOS keys on a call ordinal); ours is the node id, which
is what lets a graph be edited while a run is parked on an approval.

Three rules hold the per-node driver together, and each exists because the
alternative fails silently:

1. **The engine is not reimplemented.** `durable/replay.py` executes a node by
   replaying the graph *through* `FlowRunner` with completed nodes fed back as
   `resume_outputs` and every other node fenced off with `bind_node`. Inputs,
   branching and skips are therefore the engine's, not a driver's copy.
2. **Activated ports come from the engine's own `node-output` events**, stored on
   the claim row. `Frontier` reads them; it must never compute them. A second
   copy of `_activated_ports` would agree for a year and then disagree on one
   branch.
3. **The claim is a unique constraint, not a check.** A lost race is a NO-OP.
   `NodeClaimStore.claim()` must be atomic, and must let an owner re-enter its
   OWN claim — that is what lets a retry resume instead of deadlocking against
   the row it wrote itself.

`Coordinator.run_to_completion()` drives both operations in-process. It is a real
durable runner over a persistent store, not a stand-in for one.

### Kind ids

Canonical ids are `@particle-academy/<name>`; old spellings live on as aliases.
**Anything keyed by kind name must key on EVERY id a kind answers to** —
registry lookups, executor bindings, retry overrides and `GraphPolicy` all do.
Getting this wrong once cost a human gate: a durable override bound under the
bare name only never matched a node saved as `@particle-academy/user_input`, so
the run went straight past the person it was meant to stop for, and nothing
errored.

## Deliberate divergences

Three, all tested and all recorded in `.ai/plans/fancy-flow-py.md`:

1. **`GraphPolicy.untrusted()` fails closed.** The PHP twin returns a policy
   whose allowlist is *absent*, and an absent allowlist permits every kind — so
   a caller who forgets `allowKinds()` gets size caps and byte hygiene from a
   method named `untrusted`. Here the allowlist starts empty. No correctly
   configured policy changes verdict.
2. **No LLM auto-detection.** PHP probes for Prism / laravel-ai with
   `class_exists()`, which is free. The Python equivalent is importing a
   candidate to find out whether it is there: side effects, start-up cost, and a
   provider the author never named. A missing client aborts with
   `llm_unavailable_message()` instead.
3. **`{{ x.length }}` on a list is `None`.** JavaScript resolves it because
   arrays carry a `length` property; PHP does not, and neither does this. The
   shared table has no row for it — promoting one is in the plan.

## Parity is a test result, not a claim

- `tests/conformance/` runs `shared/expr` **and** `shared/satisfies-range` from
  `particle-academy/fancy-conformance` through `tests/conformance/loader.py`.
  A missing conformance checkout is a **failure**, never a skip.
- `tests/parity/test_graph_fixtures.py` runs the 23 golden `WorkflowSchema`
  fixtures. Read `tests/parity/fixtures/SOURCE.md` before touching them: they
  are a COPY of the PHP twin's, because those goldens are not in the shared
  package and nothing on the Node side runs them at all.
- `tests/parity/test_fixture_provenance.py` fails if that copy has drifted from
  the twin's, whenever the twin is on disk.
- `tests/parity/test_durable_driver_parity.py` runs **every fixture through the
  per-node durable driver too** and requires the same answer. That is what pins
  "what is unblocked?" against "what is next?".

`tests/conformance/loader.py` is a **third loader for a package that ships two**.
It belongs in `fancy-conformance`; until it lands there, treat this file as a
bridge and keep it behaviourally identical to the Node and PHP loaders.

## Conventions

- **Python 3.11 floor.** Frozen dataclasses, `Protocol` over base classes,
  `X | None`.
- **No runtime dependencies in the core.** Injectable protocols with offline
  defaults, never a hard dep. An adapter takes its dependency behind an extra.
- **Faithfulness first.** If in doubt, match `run-flow.ts` semantics and add a
  fixture. Divergence from the TS *code* (never the TS *contract*) gets a
  docstring at the point of divergence.
- **Regenerate fixtures deliberately.** They are golden files; only regenerate
  when behaviour legitimately changes, and eyeball the diff.

## Commands

```bash
python -m pip install -e . --group dev
pytest                              # unit + parity + conformance
pytest tests/unit/test_engine.py    # one file
ruff check . && ruff format --check .
mypy
```

`FANCY_CONFORMANCE_ROOT` and `FANCY_FLOW_PHP_FIXTURES` override the sibling-repo
discovery when the checkouts are somewhere unusual.

## Status

**0.1.0 — core parity, unreleased.** The engine, the registries, the 24 built-in
kinds plus structural `note` / `subgraph` and their executors, `{{ }}`,
capabilities, the node manifest, `GraphPolicy`, and the durable core (claims /
frontier / replay / retries / human gates / coordinator) are built and tested.

**Not built, and staged in the plan:** queue adapters (Celery, Dramatiq, Taskiq,
Procrastinate), a persistent `NodeClaimStore`, a web-framework integration, the
`agent` executor, and the Human+ / MCP surface.
