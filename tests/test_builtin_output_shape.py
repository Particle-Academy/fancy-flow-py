"""What the builtin kinds declare they emit, and what they deliberately do not.

Every row was read from THIS runtime's executor and cited beside the
declaration. None copied from the PHP or TypeScript declarations: two
declarations agreeing is not evidence, and that is exactly how a consumer's
hand-maintained table drifted into refusing a field that was legitimate while
accepting one that did not exist.
"""

import pytest

from fancy_flow.registry import builtin


def kind(name: str):
    found = builtin.register().get(name)
    assert found is not None, f"builtin `{name}` is not registered"
    return found


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("api_request", ["status", "headers", "body"]),
        ("embed_search", ["query", "matches"]),
        ("llm_router", ["route", "reason", "input"]),
        ("notify", ["sent", "channel", "to", "message"]),
        ("webhook_out", ["sent", "status", "response"]),
        ("for_each", ["items", "count"]),
        ("wait", ["waited", "duration", "input"]),
        ("log", ["logged", "level"]),
    ],
)
def test_declares_its_fields(name: str, expected: list[str]) -> None:
    shape = kind(name).output_shape_for({})
    assert shape is not None, f"`{name}` should declare a shape"
    assert [f["path"] for f in shape] == expected


def test_pass_through_kinds_stay_undeclared() -> None:
    """They emit what arrived, so their shape is not knowable from the kind.

    ``None`` is the honest answer and a reader must treat it as "unknown, do not
    refuse" -- never as "emits nothing".

    ``schedule_trigger`` is the sharp case: the executor merges its inputs into
    the TOP level, so a partial list of ``["cron", "timezone"]`` would make a
    validator refuse every merged-in key. A partial static list on a merging
    kind is a false-rejection generator, and a false rejection is one an author
    cannot comply with.
    """
    for name in (
        "branch",
        "switch_case",
        "output",
        "transform",
        "merge",
        "manual_trigger",
        "webhook_trigger",
        "human_approval",
        "variable",
        # schedule_trigger LEFT this list when `emits` arrived: a partial
        # ["cron", "timezone"] list was unsafe only while nothing could say the
        # inputs also merge. With emits="inputs-merged" beside it, the two are
        # complete together.
    ):
        assert kind(name).output_shape_for({}) is None, (
            f"`{name}` passes input through; declaring a shape would cause false refusals"
        )


def test_no_declared_field_has_an_empty_path() -> None:
    """An empty path is unaddressable, so it is only noise a reader special-cases."""
    for k in builtin.register().all():
        shape = k.output_shape_for({})
        if shape is None:
            continue
        for f in shape:
            assert f.get("path"), f"`{k.name}` declared a field with no path"


def test_declares_the_relation_where_a_field_list_cannot() -> None:
    """The half a field list cannot express.

    Each was read from its executor and checked for MERGE vs NEST before being
    assigned -- a relation with no destination can only describe a top-level
    merge.
    """
    cases = {
        "branch": "input",
        "switch_case": "input",
        "output": "input",
        "human_approval": "input",
        "manual_trigger": "input-map-merged",
        "variable": "expression:value",
        "schedule_trigger": "input-map-merged",
    }
    for name, expected in cases.items():
        assert kind(name).emits_for({}) == expected, f"`{name}` relation"


def test_transform_changes_relation_with_its_config() -> None:
    """Two returns: the input unchanged with no expression, else its shape."""
    transform = kind("transform")
    assert transform.emits_for({}) == "input"
    assert transform.emits_for({"expression": ""}) == "input"
    assert transform.emits_for({"expression": "{{ in.user }}"}) == "expression:expression"


def test_an_expression_relation_names_its_own_config_key() -> None:
    """``transform`` reads ``expression``; ``variable`` reads ``value``.

    A consumer hardcoding "the field called expression" has copied our knowledge
    one level down -- the thing this removes.
    """
    assert kind("variable").expression_config_key({}) == "value"
    assert kind("transform").expression_config_key({"expression": "{{ in.x }}"}) == "expression"
    assert kind("branch").expression_config_key({}) is None


def test_merge_concatenating_declares_nothing() -> None:
    """A list's elements are not addressable as top-level fields.

    ``[]`` would claim "emits no fields", which is false and would refuse every
    reference.
    """
    merge = kind("merge")
    assert merge.emits_for({}) == "inputs-merged"
    assert merge.emits_for({"mode": "concat"}) is None
    assert merge.output_shape_for({}) is None


def test_wait_declares_a_list_and_no_relation_because_it_nests() -> None:
    """``wait`` returns ``{"waited": …, "duration": …, "input": …}``.

    The input goes UNDER a key, so a relation would make a reader accept
    ``{{ in.<any inbound field> }}`` at top level and resolve to nothing at run
    time. This is the case that proved a relation needs a destination.
    """
    wait = kind("wait")
    assert wait.emits_for({}) is None
    assert [f["path"] for f in wait.output_shape_for({})] == ["waited", "duration", "input"]


def test_webhook_trigger_declares_no_relation_because_it_is_data_dependent() -> None:
    """``ctx.inputs if payload is None else payload`` cannot be answered from
    config, so no relation is honest. Under-claiming is free."""
    assert kind("webhook_trigger").emits_for({}) is None
