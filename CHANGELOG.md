# Changelog

All notable changes to `fancy-flow` (Python) are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

This package is pre-1.0, so **breaking changes land in MINOR releases**. The
version number is not a promise it can yet keep; the entries are.

## [0.16.0] - 2026-08-26

### Added

- **`import_workflow` now refuses a graph containing a node that cannot take
  part in it.** The Python twin of `fancy-flow-php` 0.48 and the TypeScript
  runtime's 0.64, so all three agree.

  Two shapes, both of which imported clean and then quietly did nothing. Both
  were MEASURED against the engine first, and neither of them fails:

  - **A floating node** — no inbound and no outbound edge. It is not skipped: a
    node with no incoming edge is a root, so the topological sort runs it. A
    three-node graph with one stray `log` executed `t,lonely,o`. It runs
    disconnected, receiving nothing and reaching nobody.
  - **An edge whose SOURCE is a terminal node** (`output`, `log` — the kinds
    declaring an empty output-port list). Measured: `t -> output -> log`
    imported clean and the `log` RAN, with `{{ input }}` resolving to `""`.

  **What may float:** a `note` (across every id it answers to), any kind
  categorised `annotation` or `layout` — a swimlane is never wired to anything,
  which is what a lane IS — and any kind the registry does not know. That last
  one is not a loophole: an unknown kind already produces its own issue, and we
  cannot know whether it is a step, an annotation or a lane, so claiming it must
  be wired would assert something unverifiable.

  New: `fancy_flow.analysis.check_graph_connectivity` / `may_float`.

### What a consumer must do

**Almost certainly nothing.** Only `import_workflow` changed; nothing about
running a graph did.

Where you WILL see it: loading a saved document with a stray node now returns
`ok=False` with an error naming that node's id (or the edge id, for a terminator
edge), and every offending node is reported at once rather than one per round
trip. If a stray node was a comment, make it a `note`.

## [0.15.0] - 2026-08-26

### Added

- **`try_resolve_path()` — telling "did not resolve" apart from "resolved to
  empty".**

  `resolve_path()` returns `None` both for a path that does not exist and for one
  that exists holding `None`, and at the interpolation layer that collapses
  further to `""`. In the reporting consumer's words: *"An unresolvable path
  yields `''`, so a wrong field is indistinguishable from an empty one at
  runtime."* A misspelled field renders as an empty string, exactly like a field
  that is legitimately empty — worst on LLM-authored graphs, where the field name
  was guessed to begin with.

  Same shape as the collapses fixed in 0.12.0–0.14.0, one layer up. A second
  return channel (a `NamedTuple` of `resolved` / `value`) rather than a cleverer
  sentinel, because **every sentinel is a legal value for somebody**: `""`,
  `None` and `False` are all things a real payload carries.

- **`evaluate(template, context, on_unresolved)`** — `"empty"` (today's
  behaviour, the DEFAULT), `"keep"` (leave the `{{ … }}` text so the failure is
  visible in the output without stopping the run), `"throw"` (raise
  `UnresolvedPathError`, which carries `.path`).

  **Nothing to do.** The default is unchanged and every existing call keeps its
  behaviour; opt-in before default was the reporting consumer's own condition.
  Implemented identically in `@particle-academy/fancy-flow` and
  `particle-academy/fancy-flow-php`, with the three test files mirroring each
  other case for case.

### Changed

- `resolve_path()` is now defined in terms of `try_resolve_path()` rather than
  repeating the walk. No answer changes — two copies of a traversal agree right
  up until someone edits one of them.

## Versions 0.12.0 – 0.14.0 were tagged but NEVER PUBLISHED

Read this before looking for them on PyPI: **`pip install fancy-flow==0.13.0`
will fail.** The index went 0.11.0 → 0.15.0, and every change described below
reached users in **0.15.0**.

The tooling behaved correctly throughout. `publish.yml` refuses a tag whose
version has no `CHANGELOG.md` entry, so all three publishes were rejected for
exactly the right reason — three red runs, and nobody was looking at them. The
same three fixes were blocked identically in `fancy-flow` (npm), so six tags
across two ecosystems carried work that never reached a consumer. Only the PHP
twin received them, because Packagist syncs from the tag with no gate.

The entries keep their own version numbers because the git tags exist and
someone reading `git log` needs to find them. A preflight now runs the same
assertion before `git tag`, which is the only moment the failure is preventable.

## [0.14.0] - 2026-08-26 — tagged, not published (see above)

### Added

