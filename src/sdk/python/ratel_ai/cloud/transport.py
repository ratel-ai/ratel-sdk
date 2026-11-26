"""The HTTP layer of the Ratel Cloud client.

`CloudHttpClient` joins the base URL, sends Bearer auth and JSON, and maps
failures to `CloudError`. It sends bytes through a `CloudTransport`: the default
`UrllibTransport` uses the standard library only, and tests replace it (see
`ratel_ai.cloud.testing.MockCloud`). There are no retries: mutations are not
idempotent, and the caller can send a read again.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import CloudConfig
from .errors import CloudError, error_from_response

__all__ = [
    "CloudHttpClient",
    "CloudLogEvent",
    "CloudLogger",
    "CloudResponse",
    "CloudTransport",
    "TransportRequest",
    "TransportResponse",
    "UrllibTransport",
    "console_log_event",
]


@dataclass(frozen=True)
class TransportRequest:
    """One HTTP request, as `CloudTransport.send` receives it.

    Attributes:
        method: The HTTP method, e.g. ``"GET"``.
        url: The absolute URL, including the query string.
        headers: Request headers with lowercase names.
        body: The encoded body, or `None` for no body.
        timeout: The timeout in seconds.
    """

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float


@dataclass(frozen=True)
class TransportResponse:
    """One HTTP response, as `CloudTransport.send` returns it.

    Any status is a response, including 4xx, 5xx and 304. Raise only when no
    response arrived (connection failure, timeout).

    Attributes:
        status: The HTTP status.
        headers: Response headers. Names can have any case.
        body: The raw body bytes.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes


@runtime_checkable
class CloudTransport(Protocol):
    """Sends one HTTP request to Ratel Cloud.

    The default `UrllibTransport` talks to the network. Tests inject an
    in-process transport such as `ratel_ai.cloud.testing.MockCloud`.
    """

    async def send(self, request: TransportRequest) -> TransportResponse:
        """Send `request` and return its response, whatever its status."""
        ...


@dataclass(frozen=True)
class CloudLogEvent:
    """A structured request, response or error event for logging.

    The auth header is never included.

    Attributes:
        phase: ``"request"``, ``"response"`` or ``"error"``.
        method: The HTTP method.
        path: The path, including the query string.
        url: The absolute URL.
        status: The HTTP status (``"response"`` only).
        duration_ms: Milliseconds since the request started (``"response"`` and ``"error"``).
        body: The parsed response JSON (``"response"`` only).
        error: The failure message (``"error"`` only).
    """

    phase: Literal["request", "response", "error"]
    method: str
    path: str
    url: str
    status: int | None = None
    duration_ms: float | None = None
    body: Any = None
    error: str | None = None


#: A log sink that receives each `CloudLogEvent`.
CloudLogger = Callable[[CloudLogEvent], None]


