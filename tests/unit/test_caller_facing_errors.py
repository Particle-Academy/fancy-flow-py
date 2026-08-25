"""What a caller gets when they pass the wrong shape.

Both cases were reported by the runtime's first outside consumer, porting a
working TypeScript graph. Each raised an ``AttributeError`` from inside the
runner naming an INTERNAL protocol member — ``'dict' object has no attribute
'resolve_for'`` and ``'dict' object has no attribute 'nodes'`` — which reads as
a library bug rather than as "you passed the wrong thing".

Their summary was exact: *the errors surface an internal protocol name instead
of telling the caller what to pass.*

The two get different treatment on purpose. A mapping of executors is
unambiguous, so it is ACCEPTED — the TS engine takes a plain object and porting
a graph should not require rewriting the registry. A mapping for the graph is
ambiguous (FlowGraph literal? WorkflowSchema document?), so it is REFUSED with
the name of the function that converts it. Guessing there would make one of the
two silently wrong.
"""

from __future__ import annotations

import pytest

from fancy_flow import ExecutorRegistry, FlowGraph, FlowNode, FlowRunner


def _graph() -> FlowGraph:
    return FlowGraph(nodes=(FlowNode(id="start", type="manual_trigger", label="start"),), edges=())


def test_a_plain_dict_of_executors_is_accepted() -> None:
    """Parity with the TS engine, which takes a plain object."""
    ran: list[str] = []

    result = FlowRunner().run(
        _graph(),
        {"manual_trigger": lambda ctx: ran.append("yes") or {"ok": 1}},
    )

    assert result.ok is True
    assert ran == ["yes"]


def test_an_executor_registry_still_works() -> None:
    """The coercion must not break the documented form."""
    registry = ExecutorRegistry().bind_many({"manual_trigger": lambda ctx: {"ok": 1}})

    assert FlowRunner().run(_graph(), registry).ok is True


def test_a_nonsense_executors_argument_says_what_to_pass() -> None:
    with pytest.raises(TypeError) as excinfo:
        FlowRunner().run(_graph(), 42)

    message = str(excinfo.value)
    assert "ExecutorRegistry" in message
    assert "mapping" in message
    # The failure the fix exists to remove: an internal protocol member.
    assert "resolve_for" not in message


def test_a_dict_graph_names_import_workflow_rather_than_an_attribute() -> None:
    with pytest.raises(TypeError) as excinfo:
        FlowRunner().run({"nodes": [], "edges": []}, {})

    message = str(excinfo.value)
    assert "FlowGraph" in message
    # The actionable half: the function that actually converts a document.
    assert "import_workflow" in message
    assert "has no attribute" not in message


def test_the_async_entry_point_gets_the_same_treatment() -> None:
    """`arun` is a second door into the same room.

    A fix applied to one entry point and not the other is the shape that makes
    a bug look intermittent — it depends on which call the consumer reached for.
    """
    import asyncio

    async def go() -> object:
        return await FlowRunner().arun(
            _graph(),
            {"manual_trigger": lambda ctx: {"ok": 1}},
        )

    assert asyncio.run(go()).ok is True
