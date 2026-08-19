"""The human-pause contract.

A workflow waiting for a person is not an error, but it travels the same
channel as one: the executor aborts, the runner records a reason string, and
something downstream decides whether that string meant "failed" or "waiting".

**The wire format is byte-identical to the PHP and TypeScript twins on
purpose.** The same string is produced by a node running on any runtime and
decoded by a runner on any runtime — which is what lets a consumer author in
TypeScript, execute on Python, and resume from a PHP admin screen without the
pause semantics quietly diverging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

__all__ = ["Pause", "PauseSignal"]


@dataclass(frozen=True, slots=True)
class PauseSignal:
    """A run halted, waiting for a person.

    :param node_id: the node that paused — where a submission is injected on
        resume.
    :param awaiting: what is being waited for. ``approval`` and ``input`` are
        what the builtins emit, but the value is open: a marketplace node may
        define its own (``signature``, ``payment``), and a runner that does not
        recognise one should surface it rather than guess.
    :param detail: kind-supplied context for whoever renders the wait — a form
        schema, the question, a diff to approve. Must be JSON-serialisable: it
        crosses a queue boundary and a database column.
    """

    node_id: str
    awaiting: str
    detail: Any = None

    @property
    def is_approval(self) -> bool:
        return self.awaiting == "approval"

    @property
    def is_input(self) -> bool:
        return self.awaiting == "input"


class Pause:
    """Encode and decode the reason string that marks a pause."""

    PREFIX: Final = "fancy-flow:pause:"

    #: Prefixes shipped before the contract existed, kept decodable forever.
    #: They are sitting in the ``error`` column of every run that paused under
    #: an older version, and a resume path that only works for new runs is not
    #: a resume path — it strands everything already in flight.
    LEGACY_PREFIXES: Final = {
        "awaiting-approval:": "approval",
        "awaiting-input:": "input",
    }

    @staticmethod
    def encode(signal: PauseSignal) -> str:
        """The reason string an executor aborts with.

        The payload is JSON rather than delimited fields because a node id may
        contain a colon, and a positional encoding that breaks on user data is
        the kind of bug that only ever shows up in someone else's graph.

        A ``None`` detail is omitted, matching PHP. TypeScript distinguishes an
        absent detail from an explicitly-null one; PHP and Python have one
        absent value, so the round trip is lossy in exactly that one direction
        (a TS-encoded ``"detail":null`` decodes to ``None`` here and simply
        re-encodes as absent).
        """
        payload: dict[str, Any] = {"nodeId": signal.node_id, "awaiting": signal.awaiting}
        if signal.detail is not None:
            payload["detail"] = signal.detail
        return Pause.PREFIX + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def decode(reason: str | None) -> PauseSignal | None:
        """Decode a run's error reason into a pause, or ``None`` for a real failure.

        This is the whole contract from a runner's side: call it on
        ``result.error``, and if it returns non-``None``, persist the run as
        waiting on ``signal.node_id`` instead of failing it.
        """
        if reason is None:
            return None

        if reason.startswith(Pause.PREFIX):
            body = reason[len(Pause.PREFIX) :]
            try:
                parsed = json.loads(body)
            except ValueError:
                return None
            # A malformed payload is a corrupt pause, not something to invent a
            # node id for.
            if (
                not isinstance(parsed, dict)
                or not isinstance(parsed.get("nodeId"), str)
                or not isinstance(parsed.get("awaiting"), str)
            ):
                return None
            return PauseSignal(parsed["nodeId"], parsed["awaiting"], parsed.get("detail"))

        for prefix, awaiting in Pause.LEGACY_PREFIXES.items():
            if reason.startswith(prefix):
                return PauseSignal(reason[len(prefix) :], awaiting)

        return None

    @staticmethod
    def is_pause(reason: str | None) -> bool:
        return Pause.decode(reason) is not None
