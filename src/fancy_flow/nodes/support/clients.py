"""The injectable clients the built-in executors depend on.

Every one is a :class:`typing.Protocol` plus a deterministic, offline default.
That pairing is the reason the core takes **no runtime dependencies**: a node
that needs HTTP declares an ``HttpClient`` rather than importing ``requests``,
so a workflow app that never makes a request does not inherit a networking
stack -- and every test runs offline without patching a transport.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DictStore",
    "EchoHttpClient",
    "EchoLlmClient",
    "EchoToolInvoker",
    "EmptyVectorStore",
    "HttpClient",
    "KeyValueStore",
    "LlmClient",
    "Notifier",
    "RecordingNotifier",
    "ToolInvoker",
    "VectorStore",
]


@runtime_checkable
class HttpClient(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict[str, Any]: ...  # pragma: no cover - protocol


@runtime_checkable
class LlmClient(Protocol):
    """The free-form completion backend ``llm_call`` uses.

    Distinct from :class:`fancy_flow.capabilities.LlmClient`, which is a
    *decision* contract with one method and no prompt.
    """

    def complete(
        self, prompt: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...  # pragma: no cover - protocol


@runtime_checkable
class ToolInvoker(Protocol):
    def invoke(
        self, tool: str, args: dict[str, Any] | None = None
    ) -> Any: ...  # pragma: no cover - protocol


@runtime_checkable
class VectorStore(Protocol):
    def search(self, query: str, top_k: int = 5) -> list[Any]: ...  # pragma: no cover - protocol


@runtime_checkable
class Notifier(Protocol):
    def notify(
        self, channel: str, to: str, message: str
    ) -> None: ...  # pragma: no cover - protocol


@runtime_checkable
class KeyValueStore(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...  # pragma: no cover
    def set(self, key: str, value: Any) -> None: ...  # pragma: no cover
    def delete(self, key: str) -> None: ...  # pragma: no cover
    def has(self, key: str) -> bool: ...  # pragma: no cover
    def all(self) -> dict[str, Any]: ...  # pragma: no cover


# -- deterministic defaults ---------------------------------------------


class EchoHttpClient:
    """Records requests and echoes them back. Never touches a socket."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict[str, Any]:
        headers = headers or {}
        self.requests.append({"method": method, "url": url, "headers": headers, "body": body})
        return {
            "status": 200,
            "headers": headers,
            "body": {"echoed": {"method": method, "url": url, "body": body}},
        }


class EchoLlmClient:
    """Prefixes the prompt with the model name. Deterministic by construction."""

    def __init__(self) -> None:
        self.prompts: list[dict[str, Any]] = []

    def complete(self, prompt: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        self.prompts.append({"prompt": prompt, "options": options})
        model = str(options.get("model") or "echo")
        words = _word_count(prompt)
        return {
            "text": f"[{model}] {prompt}",
            "usage": {"input_tokens": words, "output_tokens": words},
        }


class EchoToolInvoker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        args = args or {}
        self.calls.append({"tool": tool, "args": args})
        return {"tool": tool, "args": args}


class EmptyVectorStore:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, top_k: int = 5) -> list[Any]:
        self.queries.append(query)
        return []


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def notify(self, channel: str, to: str, message: str) -> None:
        self.sent.append({"channel": channel, "to": to, "message": message})


class DictStore:
    """The in-memory :class:`KeyValueStore` the defaults use."""

    def __init__(self, items: dict[str, Any] | None = None) -> None:
        self._items: dict[str, Any] = dict(items or {})

    def get(self, key: str, default: Any = None) -> Any:
        value = self._items.get(key)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        self._items[key] = value

    def delete(self, key: str) -> None:
        self._items.pop(key, None)

    def has(self, key: str) -> bool:
        return key in self._items

    def all(self) -> dict[str, Any]:
        return self._items


#: PHP's `str_word_count` default: runs of letters, apostrophes and hyphens.
#: Reproduced rather than approximated with `split()`, because the echo
#: client's usage numbers are baked into shared parity fixtures.
_WORDS = re.compile(r"[A-Za-z'-]+")


def _word_count(text: str) -> int:
    return len(_WORDS.findall(text))