- **`emits: "input-map-merged"` — merging the input MAP is not merging the
  payloads**, and one keyword was covering both. `manual_trigger` and
  `schedule_trigger` merge the raw input map; `merge` unions each port's
  PAYLOAD. Those coincide only at an entry point, because `_collect_inputs`
  seeds an entry node FLAT and keys every other node by handle.

  **Nothing to do** — a declaration about existing behaviour; no node changed
  what it emits.

## [0.13.0] - 2026-08-26 — tagged, not published (see above)

### Fixed

- **A port bound to `None` was treated as an ABSENT port**, so reading an input
  fell back to its default when the port held an explicit `None`. The fallback
  itself is right and stays — a trigger has no `in` edge — but only the ABSENT
  case may fall back. **The rule: `or` / `is None` shortcuts are safe only where
  null is not a legal value**; key presence is the only correct test.

## [0.12.0] - 2026-08-26 — tagged, not published (see above)

### Fixed

- **A branching node whose payload is `None` emitted the wrapper downstream.**
  The two port sugars unwrapped asymmetrically, so a downstream node received
  the `{branch, value}` wrapper — fields no kind declares — while the declared
  ones were absent.

## [0.11.0] - 2026-08-26

### Added

- **Runs the shared `flow/kind-declaration-surface` table** — 20 cases asserting
  this runtime declares the same things about the same kinds as the other three.

### Fixed

- **`llm_call` and `user_input` were not config-dependent here, and the shared
  table caught it on its first run.** Both are Closures in the PHP and
  TypeScript twins; here they were undeclared, so a consumer asking what
  `llm_call` emits got `None` — *nobody declared* — rather than *depends on
  config, ask the host*.

  Those are different answers requiring different responses, and the difference
  is the whole point of the field. `llm_call` now gains `data` only when the
  author set a `response_schema` (`ai.py:37`, `ai.py:80`), and `user_input`
  emits the keys its author defined (`human.py:29-32`).

  Worth recording HOW it was found: the fixture had run zero rows against this
  runtime until this release, and its first run reported exactly this. A
  parity table that nothing points at is not a check — that gap was flagged as
  remaining work before it was closed, and closing it immediately produced two
  real findings.

## [0.10.0] - 2026-08-26

### Added

- **`emits` — how a kind's output RELATES to its input.** The half a field list
  cannot express, and the reason a consumer was reimplementing our executors'
  semantics in a static analyser with nothing to fail when they changed.

  `output_shape` answers *which fields*; `emits` answers *where they come from*:
  `"input"`, `"inputs-merged"`, `"expression:<key>"`, or a callable of config.
  Read via `emits_for(config)`; `expression_config_key(config)` returns the key
  an expression relation names.

  **The config key is part of the value.** `transform` reads `expression`,
  `variable` reads `value` — a consumer hardcoding "the field called expression"
  has copied our knowledge one level down, which is the thing this removes.

  Declared: `branch`, `switch_case`, `output`, `human_approval`,
  `manual_trigger` (`input`); `variable` (`expression:value`); `transform` and
  `merge` (callables); `schedule_trigger` (`inputs-merged`, composed with its
  own `cron`/`timezone` list). Every one read from THIS runtime's executor and
  cited.

### Two rules the design needed, both from a consumer reading executors

- **A relation with no destination can only express a TOP-LEVEL merge.** `wait`
  returns `{"waited": …, "duration": …, "input": …}` — it **nests** its input
  under a key, so a relation there would make a reader accept
  `{{ in.<any inbound field> }}` at top level, which resolves to nothing at run
  time. It keeps a static list with an opaque `input` field instead.

- **`merge` in `concat` mode declares NO relation and no field list.** It emits
  a list, whose elements are not addressable as top-level fields; `[]` would
  claim "emits no fields", which is false and would refuse every reference.

  `schedule_trigger` moved the other way for the same reason: its partial
  `["cron","timezone"]` list was unsafe only while nothing could say the inputs
  also merge.

- `webhook_trigger` deliberately declares nothing — its choice is **data**-
  dependent, not config-dependent, so no relation is honest.

## [0.9.0] - 2026-08-26

### Added

- **Eight builtin kinds declare what they emit** — `api_request`,
  `embed_search`, `llm_router`, `notify`, `webhook_out`, `for_each`, `wait`,
  `log`.

  Every row was read from **this runtime's** executor and cited beside the
  declaration (`ai.py:229`, `human.py:56`, `logic.py:63`, `logic.py:118`,
  `io_.py:57-61`, `output.py:24`, …). None was copied from the PHP or
  TypeScript declarations: two declarations agreeing is not evidence, and that
  is exactly how a consumer's hand-maintained table drifted into refusing a
  legitimate field while accepting one that did not exist.

  They agree with the other two runtimes, which — arrived at independently — is
  evidence.

