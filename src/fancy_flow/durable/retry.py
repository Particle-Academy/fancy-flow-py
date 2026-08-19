"""How many times a single node may be attempted.

Why this cannot be one number
-----------------------------

A run-wide ``tries`` setting forces every workflow to pick between two bad
answers. At 1, a single flaky LLM or HTTP call takes the whole run down. Above
1, the retry replays from the last checkpoint and everything already done runs
again -- including the nodes that must not: ``git_pr_open`` opens a second pull
request.

Per-node jobs make the question per node, which is where it always belonged. A
node declaring ``sideEffects: unsafe-to-replay`` is pinned to ONE attempt and no
backoff. Everything else takes the configured tries, or a per-kind override.

Undeclared side effects are treated as the configured default rather than
assumed safe: this decides retries, and inventing a safety claim on a node
author's behalf is how a retry loop ends up posting the same webhook twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..registry import kind_id as kid
from ..registry.registry import NodeKindRegistry
from ..schema.graph import FlowNode

__all__ = ["UNSAFE_TO_REPLAY", "RetryPolicy"]

#: A node that is not safe to run twice. Same vocabulary as the node manifest.
UNSAFE_TO_REPLAY = "unsafe-to-replay"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Run-wide defaults plus per-kind overrides."""

    tries: int = 1
    backoff_seconds: float = 0.0
    #: kind id -> tries. Keyed by any spelling; every id is checked.
    per_kind: dict[str, int] = field(default_factory=dict)

    def tries_for(self, node: FlowNode, kinds: NodeKindRegistry) -> int:
        if self.is_unsafe_to_replay(node, kinds):
            return 1

        for kind_id in _ids(node, kinds):
            if kind_id in self.per_kind:
                return max(1, int(self.per_kind[kind_id]))

        return max(1, int(self.tries))

    def backoff_for(self, node: FlowNode, kinds: NodeKindRegistry) -> float:
        # Nothing to back off from: the node gets one attempt.
        if self.is_unsafe_to_replay(node, kinds):
            return 0.0
        return max(0.0, float(self.backoff_seconds))

    @staticmethod
    def is_unsafe_to_replay(node: FlowNode, kinds: NodeKindRegistry) -> bool:
        if node.type is None:
            return False
        kind = kinds.get(node.type)
        return kind is not None and kind.side_effects == UNSAFE_TO_REPLAY


def _ids(node: FlowNode, kinds: NodeKindRegistry) -> list[str]:
    """Every id a per-kind override for this node could be keyed under.

    Canonical ids are namespaced while a host almost certainly writes the bare
    one; keying on only the literal string would make the override silently
    stop applying the day a kind is renamed.
    """
    if node.type is None:
        return []
    ordered = [node.type, *kinds.ids_for(node.type), *kid.variants(node.type)]
    return list(dict.fromkeys(ordered))
