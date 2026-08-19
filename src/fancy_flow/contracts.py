"""The seams a host plugs into.

Structural :class:`typing.Protocol` rather than nominal base classes, because a
host's existing service should be usable as an executor without inheriting from
us. That is the same freedom the PHP twin gets from accepting a callable, an
interface implementation, or a class-string interchangeably.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .runtime.context import ExecutionContext

__all__ = ["NodeExecutor", "Resolver", "TriggerGuard"]


@runtime_checkable
class NodeExecutor(Protocol):
    """Behaviour for one node kind.

    An executor may equally be a plain callable taking the context; this
    protocol exists for the class-shaped case, which is what constructor
    injection wants.
    """

    def execute(self, ctx: ExecutionContext) -> Any:  # pragma: no cover - protocol
        ...


@runtime_checkable
class Resolver(Protocol):
    """Turns a class into an instance.

    The default calls the class with no arguments. A host with a DI container
    supplies its own so executors get constructor injection -- the analogue of
    the PHP twin's ``ContainerResolver``.
    """

    def make(self, cls: type) -> Any:  # pragma: no cover - protocol
        ...


@runtime_checkable
class TriggerGuard(Protocol):
    """The precondition a queued cohort run re-checks just before it starts.

    Fails CLOSED by design: when the guard cannot answer, the run does not
    start. Several runs fired by one event are serialized, and each re-asks
    whether it still should happen -- because by the time the third one is
    picked up, the first two may have made it wrong.
    """

    def should_run(self, run_key: str, context: dict[str, Any]) -> bool:  # pragma: no cover
        ...


class NativeResolver:
    """The default :class:`Resolver` -- constructs with no arguments."""

    def make(self, cls: type) -> Any:
        return cls()
