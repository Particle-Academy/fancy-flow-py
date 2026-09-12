"""Find the node packages a host has INSTALLED, and wire them in one pass.

Python had no convention for this, and the absence was not neutral -- it was
being filled. A connector lab discovering node packages by scanning
``pkgutil.iter_modules()`` for ``fancy_*`` and trying ``import <pkg>.flow`` is a
reasonable thing to write when the engine offers nothing, and it puts the
convention in the HOST instead of here. Twenty-three packages were about to ship
against it, and the twenty-fourth author would have invented a different one,
because nothing anywhere said what the shape was.

That is the same inversion as a host having to hand-write executors for builtin
kinds: work that belongs to the engine, done by everyone who uses it, slightly
differently each time.

## The seam

A node package declares an entry point in the ``fancy_flow.nodes`` group,
pointing at a module that exposes ``register(kinds, executors)``::

    # pyproject.toml
    [project.entry-points."fancy_flow.nodes"]
    stripe = "fancy_stripe.flow"

    # fancy_stripe/flow.py
    def register(kinds: NodeKindRegistry, executors: ExecutorRegistry) -> None:
        for kind in RUNNABLE_KINDS:
            kinds.register(kind)
        for name, fn in EXECUTORS.items():
            executors.bind(name, fn)

**One function doing both is the point, not a convenience.** The failure it
removes is a kind reaching the editor with nothing behind it to run -- authorable,
draggable, and dead at run time. Two separate hooks make that state reachable by
forgetting one; a single seam does not. It is the property PHP gets from
``#[FlowNode]`` discovery, reached differently because Python has no attribute
worth leaning on for this.

Register kinds BEFORE binding executors, as above: ``ExecutorRegistry.bind`` is
alias-aware only for kinds its registry already knows, so binding first silently
skips the alias fan-out and a node saved under its canonical
``@particle-academy/…`` id stops matching a binding made under the bare name.
That exact ordering bug walked a run straight past a human approval gate in the
PHP twin.

## Failures are REPORTED, never swallowed

A discovery pass that catches ``ImportError`` and continues turns "this
connector is broken" into "this connector is absent", and absent reads as fine.
Every failure lands in :attr:`DiscoveryResult.failed` with the exception, and
the caller decides. ``strict=True`` raises instead, for a host that would rather
not boot than boot half-wired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .executors import ExecutorRegistry
    from .registry import NodeKindRegistry

#: The entry-point group node packages declare themselves in.
ENTRY_POINT_GROUP = "fancy_flow.nodes"

#: The attribute a declared module must expose.
REGISTER_ATTR = "register"

__all__ = [
    "ENTRY_POINT_GROUP",
    "REGISTER_ATTR",
    "DiscoveryResult",
    "load_installed_kinds",
]


@dataclass(frozen=True)
class DiscoveryResult:
    """What a discovery pass found, and what it could not load.

    Both halves matter. A host that reports only ``loaded`` cannot tell a
    correctly empty environment from one where every package failed to import.
    """

    #: Entry-point names that registered successfully.
    loaded: tuple[str, ...] = ()

    #: ``name -> exception`` for every package that declared itself and then
    #: could not be loaded or did not expose ``register``.
    failed: dict[str, Exception] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        """One line a host can log or surface without formatting it itself."""
        parts = [f"{len(self.loaded)} node package(s) registered"]

        if self.failed:
            names = ", ".join(sorted(self.failed))
            parts.append(f"{len(self.failed)} FAILED: {names}")

        return "; ".join(parts)


def _group_entry_points() -> list[EntryPoint]:
    # `entry_points(group=...)` is the 3.10+ form. Nothing older is supported
    # by this package, so there is no legacy branch to carry -- a `.get()`
    # fallback here would be dead code pretending to be compatibility.
    return list(entry_points(group=ENTRY_POINT_GROUP))


def load_installed_kinds(
    kinds: NodeKindRegistry | None = None,
    executors: ExecutorRegistry | None = None,
    *,
    strict: bool = False,
) -> DiscoveryResult:
    """Register every installed node package into ``kinds`` and ``executors``.

    :param kinds: the kind catalogue to fill. Defaults to the shared registry.
    :param executors: the executor bindings to fill. Defaults to a fresh
        registry over ``kinds`` -- pass the one the runner will actually use,
        or the bindings go somewhere the run never consults.
    :param strict: raise on the first failure instead of collecting it.

    Returns a :class:`DiscoveryResult` naming what loaded and what did not.
    """
    from .executors import ExecutorRegistry
    from .registry import default_registry

    if kinds is None:
        kinds = default_registry()

    if executors is None:
        executors = ExecutorRegistry(kinds=kinds)

    loaded: list[str] = []
    failed: dict[str, Exception] = {}

    for entry in _group_entry_points():
        try:
            module: Any = entry.load()
            register = getattr(module, REGISTER_ATTR, None)

            if not callable(register):
                raise AttributeError(
                    f'{entry.value!r} declares the "{ENTRY_POINT_GROUP}" entry point '
                    f"but exposes no callable {REGISTER_ATTR}(kinds, executors)."
                )

            register(kinds, executors)
            loaded.append(entry.name)
        except Exception as exc:
            if strict:
                raise

            failed[entry.name] = exc

    return DiscoveryResult(loaded=tuple(loaded), failed=failed)
