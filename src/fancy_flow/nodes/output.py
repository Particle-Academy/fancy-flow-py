"""Terminal executors -- capture a result, or say something on the feed."""

from __future__ import annotations

from typing import Any

from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from .support import expr

__all__ = ["log", "output"]


def output(ctx: ExecutionContext) -> Any:
    """``output`` -- returns its incoming value so it lands in ``RunResult.outputs``."""
    return ctx.input("in", ctx.inputs)


def log(ctx: ExecutionContext) -> Any:
    """``log`` -- emit the resolved message to the run feed at the configured level."""
    level = str(ctx.option("level", "info"))
    message = expr.text(expr.evaluate(ctx.option("message", ""), ctx.inputs))
    ctx.emit(RunEvent.log(level, message, ctx.node.id))
    return {"logged": message, "level": level}
