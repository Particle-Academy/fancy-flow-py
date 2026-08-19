"""Everything an executor gets when it runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

from ..exceptions import RunAborted
from ..schema.graph import FlowNode
from .events import RunEvent
from .identity import RunIdentity
from .pause import Pause, PauseSignal

__all__ = ["ExecutionContext"]


class ExecutionContext:
    """The executor's handle on the run.

    - ``node``   the node being executed (id, kind, config, ports).
    - ``inputs`` values arriving on each input port, keyed by port id (the
      default port is ``in``), merged over any seeded initial inputs.
    - ``abort()`` stops the whole run.
    - ``emit()``  streams a :class:`RunEvent` to the run's event sink.
    - ``run``    who is running, and which attempt of which step this is.
      ``ctx.run.step_key(ctx.node.id)`` is the idempotency key for a node that
      writes to somebody else's system -- stable across retries of this step,
      distinct for every other execution of the same node. ``None`` when the
      host supplied no identity, and that is a real answer: a write with no key
      must decline or accept one attempt, never invent a key. See
      :class:`~fancy_flow.runtime.identity.RunIdentity`.
    """

    __slots__ = ("_emit", "depth", "inputs", "node", "run")

    def __init__(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        emit: Callable[[RunEvent], None],
        depth: int = 0,
        run: RunIdentity | None = None,
    ) -> None:
        self.node = node
        self.inputs = inputs
        self._emit = emit
        self.depth = depth
        self.run = run

    def abort(self, reason: str | None = None) -> NoReturn:
        """Stop the run. Raises :class:`RunAborted`; the runner records the reason."""
        raise RunAborted(reason or "aborted")

    def pause_for_human(self, awaiting: str, detail: Any = None) -> NoReturn:
        """Halt the run to wait for a person.

        Node authors should reach for this rather than hand-encoding a reason,
        so the format stays ours to change::

            values = ctx.inputs.get("values")
            if values is None:
                ctx.pause_for_human("input", {"fields": fields})

        Note the ``is None`` check rather than a truthiness test — an empty
        submission (``{}`` / ``[]``) is a real answer and must resume. A
        truthiness test pauses forever on an empty form.
        """
        self.abort(Pause.encode(PauseSignal(self.node.id, awaiting, detail)))

    def emit(self, event: RunEvent) -> None:
        """Stream a status update or partial output to the run feed."""
        self._emit(event)

    def input(self, port: str = "in", default: Any = None) -> Any:
        """Read one input port's value.

        Absent AND ``None`` both yield ``default`` — the peer runtimes spell
        this ``??``, and matching them here is what keeps a graph's behaviour
        identical when a dead branch contributes nothing.
        """
        value = self.inputs.get(port)
        return default if value is None else value

    def config(self) -> dict[str, Any]:
        """The node's resolved config."""
        return self.node.config

    def option(self, key: str, default: Any = None) -> Any:
        """Read one config key, with the same ``??`` semantics as :meth:`input`."""
        return self.node.option(key, default)
