"""Run inputs and outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .abort import AbortSignal
from .events import RunEvent
from .identity import RunIdentity

__all__ = ["RunOptions", "RunResult"]


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Options for a single run.

    :param timeout_ms: stop the run after this many milliseconds. ``None``
        disables it.
    :param signal: cooperative cancellation, checked before each node.
    :param initial_inputs: inputs seeded to entry nodes, keyed by node id then
        port.
    :param props: values for the inputs the GRAPH declares, passed BY NAME.
        ``initial_inputs`` is keyed by node id, so a caller had to know the
        trigger was called ``t`` and a rename broke every caller while the
        graph stayed valid. These are checked against the graph's own
        declaration, so a misspelling fails the run instead of sitting unread.
    :param resume_outputs: outputs of nodes already completed in a prior run,
        keyed by node id. Such a node is NOT re-executed — its stored output is
        republished on its ports, reproducing the same routing. This is the
        primitive durable resume is built on, and the reason a per-node queue
        driver never has to re-implement routing.
    :param entry_nodes: which ENTRY POINTS are live -- the ids of nodes with NO
        incoming edges this run should start from. ``None`` means unset and
        every entry point runs, exactly as before the option existed.

        A graph may hold more than one trigger -- a ``manual_trigger`` for
        hand-testing beside the event trigger that runs it for real -- and a
        trigger has no inbound edges, which IS the readiness rule. So without
        this, every trigger's branch runs on every run, whichever one fired.
        The triggers themselves are harmless; everything DOWNSTREAM of the ones
        that did not fire is not. A ``user_input`` stranded on the manual branch
        parks an event-driven run to ask a person for data the event already
        supplied, which from outside looks like the event trigger being ignored.

        Naming the live entry points makes the others INACTIVE, and the existing
        "at least one active inbound edge" rule then skips everything reachable
        only from them. No new routing logic.

        Three edges, each pinned by ``flow/entry-points`` in
        ``fancy-conformance``: ``None`` is NOT ``[]`` (unset runs every entry
        point; an empty list says none is live and runs nothing); a node
        reachable from SEVERAL entry points still runs when any one fires; and
        naming a node that HAS inbound edges names no entry point, so nothing
        runs -- validate your ids if you want a typo to be loud, because the
        runtime cannot tell one from a deliberate empty selection.
    :param depth: how deep this run is nested. ``subflow`` passes ``depth + 1``
        to the child graph it runs, so runaway recursion is reported BY NAME
        rather than as a RecursionError from somewhere unrelated.
    :param run: who is running, so a writing node can derive a stable
        idempotency key. **Deliberately not defaulted:** a key minted per call
        would change on every whole-run retry, which is exactly the failure an
        idempotency key exists to prevent -- so a host that has not supplied one
        gets ``ctx.run is None`` and a connector that declines to write blind,
        rather than a plausible-looking key that double-charges.
    """

    timeout_ms: int | None = None
    signal: AbortSignal | None = None
    initial_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    props: dict[str, Any] = field(default_factory=dict)
    resume_outputs: dict[str, Any] = field(default_factory=dict)
    entry_nodes: tuple[str, ...] | list[str] | None = None
    depth: int = 0
    run: RunIdentity | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """The result of a run.

    ``events`` is retained in full (the peer runtimes make it opt-in) so a
    caller that passed no ``on_event`` sink can still inspect the stream after
    the fact — which is what the per-node driver reads activated ports from.
    """

    ok: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    events: tuple[RunEvent, ...] = ()

    def output(self, node_id: str, default: Any = None) -> Any:
        return self.outputs.get(node_id, default)
