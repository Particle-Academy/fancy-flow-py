"""The matching engine behind ``terminal_await``.

Every test here is for a failure that is INTERMITTENT in production and green in
a naive test: small outputs arrive in one chunk, so per-chunk matching works
until it doesn't; a fake host emits plain text, so a missing ANSI strip is
invisible until a real TUI prints in colour; a fast fake answers before the wait
starts only sometimes.

So each one reproduces the awkward case deliberately. A test that feeds a whole
line in a single chunk asserts nothing about the thing that actually breaks.
"""

from __future__ import annotations

import asyncio
import re

from fancy_flow.capabilities import TerminalExit
from fancy_flow.runtime.terminal import TerminalTranscript

ESC = chr(0x1B)


def test_matches_a_pattern_split_across_chunk_boundaries() -> None:
    """THE test.

    A PTY splits wherever it splits, and ``Ready`` arriving as ``Rea`` + ``dy``
    is normal rather than exotic. Matching per chunk finds this pattern never,
    and finds it every time in a test that writes it whole.
    """

    async def go() -> object:
        t = TerminalTranscript()
        waiting = asyncio.ensure_future(t.wait_for(re.compile("Ready"), 1000))
        await asyncio.sleep(0)
        t.append("Rea")
        t.append("dy > ")
        return await waiting

    assert asyncio.run(go()).status == "matched"


def test_matches_a_word_a_tui_has_coloured_part_of() -> None:
    """Escapes AROUND the word prove nothing.

    ``Ready`` matches inside ``ESC[32mReady ESC[0m`` whether or not anything was
    stripped, so a test written that way passes with the strip deleted. The case
    that needs stripping is an escape INSIDE the match, which is ordinary for a
    TUI that highlights a prefix or repositions mid-word.
    """

    async def go() -> object:
        t = TerminalTranscript()
        waiting = asyncio.ensure_future(t.wait_for(re.compile("Ready"), 1000))
        await asyncio.sleep(0)
        t.append(f"{ESC}[1mRea{ESC}[0m{ESC}[32mdy{ESC}[0m")
        return await waiting

    assert asyncio.run(go()).status == "matched"


def test_holds_back_an_escape_sequence_that_straddles_chunks() -> None:
    """The second guise of the chunk problem, created by a per-chunk strip.

    ``ESC[3`` + ``2mReady`` stripped separately leaves ``ESC[32mReady`` -- the
    escape now INSIDE the text, so the pattern fails on output that renders as
    plain "Ready".
    """
    t = TerminalTranscript()
    t.append(f"{ESC}[3")
    t.append("2mReady")

    assert t.peek() == "Ready"


def test_gives_a_wait_output_that_arrived_before_it_started_at_once() -> None:
    """A node that types and a node that reads are two steps.

    Anything the process said in between is unrecoverable if the buffer starts
    when the WAIT does -- so a fast process is missed and a slow one caught,
    which presents as flakiness rather than as a bug.

    Asserting only "matched" is not enough, and mutation testing said so: with
    the check-before-subscribe deleted this still matched -- on the RE-check
    after the wait unwound, a full timeout later. Correct answer, useless
    latency, and a graph of a dozen awaits would each burn their whole timeout
    before proceeding. So the DEADLINE is the assertion.
    """

    async def go() -> object:
        t = TerminalTranscript()
        t.append("already here\n")
        # Five seconds of patience the transcript must not need.
        return await asyncio.wait_for(t.wait_for(re.compile("already"), 5000), timeout=0.5)

    assert asyncio.run(go()).status == "matched"


def test_consumes_through_the_match() -> None:
    """Without this a loop reads one old line forever and reports progress."""

    async def go() -> tuple[str, str]:
        t = TerminalTranscript()
        t.append("prompt> ")
        first = await t.wait_for(re.compile("prompt> "), 1000)
        second = await t.wait_for(re.compile("prompt> "), 30)
        return first.status, second.status

    assert asyncio.run(go()) == ("matched", "timeout")


