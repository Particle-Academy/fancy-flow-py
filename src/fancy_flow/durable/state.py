"""What a durable run remembers, and the seam a real database plugs into.

A durable run is bookkeeping plus one hard requirement: **the claim is a unique
constraint, not a check.** Two workers racing for the same node must produce a
no-op, not a double run, and only the storage layer can promise that. So
:class:`NodeClaimStore` is a Protocol with exactly the operations a driver
needs, and an adapter implements it over ``INSERT ... ON CONFLICT DO NOTHING``
(or its dialect's spelling).

:class:`InMemoryClaimStore` is the reference implementation and is genuinely
useful: it makes the whole per-node driver testable, and it is correct for a
single-process durable run.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

__all__ = ["InMemoryClaimStore", "NodeClaimStore", "NodeRunStatus", "NodeState"]


class NodeRunStatus:
    """Where one node of one run has got to."""

    CLAIMED: Final = "claimed"
    COMPLETED: Final = "completed"
    SKIPPED: Final = "skipped"
    FAILED: Final = "failed"
    PAUSED: Final = "paused"

    #: A node the frontier may treat as decided. A FAILED node is settled too --
    #: it will never publish, so its successors skip rather than wait forever.
    SETTLED: Final = (COMPLETED, SKIPPED, FAILED)


@dataclass(slots=True)
class NodeState:
    """One node's row.

    ``ports`` are the ports the engine's own ``node-output`` events reported.
    They are STORED, never recomputed: a second copy of the routing table would
    agree for a year and then disagree on one branch.
    """

    status: str
    ports: tuple[str, ...] = ()
    output: Any = None
    error: str | None = None
    owner: str | None = None
    attempts: int = 0


@runtime_checkable
class NodeClaimStore(Protocol):
    """The persistence a per-node driver needs.

    Six operations. An adapter over Postgres, SQLite or Redis implements these
    and nothing else; every rule about WHICH node may run lives in
    :mod:`fancy_flow.durable.frontier`, which reads only :meth:`state`.
    """

    def claim(self, run_key: str, node_id: str, owner: str) -> bool:
        """Take exclusive ownership of one node of one run.

        MUST be atomic against concurrent callers, and MUST return ``True`` for
        a caller re-entering its OWN claim -- that is what lets a job's retry
        resume instead of deadlocking against the row it wrote itself.
        """
        ...  # pragma: no cover - protocol

    def state(self, run_key: str) -> dict[str, NodeState]: ...  # pragma: no cover

    def complete(
        self, run_key: str, node_id: str, output: Any, ports: tuple[str, ...]
    ) -> None: ...  # pragma: no cover

    def skip(self, run_key: str, node_id: str) -> None: ...  # pragma: no cover

    def fail(self, run_key: str, node_id: str, error: str) -> None: ...  # pragma: no cover

    def pause(self, run_key: str, node_id: str, reason: str) -> None: ...  # pragma: no cover


@dataclass(slots=True)
class InMemoryClaimStore:
    """A correct, single-process :class:`NodeClaimStore`.

    Guarded by a lock so a thread-pool driver is safe. It is NOT durable across
    a restart, which is the honest limit: use it for tests, for a CLI run, and
    for a worker that genuinely owns the whole run.
    """

    _runs: dict[str, dict[str, NodeState]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self, run_key: str, node_id: str, owner: str) -> bool:
        with self._lock:
            run = self._runs.setdefault(run_key, {})
            existing = run.get(node_id)

            if existing is None:
                run[node_id] = NodeState(NodeRunStatus.CLAIMED, owner=owner, attempts=1)
                return True

            # Re-entering our own claim is how a retry gets back in. Anything
            # else -- another owner, or a settled node -- is a lost race, and a
            # lost race is a NO-OP rather than a duplicate run.
            own_claim = (
                existing.status in (NodeRunStatus.CLAIMED, NodeRunStatus.PAUSED)
                and existing.owner == owner
            )
            if own_claim:
                existing.attempts += 1
                existing.status = NodeRunStatus.CLAIMED
                return True
            return False

    def state(self, run_key: str) -> dict[str, NodeState]:
        with self._lock:
            return {node_id: _copy(entry) for node_id, entry in self._runs.get(run_key, {}).items()}

    def complete(self, run_key: str, node_id: str, output: Any, ports: tuple[str, ...]) -> None:
        with self._lock:
            entry = self._entry(run_key, node_id)
            entry.status = NodeRunStatus.COMPLETED
            entry.output = output
            entry.ports = tuple(ports)
            entry.error = None

    def skip(self, run_key: str, node_id: str) -> None:
        with self._lock:
            entry = self._entry(run_key, node_id)
            entry.status = NodeRunStatus.SKIPPED
            entry.ports = ()

    def fail(self, run_key: str, node_id: str, error: str) -> None:
        with self._lock:
            entry = self._entry(run_key, node_id)
            entry.status = NodeRunStatus.FAILED
            entry.error = error
            entry.ports = ()

    def pause(self, run_key: str, node_id: str, reason: str) -> None:
        with self._lock:
            entry = self._entry(run_key, node_id)
            entry.status = NodeRunStatus.PAUSED
            entry.error = reason
            entry.ports = ()

    def _entry(self, run_key: str, node_id: str) -> NodeState:
        """The row for one node, created as CLAIMED if a driver never claimed it."""
        return self._runs.setdefault(run_key, {}).setdefault(
            node_id, NodeState(NodeRunStatus.CLAIMED)
        )

    def release(self, run_key: str, node_id: str) -> None:
        """Drop a paused node's claim so a recorded answer can re-run it.

        Not part of the protocol: resuming a human gate is the host's decision
        and its storage's business. Provided here because the in-memory store is
        also what the tests resume through.
        """
        with self._lock:
            self._runs.get(run_key, {}).pop(node_id, None)


def _copy(entry: NodeState) -> NodeState:
    return NodeState(
        status=entry.status,
        ports=entry.ports,
        output=entry.output,
        error=entry.error,
        owner=entry.owner,
        attempts=entry.attempts,
    )
