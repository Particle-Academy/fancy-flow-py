"""How many of ONE run's nodes may be held at once, and which ready nodes go next.

Serial is the default
---------------------

A queued run hands out **one node at a time**: a node is dispatched only once
the node before it has settled, in the graph's own declaration order. Handing
out the whole ready frontier at once is something a host ASKS for, with
``max_concurrent=UNLIMITED_CONCURRENCY`` or a positive cap.

It used to be the other way round -- :meth:`Coordinator.advance` returned every
ready node -- and several nodes of one run sitting on the queue together is
exactly the condition the 0.22.1 sibling-order bug needed. "What ran, in what
order" also has to be the same answer on every run of the same graph, and a
frontier racing across workers cannot give it.

The limit
---------

=========================  ==================================================
value                      meaning
=========================  ==================================================
``1`` (the default)        serial: one node of the run held at a time
``N >= 1``                 up to N held at once
``UNLIMITED_CONCURRENCY``  ``0``: the whole ready frontier
anything else              refused, naming ``max_concurrent``
=========================  ==================================================

A negative number is refused rather than read as unlimited. Under a serial
default, a typo that silently turned a run parallel is the one failure this must
not have. ``True`` is refused too, even though ``True == 1``: a flag is not a
count.

Held means CLAIMED or PAUSED
----------------------------

A node parked on a person keeps its slot. This coordinator does not park the
RUN on a pause, so without that a queue adapter calling :meth:`advance` when
another job settled would hand out the gate's siblings while the person is still
deciding.

The budget is measured against work ALREADY HELD, never the size of one batch.
Two nodes settling at once each trigger an advance on a real queue, and a
per-batch cap would let each dispatch its own quota.

Pinned by ``flow/durable-dispatch`` in fancy-conformance, which the PHP and
TypeScript coordinators run against their own frontier and selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from .state import NodeRunStatus, NodeState

__all__ = ["UNLIMITED_CONCURRENCY", "select_dispatch"]

#: Dispatch the whole ready frontier. Named so a host never writes a bare ``0``.
UNLIMITED_CONCURRENCY: Final = 0


def check_max_concurrent(value: object) -> int:
    """Return ``value`` when it is a valid limit, and refuse it by name otherwise.

    Raises :class:`TypeError` for anything that is not an ``int`` -- ``bool``
    included -- and :class:`ValueError` for a negative one.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "max_concurrent must be an int: a positive cap, or UNLIMITED_CONCURRENCY (0) "
            f"for the whole ready frontier; got {value!r} ({type(value).__name__})."
        )
    if value < 0:
        raise ValueError(
            "max_concurrent must be a positive cap, or UNLIMITED_CONCURRENCY (0) for the "
            f"whole ready frontier; got {value}. A negative limit is refused rather than "
            "read as unlimited."
        )
    return value


def select_dispatch(
    ready: Sequence[str], state: Mapping[str, NodeState], max_concurrent: int
) -> list[str]:
    """The ready nodes that may be dispatched now, in the order given.

    ``ready`` is :attr:`FrontierResult.ready`, already in declaration order, and
    this never reorders it. ``state`` is the run's node state as it stands after
    the frontier's skips were settled; a skipped node is never held.

    ``UNLIMITED_CONCURRENCY`` returns all of ``ready``. Otherwise the first
    ``max_concurrent - held`` ids, where ``held`` counts CLAIMED and PAUSED
    nodes, and never fewer than none.
    """
    limit = check_max_concurrent(max_concurrent)
    if limit == UNLIMITED_CONCURRENCY:
        return list(ready)

    held = sum(1 for entry in state.values() if entry.status in NodeRunStatus.HELD)
    return list(ready[: max(0, limit - held)])
