"""Accepting a graph you did not write."""

from __future__ import annotations

import pytest

from fancy_flow import UnsafeGraph
from fancy_flow.security import GraphPolicy


def schema(nodes, edges=()):
    return {"version": 1, "graph": {"nodes": nodes, "edges": edges}}


def node(node_id: str, kind: str, **config):
    return {"id": node_id, "kind": kind, "position": {"x": 0, "y": 0}, "config": config}


def errors(policy: GraphPolicy, doc) -> list[str]:
    return [i.message for i in policy.inspect(doc) if i.is_error]


def test_an_untrusted_policy_permits_nothing_until_told_what_to_permit() -> None:
    """The fail-open this port deliberately closes.

    The PHP twin's `untrusted()` leaves the allowlist ABSENT, and an absent
    allowlist permits every kind -- so a caller who forgets `allowKinds()` gets
    size caps and byte hygiene from a method named `untrusted`. Here the
    allowlist starts empty, so nothing runs until something is named.
    """
    doc = schema([node("a", "api_request")])
    assert errors(GraphPolicy.untrusted(), doc) != []


def test_untrusted_takes_the_allowlist_inline() -> None:
    doc = schema([node("a", "transform")])
    assert errors(GraphPolicy.untrusted(allow=["transform"]), doc) == []


def test_a_trusted_policy_applies_caps_but_no_kind_restriction() -> None:
    """For graphs your own code produced. This one is identical to the twin."""
    assert errors(GraphPolicy.trusted(), schema([node("a", "api_request")])) == []


def test_an_allowlist_is_alias_aware_in_every_direction() -> None:
    """A policy keyed on the literal string is not a policy.

    Whichever spelling the caller writes, and whichever the attacker writes,
    the comparison happens on the resolved bare name.
    """
    policy = GraphPolicy.untrusted(allow=["transform"])

    for spelling in ("transform", "@particle-academy/transform", "@fancy/transform"):
        assert errors(policy, schema([node("a", spelling)])) == [], spelling


def test_a_denylist_cannot_be_dodged_by_respelling_the_kind() -> None:
    """The bypass this rule exists for.

    A denylist keyed on the string you happened to write is a suggestion the
    attacker declines by spelling the kind differently.
    """
    policy = GraphPolicy.trusted().deny_kinds(["api_request"])

    for spelling in ("api_request", "@particle-academy/api_request", "@fancy/api_request"):
        assert errors(policy, schema([node("a", spelling)])) != [], spelling


def test_a_kind_in_both_lists_is_refused() -> None:
    """The safer reading of a contradiction."""
    policy = GraphPolicy.untrusted(allow=["transform"]).deny_kinds(["transform"])
    assert errors(policy, schema([node("a", "transform")])) != []


def test_duplicate_node_ids_are_refused() -> None:
    """Every id-keyed decision downstream -- claims, checkpoints, resume --
    becomes ambiguous with a duplicate."""
    doc = schema([node("a", "transform"), node("a", "transform")])
    assert any("Duplicate node id" in m for m in errors(GraphPolicy.trusted(), doc))


def test_an_edge_to_a_node_that_does_not_exist_is_refused() -> None:
    doc = schema([node("a", "transform")], [{"id": "e", "source": "a", "target": "ghost"}])
    assert any("does not exist" in m for m in errors(GraphPolicy.trusted(), doc))


def test_size_caps_bite() -> None:
    many = [node(f"n{i}", "transform") for i in range(61)]
    policy = GraphPolicy.untrusted(allow=["transform"])
    assert any("nodes exceeds" in m for m in errors(policy, schema(many)))


def test_a_nesting_bomb_is_refused_rather_than_walked() -> None:
    deep: object = "leaf"
    for _ in range(40):
        deep = {"x": deep}
    doc = schema([node("a", "transform", payload=deep)])
    policy = GraphPolicy.untrusted(allow=["transform"])
    assert any("nests deeper" in m for m in errors(policy, doc))


def test_control_characters_are_refused_in_values_and_in_keys() -> None:
    """These do not occur in a real workflow.

    They are what is used to smuggle content past a log, a terminal, or a
    downstream parser that disagrees about where a string ends -- and a KEY is
    just as good a carrier as a value.
    """
    in_value = schema([node("a", "transform", note="hello\x00world")])
    assert any("control characters" in m for m in errors(GraphPolicy.trusted(), in_value))

    in_key = schema([{"id": "a", "kind": "transform", "config": {"bad\x1bkey": 1}}])
    assert any("control characters" in m for m in errors(GraphPolicy.trusted(), in_key))


def test_tab_newline_and_carriage_return_are_legitimate() -> None:
    doc = schema([node("a", "transform", prompt="line one\nline two\twith a tab\r")])
    assert errors(GraphPolicy.trusted(), doc) == []


def test_a_lone_surrogate_is_refused() -> None:
    """Python strings can hold what no UTF-8 encoder will accept.

    Reaching a database driver, it raises from somewhere that cannot explain
    itself -- so it is refused at the boundary, by name.
    """
    doc = schema([node("a", "transform", text="bad \ud800 char")])
    assert any("not valid UTF-8" in m for m in errors(GraphPolicy.trusted(), doc))


def test_a_host_rule_runs_alongside_the_built_in_checks() -> None:
    from fancy_flow import ImportIssue

    def no_demo_workflows(doc):
        return (
            [ImportIssue.error("demo graphs are not accepted here")]
            if doc.get("metadata", {}).get("name") == "demo"
            else []
        )

    policy = GraphPolicy.trusted().add_rule(no_demo_workflows)
    doc = schema([node("a", "transform")])
    doc["metadata"] = {"name": "demo"}

    assert errors(policy, doc) == ["demo graphs are not accepted here"]


def test_assert_safe_raises_with_every_reason() -> None:
    policy = GraphPolicy.untrusted(allow=["transform"])
    doc = schema([node("a", "api_request"), node("b", "webhook_out")])

    with pytest.raises(UnsafeGraph) as caught:
        policy.assert_safe(doc)

    assert len(caught.value.issues) == 2


def test_a_policy_is_immutable() -> None:
    """Builders return new policies. A shared policy that mutates is a policy
    one caller can loosen for everybody."""
    base = GraphPolicy.untrusted()
    widened = base.allow_kinds(["transform"])

    assert base.allowed == ()
    assert widened.allowed == ("transform",)
