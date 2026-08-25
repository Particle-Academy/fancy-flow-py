"""The executor-resolution table, run against this side.

``@particle-academy/fancy-flow`` and ``particle-academy/fancy-flow-php`` run the
identical rows from the identical file: node id → kind → ``*``, with alias
resolution in both directions and a closed failure when nothing matches.

Why the table exists, and why Python is not where the bug was
-------------------------------------------------------------

The TypeScript runtime could run the **wrong executor, silently**. Its alias
step resolved ``data.kind``'s ids before ``node.type``'s, so a node declaring
itself an ``llm_call`` ran an ``output`` executor — with the correct one
registered and sitting unused. Nothing reported it, and nothing could: running
the wrong executor and running the right one look identical from outside,
because the graph still completes and still produces a value.

**This runtime could not have that bug, and the reason is structural rather
than careful.** :class:`FlowNode` here is flattened — ``type`` IS the kind, and
there is no ``data`` slot for a second opinion to live in — so the precedence
question cannot arise. The six ``0200`` rows are skipped here, each carrying
that reason on the row itself.

That asymmetry is worth reading rather than glossing over: inventing a
``data.kind`` field on this side so it could answer rows about one would be
writing code to satisfy a table, which is the inversion the conformance package
exists to prevent.

What the eight live rows actually buy
-------------------------------------

They are not a formality. This runtime expands aliases at BIND time while
TypeScript expands them at LOOKUP time, so the table asserts which executor
RUNS rather than which key matched — and both strategies have to agree on it.

``0107`` earned its place immediately on the PHP side, which shares this
bind-time strategy: binding a kind whose alias list contains ``*`` wrote the
sentinel slot, turning one ordinary binding into a global fallback for every
unmatched node in the graph. Silently, because a fallback that exists and one
that does not both let a run complete.
"""

from __future__ import annotations

from typing import Any

from fancy_flow.executors import ExecutorRegistry
from fancy_flow.registry.node_kind import NodeKind
from fancy_flow.registry.registry import NodeKindRegistry
from fancy_flow.runtime.context import ExecutionContext
from fancy_flow.schema.graph import FlowNode

from .loader import format_summary, run_table

SUITE = "flow/executor-resolution"


def _run_case(case: dict[str, Any]) -> str | None:
    """Build the case's own registry and bindings, then resolve.

    Each binding runs a recogniser returning its label, so the answer is WHICH
    executor was chosen — the only thing a consumer can observe, and the only
    formulation neutral about when a runtime expands aliases.
    """
    kinds = NodeKindRegistry()

    for declared in case["input"]["kinds"]:
        kinds.register(
            NodeKind(
                name=declared["name"],
                category="conformance",
                label=declared["name"],
                aliases=tuple(declared.get("aliases", ())),
            )
        )

    executors = ExecutorRegistry(kinds=kinds)
    node_spec = case["input"]["node"]

    for binding in case["input"]["bindings"]:
        label = binding["executor"]

        def run(_ctx: ExecutionContext, _label: str = label) -> str:
            return _label

        # A binding keyed by the node's own id is a NODE binding, not a kind
        # binding — they live in different maps here, so the case's flat list
        # has to be routed. Keying by id is how a graph pins one node to a stub
        # without unbinding that kind everywhere else.
        if binding["key"] == node_spec["id"]:
            executors.bind_node(binding["key"], run)
        else:
            executors.bind(binding["key"], run)

    node = FlowNode(id=node_spec["id"], type=node_spec.get("type"))
    resolved = executors.resolve_for(node)

    if resolved is None:
        return None

    return resolved(
        ExecutionContext(node=node, inputs={}, emit=lambda _event: None)
    )


def test_matches_the_executor_resolution_table() -> None:
    summary = run_table(SUITE, _run_case)

    # Printed unconditionally, so a green build still shows WHAT was compared —
    # including which rows were skipped and why. A bare "6 skipped" reads
    # identically to full coverage; the reasons are the whole point.
    print("\n" + format_summary(summary))

    failures = [r for r in summary["results"] if r["status"] == "fail"]
    assert not failures, "Python disagrees with the shared table on: " + ", ".join(
        r["id"] for r in failures
    )

    # The vacuity floor, set just under the eight rows that run here. A suite
    # that skipped everything reports zero failures too, so without this a bad
    # path or a stale checkout would pass quietly — the exact failure mode the
    # conformance package exists to argue against.
    assert summary["passed"] > 6, f"only {summary['passed']} rows ran; discovery is broken"


def test_the_wildcard_is_never_written_by_alias_expansion() -> None:
    """The discrimination check, aimed at this runtime's own strategy.

    Binding is alias-EXPANDING here, so a kind answering to ``*`` could write
    the sentinel slot and silently install a catch-all. The table's ``0107``
    covers it, but only while that row exists — this pins the property directly,
    in the terms of the mechanism that produces it.
    """
    kinds = NodeKindRegistry()
    kinds.register(
        NodeKind(name="*", category="conformance", label="*", aliases=("everything",))
    )

    executors = ExecutorRegistry(kinds=kinds)
    executors.bind("everything", lambda _ctx: "aliased-star")

    unmatched = FlowNode(id="n1", type="http_request")

    assert executors.resolve_for(unmatched) is None, (
        "binding an ordinary kind must never install a global fallback; "
        "the `*` slot may only be written by an explicit bind('*')"
    )
