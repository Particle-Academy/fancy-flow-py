"""\"Did not resolve\" must be distinguishable from \"resolved to empty\".

``resolve_path`` returns ``None`` both for a path that does not exist and for a
path that exists holding ``None``. At the interpolation layer that collapse gets
worse, because ``None`` stringifies to ``""``. The consumer who reported it put
it exactly:

    "An unresolvable path yields ``''``, so a wrong field is indistinguishable
    from an empty one at runtime."

A misspelled field renders as an empty string, which looks precisely like a
field that is legitimately empty. The graph runs, the node succeeds, and the
output is quietly missing a value nobody is told about.

These cases mirror ``tests/unresolved-path.test.ts`` in ``fancy-flow`` and
``tests/Unit/UnresolvedPathTest.php`` in ``fancy-flow-php`` one for one, so the
three runtimes can be read side by side.
"""

from __future__ import annotations

import pytest

from fancy_flow.nodes.support.expr import (
    UnresolvedPathError,
    evaluate,
    resolve_path,
    try_resolve_path,
)


def ctx() -> dict:
    return {
        "in": {"text": "hello", "empty": "", "nothing": None, "count": 0},
        "n1": {"nested": {"deep": "found"}},
    }


def test_missing_path_is_unresolved() -> None:
    r = try_resolve_path("in.missing", ctx())
    assert r.resolved is False
    assert r.value is None


def test_path_holding_none_is_resolved_not_missing() -> None:
    # The whole point. resolve_path cannot tell these two apart.
    r = try_resolve_path("in.nothing", ctx())
    assert r.resolved is True
    assert r.value is None
    assert resolve_path("in.nothing", ctx()) == resolve_path("in.missing", ctx())


def test_empty_string_and_zero_are_resolved() -> None:
    # Falsy but present. `resolved` must never be computed from truthiness --
    # `if not value` here would reintroduce the exact bug being fixed.
    assert try_resolve_path("in.empty", ctx()) == (True, "")
    assert try_resolve_path("in.count", ctx()) == (True, 0)


def test_walking_into_a_scalar_or_none_is_unresolved() -> None:
    assert try_resolve_path("in.text.nope", ctx()).resolved is False
    assert try_resolve_path("in.nothing.nope", ctx()).resolved is False


def test_nesting_and_the_json_alias() -> None:
    assert try_resolve_path("n1.nested.deep", ctx()).value == "found"
    assert try_resolve_path("$json.text", ctx()).value == "hello"
    assert try_resolve_path("$input.text", ctx()).value == "hello"


def test_empty_path_is_unresolved() -> None:
    assert try_resolve_path("   ", ctx()).resolved is False


def test_list_indexing_still_works() -> None:
    c = {"in": {"rows": ["a", "b"]}}
    assert try_resolve_path("in.rows.1", c) == (True, "b")
    assert try_resolve_path("in.rows.9", c).resolved is False


def test_object_attribute_holding_none_is_resolved() -> None:
    # The Python analogue of the PHP `isset()` trap: `hasattr` is already
    # correct here (it does not care about the value), so this pins that the
    # attribute branch reports presence rather than truthiness.
    class Thing:
        name = None
        label = "x"

    c = {"o": Thing()}
    assert try_resolve_path("o.name", c) == (True, None)
    assert try_resolve_path("o.label", c) == (True, "x")
    assert try_resolve_path("o.nope", c).resolved is False


def test_dunder_paths_stay_refused() -> None:
    # A dot-path in a config field is author input. This is a security boundary,
    # not a style choice, and the new walk must not have widened it.
    class Thing:
        pass

    assert try_resolve_path("o.__class__", {"o": Thing()}).resolved is False


def test_resolve_path_is_unchanged_by_the_delegation() -> None:
    # It is now DEFINED in terms of try_resolve_path, so this pins that not one
    # answer moved.
    assert resolve_path("in.missing", ctx()) is None
    assert resolve_path("in.nothing", ctx()) is None
    assert resolve_path("in.text", ctx()) == "hello"
    assert resolve_path("in.count", ctx()) == 0
    assert resolve_path("$json.text", ctx()) == "hello"


def test_default_policy_is_unchanged_behaviour() -> None:
    assert evaluate("Hi {{ in.missing }}!", ctx()) == "Hi !"
    assert evaluate("{{ in.missing }}", ctx()) is None
    assert evaluate("{{ in.missing }}", ctx()) == evaluate("{{ in.missing }}", ctx(), "empty")


def test_keep_policy_leaves_the_template_visible() -> None:
    assert evaluate("Hi {{ in.missing }}!", ctx(), "keep") == "Hi {{ in.missing }}!"

    # Byte-identical round trip, spacing included.
    t = "a {{in.missing}} b {{   in.missing   }} c"
    assert evaluate(t, ctx(), "keep") == t

    assert (
        evaluate("x {{ in.text }} / {{ in.missing }}", ctx(), "keep")
        == "x hello / {{ in.missing }}"
    )


def test_keep_does_not_apply_to_resolved_empties() -> None:
    # The distinction the whole change exists for: resolved-but-empty
    # interpolates to nothing under EVERY policy. Only UNRESOLVED is special.
    assert evaluate("[{{ in.nothing }}]", ctx(), "keep") == "[]"
    assert evaluate("[{{ in.empty }}]", ctx(), "keep") == "[]"


def test_throw_policy_raises_naming_the_path() -> None:
    with pytest.raises(UnresolvedPathError) as excinfo:
        evaluate("Hi {{ in.missing }}", ctx(), "throw")
    assert excinfo.value.path.strip() == "in.missing"
    assert "in.missing" in str(excinfo.value)

    with pytest.raises(UnresolvedPathError):
        evaluate("{{ in.missing }}", ctx(), "throw")


def test_throw_does_not_fire_for_resolved_empties() -> None:
    assert evaluate("[{{ in.nothing }}]", ctx(), "throw") == "[]"
    assert evaluate("[{{ in.empty }}]", ctx(), "throw") == "[]"
    assert evaluate("{{ in.count }}", ctx(), "throw") == 0


def test_the_two_expression_corner_becomes_visible() -> None:
    # A template that both starts with `{{` and ends with `}}` is ONE whole
    # expression whose path contains the inner `}}{{`. Deliberate, and mirrored
    # in all three runtimes. Under "keep" the author at least SEES that the
    # template was never split.
    two_looking = "{{ in.text }} / {{ in.text }}"
    assert evaluate(two_looking, ctx()) is None
    assert evaluate(two_looking, ctx(), "keep") == two_looking
