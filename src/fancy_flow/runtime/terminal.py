"""What a terminal has said, and which terminal a node is talking to.

Ported from the TypeScript ``terminal-transcript.ts`` / ``terminal-sessions.ts``
with the same three hazards in mind, because they are properties of PTYs rather
than of a language. Each fails INTERMITTENTLY in production and passes in any
test that writes one tidy chunk:

1. **Chunks are arbitrary.** A PTY splits wherever it splits, so ``Ready >`` can
   arrive as ``Rea`` + ``dy >``. Matching a chunk on its own finds the pattern
   when output is small enough to land in one write and misses it when it is
   not, so matching runs against an ACCUMULATED buffer.

2. **Output arrives before anyone is listening.** The node that types at a
   process and the node that reads its reply are two steps; anything printed
   between them is gone if the buffer starts when the wait does. So the
   transcript is attached when the SESSION opens.

3. **Escape sequences are everywhere and they also straddle.** A TUI writes
   ``Ready`` wrapped in colour, so matching raw bytes fails on text a person can
   plainly read -- and stripping per chunk is hazard 1 again in a second guise:
   ``ESC[3`` + ``2mReady`` stripped separately leaves the escape INSIDE the
   text. An incomplete trailing sequence is therefore held back.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ..capabilities import (
    TerminalExit,
    TerminalSession,
    TerminalSessionSpec,
    terminal_host,
    terminal_unavailable_message,
)
from ..schema.graph import FlowGraph, FlowNode

__all__ = [
    "TerminalAccess",
    "TerminalSessions",
    "TerminalTranscript",
    "WaitResult",
    "spec_for_lane",
]

#: The control characters, by code point rather than as literals.
#:
#: A raw ESC byte in a source file survives most tools and not all of them, and
#: the failure is the quiet kind: strip one out and the pattern still compiles,
#: still reads correctly in review, and matches nothing -- so every wait fails
#: on coloured output while the code looks right.
_ESC = chr(0x1B)
_CSI = chr(0x9B)
_BEL = chr(0x07)

#: CSI (``ESC [ ... final``), OSC (``ESC ] ... BEL|ST``), and the two-character
#: escapes. Written out rather than taken from a dependency: it is a regex.
_ANSI = re.compile(
    f"[{_ESC}{_CSI}](?:"
    r"\[[0-?]*[ -/]*[@-~]"
    f"|\\][^{_BEL}{_ESC}]*(?:{_BEL}|{_ESC}\\\\)"
    r"|[@-Z\\-_])"
)

#: How much unterminated escape to hold back before giving up on it. An OSC
#: carrying a window title is legitimately long; a lone ESC that never
#: terminates is one byte, and holding the stream hostage behind it would stall
#: a wait forever -- a worse failure than one stray character in the output.
_MAX_PENDING_ESCAPE = 1024

#: Cap on retained text, so a chatty process across a long run cannot grow this
#: without limit. Dropping from the FRONT keeps the recent output, which is what
#: a wait is about to match.
_MAX_BUFFER = 1_000_000


@dataclass(frozen=True, slots=True)
class WaitResult:
    """How a wait ended.

    Three outcomes, not two. Folding ``exited`` into ``timeout`` is how a dead
    shell gets reported as "timed out waiting for X" -- true of the symptom and
    wrong about the cause, sending whoever reads it to lengthen a timeout on a
    process that is not running.
    """

    status: str  # "matched" | "timeout" | "exited"
    text: str
    match: re.Match[str] | None = None
    exit_code: int | None = None
    signal: str | None = None

    @property
    def matched(self) -> bool:
        return self.status == "matched"


class TerminalTranscript:
    """Accumulated terminal output, in a form a node can wait on."""

    __slots__ = ("_tail", "_text", "_waiters")

    def __init__(self) -> None:
        #: Stripped, complete text not yet consumed by a wait.
        self._text = ""
        #: Raw bytes held back because they may begin an escape sequence.
        self._tail = ""
        self._waiters: list[Callable[[], None]] = []

    def append(self, chunk: str) -> None:
        """Feed raw output in. Safe with any chunking."""
        self._tail += chunk

        safe = self._safe_length()
        if safe > 0:
            self._text += _ANSI.sub("", self._tail[:safe])
            self._tail = self._tail[safe:]

        if len(self._text) > _MAX_BUFFER:
            self._text = self._text[-_MAX_BUFFER:]

        for wake in list(self._waiters):
            wake()

    def _safe_length(self) -> int:
        """How much of the tail can be stripped now.

        All of it, unless the final ESC has not yet been terminated -- in which
        case processing stops there and resumes when the rest arrives.
        """
        last_esc = max(self._tail.rfind(_ESC), self._tail.rfind(_CSI))
        if last_esc == -1:
            return len(self._tail)

        # A complete sequence starting there means nothing is pending.
        found = _ANSI.match(self._tail, last_esc)
        if found is not None:
            return len(self._tail)

        # Unterminated, but long enough that it is not really an escape.
        if len(self._tail) - last_esc > _MAX_PENDING_ESCAPE:
            return len(self._tail)

        return last_esc

    def peek(self) -> str:
        """Unconsumed output, escape sequences removed."""
        return self._text

    def clear(self) -> None:
        """Drop everything buffered -- used before typing a new command."""
        self._text = ""
        self._tail = ""

    async def wait_for(
        self,
        pattern: re.Pattern[str],
        timeout_ms: int,
        exited: asyncio.Future[TerminalExit] | None = None,
    ) -> WaitResult:
        """Wait until ``pattern`` matches the unconsumed text.

        Checks what has ALREADY arrived before subscribing, because the common
        case is that the process answered while the previous node was still
        finishing.

        ``exited`` is a FUTURE owned by the session, not a fresh coroutine per
        call, and this method never cancels it. A coroutine created here and
        dropped when the match won was never awaited at all -- Python said so
        ("coroutine was never awaited"), and on a real host it would mean each
        wait registering another exit watcher on the same process.
        """
        loop = asyncio.get_running_loop()
        settled: asyncio.Future[WaitResult] = loop.create_future()

        def check() -> None:
            if settled.done():
                return
            found = pattern.search(self._text)
            if found is None:
                return
            # ``or 1`` so a pattern that can match empty still makes progress
            # rather than matching the same position forever.
            through = found.start() + (len(found.group(0)) or 1)
            text = self._text[:through]
            self._text = self._text[through:]
            settled.set_result(WaitResult("matched", text, match=found))

        self._waiters.append(check)
        try:
            check()
            if settled.done():
                return settled.result()

            timeout_task: asyncio.Task[None] | None = None
            if timeout_ms > 0:
                timeout_task = asyncio.ensure_future(asyncio.sleep(timeout_ms / 1000))

            waits: list[Any] = [settled]
            if timeout_task is not None:
                waits.append(timeout_task)
            if exited is not None:
                waits.append(exited)

            done, _pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)

            # Only the timer is ours to cancel. The exit future belongs to the
            # session and is shared by every wait on this terminal -- cancelling
            # it here would mean the FIRST wait to finish silently disarmed
            # exit-detection for every wait after it, and a dead shell would
            # then report as a timeout from the second node onward.
            if timeout_task is not None and not timeout_task.done():
                timeout_task.cancel()

            if settled.done():
                return settled.result()

            # A match that landed while we were unwinding still wins: the
            # process answering and then exiting is ordinary, and reporting the
            # exit there would be true and useless.
            check()
            if settled.done():
                return settled.result()

            text = self._text
            self._text = ""

            if exited is not None and exited in done:
                result = _result_or_none(exited)
                if isinstance(result, TerminalExit):
                    return WaitResult(
                        "exited", text, exit_code=result.exit_code, signal=result.signal
                    )

            return WaitResult("timeout", text)
        finally:
            if check in self._waiters:
                self._waiters.remove(check)


def _result_or_none(task: Any) -> Any:
    try:
        return task.result()
    except Exception:
        return None


class TerminalSessions:
    """One terminal per terminal lane, for the length of a run.

    ## The lifetime, and why it is not per-node

    A terminal node is only useful if the process it talks to is the SAME one
    the last node talked to: ``cd`` has to persist, and an agent TUI has to
    still be running with its conversation intact. A session opened per node
    would quietly turn a sequence of steps into a series of unrelated ones --
    each individually correct, the whole thing meaningless.

    So the session is keyed on the LANE, opened lazily by the first node inside
    it, and closed once when the run ends. A graph that never reaches a terminal
    node spawns no process at all, which is what makes it safe to draw a lane
    around nodes that mostly do other things.

    ## Why membership is ``parent_id``

    It is what the canvas already means by "inside", and it persists into the
    WorkflowSchema -- so a headless runtime resolves exactly the grouping a
    person drew, with no second association to keep in step. A lane id typed
    into each node's config would be the same fact in two places and would drift
    the first time somebody dragged a node out of a lane.
    """

    __slots__ = ("_exits", "_graph", "_lane_of", "_open", "_transcripts", "_unsubscribe")

    def __init__(self, graph: FlowGraph) -> None:
        self._graph = graph
        self._open: dict[str, TerminalSession] = {}
        self._lane_of: dict[str, str | None] = {}
        self._transcripts: dict[str, TerminalTranscript] = {}
        self._unsubscribe: dict[str, Callable[[], None]] = {}
        #: One exit watcher per lane, started with the session and shared by
        #: every wait. See :meth:`TerminalTranscript.wait_for`.
        self._exits: dict[str, asyncio.Task[TerminalExit]] = {}

    def lane_for(self, node_id: str, is_terminal_lane: Callable[[FlowNode], bool]) -> str | None:
        """The terminal lane a node belongs to, or ``None``.

        Walks UP ``parent_id`` rather than checking only the immediate parent,
        so an ordinary lane nested inside a terminal lane still resolves --
        grouping for looks should not change which terminal a node talks to.
        """
        if node_id in self._lane_of:
            return self._lane_of[node_id]

        by_id = {n.id: n for n in self._graph.nodes}
        seen: set[str] = set()
        current = by_id.get(node_id)
        answer: str | None = None

        while current is not None:
            # A cycle in parent_id is malformed rather than impossible -- a
            # dragged node with a stale parent produces one -- and walking it
            # forever would hang the run rather than fail it.
            if current.id in seen:
                break
            seen.add(current.id)

            if current.id != node_id and is_terminal_lane(current):
                answer = current.id
                break

            current = by_id.get(current.parent_id) if current.parent_id else None

        self._lane_of[node_id] = answer
        return answer

    def transcript_for(self, lane_id: str) -> TerminalTranscript:
        """The accumulated output for a lane, created on demand."""
        existing = self._transcripts.get(lane_id)
        if existing is not None:
            return existing
        created = TerminalTranscript()
        self._transcripts[lane_id] = created
        return created

    async def session(self, lane_id: str, spec: TerminalSessionSpec) -> TerminalSession:
        """The session for a lane, opening it on first use."""
        existing = self._open.get(lane_id)
        if existing is not None:
            return existing

        host = terminal_host()
        if host is None:
            raise RuntimeError(terminal_unavailable_message())

        session = await host.open(spec)
        self._open[lane_id] = session

        # Subscribed the moment the session exists, not when a node first
        # waits. A process answers on its own schedule, and output printed
        # between a send and an await is unrecoverable if the buffer starts
        # when the WAIT does -- so a fast process is missed and a slow one
        # caught, which presents as flakiness rather than as a bug.
        transcript = self.transcript_for(lane_id)
        self._unsubscribe[lane_id] = session.on_data(transcript.append)

        # Started once, here, rather than per wait. Every wait on this terminal
        # races the SAME future, which is what makes "the process died" an
        # answer each of them can give.
        watcher: asyncio.Task[TerminalExit] = asyncio.ensure_future(session.wait_exit())
        # Consumed, so a host whose wait_exit raises does not surface as
        # "Task exception was never retrieved" from somewhere unrelated.
        watcher.add_done_callback(lambda task: task.cancelled() or task.exception())
        self._exits[lane_id] = watcher

        return session

    def exit_for(self, lane_id: str) -> asyncio.Task[TerminalExit] | None:
        """The lane's exit watcher, for a wait to race. ``None`` before it opens."""
        return self._exits.get(lane_id)

    def is_open(self, lane_id: str) -> bool:
        return lane_id in self._open

    def open_lanes(self) -> Iterable[str]:
        return tuple(self._open)

    async def close(self, lane_id: str) -> None:
        """Close one lane's session."""
        # Unsubscribe BEFORE closing. A host emitting a final chunk while
        # shutting down would otherwise append to a transcript nobody will read
        # again, and a listener outliving its session keeps a run's objects
        # alive after the run is over.
        unsubscribe = self._unsubscribe.pop(lane_id, None)
        if unsubscribe is not None:
            unsubscribe()

        watcher = self._exits.pop(lane_id, None)
        if watcher is not None and not watcher.done():
            watcher.cancel()

        session = self._open.pop(lane_id, None)
        self._transcripts.pop(lane_id, None)
        if session is not None:
            await session.close()

    async def close_all(self) -> list[Exception]:
        """Close every session this run opened.

        Every close is attempted even if one raises: a host that fails to close
        one PTY must not strand the others, which would leave processes alive
        after the run had reported that it finished. Errors are RETURNED rather
        than raised, because teardown runs after the run has its outcome and
        raising there would replace a real error with a cleanup one.
        """
        errors: list[Exception] = []
        for lane_id in tuple(self._open):
            try:
                await self.close(lane_id)
            except Exception as exc:
                errors.append(exc)
        return errors


