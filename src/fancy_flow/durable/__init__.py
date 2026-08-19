"""Durable, resumable runs -- with no queue library anywhere in sight.

The research behind this is written up in the envelope's
``.ai/plans/fancy-flow-py.md``; the short version is that a JSON-graph engine
wants **checkpoint-per-node keyed by node id**, not Temporal-style event-sourced
replay. Replay exists to police arbitrary user code for non-determinism; an
interpreter over a declarative graph is deterministic by construction, so the
sandbox, the history limits and the versioning tax buy nothing. And node-id
keying survives a graph being edited while a run is parked on an approval,
where an ordinal-keyed checkpoint cannot.

So durability lives here, in the pure core:

- :mod:`.state`       what a run remembers, and the claim contract a database implements
- :mod:`.frontier`    which nodes are unblocked, restated from the engine's own rule
- :mod:`.replay`      run one node THROUGH the engine, never around it
- :mod:`.retry`       how many attempts a node gets, per node
- :mod:`.human`       gates that pause and cannot be walked past
- :mod:`.coordinator` the two operations a queue adapter dispatches

A queue adapter supplies transport and nothing else.
"""

from .coordinator import Coordinator, DurableRunResult, NodeOutcome
from .frontier import Frontier, FrontierResult
from .human import DurableApproval, DurableUserInput, NotAwaitingHuman, Submissions
from .replay import BOUNDARY, ReplayResult, is_boundary, replay_up_to
from .retry import UNSAFE_TO_REPLAY, RetryPolicy
from .state import InMemoryClaimStore, NodeClaimStore, NodeRunStatus, NodeState

__all__ = [
    "BOUNDARY",
    "UNSAFE_TO_REPLAY",
    "Coordinator",
    "DurableApproval",
    "DurableRunResult",
    "DurableUserInput",
    "Frontier",
    "FrontierResult",
    "InMemoryClaimStore",
    "NodeClaimStore",
    "NodeOutcome",
    "NodeRunStatus",
    "NodeState",
    "NotAwaitingHuman",
    "ReplayResult",
    "RetryPolicy",
    "Submissions",
    "is_boundary",
    "replay_up_to",
]
