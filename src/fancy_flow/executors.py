"""Maps nodes to the code that runs them.

Three-tier lookup, matching both peer runtimes::

    node id  ->  node kind  ->  "*" fallback

An executor may be a callable ``(ctx) -> Any``, an object with ``.execute``, or
a class implementing either (instantiated through a :class:`Resolver`).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, cast

from .contracts import NativeResolver, Resolver
from .exceptions import FlowError
from .registry import kind_id as kid
from .registry.registry import NodeKindRegistry, default_registry
from .runtime.context import ExecutionContext
from .schema.graph import FlowNode

__all__ = ["Executor", "ExecutorRegistry"]

#: Anything that can execute a node.
Executor = Any


class ExecutorRegistry:
    """Bindings from node id / kind / ``*`` to executors."""

    def __init__(
        self,
        resolver: Resolver | None = None,
        kinds: NodeKindRegistry | None = None,
    ) -> None:
        self._by_kind: dict[str, Executor] = {}
        self._by_node: dict[str, Executor] = {}
        self._resolver: Resolver = resolver or NativeResolver()
        #: The catalogue consulted for kind aliases; the shared one by default.
        self._kinds = kinds

    # -- binding ---------------------------------------------------------

    def bind(self, kind: str, executor: Executor) -> ExecutorRegistry:
        """Bind an executor to a node kind, or to the ``*`` fallback.

        **Alias-aware for kinds this registry knows.** Binding ``user_input``
        binds ``@particle-academy/user_input`` and ``@fancy/user_input`` with
        it, because they are the same kind and a caller overriding one means
        the kind.

        Keying literally was a silent trap in the PHP twin, and it cost a human
        gate: the builtins were bound under all three ids, lookup tries the
        node's literal id FIRST, and a durable override bound under the bare
        name only never matched a node saved as
        ``@particle-academy/user_input``. Nothing errored; the run simply went
        straight past the person it was meant to stop for.

        An UNKNOWN kind is still bound literally. Expanding one would claim
        ``@particle-academy/<name>`` for somebody else's node, which is the
        opposite mistake.
        """
        self._by_kind[kind] = executor

        # The `*` fallback is a sentinel, not a kind: it has no aliases and
        # must never be expanded into namespaced spellings.
        if kind == "*":
            return self

        for alias in self._alias_ids_for(kind):
            self._by_kind[alias] = executor
        return self

    def bind_node(self, node_id: str, executor: Executor) -> ExecutorRegistry:
        """Bind an executor to a single node id -- highest precedence."""
        self._by_node[node_id] = executor
        return self

    def bind_many(self, mapping: dict[str, Executor]) -> ExecutorRegistry:
        for kind, executor in mapping.items():
            self.bind(kind, executor)
        return self

    def fork(self) -> ExecutorRegistry:
        """A shallow copy sharing the resolver.

        Bind on the fork to override kinds for a single run without mutating
        the shared registry -- what a durable driver does when it swaps in a
        pausing approval executor, or fences the graph off around one node.
        """
        copy = ExecutorRegistry(self._resolver, self._kinds)
        copy._by_kind = dict(self._by_kind)
        copy._by_node = dict(self._by_node)
        return copy

    # -- lookup ----------------------------------------------------------

    def has_kind(self, kind: str) -> bool:
        """True when a binding exists under ANY id this kind answers to."""
        return any(c in self._by_kind for c in self._kind_candidates(kind))

    def has_fallback(self) -> bool:
        return "*" in self._by_kind

    def resolve_for(self, node: FlowNode) -> Callable[[ExecutionContext], Any] | None:
        """Resolve the executor for a node, following id -> kind -> ``*``.

        The kind step tries EVERY id the kind answers to, not just the one
        written in the graph. Canonical ids are namespaced while a host may
        well have bound its executor under the bare name -- resolving only the
        literal string would turn a rename into a breaking change in disguise.
        """
        raw = self._by_node.get(node.id)

        if raw is None and node.type is not None:
            for candidate in self._kind_candidates(node.type):
                if candidate in self._by_kind:
                    raw = self._by_kind[candidate]
                    break

        if raw is None:
            raw = self._by_kind.get("*")

        return None if raw is None else self._to_callable(raw)

    # -- internals -------------------------------------------------------

    def _registry(self) -> NodeKindRegistry:
        return self._kinds if self._kinds is not None else default_registry()

    def _kind_candidates(self, kind: str) -> list[str]:
        """Every id a binding for ``kind`` might have been registered under.

        Explicit aliases from the kind registry come first -- a custom kind may
        declare any alias it likes -- then the naming-convention variants,
        which cover bindings made against a kind that was never registered.
        """
        ordered = [kind, *self._registry().ids_for(kind), *kid.variants(kind)]
        seen: dict[str, None] = {}
        for item in ordered:
            seen.setdefault(item, None)
        return list(seen)

    def _alias_ids_for(self, kind: str) -> list[str]:
        """Every id a KNOWN kind answers to, minus the one just bound.

        Declared aliases come from the kind registry, because convention alone
        cannot get you from ``llm_branch`` to ``llm_router`` -- only the kind's
        own alias list does. Empty for a kind nothing has heard of.
        """
        from .registry.builtin import kind_id_index

        declared = self._registry().ids_for(kind)
        if not declared:
            # The kind registry is not necessarily populated when a binding is
            # made -- a forked registry overriding a builtin often has none at
            # all -- so fall back to the builtin index, which is the SAME
            # authority the base bindings were expanded from. Agreeing with it
            # by construction is the whole point.
            declared = kind_id_index().get(kid.bare(kind), [])

        if not declared:
            return []

        ordered = [*declared, *kid.variants(kind)]
        seen: dict[str, None] = {}
        for item in ordered:
            # ``*`` is excluded in BOTH directions, and only one was covered.
            # ``bind`` already refuses to expand the sentinel OUT to namespaced
            # spellings -- but nothing stopped an alias expanding IN to it, so a
            # kind that answers to ``*`` turned one ordinary ``bind`` into a
            # GLOBAL FALLBACK for every unmatched node in the graph. Silently:
            # a fallback that exists and one that does not both let a run
            # complete.
            #
            # The ``*`` slot may only ever be written by an explicit
            # ``bind("*")``. Found by ``flow/executor-resolution/0107``, which
            # caught the identical defect in the PHP twin -- both expand aliases
            # at BIND time, and TypeScript was unaffected only because it
            # expands at LOOKUP time and never looks the sentinel up as a kind.
            if item != kind and item != "*":
                seen.setdefault(item, None)
        return list(seen)

    def _to_callable(self, executor: Executor) -> Callable[[ExecutionContext], Any]:
        if inspect.isclass(executor):
            instance = self._resolver.make(executor)
            return self._to_callable(instance)

        execute = getattr(executor, "execute", None)
        if callable(execute):
            return cast("Callable[[ExecutionContext], Any]", execute)

        if callable(executor):
            return cast("Callable[[ExecutionContext], Any]", executor)

        raise FlowError(
            f"Executor {executor!r} must be callable, expose execute(ctx), "
            "or be a class resolving to one of those."
        )
