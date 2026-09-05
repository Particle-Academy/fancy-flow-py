"""``terminal_run`` / ``terminal_send`` / ``terminal_await``, end to end.

Driven against a fake that behaves like a real PTY in the two ways that break
naive code: it ECHOES what is typed at it -- so a node can match its own command
and report success before anything ran -- and it emits output in pieces, so a
matcher that tests one chunk at a time silently misses.

A fake that answers in one clean chunk with no echo makes all of this pass and
proves none of it.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from fancy_flow import capabilities
from fancy_flow.engine.runner import FlowRunner
from fancy_flow.registry import builtin
from fancy_flow.schema.graph import FlowEdge, FlowGraph, FlowNode

ESC = chr(0x1B)
MARKER = re.compile(r"__fancy_flow_exit_[a-z0-9]+__")


class FakeSession:
    """One terminal, with a real PTY's awkward habits."""

    def __init__(self, echo: bool = True, script=None) -> None:
        self.id = "fake"
        self.writes: list[str] = []
        self.closed = False
        self._echo = echo
        self._script = script
        self._listeners: list = []
        self._exit: asyncio.Future = asyncio.get_event_loop().create_future()

    async def write(self, data: str) -> None:
        self.writes.append(data)
        if self._echo:
            # The echo carries the exit marker with `$?` UNEXPANDED, which is
            # exactly what terminal_run's digit requirement has to survive.
            self.say(data.replace("\r", "\n"))
        if self._script is not None:
            await self._script(self, data)

    def say(self, *chunks: str) -> None:
        for chunk in chunks:
            for listener in list(self._listeners):
                listener(chunk)

    def exit(self, code: int, signal: str | None = None) -> None:
        if not self._exit.done():
            self._exit.set_result(capabilities.TerminalExit(code, signal))

    def on_data(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def wait_exit(self):
        return await self._exit

    async def close(self) -> None:
        self.closed = True


class FakeHost:
    def __init__(self, echo: bool = True, script=None) -> None:
        self.opened: list[tuple] = []
        self._echo = echo
        self._script = script

    async def open(self, spec):
        session = FakeSession(echo=self._echo, script=self._script)
        self.opened.append((spec, session))
        return session

    @property
    def session(self) -> FakeSession:
        return self.opened[0][1]


@pytest.fixture(autouse=True)
def _clean_capabilities():
    capabilities.reset()
    yield
    capabilities.reset()


def node(nid: str, ntype: str, config=None, parent: str | None = None) -> FlowNode:
    return FlowNode(id=nid, type=ntype, config=config or {}, parent_id=parent)


def lane_graph(*inner: FlowNode, lane_config=None) -> FlowGraph:
    """A terminal lane wrapping the given nodes, wired in sequence."""
    return FlowGraph(
        nodes=(node("lane", "terminal_lane", lane_config or {"command": "bash"}), *inner),
        edges=tuple(
            FlowEdge(id=f"e{i}", source=inner[i].id, target=inner[i + 1].id)
            for i in range(len(inner) - 1)
        ),
    )


def run(graph: FlowGraph):
    return asyncio.run(FlowRunner().arun(graph, builtin.executors()))


def answering(output: str, code: int = 0, delay: float = 0.0):
    """A shell that answers whatever command carries an exit marker."""

    async def script(session: FakeSession, data: str) -> None:
        found = MARKER.search(data)
        if not found:
            return
        if delay:
            await asyncio.sleep(delay)
        session.say(output, f"{found.group(0)}:{code}\n")

    return script


# -- terminal_run --------------------------------------------------------


def test_run_returns_the_output_and_the_exit_code() -> None:
    host = FakeHost(script=answering("3 passing\n"))
    capabilities.set_terminal_host(host)

    result = run(lane_graph(node("run", "terminal_run", {"command": "pytest -q"}, "lane")))

    assert result.ok is True
    assert result.outputs["run"] == {
        "output": "3 passing",
        "exitCode": 0,
        "command": "pytest -q",
    }


def test_run_does_not_mistake_the_shell_echo_for_the_result() -> None:
    """The echoed line contains the marker.

    A looser pattern matches it the instant the command is typed, so the node
    reports success with an empty output before the command has run at all --
    and every run looks fast and green.
    """
    host = FakeHost(script=answering("real output\n", delay=0.02))
    capabilities.set_terminal_host(host)

    result = run(lane_graph(node("run", "terminal_run", {"command": "sleep 1"}, "lane")))

    assert result.ok is True
    # The hazard is genuinely present in the stream -- otherwise this test would
    # be asserting against something the fake never produced.
    assert '"$?"' in host.session.writes[0]
    assert result.outputs["run"]["output"] == "real output"


def test_run_fails_the_run_on_a_non_zero_exit_by_default() -> None:
    host = FakeHost(script=answering("2 failing\n", code=1))
    capabilities.set_terminal_host(host)

    result = run(lane_graph(node("run", "terminal_run", {"command": "pytest"}, "lane")))

    assert result.ok is False
    assert "exited 1" in (result.error or "")


def test_run_hands_the_exit_code_to_the_graph_when_asked() -> None:
    host = FakeHost(script=answering("", code=1))
    capabilities.set_terminal_host(host)

    result = run(
        lane_graph(
            node("run", "terminal_run", {"command": "false", "failOnNonZero": False}, "lane")
        )
    )

    assert result.ok is True
    assert result.outputs["run"]["exitCode"] == 1


def test_run_names_a_timeout_and_says_what_to_reach_for_instead() -> None:
    capabilities.set_terminal_host(FakeHost())

    result = run(
        lane_graph(node("run", "terminal_run", {"command": "read x", "timeoutMs": 30}, "lane"))
    )

    assert result.ok is False
    assert "did not finish within 30ms" in (result.error or "")
    assert "terminal_send" in (result.error or "")


def test_run_reports_a_dead_terminal_as_a_dead_terminal() -> None:
    """Not as a timeout.

    Folding the two sends whoever reads it to lengthen a timeout on a process
    that is not running.
    """

    async def dies(session: FakeSession, data: str) -> None:
        session.exit(137, "SIGKILL")

    capabilities.set_terminal_host(FakeHost(script=dies))

    result = run(
        lane_graph(node("run", "terminal_run", {"command": "pytest", "timeoutMs": 5000}, "lane"))
    )

    assert result.ok is False
    assert "terminal exited" in (result.error or "")
    assert "137" in (result.error or "")
    assert "did not finish within" not in (result.error or "")


# -- terminal_send -------------------------------------------------------


def test_send_presses_enter_as_a_carriage_return() -> None:
    """``\\n`` works in plenty of shells and is wrong for a TUI.

    readline and Ink both listen for ``\\r``; sending the wrong one looks
    exactly like the process ignoring the prompt, which sends someone off to
    debug the agent.
    """
    host = FakeHost(echo=False)
    capabilities.set_terminal_host(host)

    run(lane_graph(node("send", "terminal_send", {"text": "hello"}, "lane")))

    assert host.session.writes == ["hello\r"]


def test_send_leaves_the_line_unsubmitted_when_asked() -> None:
    host = FakeHost(echo=False)
    capabilities.set_terminal_host(host)

    run(lane_graph(node("send", "terminal_send", {"text": "partial", "submit": False}, "lane")))

    assert host.session.writes == ["partial"]


# -- terminal_await ------------------------------------------------------


def test_await_matches_a_prompt_that_arrives_in_pieces_after_a_send() -> None:
    """The whole point: prompt an agent TUI and know when it has answered.

    The reply is emitted in three chunks with colour in the middle, which is
    what a TUI actually does.
    """

    async def replies(session: FakeSession, data: str) -> None:
        if not data.startswith("Summarise"):
            return
        await asyncio.sleep(0.005)
        session.say("think", "ing", f"...\n{ESC}[32mDone{ESC}[0m\n> ")

    capabilities.set_terminal_host(FakeHost(echo=False, script=replies))

    result = run(
        lane_graph(
            node("send", "terminal_send", {"text": "Summarise the failure"}, "lane"),
            node("wait", "terminal_await", {"pattern": "Done", "timeoutMs": 2000}, "lane"),
        )
    )

    assert result.ok is True
    assert result.outputs["wait"]["matched"] is True
    assert result.outputs["wait"]["matchedText"] == "Done"


def test_await_treats_plain_text_as_literal_not_as_a_regex() -> None:
    """A prompt like ``? (y/n)`` is full of regex metacharacters.

    Read as a pattern it either fails to compile or matches something else --
    and the second is the dangerous half, because the run continues.
    """

    async def prompts(session: FakeSession, data: str) -> None:
        session.say("Continue? (y/n) ")

    capabilities.set_terminal_host(FakeHost(echo=False, script=prompts))

    result = run(
        lane_graph(
            node("send", "terminal_send", {"text": "go"}, "lane"),
            node(
                "wait",
                "terminal_await",
                {"pattern": "Continue? (y/n)", "timeoutMs": 2000},
                "lane",
            ),
        )
    )

    assert result.ok is True
    assert result.outputs["wait"]["matched"] is True


def test_await_returns_capture_groups_in_regex_mode() -> None:
    async def announces(session: FakeSession, data: str) -> None:
        session.say("session ab12 ready\n")

    capabilities.set_terminal_host(FakeHost(echo=False, script=announces))

    result = run(
        lane_graph(
            node("send", "terminal_send", {"text": "start"}, "lane"),
            node(
                "wait",
                "terminal_await",
                {
                    "pattern": "session ([a-z0-9]+) ready",
                    "mode": "regex",
                    "timeoutMs": 2000,
                },
                "lane",
            ),
        )
    )

    assert result.outputs["wait"]["groups"] == ["ab12"]


def test_await_fails_the_run_by_default_when_the_pattern_never_appears() -> None:
    """The default has to be failure.

    Shrugging lets the next node type at a process that never became ready
    while the run reports success -- the exact shape where a broken workflow
    looks like a working one.
    """
    capabilities.set_terminal_host(FakeHost(echo=False))

    result = run(
        lane_graph(node("wait", "terminal_await", {"pattern": "never", "timeoutMs": 30}, "lane"))
    )

    assert result.ok is False
    assert "did not appear within 30ms" in (result.error or "")


def test_await_continues_only_when_the_author_asked_it_to() -> None:
    capabilities.set_terminal_host(FakeHost(echo=False))

    result = run(
        lane_graph(
            node(
                "wait",
                "terminal_await",
                {"pattern": "never", "timeoutMs": 30, "onTimeout": "continue"},
                "lane",
            )
        )
    )

    assert result.ok is True
    assert result.outputs["wait"]["matched"] is False
    # And it SAYS so. A silent "matched": False changes what the rest of the run
    # means, and nobody reads it until they are debugging the wrong node.
    warnings = [e for e in result.events if getattr(e, "type", "") == "log"]
    assert any("did not appear" in (w.message or "") for w in warnings)


def test_await_names_an_invalid_regex_as_a_config_problem() -> None:
    capabilities.set_terminal_host(FakeHost(echo=False))

    result = run(
        lane_graph(
            node("wait", "terminal_await", {"pattern": "([unclosed", "mode": "regex"}, "lane")
        )
    )

    assert result.ok is False
    assert "invalid regex" in (result.error or "")


# -- the lane itself -----------------------------------------------------


def test_a_lane_s_nodes_share_one_terminal() -> None:
    """Two sessions would look correct at every individual node.

    It is the shared state between them -- ``cd``, an agent's conversation --
    that silently stops existing.
    """
    host = FakeHost(script=answering("ok\n"))
    capabilities.set_terminal_host(host)

    result = run(
        lane_graph(
            node("run", "terminal_run", {"command": "cd /srv"}, "lane"),
            node("send", "terminal_send", {"text": "hello"}, "lane"),
        )
    )

    assert result.ok is True
    assert len(host.opened) == 1


def test_nothing_is_opened_until_a_node_uses_it() -> None:
    """A lane drawn around nodes that mostly do other things costs nothing."""
    host = FakeHost()
    capabilities.set_terminal_host(host)

    graph = FlowGraph(
        nodes=(
            node("lane", "terminal_lane", {"command": "bash"}),
            node("log", "log", {}, "lane"),
        ),
        edges=(),
    )
    result = run(graph)

    assert result.ok is True
    assert host.opened == []


def test_the_session_is_closed_when_the_run_ends() -> None:
    host = FakeHost(script=answering("ok\n"))
    capabilities.set_terminal_host(host)

    run(lane_graph(node("run", "terminal_run", {"command": "true"}, "lane")))

    assert host.session.closed is True


def test_the_session_is_closed_even_when_the_run_fails() -> None:
    """The path that leaks.

    A PTY surviving a failed run is a process nobody is watching and nothing
    will close, and it is invisible until there are a hundred of them.
    """
    host = FakeHost(script=answering("boom\n", code=1))
    capabilities.set_terminal_host(host)

    result = run(lane_graph(node("run", "terminal_run", {"command": "false"}, "lane")))

    assert result.ok is False
    assert host.session.closed is True


def test_a_terminal_node_outside_a_lane_says_which_lane_it_is_missing() -> None:
    host = FakeHost()
    capabilities.set_terminal_host(host)

    graph = FlowGraph(nodes=(node("send", "terminal_send", {"text": "hi"}),), edges=())
    result = run(graph)

    assert result.ok is False
    assert "not inside a terminal lane" in (result.error or "")
    # Nothing was spawned. A node that quietly opened its own shell would be one
    # unmanaged process per node, which is what the lane exists to prevent.
    assert host.opened == []


def test_a_missing_host_is_named_as_a_missing_host() -> None:
    """A configuration problem, not a failed terminal.

    Calling it "the terminal failed" sends someone to debug a process that was
    never started.
    """
    result = run(lane_graph(node("send", "terminal_send", {"text": "hi"}, "lane")))

    assert result.ok is False
    assert "No terminal host is registered" in (result.error or "")


def test_nesting_resolves_to_the_nearest_terminal_lane() -> None:
    """Grouping for looks must not change which terminal a node talks to."""
    host = FakeHost(echo=False)
    capabilities.set_terminal_host(host)

    graph = FlowGraph(
        nodes=(
            node("lane", "terminal_lane", {"command": "bash"}),
            node("inner", "lane", {}, "lane"),
            node("send", "terminal_send", {"text": "deep"}, "inner"),
        ),
        edges=(),
    )
    result = run(graph)

    assert result.ok is True
    assert len(host.opened) == 1
    assert host.session.writes == ["deep\r"]


def test_the_capability_status_reports_the_terminal() -> None:
    """So a host can learn it is missing BEFORE a run fails halfway."""
    assert capabilities.status()["terminal"] is False
    unregister = capabilities.set_terminal_host(FakeHost())
    assert capabilities.status()["terminal"] is True
    unregister()
    assert capabilities.status()["terminal"] is False
