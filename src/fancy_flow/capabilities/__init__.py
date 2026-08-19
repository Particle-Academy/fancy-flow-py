"""Host capabilities -- the services core nodes need but must never depend on.

A node that imports a provider SDK forces every consumer to install it: a
workflow app that never calls a model should not inherit an LLM dependency. So
core declares the CONTRACT and the host supplies the implementation.

Two ways in: the module-level setters below (so the framework-free core stays
usable with no container), or an adapter package that registers on the host's
behalf at startup.

Unlike the PHP twin there is **no auto-detection**. PHP can afford it because
``class_exists()`` is free; the Python equivalent is importing a candidate
package to find out whether it is there, which has side effects, costs start-up
time, and silently picks a provider the author never named. A missing client
therefore aborts the node with :func:`llm_unavailable_message`, which says what
to register -- an outcome the author can act on, rather than a guess they have
to discover.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..schema.graph import FlowGraph

__all__ = [
    "LlmClient",
    "LlmRoute",
    "LlmRouteChoice",
    "LlmRouteRequest",
    "WorkflowResolutionFailure",
    "WorkflowResolver",
    "llm_client",
    "llm_unavailable_message",
    "reset",
    "set_llm_client",
    "set_workflow_resolver",
    "status",
    "workflow_resolver",
]


@dataclass(frozen=True, slots=True)
class LlmRoute:
    """One route a model may choose between.

    The description is what the model actually reads when deciding, so it is
    the field that determines routing quality.
    """

    port: str
    description: str | None = None

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> LlmRoute:
        return LlmRoute(
            port=str(raw.get("port") or "").strip(),
            description=str(raw["description"]) if raw.get("description") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class LlmRouteRequest:
    prompt: str
    routes: tuple[LlmRoute, ...]
    system: str | None = None
    provider: str | None = None
    model: str | None = None
    credential: str | None = None


@dataclass(frozen=True, slots=True)
class LlmRouteChoice:
    """The port the model picked, and why.

    ``reason`` travels with the value down the graph, so a completed run
    explains itself without the model call being replayed.
    """

    port: str
    reason: str | None = None


@runtime_checkable
class LlmClient(Protocol):
    """The only thing core asks of an LLM: given routes, pick one.

    ``llm_router`` is a shuttle, not an engine. It carries the routes out to
    whatever the host registered and carries the choice back -- no provider
    SDK, no prompt engineering, no response parsing, no retry policy. That is
    what lets an opinionated node ship as a builtin without every consumer
    inheriting an LLM dependency.

    Implementations should constrain the model to the declared ports
    (structured output / enum) rather than parsing a port name out of a
    sentence.
    """

    def choose_route(
        self, request: LlmRouteRequest
    ) -> LlmRouteChoice: ...  # pragma: no cover - protocol


@dataclass(frozen=True, slots=True)
class WorkflowResolutionFailure:
    """Why a ``subflow`` reference could not be honoured.

    A version mismatch and a missing workflow want different errors: reporting
    a mismatch as "not found" sends an author looking for a workflow that is
    sitting right there.
    """

    reason: str
    available: int | None = None
    message: str | None = None

    VERSION_MISMATCH = "version-mismatch"
    NOT_FOUND = "not-found"

    @property
    def is_version_mismatch(self) -> bool:
        return self.reason == self.VERSION_MISMATCH


@runtime_checkable
class WorkflowResolver(Protocol):
    """Resolve a workflow reference to a runnable graph.

    ``subflow`` NAMES another workflow rather than embedding it, so the host
    owns where workflows live -- a database, a file, an API.

    ``version`` is a parameter rather than part of the reference string because
    a stringly-typed protocol (``invoice-triage@3``) is one every host invents
    differently. A workflow another workflow depends on is an INTERFACE, and
    interfaces need pins: without one, a parent goes on calling
    ``invoice-triage``, someone edits that child, and the parent runs different
    logic having reported success the whole time.
    """

    def resolve(
        self, ref: str, version: int | None = None
    ) -> FlowGraph | WorkflowResolutionFailure | None: ...  # pragma: no cover - protocol


# -- module state --------------------------------------------------------

_llm_client: LlmClient | None = None
_workflow_resolver: WorkflowResolver | None = None


def set_llm_client(client: LlmClient | None) -> Callable[[], None]:
    """Install the host's LLM client. Returns an unregister callable."""
    global _llm_client
    _llm_client = client

    def unregister() -> None:
        global _llm_client
        if _llm_client is client:
            _llm_client = None

    return unregister


def llm_client() -> LlmClient | None:
    return _llm_client


def llm_unavailable_message() -> str:
    """Why no client is available, phrased as what to do about it."""
    return (
        "No LLM client is registered, so llm_router cannot ask a model which route to "
        "take. Register one with fancy_flow.capabilities.set_llm_client(client) - any "
        "object with choose_route(LlmRouteRequest) -> LlmRouteChoice will do."
    )


def set_workflow_resolver(resolver: WorkflowResolver | None) -> Callable[[], None]:
    """Install the host's workflow resolver. Returns an unregister callable."""
    global _workflow_resolver
    _workflow_resolver = resolver

    def unregister() -> None:
        global _workflow_resolver
        if _workflow_resolver is resolver:
            _workflow_resolver = None

    return unregister


def workflow_resolver() -> WorkflowResolver | None:
    return _workflow_resolver


def status() -> dict[str, bool]:
    """Which capabilities are currently satisfied.

    Exists so a host -- or an agent over MCP -- can answer "what does this
    graph need that I have not wired?" BEFORE a run fails halfway through.
    """
    return {
        "llm": _llm_client is not None,
        "workflow_resolver": _workflow_resolver is not None,
    }


def reset() -> None:
    """Clear everything. Test isolation."""
    global _llm_client, _workflow_resolver
    _llm_client = None
    _workflow_resolver = None
