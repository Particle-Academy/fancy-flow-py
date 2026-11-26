"""No sampling parameter unless the author sets one.

``llm_call`` and ``agent`` declared ``temperature`` with ``default: 0.7``.
``default_config_for`` copies every declared default into a fresh node, so an
authoring tool, the TypeScript editor and :func:`import_workflow` (for a node
saved without a config) all wrote ``temperature: 0.7`` into nodes nobody had
configured, and the executor forwarded it to the client. Current Anthropic
models reject a request that sets ``temperature``.

The default is now ABSENT. An explicit value still passes through untouched:
whether a provider accepts it is the adapter's call, not the engine's.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow import ExecutionContext, FlowNode, NodeKind, NodeKindRegistry, builtin
from fancy_flow import import_workflow as import_doc
from fancy_flow.nodes import ai

SAMPLING_KEYS = ("temperature", "top_p", "top_k")


def registry() -> NodeKindRegistry:
    reg = builtin.register(NodeKindRegistry(), with_structural=True)
    reg.register(NodeKind.from_dict(builtin.agent_kind()))
    return reg


@pytest.mark.parametrize("name", ["llm_call", "agent"])
def test_a_fresh_node_carries_no_temperature(name: str) -> None:
    reg = registry()
    kind = reg.get(name)
    assert kind is not None
    config = reg.default_config_for(kind)
    assert "temperature" not in config
    # The field is still declared, so an author can set it.
    assert any(f.get("key") == "temperature" for f in kind.to_dict()["configSchema"])


def test_other_defaults_are_untouched() -> None:
    reg = registry()
    kind = reg.get("llm_call")
    assert kind is not None
    assert reg.default_config_for(kind)["max_tokens"] == 1024


def test_no_kind_declares_a_default_for_any_sampling_parameter() -> None:
    reg = registry()
    kinds = reg.all()
    # Vacuity guard: an empty registry would pass the loop below.
    assert len(kinds) > 10
    for kind in kinds:
        for field in kind.to_dict().get("configSchema", []):
            if field.get("key") in SAMPLING_KEYS:
                assert "default" not in field, f"{kind.name}.{field['key']}"
        fresh = reg.default_config_for(kind)
        for key in SAMPLING_KEYS:
            assert key not in fresh, f"{kind.name} fresh config"


def _doc(config: dict[str, Any] | None) -> dict[str, Any]:
    llm: dict[str, Any] = {"id": "llm", "kind": "llm_call", "position": {"x": 1, "y": 0}}
    if config is not None:
        llm["config"] = config
    return {
        "version": 1,
        "graph": {
            "nodes": [{"id": "t", "kind": "manual_trigger", "position": {"x": 0, "y": 0}}, llm],
            "edges": [{"id": "e1", "source": "t", "target": "llm"}],
        },
    }


def _llm(result: Any) -> FlowNode:
    return next(n for n in result.graph.nodes if n.id == "llm")


def test_importing_a_node_saved_without_config_does_not_invent_a_temperature() -> None:
    result = import_doc(_doc(None), registry=registry())
    assert "temperature" not in _llm(result).config


def test_an_explicit_temperature_survives_import() -> None:
    result = import_doc(
        _doc({"model": "m", "prompt": "p", "temperature": 0.7}), registry=registry()
    )
    assert _llm(result).config["temperature"] == 0.7


class Recording:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    def complete(self, prompt: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        self.options = options
        return {"text": "ok"}


def _run(config: dict[str, Any]) -> dict[str, Any]:
    client = Recording()
    ctx = ExecutionContext(FlowNode("n", "llm_call", config=config), {}, lambda e: None, 0)
    ai.LlmCall(client).execute(ctx)
    assert client.options is not None
    return client.options


def test_the_executor_sends_no_sampling_parameter_for_a_fresh_node() -> None:
    reg = registry()
    kind = reg.get("llm_call")
    assert kind is not None
    options = _run({**reg.default_config_for(kind), "model": "m", "prompt": "p"})
    for key in SAMPLING_KEYS:
        assert key not in options


def test_the_executor_passes_an_explicit_temperature_through_unchanged() -> None:
    assert _run({"model": "m", "prompt": "p", "temperature": 0.2})["temperature"] == 0.2