### Deliberately still undeclared

- `branch`, `switch_case`, `output`, `transform`, `merge`, `manual_trigger`,
  `webhook_trigger`, `human_approval`, `variable`, `schedule_trigger`. They emit
  what arrived, so their shape is not knowable from the kind alone, and `None`
  is the honest answer — read it as *unknown, do not refuse*, never as *emits
  nothing*. A test asserts they stay that way.

  `schedule_trigger` is the sharp case: it merges its inputs into the TOP level,
  so a partial list of `["cron", "timezone"]` would make a validator **refuse
  every merged-in key**. A partial static list on a merging kind is a
  false-rejection generator.

## [0.8.0] - 2026-08-26

### Added

- **`outputShape` — the FIELDS a kind emits, not its ports.** It existed in the
  TypeScript twin and in neither backend, so a host running on Python had
  nothing to check `{{ in.field }}` against.

  The consequence was not theoretical. A design partner hand-maintained a table
  of emissions derived by reading our executors' source, because the runtime
  their graphs execute in had nowhere to declare it. That table drifted: it
  **refused a legitimate `{{ in.title }}` while accepting a field that does not
  exist** — a false rejection an author cannot comply with.

  Three states, and the third is the point:

  | | means |
  |---|---|
  | `null` | NOT DECLARED — nobody has said |
  | `[]` | declares that it emits no fields |
  | a list | `[{ path: "text", type: "string" }, …]` |

  Collapsing "not declared" into "declares nothing" is the bug. It is the same
  shape as `graph.inputs` dropped on import and `sideEffects` declared by
  nothing: **a capability present in one runtime and absent in the others, where
  absent reads as a legitimate answer.**

  **A callable of config is a first-class form**, not an escape hatch: a
  `user_input` emits the keys its author defined and a `system_event` its
  event's payload, and no static list knows either. Read it through
  `output_shape_for(config)` rather than the property directly, so both forms resolve
  identically and a caller cannot handle only the one it met first.

  Serialising a dynamic shape writes `"dynamic"` rather than omitting it —
  omission would say "no outputShape", which reads as "emits nothing" and would
  reintroduce the exact failure at the serialisation seam. It comes back as a
  callable yielding null: *a shape exists, and this process cannot resolve it.*

  **What a consumer must DO:** nothing. Purely additive — every existing kind
  reads `null`, which is the honest answer for a kind that has never declared
  one. Populating the builtins follows.

## [0.7.0] - 2026-08-25

### Added

- **`migrate_schema()` — a stored graph written against an OLDER schema now
  upgrades on read instead of being rejected.**

  The version has always been on the document. Only the TypeScript runtime acted
  on it — this runtime compared it and errored on any mismatch. So the day schema
  v2 was cut, **every stored graph would have hard-failed to import here.** On the
  PHP twin, which shares the defect, that is where durable runs RESUME, so a run
  parked on a human approval would have become unresumable.

  It could only ever be fixed BEFORE the bump: afterwards the documents are
  already unreadable by the very code meant to migrate them.

  Three rules — a **past** version migrates forward step by step; a **future**
  version is left ALONE, because we cannot know what a later schema means and
  guessing downward is worse than the version check reporting it; and a **gap**
  in the step table is left alone for the same reason.

  **What to do: nothing.** The table is empty because v1 is current, so every
  document passes through untouched.

  `steps` is a parameter rather than a hard-coded lookup because otherwise the
  seam could not be tested: with only v1 in existence there is no old document to
  migrate, so a test against the built-in table would pass identically against a
  function that did nothing.

  The PHP twin ships the identical seam in 0.31.0, and TypeScript gained the same
  step-table shape in 0.56.0. One design, three runtimes.

## [0.6.1] - 2026-08-25

### Fixed

