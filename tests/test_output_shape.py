"""``output_shape`` declares the FIELDS a kind emits -- not its ports.

It existed in the TypeScript twin and in neither backend. A consumer running on
PHP or Python had nothing to check ``{{ in.field }}`` against and had to
hand-maintain a table derived by reading our executors' source. That table
drifted and refused a legitimate ``{{ in.title }}`` while accepting a field that
does not exist -- a false rejection the author cannot comply with.

Fourth capability found present in one runtime and absent in the others, where
**absent reads as "this kind emits nothing"**: a legitimate answer, so the gap
is invisible. Same shape as ``graph.inputs`` dropped on import and
``side_effects`` declared by nothing.
"""

from fancy_flow.registry.node_kind import NodeKind


def test_accepts_a_static_field_list() -> None:
    kind = NodeKind.from_dict(
        {
            "name": "llm_call",
            "category": "ai",
            "label": "LLM Call",
            "outputShape": [
                {"path": "text", "type": "string", "description": "The model's completion."},
                {"path": "usage", "type": "object"},
            ],
        }
    )

    resolved = kind.output_shape_for({})
    assert resolved is not None
    assert [f["path"] for f in resolved] == ["text", "usage"]


def test_accepts_a_callable_of_config() -> None:
    """The form that cannot be lost in the port.

    A ``user_input`` emits the keys its author defined and a ``system_event``
    its event's payload; no static list can know either. Those two are this
    port's acceptance test -- a plain-list-only port would be a
    legitimate-looking value that cannot express them, and therefore an
    invisible loss.
    """
    kind = NodeKind(
        name="user_input",
        category="human",
        label="User Input",
        output_shape=lambda config: [
            {"path": f["key"], "type": "string"} for f in config.get("fields", [])
        ],
    )

    resolved = kind.output_shape_for({"fields": [{"key": "email"}, {"key": "note"}]})
    assert resolved is not None
    assert [f["path"] for f in resolved] == ["email", "note"]


def test_not_declared_is_not_declares_nothing() -> None:
    """The distinction the field exists to carry.

    ``None`` means nobody said. ``[]`` means this kind genuinely emits no
    fields. Collapsing them is the bug.
    """
    undeclared = NodeKind.from_dict({"name": "transform", "category": "logic", "label": "T"})
    assert undeclared.output_shape_for({}) is None
    assert "outputShape" not in undeclared.to_dict()

    emits_nothing = NodeKind.from_dict(
        {"name": "log", "category": "io", "label": "Log", "outputShape": []}
    )
    assert emits_nothing.output_shape_for({}) == []


def test_a_dynamic_shape_says_so_when_serialised() -> None:
    """A callable cannot cross a JSON boundary.

    Dropping it would make the manifest say "no outputShape", which reads as
    "emits nothing" -- the very failure this field exists to fix, reintroduced
    at the serialisation seam.
    """
    kind = NodeKind(
        name="user_input",
        category="human",
        label="User Input",
        output_shape=lambda config: [{"path": "email", "type": "string"}],
    )

    payload = kind.to_dict()
    assert payload["outputShape"] == "dynamic"

    restored = NodeKind.from_dict(payload)
    assert restored.has_dynamic_output_shape() is True
    # "a shape exists and this process cannot resolve it" -- never a field list.
    assert restored.output_shape_for({}) is None


def test_round_trips_a_static_shape_unchanged() -> None:
    fields = [{"path": "text", "type": "string"}]
    kind = NodeKind.from_dict(
        {"name": "llm_call", "category": "ai", "label": "L", "outputShape": fields}
    )

    assert kind.to_dict()["outputShape"] == fields
    assert NodeKind.from_dict(kind.to_dict()).output_shape_for({}) == fields
