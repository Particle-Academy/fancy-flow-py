"""``for_each`` says what a ``results`` entry looks like.

Each entry is that iteration's node outputs KEYED BY NODE ID, and a failed
item's entry is None. An authoring agent reads this text, and a builder in lab
run 1406 wrote ``{{ in.results.0.total }}`` where the value is at
``results.0.score-1.total``. Kept identical in meaning to the TypeScript, PHP
and Rust declarations.
"""

from __future__ import annotations

from typing import Any

from fancy_flow import NodeKindRegistry, builtin


def for_each() -> dict[str, Any]:
    kind = builtin.register(NodeKindRegistry()).get("for_each")
    assert kind is not None
    return kind.to_dict()


def test_the_results_field_names_the_keyed_shape() -> None:
    field = next(f for f in for_each()["outputShape"] if f["path"] == "results")
    text = field["description"]
    assert "keyed by node id" in text
    assert "results.0.<node-id>.<field>" in text
    assert "None" in text


def test_the_kind_description_says_the_same() -> None:
    text = for_each()["description"]
    assert "keyed by node id" in text
    assert "results.0.<node-id>.<field>" in text
