"""IO executors -- outbound HTTP.

Named ``io_`` so it cannot be mistaken for the standard library's ``io`` in a
traceback. Both nodes take an injected :class:`HttpClient`; core never imports
an HTTP library.
"""

from __future__ import annotations

from typing import Any

from ..runtime.context import ExecutionContext
from ..runtime.events import RunEvent
from .support import expr
from .support.clients import HttpClient

__all__ = ["ApiRequest", "WebhookOut"]


class ApiRequest:
    """``api_request`` -- an HTTP request to any URL.

    Returns the client's ``{status, headers, body}`` response verbatim: a node
    that reshapes it would make every host's error handling guess.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def execute(self, ctx: ExecutionContext) -> Any:
        method = str(ctx.option("method", "GET")).upper()
        url = expr.text(expr.evaluate(ctx.option("url", ""), ctx.inputs))
        headers = ctx.option("headers", {})
        headers = headers if isinstance(headers, dict) else {}
        body = expr.evaluate(ctx.option("body"), ctx.inputs)

        ctx.emit(RunEvent.log("info", f"api_request {method} {url}", ctx.node.id))

        return self._http.send(method, url, headers, body)


class WebhookOut:
    """``webhook_out`` -- POST a payload to a configured URL."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def execute(self, ctx: ExecutionContext) -> Any:
        url = expr.text(expr.evaluate(ctx.option("url", ""), ctx.inputs))
        headers = ctx.option("headers", {})
        headers = headers if isinstance(headers, dict) else {}
        payload = expr.evaluate(ctx.option("payload"), ctx.inputs)

        ctx.emit(RunEvent.log("info", f"webhook_out -> {url}", ctx.node.id))
        response = self._http.send("POST", url, headers, payload)

        return {
            "sent": True,
            "status": response.get("status"),
            "response": response.get("body"),
        }
