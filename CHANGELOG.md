# Changelog

All notable changes to `fancy-flow` (Python) are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

This package is pre-1.0, so **breaking changes land in MINOR releases**. The
version number is not a promise it can yet keep; the entries are.

## [Unreleased]

## [0.2.0] - 2026-08-24

### Fixed

- **`subflow` now runs its child against the parent's registry.** It fell back
  to `builtin_executors(...)` — the BARE builtins — so a host-registered kind
  resolved at top level and vanished one level down, and a host that had
  REPLACED a builtin got the package's version inside the child. The same graph
  behaved two ways depending on nesting depth, and nothing warned, because an
  unregistered kind fails closed with no outputs.

  The registry now rides on `ExecutionContext.executors`, so any executor that
  starts a nested run inherits it without opting in.

  Reported against the PHP twin as `fancy-flow-php#7` and fixed in all three
  runtimes together — found here by checking parity rather than assuming it.

### Added

- **Per-node status messages**, matching `@particle-academy/fancy-flow` 0.49.0
  and `particle-academy/fancy-flow-php` 0.21.0. A `FlowNode` may carry
  `starting_msg` / `stopping_msg`; the runner announces them around that node as
  `RunEvent.node_message()` (`node-message`, `phase` of `start` or `end`).
  Carried through `import_workflow` / `export_workflow`, and omitted from the
  document entirely when unset.

  Opt-in per node — most nodes in a graph are plumbing, and narrating all of
  them buries the steps a person follows.

  **`stopping_msg` fires only when the node SUCCEEDS.** A completion message
  after a raise tells a human the opposite of what happened.


### Added

- **`RunIdentity` on the execution context, so a node that WRITES can send an
  idempotency key.** `ctx.run` is new, and `ctx.run.step_key(ctx.node.id)` is
  the key.

  Nothing in `{node, inputs, emit, depth}` could produce a key that is the same
  on a retry and different on a different execution, so a writing connector had
  to declare `unsafe-to-replay` and send none — meaning a timed-out payment
  could never be retried, because retrying it would charge the card twice.

  What identifies a step is deliberately **not** `(run, node)`: a node
  legitimately runs many times in one run, once per subflow invocation and once
  per loop iteration. The key is the run key plus the *path of invocations* that
  led here plus an optional occurrence — `run_9f2c:billing/pay#3` — and
  `attempt` is carried on the identity but **deliberately absent from the key**.

  `is_replay_safe(window_seconds, now)` answers the other half: providers forget
  keys (Stripe after 24h), and past that window resending the key and minting a
  fresh one BOTH write twice, so the caller must refuse. Attempt 1 is always
  safe, which is what lets a run park on a human gate for a week and then write.

  Pinned across Python, TypeScript and PHP by `shared/flow-run-identity` in
  `fancy-conformance` (25 rows).

### Fixed

- **`Coordinator.retry` was wired to nothing.** `RetryPolicy` was a declared
  field with **no read site anywhere in the package** — so a host constructing
  `Coordinator(retry=RetryPolicy(tries=3))` got exactly one attempt per node and
  no error to say otherwise. `unsafe-to-replay` appeared to be honoured only
  because nothing retried at all.

  `run_to_completion()` now consults the policy: a failed node whose attempts
  are not exhausted is retried under the **same owner token**, so it re-enters
  its own claim and derives the **same** step key — which is what makes the
  retry idempotent rather than duplicative. A node declaring `unsafe-to-replay`
  is still pinned to one attempt whatever `tries` says.

  `NodeOutcome` gains `attempt` and `retryable`. A failed-but-retryable node is
  deliberately left CLAIMED rather than recorded FAILED: a FAILED node settles,
  and settling one mid-retry skips everything downstream while the run reports a
  tidy finish.

### Changed

- **BREAKING (unreleased): `Coordinator.run_key` is now `Coordinator.run`**, and
  takes a `RunIdentity` or a bare run-key string. *What to do:* rename the
  keyword — `Coordinator(..., run="run_9f2c")`. `coordinator.run_key` still
  reads the key.
