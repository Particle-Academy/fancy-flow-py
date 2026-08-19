"""Human gates that cannot be walked past.

The framework-free executors in :mod:`fancy_flow.nodes.human` are
pass-throughs, so a graph can be exercised offline. These are their durable
replacements: they PAUSE the run, and a recorded answer -- not an input value --
is what resumes it.

Fail closed, and why
--------------------

A gate pauses because it **is** a human node, not because its input port happens
to be empty. This is not a preference; it is a fix. Both peer runtimes once
decided whether to pause by reading their own input, so a pre-filled ``values``
or ``approved`` value ran the flow straight past the person it was waiting for
-- silently, with the run reporting success.

Restoring the old behaviour is possible, explicit, and per node:
``autoAnswerFromInput``. Turn it on for a step that is a form when a human is
present and a pass-through when an upstream node already produced the answer.
On an approval node, weigh it harder: it means the graph, not a person, can
approve.

The other half of the fix
-------------------------

Recording an answer for a node the run is not parked on RAISES rather than
queueing a write nobody reads. See :meth:`Submissions.record`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..exceptions import FlowError
from ..nodes.support import expr
from ..runtime.context import ExecutionContext
from ..runtime.ports import Port

__all__ = ["DurableApproval", "DurableUserInput", "NotAwaitingHuman", "Submissions"]


class NotAwaitingHuman(FlowError):  # noqa: N818 - a condition, not an "Error"
    """An answer was recorded for a node the run is not waiting on."""


@dataclass(slots=True)
class Submissions:
    """Answers recorded for human gates, keyed by node id.

    Deliberately separate from the run's inputs. Keeping them in one bag is
    precisely what let a pre-filled input satisfy a gate.
    """

    _answers: dict[str, Any] = field(default_factory=dict)
    #: The node the run is currently parked on, if any.
    awaiting: str | None = None

    def record(self, node_id: str, value: Any) -> None:
        """Record an answer for the node the run is parked on.

        Raises when the run is not waiting on that node. A queued answer for a
        node that never paused is a write nobody reads -- and it looks, from the
        outside, exactly like a submission that worked.
        """
        if self.awaiting is not None and self.awaiting != node_id:
            raise NotAwaitingHuman(
                f"This run is waiting on {self.awaiting!r}, not {node_id!r}. "
                "Recording an answer for a node the run is not parked on would be "
                "stored and never read."
            )
        if self.awaiting is None:
            raise NotAwaitingHuman(
                f"This run is not waiting for anyone, so an answer for {node_id!r} has "
                "nothing to resume."
            )
        self._answers[node_id] = value
        self.awaiting = None

    def answered(self, node_id: str) -> bool:
        return node_id in self._answers

    def answer(self, node_id: str) -> Any:
        return self._answers.get(node_id)

    def park(self, node_id: str) -> None:
        self.awaiting = node_id


class DurableUserInput:
    """``user_input`` -- pauses until a submission for THIS node is recorded."""

    def __init__(self, submissions: Submissions) -> None:
        self._submissions = submissions

    def execute(self, ctx: ExecutionContext) -> Any:
        if self._submissions.answered(ctx.node.id):
            return self._submissions.answer(ctx.node.id)

        if ctx.option("autoAnswerFromInput", False) is True:
            values = ctx.inputs.get("values")
            if values is not None:
                return values

        self._submissions.park(ctx.node.id)
        ctx.pause_for_human(
            "input",
            {"title": ctx.option("title", "Need your input"), "fields": ctx.option("fields", [])},
        )


class DurableApproval:
    """``human_approval`` -- pauses until a decision for THIS node is recorded."""

    def __init__(self, submissions: Submissions) -> None:
        self._submissions = submissions

    def execute(self, ctx: ExecutionContext) -> Any:
        if self._submissions.answered(ctx.node.id):
            approved = expr.truthy(self._submissions.answer(ctx.node.id))
            return Port.branch("approved" if approved else "denied", ctx.input("in", ctx.inputs))

        if ctx.option("autoAnswerFromInput", False) is True:
            decision = ctx.inputs.get("approved")
            if decision is not None:
                return Port.branch(
                    "approved" if expr.truthy(decision) else "denied",
                    ctx.input("in", ctx.inputs),
                )

        self._submissions.park(ctx.node.id)
        ctx.pause_for_human(
            "approval",
            {
                "title": ctx.option("title", "Approve action"),
                "description": ctx.option("description"),
            },
        )
