"""The three terminal primitives.

Async by necessity: waiting on a process is not something a synchronous runner
can do, so these are only usable through :meth:`FlowRunner.arun`. The sync
driver already reports that clearly -- it refuses an awaitable by name rather
than storing a coroutine object as if it were a value.

## Why three, and not one

A terminal is two different things depending on what is running in it, and one
node cannot serve both:

- **A shell** answers and returns to a prompt, so the useful unit is "run this,
  tell me what it said and whether it worked" -- ``terminal_run``.
- **A TUI** (Claude Code, Codex, a REPL, an installer asking a question) never
  finishes. There is no exit code to wait for and no prompt to come back to;
  there is only text going in and text coming out. That is ``terminal_send`` +
  ``terminal_await``, and it is the pair that makes an agent TUI drivable at
  all.

Collapsing them would mean guessing which mode the author meant, and guessing
wrong on a TUI means hanging until a timeout.
"""

from __future__ import annotations

import random
import re
import time
from typing import Any

from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.terminal import TerminalAccess

__all__ = [
    "exit_marker",
    "terminal_await",
    "terminal_run",
    "terminal_send",
]

DEFAULT_TIMEOUT_MS = 120_000

#: Enter, in a PTY, is a carriage return. ``\\n`` works in plenty of shells and
#: is wrong for a TUI -- readline and Ink both listen for ``\\r`` -- and sending
#: the wrong one looks exactly like the process ignoring the prompt, which sends
#: someone off to debug the agent.
ENTER = "\r"


def exit_marker(nonce: str) -> tuple[str, re.Pattern[str]]:
    """The marker ``terminal_run`` appends so it can tell when a command ended.

    A persistent shell has no completion channel -- output just stops arriving,
    and "stopped arriving" is indistinguishable from "still thinking". So the
    command is followed by an echo of its own exit status, and THAT is what the
    node waits for.

    The nonce matters: without it a command whose own output happened to contain
    the marker would end the wait early, and a stale marker left in the buffer
    would satisfy the NEXT command instantly.

    The expanded status is required to be DIGITS. A terminal echoes what was
    typed, and that echo carries the marker with ``$?`` unexpanded -- so
    requiring digits is what stops the node matching its own command line and
    reporting success before anything has run.
    """
    token = f"__fancy_flow_exit_{nonce}__"
    return token, re.compile(re.escape(token) + r":(\d+)")


_counter = 0


def _new_nonce() -> str:
    global _counter
    _counter += 1
    return f"{int(time.time() * 1000):x}{_counter:x}{random.randrange(16**6):06x}"


def _without_marker(output: str, token: str) -> str:
    """Drop every line carrying the marker.

    Two exist in a normal run -- the shell's echo of the typed command, and the
    marker line itself -- and neither is output the author asked for. Filtering
    by LINE rather than slicing by index survives an echo that is wrapped,
    disabled, or reordered, none of which is under our control.
    """
    return "\n".join(line for line in output.split("\n") if token not in line).strip()


def _terminal(ctx: ExecutionContext) -> TerminalAccess:
    """The lane's terminal, or an abort naming the real problem."""
    access: TerminalAccess | None = ctx.terminal
    if access is None:
        label = ctx.node.label or ctx.node.id
        ctx.abort(
            f'"{label}" is a terminal node but is not inside a terminal lane. Drag it into '
            "one -- a terminal node outside a lane has no session to talk to, and opening a "
            "private shell for it would defeat the point of the lane."
        )
    return access


def _timeout_ms(ctx: ExecutionContext) -> int:
    value = ctx.option("timeoutMs", DEFAULT_TIMEOUT_MS)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_MS
    return parsed if parsed >= 0 else DEFAULT_TIMEOUT_MS


