"""AI executors.

On AI this package is a **shuttle, not an engine**: it declares the client
contracts and never imports a provider SDK. ``llm_router`` in particular
contains no prompt engineering, no response parsing and no retry policy -- all
of that belongs to the client, which is what lets an opinionated node ship as a
builtin without every consumer inheriting an LLM dependency.

The one thing these nodes DO own is graph integrity, because that is a workflow
concern rather than an AI one: a port the model invents must never route.
"""

from __future__ import annotations

from typing import Any

from .. import capabilities as caps
from ..exceptions import FlowError
from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from ..runtime.ports import Port
from ..schema.graph import PortDescriptor
from .support import expr, structured
from .support.clients import LlmClient, ToolInvoker, VectorStore

__all__ = ["EmbedSearch", "LlmCall", "LlmRouter", "ToolUse"]


class LlmCall:
    """``llm_call`` -- prompt in, completion out, optionally schema-typed."""

    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    def execute(self, ctx: ExecutionContext) -> Any:
        prompt = expr.text(expr.evaluate(ctx.option("prompt", ""), ctx.inputs))
        schema = _schema(ctx.option("response_schema"))

        options = {
            "provider": ctx.option("provider", "anthropic"),
            "model": ctx.option("model"),
            "system": ctx.option("system"),
            "temperature": ctx.option("temperature"),
            "max_tokens": ctx.option("max_tokens"),
            "tools": ctx.option("tools"),
            # Carried to the adapter so a client supporting provider-native
            # structured output can constrain the model, instead of hoping the
            # prompt wording holds.
            "response_schema": schema,
        }
        options = {k: v for k, v in options.items() if v is not None}

        ctx.emit(
            RunEvent.log("info", "llm_call -> " + str(ctx.option("model", "model")), ctx.node.id)
        )

        result = self._llm.complete(prompt, options)

        if schema is None:
            return result

        return self._structured(result, schema, ctx)

    def _structured(
        self, result: dict[str, Any], schema: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Attach ``data`` -- the parsed, schema-checked value.

        An adapter using provider-native structured output should have returned
        ``data`` already; that value is still validated rather than trusted,
        because "the provider promised" is not the same as "the provider did",
        and the whole point of asking for a schema is that the next node can
        rely on the shape.

        With no ``data``, the text is parsed -- the case for every adapter that
        ignores ``response_schema``, which without this would hand a downstream
        ``{{ $json.data.title }}`` nothing and report success.
        """
        data = (
            result["data"] if "data" in result else structured.extract(str(result.get("text", "")))
        )

        errors = structured.validate(data, schema)
        if errors:
            # Loudly, with the reasons. A schema-invalid result flowing on as
            # None is the silent-empty-parse this feature exists to end.
            raise FlowError(
                "The model's response did not match the requested schema: " + "; ".join(errors)
            )

        ctx.emit(RunEvent.log("info", "llm_call -> schema-valid data", ctx.node.id))
        return {**result, "data": data}


def _schema(raw: Any) -> dict[str, Any] | None:
    """Accept a schema as a mapping or as a JSON string.

    The editor's ``json`` field can hand either across, depending on whether
    the host stored the parsed value or the raw text the author typed.
    Accepting one and silently ignoring the other would make the feature work
    on one host and do nothing on another.
    """
    if isinstance(raw, dict):
        return raw or None
    if isinstance(raw, str) and raw.strip() != "":
        import json

        try:
            decoded = json.loads(raw)
        except ValueError as exc:
            raise FlowError(
                "`response_schema` is not valid JSON, so the model cannot be constrained by it."
            ) from exc
        if not isinstance(decoded, dict):
            raise FlowError(
                "`response_schema` is not valid JSON, so the model cannot be constrained by it."
            )
        return decoded
    return None


class LlmRouter:
    """``llm_router`` -- let a model choose which route the flow takes."""

    def __init__(self, client: caps.LlmClient | None = None) -> None:
        #: An explicit client; ``None`` resolves through the capability module.
        self._client = client

    @staticmethod
    def declared_routes(config: dict[str, Any]) -> list[caps.LlmRoute]:
        """The node's declared routes. Blank ports are dropped.

        A half-typed route must not become a real one.
        """
        raw = config.get("routes")
        if not isinstance(raw, (list, tuple)):
            return []
        routes = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            route = caps.LlmRoute.from_dict(entry)
            if route.port != "":
                routes.append(route)
        return routes

    @staticmethod
    def resolve_fallback_port(routes: list[caps.LlmRoute], fallback_enabled: bool) -> str:
        """Where a run goes when the model returns a port that was never offered.

        Emitting on a port with no edge silently ends the branch -- the worst
        failure mode in a workflow engine, because the run then reports SUCCESS
        having done nothing. So: the ``fallback`` port when it exists, else the
        first declared route, and always loudly.
        """
        if fallback_enabled:
            return "fallback"
        return routes[0].port if routes else "out"

    @staticmethod
    def ports(config: dict[str, Any]) -> list[PortDescriptor]:
        """Ports derived from the ``routes`` list.

        The twin of the TypeScript kind's ``outputs: (config) => routePorts()``.
        :class:`NodeKind` declares STATIC ports, so this is exposed as a
        function for hosts (and the editor bridge) to call with a node's config.
        Blank and duplicate ports are dropped so a half-typed route cannot
        collide with a real one.
        """
        ports: list[PortDescriptor] = []
        seen: set[str] = set()
        for route in LlmRouter.declared_routes(config):
            if route.port in seen:
                continue
            seen.add(route.port)
            ports.append(PortDescriptor(route.port, route.port))
        if config.get("fallback", True) is not False and "fallback" not in seen:
            ports.append(PortDescriptor("fallback", "fallback"))
        if not ports:
            ports.append(PortDescriptor("out"))
        return ports

    def execute(self, ctx: ExecutionContext) -> Any:
        config = ctx.config()
        routes = self.declared_routes(config)

        if not routes:
            ctx.abort("llm_router has no routes configured")

        client = self._client or caps.llm_client()
        if client is None:
            # Fail loudly rather than guessing a branch. A silent default here
            # would look like the model made a choice.
            ctx.abort(caps.llm_unavailable_message())

        fallback_enabled = config.get("fallback", True) is not False

        choice = client.choose_route(
            caps.LlmRouteRequest(
                prompt=self._prompt(ctx, config),
                routes=tuple(routes),
                system=_str(config, "system"),
                provider=_str(config, "provider"),
                model=_str(config, "model"),
                credential=_str(config, "credential"),
            )
        )

        offered = {route.port for route in routes}
        port = choice.port
        reason = choice.reason

        if port not in offered:
            safe = self.resolve_fallback_port(routes, fallback_enabled)
            ctx.emit(
                RunEvent.log(
                    "warn",
                    f'llm_router: model returned "{port or "(nothing)"}", which is not a '
                    f'declared route. Routing to "{safe}".',
                    ctx.node.id,
                )
            )
            if reason is None:
                reason = f'unrecognised route "{port}"'
            port = safe

        # The reason travels WITH the value, so a completed run explains itself
        # without needing the model call replayed.
        return Port.only(port, {"route": port, "reason": reason, "input": ctx.inputs})

    def _prompt(self, ctx: ExecutionContext, config: dict[str, Any]) -> str:
        prompt = config.get("prompt")
        if isinstance(prompt, str) and prompt != "":
            return prompt
        # With no prompt configured, route on whatever arrived.
        return expr.text(ctx.inputs)


def _str(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    return value if isinstance(value, str) and value != "" else None


class ToolUse:
    """``tool_use`` -- hand control to a host-registered tool by name."""

    def __init__(self, tools: ToolInvoker) -> None:
        self._tools = tools

    def execute(self, ctx: ExecutionContext) -> Any:
        tool = str(ctx.option("tool", ""))
        args = expr.evaluate(ctx.option("args", {}), ctx.inputs)
        args = args if isinstance(args, dict) else {"value": args}
        return self._tools.invoke(tool, args)


class EmbedSearch:
    """``embed_search`` -- embed a query and search a vector store."""

    def __init__(self, vectors: VectorStore) -> None:
        self._vectors = vectors

    def execute(self, ctx: ExecutionContext) -> Any:
        query = expr.text(expr.evaluate(ctx.option("query", ""), ctx.inputs))
        top_k = int(ctx.option("topK", 5))
        return {"query": query, "matches": self._vectors.search(query, top_k)}
