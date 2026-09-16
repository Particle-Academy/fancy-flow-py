"""A human gate one level down must still decode at the top (fancy-flow-php#21).

A pause and a failure travel the SAME channel -- ``ctx.abort`` with an encoded
reason -- and ``Pause.decode`` is prefix-anchored. So wrapping an unsuccessful
child run as ``subflow "x" failed: <reason>`` did not merely decorate the text:
it moved the prefix off position 0 and the pause stopped decoding entirely.

What that cost: the durable coordinator read a FAILED run instead of one parked
on a person, so a ``human_approval`` or ``user_input`` inside a subflow could
never be answered, and retry policy counted someone's pending decision as a
fault. The run looked finished and failed, which is the quiet kind of wrong.

The Rust twin never had this and carries a comment at the same line saying why.
This runtime, PHP and TypeScript all did.

**Assert that a pause DECODES; never assert on its text.** The reason is
verbatim by contract, so a test pinned to the wording would pass against the
very decoration it exists to stop.
"""

from __future__ import annotations

from typing import Any

from fancy_flow import (
    FlowGraph,
    FlowNode,
    FlowRunner,
    NodeKindRegistry,
    builtin,
)
from fancy_flow.capabilities import WorkflowResolver
from fancy_flow.nodes.structural import Subflow
from fancy_flow.runtime.pause import Pause


class _FixedResolver(WorkflowResolver):
    """A resolver over one child graph."""

    def __init__(self, child: FlowGraph) -> None:
        self._child = child

    def resolve(self, ref: str, version: int | None = None) -> Any:
        return self._child


def _run(body: Any) -> Any:
    """Run `subflow -> child`, where the child's only node does ``body``."""
    child = FlowGraph((FlowNode("gate", type="hostGate"),), ())
    registry = builtin.register(NodeKindRegistry(), with_structural=True)

    executors = builtin.executors()
    executors.bind("hostGate", body)
    executors.bind("subflow", Subflow(resolver=_FixedResolver(child)))

    parent = FlowGraph(
        (FlowNode("sf", type="subflow", config={"workflow": "child"}),),
        (),
    )
    return FlowRunner(registry).run(parent, executors)


def test_a_pause_raised_inside_a_subflow_still_decodes_at_the_top() -> None:
    result = _run(lambda ctx: ctx.pause_for_human("approval", {"title": "Approve item"}))

    assert result.ok is False

    pause = Pause.decode(result.error or "")
    assert pause is not None, "a gate inside a subflow must stay resumable"
    assert pause.node_id == "gate"
    assert pause.awaiting == "approval"


def test_a_genuine_child_failure_still_names_the_subflow() -> None:
    # The other half. The `subflow "x" failed:` prefix is real context for a
    # real failure and must survive the fix -- otherwise a child error arrives
    # at the top with nothing saying which child produced it.
    result = _run(lambda ctx: ctx.abort("the child exploded"))

    assert result.ok is False
    assert 'subflow "child" failed:' in (result.error or "")
    assert "the child exploded" in (result.error or "")
    assert Pause.decode(result.error or "") is None
