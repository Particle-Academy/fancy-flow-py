"""``transform``: the editor's defaults are admitted, and fields mode runs.

Reported by Maipa against 0.29.0. The TypeScript editor writes
``{"mode": "fields", "fields": [{"key": "", "value": ""}]}`` for a new transform
node, and this port declared ONLY a required ``expression``, so
``validate_config`` refused the editor's own defaults -- and fields mode did not
exist here at all, so a graph built field-by-field in the editor passed its
input straight through.

Every expectation below was read off ``@particle-academy/fancy-flow``
(``transformPath`` / ``validateConfig`` in 0.82.0), the reference for this kind,
including the exact issue messages. None is restated from this port's code.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow import FlowRunner, NodeKindRegistry, RunOptions, builtin, import_workflow
from fancy_flow.registry.node_kind import NodeKind

EDITOR_DEFAULTS = {"mode": "fields", "fields": [{"key": "", "value": ""}]}


@pytest.fixture
def registry() -> NodeKindRegistry:
    return builtin.register(NodeKindRegistry(), with_structural=True)


def kind(registry: NodeKindRegistry):
    found = registry.get("transform")
    assert found is not None
    return found


def run(config: dict[str, Any] | None, payload: Any) -> Any:
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    node: dict[str, Any] = {"id": "tf", "kind": "transform", "position": {"x": 1, "y": 0}}
    if config is not None:
        node["config"] = config
    graph = import_workflow(
        {
            "version": 1,
            "graph": {
                "nodes": [
                    {"id": "t", "kind": "manual_trigger", "position": {"x": 0, "y": 0}},
                    node,
                ],
                "edges": [{"id": "e1", "source": "t", "target": "tf"}],
            },
        },
        lenient=True,
        registry=registry,
    ).graph
    result = FlowRunner().run(
        graph, builtin.executors(), options=RunOptions(initial_inputs={"t": payload})
    )
    assert result.ok, result.error
    return result.outputs["tf"]


# -- declaration -------------------------------------------------------------


def test_the_defaults_are_exactly_what_the_editor_writes(registry: NodeKindRegistry) -> None:
    assert registry.default_config_for(kind(registry)) == EDITOR_DEFAULTS


def test_the_declared_keys_match_the_reference(registry: NodeKindRegistry) -> None:
    schema = [field.to_dict() for field in kind(registry).config_schema]
    assert [f["key"] for f in schema] == ["mode", "fields", "expression"]
    assert schema[0]["type"] == "select"
    assert [o["value"] for o in schema[0]["options"]] == ["fields", "expression"]
    assert schema[1]["type"] == "repeater"
    assert schema[1]["skipBlankRows"] is True
    assert [(f["key"], f["type"], f.get("required")) for f in schema[1]["fields"]] == [
        ("key", "text", True),
        ("value", "expression", True),
    ]
    assert schema[2]["type"] == "expression"
    assert "required" not in schema[2]


def test_a_new_node_does_not_share_the_default_rows(registry: NodeKindRegistry) -> None:
    first = registry.default_config_for(kind(registry))
    first["fields"][0]["key"] = "mutated"
    assert registry.default_config_for(kind(registry)) == EDITOR_DEFAULTS


# -- admission ---------------------------------------------------------------


def test_the_editor_defaults_are_admitted(registry: NodeKindRegistry) -> None:
    assert registry.validate_config(kind(registry), dict(EDITOR_DEFAULTS)) == []


def test_expression_mode_with_the_stale_default_row_is_admitted(
    registry: NodeKindRegistry,
) -> None:
    config = {
        "mode": "expression",
        "fields": [{"key": "", "value": ""}],
        "expression": "{{ $json.a }}",
    }
    assert registry.validate_config(kind(registry), config) == []


def test_a_row_with_no_keys_at_all_is_blank(registry: NodeKindRegistry) -> None:
    assert registry.validate_config(kind(registry), {"mode": "fields", "fields": [{}]}) == []


@pytest.mark.parametrize(
    ("config", "issues"),
    [
        # A value with no key: the executor would drop it, so it is refused.
        (
            {"mode": "fields", "fields": [{"key": "", "value": "{{ $json.a }}"}]},
            [{"key": "fields", "message": "Output fields item 1: Key is required"}],
        ),
        (
            {"mode": "rows", "fields": []},
            [{"key": "mode", "message": "Build the output must be one of fields, expression"}],
        ),
        (
            {"mode": "fields", "fields": "name"},
            [{"key": "fields", "message": "Output fields must be a list"}],
        ),
        (
            {"mode": "fields", "fields": ["name"]},
            [{"key": "fields", "message": "Output fields item 1 must be an object"}],
        ),
        (
            {"mode": "fields", "fields": [{"key": 5, "value": "x"}]},
            [{"key": "fields", "message": "Output fields item 1: Key must be a string"}],
        ),
        (
            {"mode": "expression", "expression": 5},
            [{"key": "expression", "message": "Expression (advanced) must be a string"}],
        ),
    ],
)
def test_invalid_configs_are_still_refused(
    registry: NodeKindRegistry, config: dict[str, Any], issues: list[dict[str, str]]
) -> None:
    assert registry.validate_config(kind(registry), config) == issues


# -- a whitespace-only key is blank, in admission as in the executor -----------
#
# ``{"key": "   ", "value": "Configured synthetic value"}`` validated with no
# issue (the check compared with ""), then ran as a passthrough: the executor
# strips the key, finds it blank and skips the row, so the value vanished.
# Reported by Maipa against the published 0.30.0. Messages read off
# ``@particle-academy/fancy-flow`` 0.82.1.

KEY_REQUIRED = [{"key": "fields", "message": "Output fields item 1: Key is required"}]


def test_a_whitespace_only_key_with_a_value_is_refused(registry: NodeKindRegistry) -> None:
    config = {"mode": "fields", "fields": [{"key": "   ", "value": "Configured synthetic value"}]}
    assert registry.validate_config(kind(registry), config) == KEY_REQUIRED


@pytest.mark.parametrize("key", ["\t", "\n", " \r\n ", chr(0x00A0), chr(0x2003), chr(0x3000)])
def test_every_whitespace_the_executor_strips_is_refused(
    registry: NodeKindRegistry, key: str
) -> None:
    config = {"mode": "fields", "fields": [{"key": key, "value": "x"}]}
    assert registry.validate_config(kind(registry), config) == KEY_REQUIRED
    # ...and the executor really does drop that row: same helper, same verdict.
    assert run(config, {"a": 1}) == {"a": 1}


def test_a_whitespace_only_key_with_an_empty_value_is_a_blank_row(
    registry: NodeKindRegistry,
) -> None:
    fields_mode = {"mode": "fields", "fields": [{"key": "   ", "value": ""}]}
    assert registry.validate_config(kind(registry), fields_mode) == []
    assert (
        registry.validate_config(kind(registry), {"mode": "fields", "fields": [{"key": "\t"}]})
        == []
    )


def test_a_padded_key_is_admitted_and_runs_under_the_trimmed_key(
    registry: NodeKindRegistry,
) -> None:
    config = {"mode": "fields", "fields": [{"key": "  name ", "value": "{{ $json.first }}"}]}
    assert registry.validate_config(kind(registry), config) == []
    assert run(config, {"first": "Ada"}) == {"name": "Ada"}


def test_the_value_is_judged_unstripped(registry: NodeKindRegistry) -> None:
    padded = {"mode": "fields", "fields": [{"key": "pad", "value": "  "}]}
    assert registry.validate_config(kind(registry), padded) == []
    no_key = {"mode": "fields", "fields": [{"key": "", "value": "  "}]}
    assert registry.validate_config(kind(registry), no_key) == KEY_REQUIRED


def test_a_repeater_that_did_not_opt_in_does_not_strip(registry: NodeKindRegistry) -> None:
    other = NodeKind.from_dict(
        {
            "name": "acme/rows",
            "category": "logic",
            "label": "Rows",
            "configSchema": [
                {
                    "type": "repeater",
                    "key": "rows",
                    "label": "Rows",
                    "fields": [{"type": "text", "key": "key", "label": "Key", "required": True}],
                }
            ],
        }
    )
    assert registry.validate_config(other, {"rows": [{"key": "  "}]}) == []


# -- execution ---------------------------------------------------------------


def test_the_untouched_default_passes_the_input_through() -> None:
    assert run(dict(EDITOR_DEFAULTS), {"a": 1}) == {"a": 1}


def test_a_node_saved_with_no_config_runs_the_defaults() -> None:
    assert run(None, {"a": 1}) == {"a": 1}


def test_fields_mode_builds_the_object_and_skips_blank_rows() -> None:
    config = {
        "mode": "fields",
        "fields": [
            {"key": "", "value": ""},
            {"key": " name ", "value": "{{ $json.first }}"},
            {"key": "greeting", "value": "Hi {{ $json.first }}"},
            {"key": "n", "value": 3},
            None,
            "x",
        ],
    }
    assert run(config, {"first": "Ada"}) == {"name": "Ada", "greeting": "Hi Ada", "n": 3}


def test_a_later_row_overwrites_an_earlier_one_with_the_same_key() -> None:
    config = {"mode": "fields", "fields": [{"key": "a", "value": "1"}, {"key": "a", "value": "2"}]}
    assert run(config, {}) == {"a": "2"}


def test_expression_mode_ignores_stale_fields() -> None:
    config = {
        "mode": "expression",
        "fields": [{"key": "who", "value": "{{ $json.user.name }}"}],
        "expression": "{{ $json.user }}",
    }
    assert run(config, {"user": {"name": "Ada"}}) == {"name": "Ada"}


def test_fields_mode_ignores_a_stale_expression() -> None:
    config = {
        "mode": "fields",
        "fields": [{"key": "who", "value": "{{ $json.user.name }}"}],
        "expression": "{{ $json.user }}",
    }
    assert run(config, {"user": {"name": "Ada"}}) == {"who": "Ada"}


def test_expression_mode_with_a_blank_expression_passes_through() -> None:
    assert run({"mode": "expression", "expression": "   "}, {"a": 1}) == {"a": 1}


def test_no_mode_and_no_expression_falls_through_to_fields() -> None:
    assert run({"fields": [{"key": "b", "value": "{{ $json.a }}"}]}, {"a": 1}) == {"b": 1}


def test_an_unknown_mode_takes_the_fields_path_as_the_reference_does() -> None:
    # The validator reports it; the run does what TypeScript's executor does.
    config = {
        "mode": "rows",
        "fields": [{"key": "b", "value": "{{ $json.a }}"}],
        "expression": "{{ $json }}",
    }
    assert run(config, {"a": 1}) == {"b": 1}


# -- emits -------------------------------------------------------------------


def test_emits_follows_the_path_the_executor_takes(registry: NodeKindRegistry) -> None:
    transform = kind(registry)
    assert transform.emits_for(dict(EDITOR_DEFAULTS)) == "input"
    assert transform.emits_for({"mode": "expression", "expression": "   "}) == "input"
    assert transform.emits_for({"expression": "{{ $json.a }}"}) == "expression:expression"
    assert (
        transform.emits_for(
            {
                "mode": "fields",
                "fields": [{"key": "x", "value": "1"}],
                "expression": "{{ $json.a }}",
            }
        )
        is None
    )
