"""Kind ids, the kind registry, and the executor registry.

The alias rules here are not bookkeeping. A kind id is written into every saved
document, so anything keyed by kind name must key on EVERY id that kind answers
to -- and the one time that was got wrong, a human approval gate stopped
pausing and no test noticed, because the bare name still worked.
"""

from __future__ import annotations

import pytest

from fancy_flow import ExecutorRegistry, FlowNode, NodeKind, NodeKindRegistry, builtin
from fancy_flow.registry import kind_id as kid

# -- kind ids ------------------------------------------------------------


def test_canonical_and_bare_round_trip() -> None:
    assert kid.canonical("branch") == "@particle-academy/branch"
    assert kid.canonical("@particle-academy/branch") == "@particle-academy/branch"
    assert kid.bare("@particle-academy/branch") == "branch"
    assert kid.bare("branch") == "branch"


def test_matches_only_our_namespaces() -> None:
    assert kid.matches("note", "note")
    assert kid.matches("@particle-academy/note", "note")
    assert kid.matches("@fancy/note", "note")
    # Somebody else's node with the same short name is NOT ours.
    assert not kid.matches("@acme/note", "note")


def test_variants_are_ordered_by_preference_and_deduplicated() -> None:
    assert kid.variants("@particle-academy/branch") == [
        "@particle-academy/branch",
        "@fancy/branch",
        "branch",
    ]


# -- the kind registry ---------------------------------------------------


def test_a_kind_resolves_through_every_alias() -> None:
    registry = NodeKindRegistry().register(
        NodeKind(
            name="@particle-academy/llm_router",
            category="ai",
            label="Router",
            aliases=("llm_router", "llm_branch", "@fancy/llm_branch"),
        )
    )

    spellings = (
        "@particle-academy/llm_router",
        "llm_router",
        "llm_branch",
        "@fancy/llm_branch",
    )
    for spelling in spellings:
        assert registry.get(spelling) is not None, spelling


def test_unregister_removes_the_aliases_too() -> None:
    registry = NodeKindRegistry().register(
        NodeKind(name="@x/a", category="c", label="A", aliases=("a",))
    )
    registry.unregister("a")
    assert registry.get("@x/a") is None
    assert registry.get("a") is None


def test_default_config_leaves_a_present_key_alone_even_when_it_is_none() -> None:
    """`None` is a value an author chose. A default must not overwrite it."""
    from fancy_flow import ConfigField

    registry = NodeKindRegistry()
    kind = NodeKind(
        name="k",
        category="c",
        label="K",
        config_schema=(ConfigField(type="text", key="a", label="A", default="x"),),
        default_config={"a": None},
    )
    assert registry.default_config_for(kind)["a"] is None


def test_validate_config_reports_required_and_type_problems() -> None:
    from fancy_flow import ConfigField

    registry = NodeKindRegistry()
    kind = NodeKind(
        name="k",
        category="c",
        label="K",
        config_schema=(
            ConfigField(type="text", key="name", label="Name", required=True),
            ConfigField(type="number", key="n", label="N", min=1, max=5),
            ConfigField(
                type="select",
                key="mode",
                label="Mode",
                options=({"value": "a", "label": "a"},),
            ),
        ),
    )

    issues = registry.validate_config(kind, {"n": 9, "mode": "z"})
    keys = {i["key"] for i in issues}
    assert keys == {"name", "n", "mode"}


def test_a_boolean_is_not_a_number() -> None:
    """`True` is an `int` in Python. A switch must not satisfy a number field."""
    from fancy_flow import ConfigField

    registry = NodeKindRegistry()
    kind = NodeKind(
        name="k",
        category="c",
        label="K",
        config_schema=(ConfigField(type="number", key="n", label="N"),),
    )
    assert registry.validate_config(kind, {"n": True}) != []


# -- the executor registry -----------------------------------------------


def test_lookup_order_is_node_then_kind_then_fallback() -> None:
    registry = (
        ExecutorRegistry()
        .bind("*", lambda ctx: "fallback")
        .bind("k", lambda ctx: "kind")
        .bind_node("n1", lambda ctx: "node")
    )

    assert registry.resolve_for(FlowNode("n1", "k"))(None) == "node"  # type: ignore[arg-type]
    assert registry.resolve_for(FlowNode("n2", "k"))(None) == "kind"  # type: ignore[arg-type]
    assert registry.resolve_for(FlowNode("n3", "other"))(None) == "fallback"  # type: ignore[arg-type]


def test_binding_a_builtin_by_bare_name_also_binds_its_namespaced_ids() -> None:
    """The human-gate bug, pinned.

    A durable override bound under the bare name only never matched a node
    saved as `@particle-academy/user_input`, so the run went straight past the
    person it was meant to stop for. Nothing errored.
    """
    registry = builtin.executors().bind("user_input", lambda ctx: "override")

    for spelling in ("user_input", "@particle-academy/user_input", "@fancy/user_input"):
        node = FlowNode("n", spelling)
        assert registry.resolve_for(node)(None) == "override", spelling  # type: ignore[arg-type]