- **`graph.inputs` was dropped on import and never written on export**, so a
  workflow's own declaration of what it ACCEPTS did not survive this runtime.

  That declaration is what `resolve_workflow_props` validates against, so every
  imported graph declared nothing and **every prop was rejected** with *"this
  workflow declares no inputs"*. Props shipped in 0.4.0 and could not be used on
  any graph loaded from a schema — which is every graph that came from anywhere.

  Export had the mirror problem: a graph designed in the TypeScript editor —
  which does emit `graph.inputs` — passed through here and came out silently
  undeclared.

  **The PHP twin had the identical gap, found the same day and reported by a
  consumer who hit it there.** Both ports transcribed the node/edge loop and
  neither carried the declaration beside it — a shape worth naming, because
  nothing failed: the graph imported cleanly, ran cleanly, and simply refused
  every value.

  A malformed entry is dropped rather than aborting the import. A bad
  declaration should not cost a consumer their whole graph, and
  `resolve_workflow_props` judges values anyway.

  **What to do: nothing.** A graph that declares no inputs behaves exactly as
  before, and `export` still omits the key entirely when there is nothing to
  write — an always-present `"inputs": []` would change the bytes of every graph
  ever saved.

## [0.6.0] - 2026-08-25

### Added

- **`RunOptions.entry_nodes` — run only the trigger that actually fired.** Names
  the live entry points; everything reachable only from the others is skipped.

  A graph may hold more than one trigger — a `manual_trigger` for hand-testing
  beside the event trigger that runs it for real — and a trigger has no inbound
  edges, which **is** the readiness rule. So every trigger's branch ran on every
  run, whichever one fired.

  The triggers themselves were harmless; everything downstream of the ones that
  did not fire was not. Measured in production against the PHP twin: an empty
  payload winning a race into a shared `transform`, and — with no workaround — a
  `user_input` on the manual branch executing during an **event**-triggered run,
  parking it to ask a person for data the event had already supplied.

  ```python
  FlowRunner().run(graph, executors, options=RunOptions(entry_nodes=["evt"]))
  ```

  **What to do: nothing.** Leaving it `None` behaves exactly as before, and that
  compatibility guarantee is row `0101` of the shared table.

  Two edges worth knowing, both pinned: **`None` is not `[]`** — unset runs every
  entry point, an empty list says none is live and runs nothing; and naming a
  node that HAS inbound edges names no entry point, so nothing runs. Validate
  your ids if you want a typo to be loud — the runtime cannot tell one from a
  deliberate empty selection.

  Pinned by `flow/entry-points` in `fancy-conformance` (7 rows), written as a
  specification before any runtime implemented it. Verified red: removing the
  gate fails exactly 5 of the 7, and the same 5 fail in the TypeScript and PHP
  twins — the shared corpus doing its job rather than three runtimes each
  re-deriving a paragraph.

## [0.5.0] - 2026-08-25

### Fixed

- **Binding one ordinary kind could silently install a GLOBAL FALLBACK for every
  unmatched node.** `bind` expands a kind into every id it answers to, and
  already refused to expand the `*` sentinel *outwards* — but nothing stopped an
  alias expanding *inwards* to it. A kind whose alias list contains `*` therefore
  turned `bind("everything", …)` into `bind("*", …)`, and from then on every node
  with no executor of its own ran that one.

  Silent by construction: a fallback that exists and a fallback that does not
  both let the run complete, and both produce a value. The `*` slot may now only
  be written by an explicit `bind("*")`.

  **This runtime and the PHP twin had the identical defect, for the identical
  reason** — both expand aliases at BIND time. TypeScript was unaffected only
  because it expands at LOOKUP time and never looks the sentinel up as a kind.
  One fixture row found it in two runtimes on the day it was written, which is
  the argument for the shared corpus made better than any prose about it.

  **What to do: nothing**, unless you registered a kind literally named `*`.

### Added

- **`flow/executor-resolution` runs here** — the `node id → kind → *` order,
  alias resolution in both directions, and failing closed when nothing matches.
  Eight rows run; the six `0200` rows carry a stated structural skip, because
  this runtime's `FlowNode` is FLATTENED: `type` IS the kind and there is no
  `data` slot for a `data.kind` to disagree from.

  The TypeScript runtime could run the WRONG executor when `type` and
  `data.kind` named two different registered kinds — with the correct executor
  registered and unused. This runtime cannot, structurally rather than by care.
  Adding a `data.kind` field here so it could answer rows about one would be
  writing code to satisfy a table, which is the inversion the conformance
  package exists to prevent, so the asymmetry is recorded on the rows instead.

## [0.4.1] - 2026-08-25

Everything here came from the runtime's FIRST OUTSIDE CONSUMER, who ran the
same 5-node graph on this engine and on the TypeScript one and diffed them.
Their headline was that the runtime works and the outputs and event types match
exactly; these are the edges that differ.

### Fixed