@dataclass(frozen=True, slots=True)
class TerminalAccess:
    """A node's handle on the terminal its lane owns.

    Both members are CALLABLES rather than an already-open session, and that
    single shape carries the whole lifetime rule: the terminal opens on first
    USE. A node sitting inside a terminal lane that never calls either never
    spawns a process, which is what makes a lane safe to draw around nodes that
    mostly do other things.

    A node OUTSIDE any terminal lane gets ``None`` instead of one of these, and
    that is a real answer rather than a missing one -- a terminal node outside a
    lane must say so rather than quietly opening a shell of its own, because one
    unmanaged process per node is what the lane exists to prevent.
    """

    lane_id: str
    _sessions: TerminalSessions
    _spec: TerminalSessionSpec

    async def session(self) -> TerminalSession:
        """The lane's session, opening it if this is the first use."""
        return await self._sessions.session(self.lane_id, self._spec)

    async def exit_signal(self) -> Any:
        """The lane's exit watcher, for a wait to race.

        Opens the session first, for the same reason ``transcript`` does: an
        exit signal for a process nobody started can never fire, and a wait
        racing it would simply never learn that its terminal was not there.
        """
        await self.session()
        return self._sessions.exit_for(self.lane_id)

    async def transcript(self) -> TerminalTranscript:
        """What the terminal has said, accumulated and matchable.

        Opens the session first, deliberately. A transcript handed out before
        anything feeds it looks perfectly healthy and matches nothing, and
        "waited and nothing came" is the hardest failure here to tell apart from
        a process that is merely slow.
        """
        await self.session()
        return self._sessions.transcript_for(self.lane_id)


def spec_for_lane(lane: FlowNode) -> TerminalSessionSpec:
    """The spec a terminal lane node declares."""
    config = lane.config or {}

    def text(key: str) -> str | None:
        value = config.get(key)
        return value if isinstance(value, str) and value != "" else None

    env = config.get("env")
    args = config.get("args")

    return TerminalSessionSpec(
        command=text("command"),
        args=tuple(str(a) for a in args) if isinstance(args, (list, tuple)) else (),
        cwd=text("cwd"),
        env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else None,
    )
