# Changelog

All notable changes to `fancy-flow` (Python) are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

This package is pre-1.0, so **breaking changes land in MINOR releases**. The
version number is not a promise it can yet keep; the entries are.

## [Unreleased]

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
- **26 built-in kinds** across trigger / logic / data / ai / io / human / output,
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
