"""A node can activate a CHOSEN SUBSET of its ports (fancy-flow-php#18, MOIC).

The engine knew two answers: ``__port`` / ``branch`` lit exactly one port, and
anything else lit EVERY declared port. There was no way to say "these two of
five", so a router that matched two lanes had to drop the rest of the work or
wake lanes nobody asked for.

``Port.many(["a", "c"])`` lights both with one payload; ``Port.many({"a": x,
"c": y})`` gives each its own. An explicitly empty collection lights nothing,
which is the rule an explicitly empty ``outputs`` list already follows.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fancy_flow import (
    ExecutorRegistry,
    FlowEdge,
    FlowGraph,
    FlowNode,
    FlowRunner,
    NodeKind,
    NodeKindRegistry,
    Port,
    PortDescriptor,
)

ABSENT = "__absent__"


def run_router_with(route: Callable[[], Any]) -> dict[str, Any]:
    """Run a five-port router; return what each sink received, by node id."""
    graph = FlowGraph(
        (
            FlowNode("r", type="router"),
            FlowNode("A", type="sink"),
            FlowNode("B", type="sink"),
            FlowNode("C", type="sink"),
        ),
        (
            FlowEdge("e1", "r", "A", source_handle="a"),
            FlowEdge("e2", "r", "B", source_handle="b"),
            FlowEdge("e3", "r", "C", source_handle="c"),
        ),
    )

    seen: dict[str, Any] = {}

    # Ports declared on the KIND, the way a host registers them.
    kinds = NodeKindRegistry()
    kinds.register(
        NodeKind(
            name="router",
            category="logic",
            label="Router",
            outputs=[PortDescriptor(p) for p in ("a", "b", "c", "d", "e")],
        )
    )

    def sink(ctx: Any) -> Any:
        # `.get` with a default, never `or` / `is None`: a payload that IS None
        # is a payload, which is half of what these rows assert.
        seen[ctx.node.id] = ctx.inputs.get("in", ABSENT)
        return None

    executors = ExecutorRegistry(kinds=kinds).bind("router", lambda ctx: route()).bind("sink", sink)

    # The runner resolves the declared-port fallback against ITS kinds, so the
    # host registry goes to both.
    FlowRunner(kinds=kinds).run(graph, executors)

    return seen


def test_lights_the_listed_ports_and_leaves_the_rest_dark() -> None:
    seen = run_router_with(lambda: Port.many(["a", "c"], {"matched": True}))

    assert seen["A"] == {"matched": True}
    assert seen["C"] == {"matched": True}
    assert "B" not in seen


def test_gives_each_lit_port_its_own_payload_when_handed_a_map() -> None:
    seen = run_router_with(lambda: Port.many({"a": {"queue": "billing"}, "c": {"queue": "abuse"}}))

    assert seen["A"] == {"queue": "billing"}
    assert seen["C"] == {"queue": "abuse"}
    assert "B" not in seen


def test_carries_a_per_port_payload_of_none_rather_than_the_result() -> None:
    # The distinction `branch` had to learn, per port: present-and-None is a
    # payload, not an absent one.
    seen = run_router_with(lambda: Port.many({"a": None}))

    assert "A" in seen
    assert seen["A"] is None


def test_lights_nothing_for_an_explicitly_empty_list() -> None:
    assert run_router_with(lambda: Port.many([])) == {}


def test_reads_the_raw_wire_shape_not_only_the_sugar() -> None:
    # A host in another language emits the shape directly; the engine is what
    # has to agree.
    seen = run_router_with(lambda: {"__ports": ["a", "c"], "value": "v"})

    assert seen["A"] == "v"
    assert seen["C"] == "v"
    assert "B" not in seen


def test_leaves_the_one_port_and_every_port_rules_exactly_as_they_were() -> None:
    assert run_router_with(lambda: Port.only("b", "only-b")) == {"B": "only-b"}

    every = run_router_with(lambda: {"plain": True})
    assert sorted(every) == ["A", "B", "C"]