@dataclass(frozen=True)
class CloudResponse:
    """A response that `CloudHttpClient.request` accepted.

    Attributes:
        status: The HTTP status.
        headers: Response headers with lowercase names.
        json: The parsed JSON body, or `None` when the body is empty or not JSON.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    json: Any = None


class CloudHttpClient:
    """Base-URL join, Bearer auth, JSON in and out, typed errors."""

    def __init__(
        self,
        config: CloudConfig,
        *,
        transport: CloudTransport | None = None,
        logger: CloudLogger | None = None,
        debug: bool = False,
    ) -> None:
        """Create the client.

        Args:
            config: The resolved settings.
            transport: The transport to send through. Defaults to `UrllibTransport`.
            logger: A log sink. It takes precedence over ``debug``.
            debug: Print each request and its response or error to stderr
                (never the auth header). Ignored when ``logger`` is set.
        """
        self._config = config
        self._transport: CloudTransport = transport if transport is not None else UrllibTransport()
        sink: CloudLogger | None = logger
        if sink is None and debug:
            sink = console_log_event
        self._sink = sink

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str | None] | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        accept_statuses: Collection[int] = (),
        timeout: float | None = None,
    ) -> CloudResponse:
        """Send one request and return the accepted response.

        Args:
            method: The HTTP method.
            path: The path relative to the base URL, e.g. ``"/skills"``.
            query: Query parameters. `None` values are left out.
            body: A JSON-serializable body, or `None` for no body.
            headers: Extra request headers.
            accept_statuses: Non-2xx statuses to return instead of raising (e.g. 304).
            timeout: Per-call timeout in seconds. Defaults to the configured timeout.

        Returns:
            The response, with its JSON body parsed.

        Raises:
            CloudError: No API key is set (code ``"config"``), no response arrived
                (``"network_error"``), or the status is not accepted.
        """
        api_key = self._config.api_key
        if api_key is None or api_key == "":
            raise CloudError(
                "Ratel Cloud needs an API key: pass api_key or set RATEL_API_KEY",
                status=None,
                code="config",
            )
        suffix = _query_suffix(query)
        url = self._config.base_url + path + suffix
        logged_path = path + suffix

        request_headers = {"authorization": f"Bearer {api_key}"}
        for name, value in (headers or {}).items():
            request_headers[name.lower()] = value
        encoded: bytes | None = None
        if body is not None:
            request_headers["content-type"] = "application/json"
            encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        self._log(CloudLogEvent(phase="request", method=method, path=logged_path, url=url))
        started = time.monotonic()
        try:
            response = await self._transport.send(
                TransportRequest(
                    method=method,
                    url=url,
                    headers=request_headers,
                    body=encoded,
                    timeout=timeout if timeout is not None else self._config.timeout,
                )
            )
        except Exception as err:
            message = str(err) or type(err).__name__
            self._log(
                CloudLogEvent(
                    phase="error",
                    method=method,
                    path=logged_path,
                    url=url,
                    duration_ms=_elapsed_ms(started),
                    error=message,
                )
            )
            raise CloudError(
                f"Ratel Cloud request failed: {message}", status=None, code="network_error"
            ) from err

        parsed = _parse_json(response.body)
        self._log(
            CloudLogEvent(
                phase="response",
                method=method,
                path=logged_path,
                url=url,
                status=response.status,
                duration_ms=_elapsed_ms(started),
                body=parsed,
            )
        )
        ok = 200 <= response.status < 300 or response.status in accept_statuses
        if not ok:
            raise error_from_response(response.status, parsed)
        return CloudResponse(
            status=response.status,
            headers={name.lower(): value for name, value in response.headers.items()},
            json=parsed,
        )

    async def json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send one request and return only its parsed JSON body.

        Args:
            method: The HTTP method.
            path: The path relative to the base URL.
            **kwargs: The keyword arguments of `request`.

        Returns:
            The parsed JSON body.
        """
        return (await self.request(method, path, **kwargs)).json

    def _log(self, event: CloudLogEvent) -> None:
        if self._sink is None:
            return
        # Observability is best-effort: a failing sink must not fail the request.
        with contextlib.suppress(Exception):
            self._sink(event)


class UrllibTransport:
    """The default `CloudTransport`: `urllib` in a worker thread."""

    async def send(self, request: TransportRequest) -> TransportResponse:
        """Send `request` over the network and return its response, whatever its status."""
        return await asyncio.to_thread(self._send_blocking, request)

    def _send_blocking(self, request: TransportRequest) -> TransportResponse:
        prepared = Request(
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            with urlopen(prepared, timeout=request.timeout) as response:
                return TransportResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as err:
            with err:
                body = err.read()
            headers = dict(err.headers.items()) if err.headers is not None else {}
            return TransportResponse(status=err.code, headers=headers, body=body)


def console_log_event(event: CloudLogEvent) -> None:
    """Print one event to stderr. The sink used for ``debug=True``.

    Args:
        event: The event to print.
    """
    if event.phase == "request":
        line = f"[ratel-cloud] -> {event.method} {event.path}"
    elif event.phase == "response":
        line = (
            f"[ratel-cloud] <- {event.status} {event.method} {event.path} "
            f"({event.duration_ms:.0f}ms) {event.body!r}"
        )
    else:
        line = (
            f"[ratel-cloud] x {event.method} {event.path} - {event.error} "
            f"({event.duration_ms:.0f}ms)"
        )
    print(line, file=sys.stderr)


def _query_suffix(query: Mapping[str, str | None] | None) -> str:
    if not query:
        return ""
    encoded = urlencode([(name, value) for name, value in query.items() if value is not None])
    return f"?{encoded}" if encoded else ""


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _parse_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
