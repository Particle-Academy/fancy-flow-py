"""Trigger executors -- the entry points a run starts from.

A trigger does not decide WHEN it fires; the host does (a click, an inbound
request, a scheduler tick). What it owns is the shape of the payload that
reaches the rest of the graph.
"""

from __future__ import annotations

from typing import Any

from ..runtime.context import ExecutionContext

__all__ = ["manual_trigger", "schedule_trigger", "webhook_trigger"]


def manual_trigger(ctx: ExecutionContext) -> Any:
    """``manual_trigger`` -- passes the seeded payload straight through on ``out``."""
    return ctx.inputs


def webhook_trigger(ctx: ExecutionContext) -> Any:
    """``webhook_trigger`` -- emits the request payload.

    Seeded under ``payload`` when the host separates the body from its
    envelope, otherwise the whole seed.
    """
    payload = ctx.inputs.get("payload")
    return ctx.inputs if payload is None else payload


def schedule_trigger(ctx: ExecutionContext) -> Any:
    """``schedule_trigger`` -- the schedule context merged with any seeded payload.

    The seed wins on a key collision, which is what lets a host inject the tick
    it actually fired for.
    """
    out: dict[str, Any] = {
        "cron": ctx.option("cron"),
        "timezone": ctx.option("timezone", "UTC"),
    }
    if isinstance(ctx.inputs, dict):
        out.update(ctx.inputs)
    return out