- `NodeState` gains `first_attempt_at`, the retry clock. It is stamped on the
  first claim and **must never move**: a store that refreshed it per attempt
  would report a retry 25 hours late as seconds old, and a connector would reuse
  a key the provider forgot yesterday. Adapters constructing `NodeState` get a
  sane default and need no change.

## [0.1.0] - unreleased

The first cut: the pure engine, the node contracts, the registries, and a
durable layer that needs no queue library.

### Added

- **`FlowRunner`** — the port of `runFlow`. Kahn topological order, the three
  port-activation conventions (`__port`, `branch`, declared outputs), cycle
  detection, timeout, host abort, and `resume_outputs`.
  - One graph walk, driven two ways: `run()` is synchronous, `arun()` awaits
    awaitable executors. Both drive the same generator, so the routing rules
    exist once.
  - `run()` **refuses** an awaitable rather than storing a coroutine object that
    would look like success and reach every downstream node.
- **WorkflowSchema v1** — `import_workflow` / `export_workflow` / `to_json`,
  with `ImportIssue` diagnostics for unknown kinds, missing required config and
  dangling edges.
- **Registries** — `NodeKindRegistry` (kinds, aliases, config defaults and
  validation) and `ExecutorRegistry` (node id → kind → `*`, alias-aware in both
  directions, callables / objects / classes through a `Resolver`).
- **24 built-in kinds** across trigger / logic / data / ai / io / human / output,
  plus structural `note` and `subgraph`, each with a framework-free default
  executor and an offline client.
- **`{{ }}` expressions** — dot-paths only, no evaluation. Scanned rather than
  matched with a regex, so the linear-time property is structural rather than
  pattern-dependent.
- **Capabilities** — `LlmClient` and `WorkflowResolver` as host seams, so core
  ships an opinionated `llm_router` and `subflow` without any consumer
  inheriting a provider SDK.
- **`fancy_flow.durable`** — resumable runs with no queue dependency:
  `NodeClaimStore` (+ an in-memory reference), `Frontier`, per-node `replay`,
  `RetryPolicy`, fail-closed human gates, and a `Coordinator` exposing the two
  operations a queue adapter dispatches.
- **`GraphPolicy`** — kind allow/deny lists, size caps, byte hygiene and host
  rules for a graph that arrived over the wire.
- **Node manifests** — `validate`, `check_runtime_support`,
  `check_capabilities`, `satisfies_range`. The `runtimes` map is open, so a node
  can declare a `py` backend with no change to any runtime's validator.
- **Parity suites** — `shared/expr` and `shared/satisfies-range` from
  `particle-academy/fancy-conformance`; the 23 golden `WorkflowSchema` fixtures;
  a provenance check against the PHP twin's copies; and every fixture run a
  second time through the durable driver.

### Changed

Three deliberate divergences from the PHP twin, each tested and each explained
at the point of divergence:

- **`GraphPolicy.untrusted()` fails closed.** The PHP twin's leaves the
  allowlist *absent*, which permits every kind — so a caller who forgets
  `allowKinds()` gets size caps and byte hygiene from a method named
  `untrusted`. Here nothing is permitted until something is named.
  **What you must do:** pass the kinds you permit, either as
  `GraphPolicy.untrusted(allow=[...])` or with `.allow_kinds([...])`. Any policy
  that already named its kinds is unaffected.
- **No LLM auto-detection.** PHP probes for Prism / laravel-ai with
  `class_exists()`. The Python equivalent is importing a candidate to find out
  whether it is installed. **What you must do:** call
  `fancy_flow.capabilities.set_llm_client(...)`. A missing client aborts the
  node with a message naming that call.
- **`{{ list.length }}` resolves to `None`,** matching PHP rather than
  JavaScript. **What you must do:** nothing, unless a graph authored against the
  Node runtime relied on it — in which case it was already broken on PHP.

[Unreleased]: https://github.com/Particle-Academy/fancy-flow-py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Particle-Academy/fancy-flow-py/releases/tag/v0.1.0
