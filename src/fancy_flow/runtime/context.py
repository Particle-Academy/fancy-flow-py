"""Everything an executor gets when it runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

from ..exceptions import RunAborted
from ..schema.graph import FlowNode
from .events import RunEvent
from .identity import RunIdentity
from .pause import Pause, PauseSignal
from .terminal import TerminalAccess

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
    - ``terminal`` the terminal this node's lane owns, or ``None`` outside one.
      Both its members are callables, so the terminal opens on first USE -- a
      node inside a terminal lane that never touches it spawns no process.
    """

    __slots__ = ("_emit", "depth", "executors", "inputs", "node", "run", "terminal")

    def __init__(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        emit: Callable[[RunEvent], None],
        depth: int = 0,
        run: RunIdentity | None = None,
        executors: Any = None,
        terminal: TerminalAccess | None = None,
    ) -> None:
        self.node = node
        self.inputs = inputs
        self._emit = emit
        self.depth = depth
        self.run = run
        # The registry THIS run is executing against, handed down so an
        # executor that starts a NESTED run gives the child the same executors
        # as the parent. ``subflow`` previously fell back to the bare builtins,
        # so a host kind resolved at top level and vanished one level down, and
        # a host that had REPLACED a builtin got the package's version in the
        # child. Same graph, different behaviour by nesting depth.
        self.executors = executors
        # The terminal this node's lane owns, or None when it is not inside a
        # terminal lane. None is a REAL answer: a terminal node outside a lane
        # must say so rather than quietly opening a shell of its own.
        self.terminal = terminal

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

        A port BOUND to ``None`` is NOT an absent port, and only the absent one
        falls back.

        This used to say the opposite -- that absent and ``None`` both yield
        ``default``, matching the peers' ``??`` -- and it was a faithful port of
        a defect. Eleven executors read ``ctx.input("in", ctx.inputs)``, whose
        default is the whole inputs map, so a port holding ``None`` yielded
        every input the node had rather than ``None``. The substitute is
        PLAUSIBLE, which is what makes it worse than a visibly-odd one: an
        inputs map looks exactly like real data, so a downstream node reads
        fields from the wrong place and nothing looks wrong anywhere.

        The fallback itself is right and stays: a trigger has no ``in`` edge,
        and "the ``in`` port, or everything if there is no ``in`` port" is what
        lets an entry node read its seeded payload.

        The rule, which cost four instances in one day to learn: **``??`` (and
        ``is None``, and ``unwrap_or``) is safe only where null is not a legal
        value.** Where it is, key presence is the only correct test.
        """
        # dict.get already has exactly the right semantics: it returns a
        # STORED None and falls back only when the key is absent. It was the
        # explicit `is None` check layered on top that was wrong.
        return self.inputs.get(port, default)

    def config(self) -> dict[str, Any]:
        """The node's resolved config."""
        return self.node.config

    def option(self, key: str, default: Any = None) -> Any:
        """Read one config key, with the same ``??`` semantics as :meth:`input`."""
        return self.node.option(key, default)
