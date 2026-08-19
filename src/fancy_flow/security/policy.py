"""What a graph must satisfy before an untrusted author's copy of it is allowed
near a queue.

:func:`fancy_flow.import_workflow` already answers "is this graph COHERENT?" --
unknown kinds, dangling edges, missing required config. This answers a different
question: **"is it safe to accept and persist?"** A graph arriving over HTTP
from someone you have never met is a payload first and a workflow second, and it
gets written to a queue row and rehydrated later by a worker that trusts it.

The checks, and what each is actually for:

- **Kind policy.** An allowlist is the load-bearing control: it decides which
  executors a stranger may cause to run. Everything else is depth in front of it.
- **Size caps.** Nodes, edges, nesting depth, string length, total bytes. A
  deeply nested config is a stack overflow in whatever parses it next, and an
  enormous one is a queue row nobody can process.
- **Byte hygiene.** Lone surrogates, NUL, and C0/C1 controls are rejected in
  every string. These do not occur in a real workflow and are exactly what is
  used to smuggle content past a log, a terminal, or a downstream parser that
  disagrees with Python about where a string ends.
- **Structure.** Duplicate node ids and edges pointing at nodes that do not
  exist -- cheap to check, and a duplicate id makes every id-keyed decision
  downstream ambiguous.
- **Custom rules.** A host knows things this package cannot. :meth:`add_rule`
  takes those without anyone patching this class.

The kind policy is ALIAS-AWARE, and that is the whole point
------------------------------------------------------------

A kind answers to several ids -- ``user_input``,
``@particle-academy/user_input``, ``@fancy/user_input``. A denylist keyed on the
literal string you happened to write is not a denylist: it is a suggestion the
attacker declines by spelling the kind differently. Every id a kind answers to
is resolved before any comparison.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..exceptions import UnsafeGraph
from ..registry import kind_id as kid
from ..schema.issues import ImportIssue

__all__ = ["GraphPolicy", "UnsafeGraph"]

Rule = Callable[[dict[str, Any]], list[ImportIssue]]

#: NUL and the C0/C1 control ranges, minus tab, newline and carriage return,
#: which are legitimate in a prompt or a description.
_CONTROL = frozenset(
    [chr(c) for c in range(0x00, 0x20) if c not in (0x09, 0x0A, 0x0D)]
    + [chr(0x7F)]
    + [chr(c) for c in range(0x80, 0xA0)]
)


@dataclass(frozen=True, slots=True)
class GraphPolicy:
    """An immutable policy. Every ``with``/``allow``/``deny`` returns a new one."""

    max_nodes: int = 60
    max_edges: int = 120
    max_depth: int = 12
    max_string_length: int = 20_000
    max_bytes: int = 256_000
    #: Bare kind names. ``None`` means "no allowlist".
    allowed: tuple[str, ...] | None = None
    #: Bare kind names.
    denied: tuple[str, ...] = ()
    rules: tuple[Rule, ...] = ()

    @staticmethod
    def untrusted(allow: list[str] | None = None) -> GraphPolicy:
        """The posture for a graph you did not write.

        Deliberately strict, and deliberately an ALLOWLIST: a denylist of
        dangerous kinds is a list you have to keep complete forever, and the
        first kind added to the package after you wrote it is permitted by
        default. An allowlist fails the other way, which is the correct way.

        The caller names what it wants to permit, because only the caller
        knows -- this package cannot guess which of its own kinds are safe in
        someone else's app.

        **Divergence from the PHP twin, on purpose.** ``GraphPolicy::untrusted()``
        there returns a policy whose allowlist is ABSENT rather than empty, and
        an absent allowlist permits every kind. Its own docblock says "empty by
        design" and "an allowlist fails the other way, which is the correct
        way" -- but a caller who writes ``GraphPolicy::untrusted()->assert()``
        and forgets ``allowKinds()`` gets size caps and byte hygiene with **no
        kind restriction at all**, from a method named ``untrusted``.

        Here, ``untrusted()`` starts with an EMPTY allowlist: nothing is
        permitted until something is named. That changes no verdict for a
        correctly configured policy, and turns a silent fail-open into a loud
        rejection. Recorded in the plan as a fix to carry back to the PHP twin.

        Pass ``allow`` to name the permitted kinds in one call.
        """
        policy = GraphPolicy(allowed=())
        return policy.allow_kinds(allow) if allow else policy

    @staticmethod
    def trusted() -> GraphPolicy:
        """Caps only, no kind policy -- for graphs your own code produced."""
        return GraphPolicy(
            max_nodes=5_000,
            max_edges=10_000,
            max_depth=32,
            max_string_length=1_000_000,
            max_bytes=8_000_000,
        )

    def allow_kinds(self, kinds: list[str]) -> GraphPolicy:
        """Permit ONLY these kinds. Any spelling; every id each kind answers to
        is permitted with it."""
        return replace(self, allowed=tuple(dict.fromkeys(kid.bare(k) for k in kinds)))

    def deny_kinds(self, kinds: list[str]) -> GraphPolicy:
        """Refuse these kinds.

        Applied after the allowlist, so a kind named in both is refused -- the
        safer reading of a contradiction.
        """
        return replace(
            self,
            denied=tuple(dict.fromkeys([*self.denied, *(kid.bare(k) for k in kinds)])),
        )

    def with_limits(
        self,
        max_nodes: int | None = None,
        max_edges: int | None = None,
        max_depth: int | None = None,
        max_string_length: int | None = None,
        max_bytes: int | None = None,
    ) -> GraphPolicy:
        return replace(
            self,
            max_nodes=self.max_nodes if max_nodes is None else max_nodes,
            max_edges=self.max_edges if max_edges is None else max_edges,
            max_depth=self.max_depth if max_depth is None else max_depth,
            max_string_length=(
                self.max_string_length if max_string_length is None else max_string_length
            ),
            max_bytes=self.max_bytes if max_bytes is None else max_bytes,
        )

    def add_rule(self, rule: Rule) -> GraphPolicy:
        """Add a host rule. Receives the raw schema, returns any issues.

        The extension point that keeps hosts from forking this class: a rule can
        assert anything about the graph, and runs alongside the built-in checks
        rather than replacing them.
        """
        return replace(self, rules=(*self.rules, rule))

    def inspect(self, schema: dict[str, Any]) -> list[ImportIssue]:
        """Every problem with this schema. Empty means it may be accepted.

        Returns rather than raises so a UI can show all of them at once; use
        :meth:`assert_safe` at the boundary where you just need it to stop.
        """
        issues: list[ImportIssue] = []

        # Byte size first: everything below walks the structure, and there is no
        # reason to walk a payload already too large to accept.
        try:
            encoded = json.dumps(schema, ensure_ascii=False).encode("utf-8", "surrogatepass")
        except (TypeError, ValueError):
            return [
                ImportIssue.error(
                    "The graph could not be encoded as JSON, so it cannot be stored or replayed."
                )
            ]

        if len(encoded) > self.max_bytes:
            return [ImportIssue.error(f"The graph is larger than the {self.max_bytes}-byte limit.")]

        raw_graph = schema.get("graph")
        graph: dict[str, Any] = raw_graph if isinstance(raw_graph, dict) else schema
        raw_nodes = graph.get("nodes")
        raw_edges = graph.get("edges")
        nodes: list[Any] = raw_nodes if isinstance(raw_nodes, list) else []
        edges: list[Any] = raw_edges if isinstance(raw_edges, list) else []

        if len(nodes) > self.max_nodes:
            issues.append(
                ImportIssue.error(f"{len(nodes)} nodes exceeds the limit of {self.max_nodes}.")
            )
        if len(edges) > self.max_edges:
            issues.append(
                ImportIssue.error(f"{len(edges)} edges exceeds the limit of {self.max_edges}.")
            )

        seen: set[str] = set()

        for node in nodes:
            if not isinstance(node, dict):
                issues.append(ImportIssue.error("A node is not an object."))
                continue

            node_id = node.get("id") if isinstance(node.get("id"), str) else None
            if not node_id:
                issues.append(ImportIssue.error("A node has no id."))
                continue

            if node_id in seen:
                # Every id-keyed decision downstream -- claims, checkpoints,
                # resume -- becomes ambiguous with a duplicate.
                issues.append(ImportIssue.error(f'Duplicate node id "{node_id}".', node_id))
            seen.add(node_id)

            kind = node.get("kind") or node.get("type")
            if not isinstance(kind, str) or kind == "":
                issues.append(ImportIssue.error("A node has no kind.", node_id))
            else:
                issues.extend(self._kind_issues(kind, node_id))

            issues.extend(self._value_issues(node, node_id, 0))

        for edge in edges:
            if not isinstance(edge, dict):
                issues.append(ImportIssue.error("An edge is not an object."))
                continue

            edge_id = edge.get("id") if isinstance(edge.get("id"), str) else None
            for end in ("source", "target"):
                ref = edge.get(end)
                if not isinstance(ref, str) or ref not in seen:
                    issues.append(
                        ImportIssue.error(
                            f"An edge points at a {end} node that does not exist.",
                            None,
                            edge_id,
                        )
                    )

        for rule in self.rules:
            issues.extend(rule(schema))

        return issues

    def assert_safe(self, schema: dict[str, Any]) -> None:
        """Raise :class:`UnsafeGraph` unless the schema satisfies this policy."""
        errors = [i for i in self.inspect(schema) if i.is_error]
        if errors:
            raise UnsafeGraph(errors)

    # -- internals -------------------------------------------------------

    def _kind_issues(self, kind: str, node_id: str) -> list[ImportIssue]:
        from ..registry.builtin import kind_id_index

        # Compare on the BARE name after resolving every id this kind answers
        # to. `@particle-academy/api_request` and `api_request` are the same
        # executor; a policy that only knew the string it was handed would be
        # bypassed by spelling it the other way.
        bare = kid.bare(kind)
        aliases = kind_id_index().get(bare, [])
        names = list(dict.fromkeys([bare, *(kid.bare(a) for a in aliases)]))

        if any(name in self.denied for name in names):
            return [ImportIssue.error(f'The kind "{kind}" is not permitted here.', node_id)]

        if self.allowed is None:
            return []

        if any(name in self.allowed for name in names):
            return []

        return [ImportIssue.error(f'The kind "{kind}" is not on the allowed list.', node_id)]

    def _value_issues(self, value: Any, node_id: str, depth: int) -> list[ImportIssue]:
        if depth > self.max_depth:
            # Depth is checked before recursing further, so a nesting bomb is
            # refused rather than parsed. Whatever reads this next -- a JSON
            # decoder, a serializer, a template -- recurses too.
            return [
                ImportIssue.error(
                    f"A node's config nests deeper than {self.max_depth} levels.", node_id
                )
            ]

        if isinstance(value, str):
            if len(value) > self.max_string_length:
                return [
                    ImportIssue.error(
                        f"A string in this node is longer than {self.max_string_length} "
                        "characters.",
                        node_id,
                    )
                ]
            if not _is_encodable(value):
                # Python strings can hold lone surrogates that no UTF-8 encoder
                # will accept. Reaching a database driver, they raise from
                # somewhere that cannot explain itself.
                return [
                    ImportIssue.error(
                        "A string in this node is not valid UTF-8.",
                        node_id,
                    )
                ]
            if any(char in _CONTROL for char in value):
                return [
                    ImportIssue.error("A string in this node contains control characters.", node_id)
                ]
            return []

        issues: list[ImportIssue] = []

        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    issues.extend(self._value_issues(key, node_id, depth + 1))
                issues.extend(self._value_issues(item, node_id, depth + 1))
        elif isinstance(value, (list, tuple)):
            for item in value:
                issues.extend(self._value_issues(item, node_id, depth + 1))

        return issues


def _is_encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
