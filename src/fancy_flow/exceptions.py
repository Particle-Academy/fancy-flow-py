"""Every error this package raises.

The hierarchy is deliberately shallow. A host catches :class:`FlowError` to
mean "fancy-flow said no", and the two subclasses below are the only
distinctions the runtime itself makes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle: schema imports nothing here
    from .schema.issues import ImportIssue

__all__ = ["FlowError", "RunAborted", "UnsafeGraph"]


class FlowError(Exception):
    """Base class for everything fancy-flow raises."""


# N818 wants an `Error` suffix. These names are the peer runtimes' own
# (`RunAborted`, `UnsafeGraph`), and a reader moving between the three
# implementations should meet the same word.
class RunAborted(FlowError):  # noqa: N818
    """An executor called ``ctx.abort()``, or a host tripped the abort signal.

    Not necessarily a failure: a human gate pauses by aborting with an encoded
    reason (see :mod:`fancy_flow.runtime.pause`), which is why the runner
    records the reason string rather than deciding what it meant.
    """

    def __init__(self, reason: str = "aborted") -> None:
        super().__init__(reason)
        self.reason = reason


class UnsafeGraph(FlowError):  # noqa: N818
    """A graph failed :class:`fancy_flow.security.GraphPolicy`.

    Carries every issue, not the first: a caller fixing a rejected graph wants
    the whole list, and a validator that reveals one problem per attempt turns
    a five-minute fix into five round trips.
    """

    def __init__(self, issues: list[ImportIssue]) -> None:
        self.issues = list(issues)
        joined = "; ".join(issue.message for issue in self.issues)
        super().__init__(f"The graph was refused: {joined}")