async def terminal_run(ctx: ExecutionContext) -> dict[str, Any]:
    """Send a command, wait for it to finish, report its exit code.

    Shell only, and the node says so: a TUI never returns to a prompt, so there
    is nothing for the marker to ride on. Use send + await there.
    """
    command = str(ctx.option("command", "") or "").strip()
    if not command:
        ctx.abort("terminal_run has no command configured")

    access = _terminal(ctx)
    session = await access.session()
    transcript = await access.transcript()

    token, pattern = exit_marker(_new_nonce())
    timeout_ms = _timeout_ms(ctx)

    # Everything the shell said before this command belongs to somebody else.
    # Carrying it into this node's result would attribute the previous
    # command's text -- and, worse, let a marker-shaped string from earlier
    # satisfy this wait.
    transcript.clear()

    await session.write(f"{command}; printf '{token}:%s\\n' \"$?\"{ENTER}")

    result = await transcript.wait_for(pattern, timeout_ms, await access.exit_signal())

    if result.status == "exited":
        signal = f", signal {result.signal}" if result.signal else ""
        ctx.abort(
            f"The terminal exited (code {result.exit_code}{signal}) while running "
            f'"{command}". The lane\'s session is gone, so every later node in this '
            "lane would fail too."
        )

    if result.status == "timeout":
        ctx.abort(
            f'"{command}" did not finish within {timeout_ms}ms. Raise the node\'s timeout '
            "if it is genuinely slow -- but a command that waits for input never finishes "
            "at all, and needs terminal_send + terminal_await."
        )

    assert result.match is not None
    exit_code = int(result.match.group(1))
    output = _without_marker(result.text, token)

    if ctx.option("failOnNonZero", True) is not False and exit_code != 0:
        # Aborting is the default because the alternative is the failure this
        # whole estate keeps finding: a step that failed, a run that reports
        # success, and nothing anywhere saying the two disagree.
        ctx.abort(f'"{command}" exited {exit_code}.\n{output}')

    return {"output": output, "exitCode": exit_code, "command": command}


async def terminal_send(ctx: ExecutionContext) -> dict[str, Any]:
    """Type at the process and move on.

    It does NOT wait, deliberately: what counts as an answer is the author's
    decision, and pairing every send with a hardcoded wait would make the common
    case -- send, then await a specific prompt -- impossible to express.
    """
    access = _terminal(ctx)
    session = await access.session()

    body = str(ctx.option("text", "") or "")
    # A send that does not press Enter leaves the text sitting on the process's
    # input line, which looks exactly like the process ignoring it.
    submit = ctx.option("submit", True) is not False

    if ctx.option("clearFirst", False) is True:
        (await access.transcript()).clear()

    await session.write(f"{body}{ENTER}" if submit else body)

    return {"sent": body, "submitted": submit}


def _compile_pattern(ctx: ExecutionContext, raw: str) -> re.Pattern[str]:
    """The pattern a wait looks for.

    Plain text is the default and is ESCAPED, so a prompt like ``? (y/n)``
    matches itself instead of being read as a regex -- which either fails to
    compile or, worse, matches something else while looking like it works.
    """
    try:
        return re.compile(raw if ctx.option("mode", "text") == "regex" else re.escape(raw))
    except re.error as exc:
        # Named as an INVALID PATTERN. A bad regex raised raw reads as a crash
        # in the engine rather than as a typo in one node's config.
        ctx.abort(f"terminal_await has an invalid regex: {exc}")


async def terminal_await(ctx: ExecutionContext) -> dict[str, Any]:
    """Wait for the process to say something.

    The other half of driving a TUI. Without it a graph can type at Claude Code
    and never learn that it has answered, so every downstream node runs against
    whatever happened to be on screen when the run started.
    """
    access = _terminal(ctx)
    transcript = await access.transcript()

    raw = str(ctx.option("pattern", "") or "").strip()
    if not raw:
        ctx.abort("terminal_await has no pattern configured -- there is nothing to wait for")

    pattern = _compile_pattern(ctx, raw)
    timeout_ms = _timeout_ms(ctx)

    result = await transcript.wait_for(pattern, timeout_ms, await access.exit_signal())

    if result.status == "exited":
        signal = f", signal {result.signal}" if result.signal else ""
        ctx.abort(
            f"The terminal exited (code {result.exit_code}{signal}) while waiting for "
            f"{raw!r}. It never appeared, and the lane's session is gone."
        )

    if result.status == "timeout":
        # The default is to FAIL. An await that shrugs lets the next node type
        # at a process that never became ready while the run reports success,
        # so continuing has to be something the author asked for.
        if ctx.option("onTimeout", "fail") == "continue":
            # Said out loud. A node that continues after not finding what it
            # was told to wait for has changed what the rest of the run means,
            # and a silent "matched": False is the kind of thing nobody reads
            # until they are already debugging the wrong node.
            ctx.emit(
                RunEvent.log(
                    "warn",
                    f"terminal_await: {raw!r} did not appear within {timeout_ms}ms; "
                    "continuing as configured.",
                    ctx.node.id,
                )
            )
            return {"matched": False, "output": result.text, "groups": []}

        ctx.abort(
            f"{raw!r} did not appear within {timeout_ms}ms. Last output:\n{result.text[-2000:]}"
        )

    assert result.match is not None
    return {
        "matched": True,
        "output": result.text,
        "matchedText": result.match.group(0),
        # Capture groups are the reason to use regex mode at all -- a prompt
        # reporting a session id or a path is only useful if the value comes out.
        "groups": list(result.match.groups()),
    }
