# Where these came from, and why that is a problem

Copied from `particle-academy/fancy-flow-php` at `tests/Parity/fixtures/`,
commit `8f3f19c` (tag `v0.17.0`).

Each file is a `WorkflowSchema` plus `initialInputs` and a baked-in golden
`{ok, outputs}`. Running it through this engine with the default offline
executors must reproduce that result exactly.

## This is a copy, and copies drift

The flow-engine spec says parity "is asserted, not asserted-to", and for
`shared/expr` and `shared/satisfies-range` that is true — those tables live in
`particle-academy/fancy-conformance` and every runtime loads the one file.

**These 23 graph fixtures are not in that package.** They live in the PHP
twin's own test directory, and as of `fancy-flow` 0.44.0 **nothing on the Node
side runs them at all** — `fancy-flow`'s only conformance test is
`tests/conformance-expr.test.ts`. So the golden `WorkflowSchema` results have
exactly one implementation asserting them, and this directory makes that two by
copying rather than by sharing.

Two copies agree right up until someone adds a case to one of them, and nothing
anywhere reports that. It is the same defect `satisfiesRange` had until
fancy-flow-php 0.14.2.

## What holds it together until that is fixed

`tests/parity/test_fixture_provenance.py` compares this directory against the
PHP twin's whenever the twin is on disk (the normal case inside the `.agi`
envelope), and **fails** on any difference — a missing file, an extra file, or
a changed byte. It does not skip when the twin is absent; it reports that it
could not check, which is a different thing from checking and finding nothing.

## The actual fix

Promote these to a `flow/graph-runs` suite in `fancy-conformance`
(`caseFormat: "table"` does not fit — each case is a document plus an expected
output map, so either a table whose `input` holds the whole schema, or the
`directory` format). Then all three runtimes read one file and this directory
goes away.