def test_returns_the_text_up_to_the_match_not_the_whole_buffer() -> None:
    async def go() -> tuple[str, str]:
        t = TerminalTranscript()
        t.append("line one\nline two\nDONE\ntrailing")
        result = await t.wait_for(re.compile("DONE"), 1000)
        return result.text, t.peek()

    text, left = asyncio.run(go())
    assert text == "line one\nline two\nDONE"
    assert left == "\ntrailing"


def test_reports_a_timeout_with_what_it_did_see() -> None:
    async def go() -> object:
        t = TerminalTranscript()
        t.append("nothing useful")
        return await t.wait_for(re.compile("never"), 30)

    result = asyncio.run(go())
    assert result.status == "timeout"
    assert result.text == "nothing useful"


def test_reports_the_process_exiting_as_its_own_outcome() -> None:
    """The wrong-diagnosis case.

    A dead shell reported as "timed out waiting for X" sends whoever reads it to
    lengthen a timeout on a process that is not running -- true of the symptom,
    useless about the cause.
    """

    async def go() -> object:
        t = TerminalTranscript()
        exited: asyncio.Future[TerminalExit] = asyncio.get_running_loop().create_future()
        waiting = asyncio.ensure_future(t.wait_for(re.compile("never"), 5000, exited))
        await asyncio.sleep(0)
        exited.set_result(TerminalExit(137, "SIGKILL"))
        return await waiting

    result = asyncio.run(go())
    assert result.status == "exited"
    assert result.exit_code == 137
    assert result.signal == "SIGKILL"


def test_a_match_that_arrived_before_the_exit_wins_the_race() -> None:
    """A process that prints its answer and immediately exits is ordinary.

    If the exit won that race the run would fail on output it had already been
    given, and the failure would depend on scheduling.
    """

    async def go() -> object:
        t = TerminalTranscript()
        exited: asyncio.Future[TerminalExit] = asyncio.get_running_loop().create_future()
        waiting = asyncio.ensure_future(t.wait_for(re.compile("all done"), 5000, exited))
        await asyncio.sleep(0)
        t.append("all done\n")
        exited.set_result(TerminalExit(0))
        return await waiting

    assert asyncio.run(go()).status == "matched"


def test_the_exit_future_survives_a_wait_that_matched() -> None:
    """One exit watcher per SESSION, shared by every wait on it.

    A wait that cancelled the future on its way out would silently disarm
    exit-detection for every later wait on the same terminal -- so a dead shell
    would be named correctly by the first node and reported as a timeout by
    every one after it. That is worse than never naming it, because the first
    report teaches you to trust the second.
    """

    async def go() -> tuple[str, bool, str]:
        t = TerminalTranscript()
        exited: asyncio.Future[TerminalExit] = asyncio.get_running_loop().create_future()

        # The data must arrive AFTER the wait starts, so the first wait goes
        # through the race and reaches the cleanup that could cancel the shared
        # future. Appending first would satisfy the check-before-subscribe and
        # return early, skipping the very code this test is about -- which is
        # what it did, and mutation testing caught it.
        waiting = asyncio.ensure_future(t.wait_for(re.compile("first"), 1000, exited))
        await asyncio.sleep(0)
        t.append("first\n")
        first = await waiting

        still_usable = not exited.cancelled() and not exited.done()

        waiting = asyncio.ensure_future(t.wait_for(re.compile("never"), 5000, exited))
        await asyncio.sleep(0)
        exited.set_result(TerminalExit(1))
        second = await waiting

        return first.status, still_usable, second.status

    assert asyncio.run(go()) == ("matched", True, "exited")


def test_clear_drops_output_the_next_wait_must_not_match() -> None:
    async def go() -> object:
        t = TerminalTranscript()
        t.append("stale prompt> ")
        t.clear()
        return await t.wait_for(re.compile("stale"), 30)

    assert asyncio.run(go()).status == "timeout"


def test_gives_up_on_an_esc_that_never_terminates() -> None:
    """A lone ESC would otherwise hold the entire stream behind it.

    A wait would then hang on output that had already arrived -- a worse failure
    than one stray character in the text.
    """
    t = TerminalTranscript()
    t.append(ESC + "x" * 2000)

    assert len(t.peek()) > 1000