def test_a_rename_alias_is_reachable_from_the_old_spelling() -> None:
    """Convention alone cannot get you from `llm_branch` to `llm_router`."""
    registry = builtin.executors()
    assert registry.resolve_for(FlowNode("n", "llm_branch")) is not None
    assert registry.resolve_for(FlowNode("n", "@fancy/llm_branch")) is not None


def test_an_unknown_kind_is_stored_literally_and_not_namespaced() -> None:
    """Binding an unknown kind must not WRITE namespaced entries.

    Expanding one would claim `@particle-academy/<name>` for somebody else's
    node -- the opposite of the aliasing mistake, and just as wrong.

    Note this is about what is stored, not about what resolves. Lookup stays
    deliberately tolerant in both directions (see the next test): that is what
    lets a host bind by bare name and still match a namespaced node.
    """
    registry = ExecutorRegistry().bind("acme_thing", lambda ctx: "x")

    stored = registry._by_kind
    assert list(stored) == ["acme_thing"]


def test_lookup_is_tolerant_of_spelling_in_both_directions() -> None:
    """A host binds one spelling; a document carries another. Both must work.

    The trade is real and deliberate: a third party's `@acme/branch` will match
    a host binding of bare `branch`. The peer runtimes make the same trade,
    because the alternative -- a rename silently ceasing to match -- is a
    breaking change wearing the costume of a rename.
    """
    registry = ExecutorRegistry().bind("acme_thing", lambda ctx: "x")

    assert registry.resolve_for(FlowNode("n", "acme_thing")) is not None
    assert registry.resolve_for(FlowNode("n", "@particle-academy/acme_thing")) is not None


def test_the_star_fallback_is_never_expanded_into_namespaced_spellings() -> None:
    registry = ExecutorRegistry().bind("*", lambda ctx: "x")
    assert registry.has_fallback()
    assert registry.resolve_for(FlowNode("n", "@particle-academy/*")) is not None  # via fallback
    assert "@particle-academy/*" not in registry._by_kind


def test_fork_does_not_mutate_the_original() -> None:
    base = ExecutorRegistry().bind("k", lambda ctx: "base")
    fork = base.fork().bind("k", lambda ctx: "fork")

    assert base.resolve_for(FlowNode("n", "k"))(None) == "base"  # type: ignore[arg-type]
    assert fork.resolve_for(FlowNode("n", "k"))(None) == "fork"  # type: ignore[arg-type]


def test_a_class_executor_is_instantiated_through_the_resolver() -> None:
    class Made:
        def __init__(self) -> None:
            self.tag = "made"

        def execute(self, ctx):  # type: ignore[no-untyped-def]
            return self.tag

    registry = ExecutorRegistry().bind("k", Made)
    assert registry.resolve_for(FlowNode("n", "k"))(None) == "made"  # type: ignore[arg-type]


def test_a_host_resolver_gets_to_construct_executors() -> None:
    class Injected:
        def __init__(self, dep: str) -> None:
            self.dep = dep

        def execute(self, ctx):  # type: ignore[no-untyped-def]
            return self.dep

    class ContainerResolver:
        def make(self, cls):  # type: ignore[no-untyped-def]
            return cls("from-container")

    registry = ExecutorRegistry(resolver=ContainerResolver()).bind("k", Injected)
    assert registry.resolve_for(FlowNode("n", "k"))(None) == "from-container"  # type: ignore[arg-type]


def test_a_nonsense_executor_is_refused_by_name() -> None:
    from fancy_flow import FlowError

    registry = ExecutorRegistry().bind("k", 42)
    with pytest.raises(FlowError):
        registry.resolve_for(FlowNode("n", "k"))


# -- the builtin library -------------------------------------------------


def test_every_builtin_kind_has_an_executor_bound_under_every_id() -> None:
    """The forcing function: a kind added without an executor fails here."""
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    executors = builtin.executors()

    missing = []
    for kind in registry.all():
        if kid.matches(kind.name, "note"):
            continue  # never executed by design
        for kind_id in kind.ids():
            if executors.resolve_for(FlowNode("n", kind_id)) is None:
                missing.append(kind_id)

    assert missing == []


def test_the_kind_id_index_covers_the_opt_in_kinds_too() -> None:
    """`agent` is opt-in but must still expand its aliases, or an executor bound
    for it lands under one id and misses the other two."""
    index = builtin.kind_id_index()
    assert index["agent"] == [
        "@particle-academy/agent",
        "agent",
        "@fancy/agent",
    ]
    assert "note" in index
    assert "subgraph" in index
