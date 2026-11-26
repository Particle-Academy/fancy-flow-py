"""The ``model`` placeholder names a current model.

The LLM kinds suggested ``claude-sonnet-4-5``, which retires on 2026-11-30. A
placeholder is what an author (or an agent reading the kind manifest) copies,
so a retired id there becomes a node that fails on its first call.
"""

from __future__ import annotations

import json

from fancy_flow import NodeKind, NodeKindRegistry, builtin

CURRENT = "claude-sonnet-5-5"


def registry() -> NodeKindRegistry:
    reg = builtin.register(NodeKindRegistry(), with_structural=True)
    reg.register(NodeKind.from_dict(builtin.agent_kind()))
    return reg


def test_every_model_field_suggests_a_current_model() -> None:
    fields = [
        (kind.name, field)
        for kind in registry().all()
        for field in kind.to_dict().get("configSchema", [])
        if field.get("key") == "model"
    ]
    # Vacuity guard: llm_call, llm_router and agent all declare one.
    assert len(fields) >= 3
    for name, field in fields:
        assert field.get("placeholder") == CURRENT, name


def test_no_config_field_suggests_claude_sonnet_4_5() -> None:
    text = json.dumps([k.to_dict().get("configSchema", []) for k in registry().all()])
    assert "claude-sonnet-4-5" not in text
