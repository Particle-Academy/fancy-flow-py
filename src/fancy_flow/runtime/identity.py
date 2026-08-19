"""Who is running, which step this is, and how many times it has been tried.

Why an engine needs this at all
-------------------------------

A node that WRITES to somebody else's system -- charge a card, send a message,
open a pull request -- can only survive a retry if the retry carries the same
idempotency key the first attempt did. Otherwise the provider treats the second
call as a new request and the customer is charged twice.

Until this existed :class:`~fancy_flow.runtime.context.ExecutionContext` was
``{node, inputs, emit, depth}``, and nothing in it could produce such a key.
Both obvious fallbacks are worse than sending no key at all:

- the **node id alone** is stable across retries, and also across RUNS -- two
  legitimate payments share a key and the provider silently collapses the second
  into the first: a payment that never happened, reported as success;
- a **fresh random value** is unique per run, and also per ATTEMPT -- a retry
  creates a second charge, which is the thing being avoided.

What actually identifies a step
-------------------------------

Not ``(run, node)``. A node legitimately executes more than once inside one run:
once per subflow invocation, once per iteration of a loop an executor drives
itself. ``(run, node)`` would give every one of those the same key, and a
provider would honour exactly one of them.

So a step is identified by the **path of invocations that led to it**, plus an
optional **occurrence** for repetition at the same level::

    runKey ":" segment ("/" segment)*     segment := escape(id) ["#" occurrence]

And the part that is easy to get backwards: **``attempt`` is NOT in the key.**
It is carried here for logging and for :meth:`RunIdentity.is_replay_safe`, and
putting it in the key would restore the exact bug the key exists to prevent.

Pinned cross-runtime by ``shared/flow-run-identity`` in ``fancy-conformance``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = ["RunIdentity", "escape_segment"]


def escape_segment(value: str) -> str:
    """Escape one segment so the composition is injective.

    ``%`` FIRST, or the escaping is not reversible: escaping ``/`` before ``%``
    turns a literal ``a%2Fb`` into the same text as the escaped form of ``a/b``,
    which is the collision this exists to prevent, reintroduced by its own fix.
    """
    return value.replace("%", "%25").replace("/", "%2F").replace("#", "%23")


def _render(value: str, occurrence: int | None) -> str:
    escaped = escape_segment(value)
    # ``occurrence == 0`` is a real occurrence. A truthiness check here silently
    # collapses iteration 0 into the un-iterated key.
    return escaped if occurrence is None else f"{escaped}#{occurrence}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _instant(value: datetime | str) -> float:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"RunIdentity: firstAttemptAt is not a parseable timestamp: {value!r}"
            ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """A run, a position inside it, and how many times this position was tried.

    Immutable. :meth:`descend` returns a new identity rather than mutating, so
    an executor cannot change what its siblings see.
    """

    #: Stable for the whole run: same across retries, resumes, workers and hosts.
    run_key: str
    #: Enclosing invocation segments, outermost first, ALREADY RENDERED. Empty at
    #: the top level; a subflow pushes the invoking node's id.
    path: tuple[str, ...] = ()
    #: 1-based attempt of THIS logical step. Never part of the key. The durable
    #: driver sets it from the node's claim row, which is exact; a plain
    #: :class:`FlowRunner` call gets whatever the host passed, which is
    #: run-scoped and therefore conservative.
    attempt: int = 1
    #: ISO-8601 UTC instant of attempt 1 of this step.
    first_attempt_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not self.run_key or not self.run_key.strip():
            raise ValueError("RunIdentity: run_key must be a non-empty string.")
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "attempt", max(1, int(self.attempt)))

    def step_key(self, node_id: str, occurrence: int | None = None) -> str:
        """The identity of one execution of one node.

        Stable across retries of that execution, distinct from every other
        execution of the same node. Pass ``occurrence`` when an executor runs
        the same node more than once at the same level (a loop body, one item of
        a fan-out it drives itself).
        """
        return f"{self.run_key}:{'/'.join((*self.path, _render(node_id, occurrence)))}"

    def descend(self, segment: str, occurrence: int | None = None) -> RunIdentity:
        """A child identity for work nested inside this step.

        ``subflow`` pushes the invoking node's id, so a node inside the child
        graph cannot collide with a same-named node in the parent. Attempt and
        ``first_attempt_at`` are carried down unchanged: the nested work happens
        inside this step's attempt, and shares its clock.
        """
        return RunIdentity(
            self.run_key,
            (*self.path, _render(segment, occurrence)),
            self.attempt,
            self.first_attempt_at,
        )

    def with_attempt(self, attempt: int, first_attempt_at: str | None = None) -> RunIdentity:
        """A copy on a different attempt, first-attempt clock preserved."""
        return RunIdentity(
            self.run_key,
            self.path,
            attempt,
            first_attempt_at if first_attempt_at is not None else self.first_attempt_at,
        )

    def is_replay_safe(
        self, window_seconds: float | None, now: datetime | str | None = None
    ) -> bool:
        """May this attempt reuse the step key and still be deduplicated?

        Providers forget idempotency keys -- Stripe after 24 hours. Past that
        window, resending the key creates a second charge and sending a fresh
        one creates a second charge, so **the caller must refuse rather than
        pick between them**: a loud stuck run beats a silent double write.

        ``True`` on attempt 1 whatever the elapsed time -- nothing was sent on
        an earlier attempt, so there is nothing for the provider to have
        forgotten. That is what lets a run park on a human gate for a week and
        then write.

        ``window_seconds=None`` means the provider does not expire keys. ``0``
        means it does not dedupe at all, so no retry may reuse a key -- it is a
        real window, not an absent one, and reading ``0`` as ``None`` turns
        "this provider does not dedupe" into "this provider dedupes forever".
        """
        if self.attempt <= 1:
            return True
        if window_seconds is None:
            return True
        if window_seconds <= 0:
            return False

        now_ts = _instant(now) if now is not None else datetime.now(UTC).timestamp()
        # Clock skew between two workers must not turn a legitimate retry into a
        # refusal, so a negative elapsed clamps to zero.
        elapsed = max(0.0, now_ts - _instant(self.first_attempt_at))

        # Inclusive: a key written at T is remembered THROUGH T + window.
        return elapsed <= window_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "runKey": self.run_key,
            "path": list(self.path),
            "attempt": self.attempt,
            "firstAttemptAt": self.first_attempt_at,
        }

    @staticmethod
    def from_value(value: RunIdentity | dict[str, Any] | str) -> RunIdentity:
        """Rebuild from a queue payload, or promote a bare run key."""
        if isinstance(value, RunIdentity):
            return value
        if isinstance(value, str):
            return RunIdentity(value)
        return RunIdentity(
            str(value.get("runKey", "")),
            tuple(value.get("path", ())),
            int(value.get("attempt", 1)),
            str(value["firstAttemptAt"]) if value.get("firstAttemptAt") else _now_iso(),
        )
