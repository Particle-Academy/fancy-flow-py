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
    :param resume_outputs: outputs of nodes already completed in a prior run,
        keyed by node id. Such a node is NOT re-executed — its stored output is
        republished on its ports, reproducing the same routing. This is the
        primitive durable resume is built on, and the reason a per-node queue
        driver never has to re-implement routing.
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
    resume_outputs: dict[str, Any] = field(default_factory=dict)
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
