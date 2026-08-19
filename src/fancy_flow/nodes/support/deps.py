"""What the built-in executors are wired to.

Every field defaults to a deterministic offline implementation, so
``ExecutorDeps()`` yields a fully working executor set with no network, no
provider SDK and no configuration. Pass real clients to connect the builtins to
the outside world; an adapter package builds this from a host's container.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clients import (
    DictStore,
    EchoHttpClient,
    EchoLlmClient,
    EchoToolInvoker,
    EmptyVectorStore,
    HttpClient,
    KeyValueStore,
    LlmClient,
    Notifier,
    RecordingNotifier,
    ToolInvoker,
    VectorStore,
)

__all__ = ["ExecutorDeps"]


@dataclass(slots=True)
class ExecutorDeps:
    http: HttpClient = field(default_factory=EchoHttpClient)
    llm: LlmClient = field(default_factory=EchoLlmClient)
    tools: ToolInvoker = field(default_factory=EchoToolInvoker)
    vectors: VectorStore = field(default_factory=EmptyVectorStore)
    notifier: Notifier = field(default_factory=RecordingNotifier)
    memory: KeyValueStore = field(default_factory=DictStore)
    data: KeyValueStore = field(default_factory=DictStore)