- **`__version__` reported a version three releases old.** It was the literal
  `"0.1.0"` while the distribution was `0.4.0`, and nothing compared them — so
  `pip install fancy-flow` handed you 0.4.0 and the package told you 0.1.0.
  Anything gating on the version at runtime branched on a release that no longer
  existed.

  It is now read from the installed distribution metadata, which removes the
  second copy rather than re-syncing it. A test asserts the property AND that
  the literal has not come back, because re-introducing one would pass on the
  day it was written and drift on the next release — which is how this happened
  the first time.

- **Passing a plain dict of executors raised `AttributeError: 'dict' object has
  no attribute 'resolve_for'`** from inside the runner. A mapping is now
  ACCEPTED and wrapped, matching the TypeScript engine, which takes a plain
  object — porting a graph between runtimes should not require rewriting the
  registry. Anything that is neither a registry nor a mapping raises a
  `TypeError` naming both.

- **Passing a dict as the graph raised `AttributeError: 'dict' object has no
  attribute 'nodes'`.** It now raises a `TypeError` naming `FlowGraph` and
  pointing at `import_workflow` for WorkflowSchema JSON.

  A dict graph is deliberately NOT accepted: a mapping could be a `FlowGraph`
  literal or a WorkflowSchema document, and guessing would make one of them
  quietly wrong. The consumer's framing was exact — *the errors surface an
  internal protocol name instead of telling the caller what to pass* — and both
  entry points (`run` and `arun`) get the same treatment, since a fix applied to
  one door makes the bug look intermittent.

## [0.4.0] - 2026-08-25

### Added

- **Workflow props — the Python twin.** `FlowGraph.inputs` declares what a
  workflow accepts and `RunOptions.props` carries what a caller passed, by NAME
  rather than keyed by node id.

  Unknown key, missing required value and wrong type each **fail the run before
  any node executes**. What this replaces was silence: `initial_inputs` is keyed
  by node id, so a caller had to know the trigger was called `t`, and a
  misspelled key was not an error — the value sat unread while the run reported
  success.

  Entry nodes are seeded by bare name (so an existing graph reading
  `{{ topic }}` keeps working unchanged) and every node gets `$props` when the
  workflow declares inputs. The expression resolver needed no change: `$props`
  is an ordinary input key and it already walks dot-paths.

  `bool` is tested before `int` in the type check, because
  `isinstance(True, int)` is true in Python — a `number` declaration would
  otherwise accept `True`, which is a check that runs, passes and asserts
  nothing.

  Pinned by `flow/workflow-props` in `fancy-conformance` (21 cases), run from
  the same file by the TypeScript and PHP runtimes.

### Fixed

- **The conformance loader skipped rows meant for OTHER languages.**
  `run_table` tested the whole `skip` map for truthiness, so a case skipped for
  PHP alone was skipped on Python too — and the log still read green, because a
  skip is not a failure. The empty-reason guard beside it was simultaneously
  unreachable, since `str({"php": "..."})` is never blank.

  This is precisely the defect `fancy-conformance`'s own notes predicted for
  the private loader copies, and it cost a real row: `0106` is skipped for PHP
  (its input is unrepresentable there) and must RUN here. The suite now reports
  21 passed / 0 skipped where it previously reported 20 / 1.

  The copy still exists only because the promoted `fancy-conformance` Python
  loader is not published on PyPI yet; when it is, this file should be deleted
  rather than maintained.

## [Unreleased]

## [0.3.0] - 2026-08-24

### Added

- **A node's inputs are now addressable by the SOURCE NODE'S ID**, alongside the
  port, whenever the edge declared no `targetHandle` (fancy-flow-php#8).

  ```
  {{ in.text }}    // still works, unchanged
  {{ n2.text }}    // now works too
  ```

  **The failure this closes is silence, not inconvenience.** Authors reach for
  node ids — it is how every graph tool addresses nodes, and it is the first
  thing an assistant generating a graph writes. That resolved to nothing, and
  *nothing failed*: an unresolvable path yields an empty string, so the node
  ran, the run reported success, and the damage was output that was quietly
  wrong. The reporting consumer shipped a `document.md` containing the literal
  text of its own template, on a green run, and found out when a human opened
  the file.

  `targetHandle` is unchanged and remains the mechanism for reading something
  other than the immediate predecessor. The model was never wrong — the obvious
  spelling just meant nothing.

  **Strictly additive.** The alias is written only for edges that named no
  handle (an edge that named one said what it meant), and never over a key
  already present from the host's initial inputs or an earlier edge. A dead
  branch contributes nothing, as before.

  **What you must do: nothing.**

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
