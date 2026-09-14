"""A template that starts with ``{{`` and ends with ``}}`` but holds several references.

fancy-flow-php#16. ``_whole_expression`` used to ask only whether the trimmed
template starts with ``{{`` and ends with ``}}``, so
``{{ in.text }} --- {{ user.transcript }}`` was ONE path,
``in.text }} --- {{ user.transcript``, which resolves to nothing. The template
returned ``None`` under "empty", itself under "keep", and raised under "throw".
A consumer's document node wrote nothing, with every reference valid.

It was documented as a deliberate corner and mirrored in all four runtimes,
which is why no parity table could catch it. The whole-string branch now
applies only to exactly ONE expression; anything else interpolates.

Mirrors ``tests/Unit/TemplateWithSeveralReferencesTest.php`` in fancy-flow-php
0.52.2 and ``tests/template-with-several-references.test.ts`` in fancy-flow.
"""

from __future__ import annotations

from typing import Any

import pytest

from fancy_flow.nodes.support.expr import UnresolvedPathError, UnresolvedPolicy, evaluate


def ctx() -> dict[str, Any]:
    return {
        "in": {"text": "SUMMARY"},
        "user": {"transcript": "TRANSCRIPT", "title": "Call"},
        "a": 1,
        "b": 2,
    }


POLICIES: list[UnresolvedPolicy] = ["empty", "keep", "throw"]


@pytest.mark.parametrize("policy", POLICIES)
def test_interpolates_every_reference_of_a_template_that_starts_and_ends_with_one(
    policy: UnresolvedPolicy,
) -> None:
    template = "{{ in.text }}\n\n---\n\n## Original\n\n{{ user.transcript }}"
    assert evaluate(template, ctx(), policy) == "SUMMARY\n\n---\n\n## Original\n\nTRANSCRIPT"
    assert evaluate("{{ user.title }} - {{ in.text }}", ctx(), policy) == "Call - SUMMARY"
    assert (
        evaluate("Summary: {{ in.text }} --- {{ user.transcript }}", ctx(), policy)
        == "Summary: SUMMARY --- TRANSCRIPT"
    )
    # Adjacent references are two references, not one path spanning `}}{{`.
    assert evaluate("{{ a }}{{ b }}", ctx(), policy) == "12"


@pytest.mark.parametrize("policy", POLICIES)
def test_still_returns_the_typed_value_for_exactly_one_expression(
    policy: UnresolvedPolicy,
) -> None:
    assert evaluate("{{ in.text }}", ctx(), policy) == "SUMMARY"
    value = evaluate(" {{ a }} ", ctx(), policy)
    assert value == 1
    assert isinstance(value, int)


@pytest.mark.parametrize("policy", POLICIES)
def test_an_inner_opening_brace_is_not_one_expression_either(policy: UnresolvedPolicy) -> None:
    # The rule's second condition. `{{ a {{ b }}` is malformed; the scan pairs
    # the first `{{` with the first `}}`, and that path does not resolve, so
    # each policy applies to it as an interpolated reference -- never the
    # whole-string branch's None.
    malformed = "{{ a {{ b }}"
    if policy == "throw":
        with pytest.raises(UnresolvedPathError):
            evaluate(malformed, ctx(), policy)
    else:
        assert evaluate(malformed, ctx(), policy) == (malformed if policy == "keep" else "")


def test_applies_the_policy_per_reference_when_one_of_several_does_not_resolve() -> None:
    template = "{{ in.text }} / {{ in.nope }}"

    assert evaluate(template, ctx()) == "SUMMARY / "
    assert evaluate(template, ctx(), "keep") == "SUMMARY / {{ in.nope }}"
    with pytest.raises(UnresolvedPathError) as excinfo:
        evaluate(template, ctx(), "throw")
    # The unresolved REFERENCE is named, not a path spanning the whole template.
    assert excinfo.value.path.strip() == "in.nope"
