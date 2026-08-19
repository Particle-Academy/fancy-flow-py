"""Cooperative cancellation — the analogue of the DOM ``AbortSignal``.

The runner checks the signal before each node. Python has no ambient
cancellation for synchronous code, so a signal is tripped either from inside an
executor (through the shared controller) or from another thread; the async
runner additionally honours :class:`asyncio.CancelledError` naturally.
"""

from __future__ import annotations

__all__ = ["AbortController", "AbortSignal"]


class AbortSignal:
    """A flag the runner polls between nodes."""

    __slots__ = ("_aborted", "reason")

    def __init__(self, aborted: bool = False, reason: str | None = None) -> None:
        self._aborted = aborted
        self.reason = reason

    @property
    def aborted(self) -> bool:
        return self._aborted

    def _mark_aborted(self, reason: str | None = None) -> None:
        """Internal — tripped by :class:`AbortController`."""
        self._aborted = True
        self.reason = reason


class AbortController:
    """Owns an :class:`AbortSignal` and can trip it."""

    __slots__ = ("signal",)

    def __init__(self) -> None:
        self.signal = AbortSignal()

    def abort(self, reason: str | None = None) -> None:
        self.signal._mark_aborted(reason)
