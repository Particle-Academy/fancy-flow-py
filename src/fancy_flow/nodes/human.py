"""Human-gate executors.

``user_input`` and ``human_approval`` are the two places a workflow stops for a
person. The framework-free defaults here are **pass-throughs**, exactly as in
the PHP twin: they let a graph be exercised end to end offline. A durable host
replaces them with the pausing variants in
:mod:`fancy_flow.durable.human` -- and *that* is where the fail-closed rule
lives, because only a durable runner has somewhere to park.

The rule, restated so nobody re-derives it wrongly: a gate pauses because it
**is** a human node, not because its input port happens to be empty. Pre-filled
inputs -- initial inputs, an upstream edge, a submission recorded before the
node ran -- never satisfy the gate. Only a recorded answer for *that node* does.
"""

from __future__ import annotations

from typing import Any

from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.ports import Port
from .support import expr
from .support.clients import Notifier

__all__ = ["Notify", "human_approval", "user_input"]


def user_input(ctx: ExecutionContext) -> Any:
    """``user_input`` -- offline default: treat the incoming values as the submission."""
    values = ctx.inputs.get("values")
    return ctx.input("in", ctx.inputs) if values is None else values


def human_approval(ctx: ExecutionContext) -> Any:
    """``human_approval`` -- offline default: read an ``approved`` flag, defaulting to yes."""
    decision = ctx.inputs.get("approved")
    approved = True if decision is None else expr.truthy(decision)
    return Port.branch("approved" if approved else "denied", ctx.input("in", ctx.inputs))


class Notify:
    """``notify`` -- send a message through a host :class:`Notifier`."""

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    def execute(self, ctx: ExecutionContext) -> Any:
        channel = str(ctx.option("channel", "slack"))
        to = str(ctx.option("to", ""))
        message = expr.text(expr.evaluate(ctx.option("message", ""), ctx.inputs))

        self._notifier.notify(channel, to, message)
        ctx.emit(RunEvent.log("info", f"notify -> {channel}:{to}", ctx.node.id))

        return {"sent": True, "channel": channel, "to": to, "message": message}
