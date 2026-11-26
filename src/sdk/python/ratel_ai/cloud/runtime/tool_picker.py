"""`ratel_cloud`: the Ratel Cloud Tool Picker as a `ratel_ai.RankFn` (port of ``tool-picker.ts``).

The picker ranks the tool catalog that the project synced to Cloud (see
`ratel_ai.cloud.runtime.attach`). It sends only ``{query, mode, top_k}``, never
the candidate texts. Every failure raises `ratel_ai.RetrieverError` with the
same ``code`` and ``transient`` value as the TypeScript SDK, so a transient
failure of a reranker falls back to the first stage's order.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Literal
from urllib.error import URLError

from ..._custom_ranking import RankCandidate, RankedId
from ...exceptions import RetrieverError
from ..client import RatelCloudClient
from ..config import CloudConfig, resolve_cloud_config
from ..transport import CloudTransport, TransportRequest, TransportResponse, UrllibTransport

__all__ = ["RatelCloud", "ToolPicker", "ToolPickerMode", "ratel_cloud"]

#: How the Tool Picker ranks: ``"instant"`` is BM25 only (free), ``"precise"`` puts
#: a BM25 shortlist in front of Jev, and ``"exhaustive"`` asks Jev about the whole
#: catalog (slow: Cloud stops at 45 s).
ToolPickerMode = Literal["instant", "precise", "exhaustive"]

#: Cloud's ``top_k`` ceiling for ``/tools/pick``.
_MAX_TOP_K = 20
#: Cloud rejects longer queries with a 400, which would fail a reranker instead of
#: falling back.
_MAX_QUERY_CHARS = 2_000
_PICK_TIMEOUT = 15.0
#: Above Cloud's 45 s pick deadline, so its 504 arrives before the client gives up.
_EXHAUSTIVE_TIMEOUT = 60.0
_MODES = ("instant", "precise", "exhaustive")
_WHOLE_SECONDS = re.compile(r"\d+")


class ToolPicker:
    """A `ratel_ai.RankFn` that ranks with the Ratel Cloud Tool Picker.

    Pass it as ``retrieve_fn`` (with ``method="custom"``) or as ``reranker_fn``.
    It is async: the catalog awaits it.

    Attributes:
        mode: The `ToolPickerMode` this picker asks in.
    """

    def __init__(self, settings: _Settings, mode: ToolPickerMode) -> None:
        """Create the picker. Use `ratel_cloud` instead."""
        self._settings = settings
        self.mode: ToolPickerMode = mode

    async def __call__(
        self, query: str, candidates: list[RankCandidate], top_k: int
    ) -> list[RankedId]:
        """Rank the project's synced tools for ``query``.

        The query is cut to 2000 characters and ``top_k`` to 20. Skill candidates,
        no candidates or a ``top_k`` of zero return ``[]`` without a request.

        Args:
            query: What the caller wants to do.
            candidates: The catalog items the SDK offers. Only their kind is read.
            top_k: How many tools to return.

        Returns:
            The picked ids and scores, best first.

        Raises:
            RetrieverError: The pick failed. See the README for the codes.
        """
        # The picker ranks the project's synced tool catalog; it knows no skills. The
        # SDK calls a RankFn once per catalog, so every candidate has the first one's kind.
        if not candidates or top_k <= 0 or candidates[0].kind == "skill":
            return []
        config = self._settings.config()
        if not config.api_key:
            raise RetrieverError(
                "Ratel Cloud Tool Picker needs an API key: pass api_key or set RATEL_API_KEY",
                "Config",
            )
        timeout = self._settings.timeout
        if timeout is None:
            timeout = _EXHAUSTIVE_TIMEOUT if self.mode == "exhaustive" else _PICK_TIMEOUT
        body = {"query": _truncate(query), "mode": self.mode, "top_k": min(top_k, _MAX_TOP_K)}
        request = TransportRequest(
            method="POST",
            url=f"{config.base_url}/tools/pick",
            headers={
                "authorization": f"Bearer {config.api_key}",
                "content-type": "application/json",
            },
            body=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        try:
            response = await asyncio.wait_for(self._settings.transport.send(request), timeout)
        except Exception as error:
            if _is_timeout(error):
                raise RetrieverError(
                    f"Ratel Cloud Tool Picker did not answer within {timeout:g} s",
                    "Timeout",
                    transient=True,
                ) from error
            raise RetrieverError(
                f"Ratel Cloud Tool Picker is unreachable: {_error_text(error)}",
                "Unreachable",
                transient=True,
            ) from error

        if not 200 <= response.status < 300:
            raise _http_error(response)
        ranked = _parse_ranked(response.body)
        if ranked is None:
            raise RetrieverError(
                "Ratel Cloud Tool Picker returned an unexpected response",
                "Malformed",
                transient=True,
                status=response.status,
            )
        return ranked

    def with_mode(self, mode: ToolPickerMode) -> ToolPicker:
        """Return the same picker with another mode. This picker does not change.

        Args:
            mode: ``"instant"``, ``"precise"`` or ``"exhaustive"``.

        Returns:
            The new picker.

        Raises:
            ValueError: ``mode`` is not a known mode.
        """
        if mode not in _MODES:
            raise ValueError(f"unknown Tool Picker mode {mode!r}; use one of {_MODES}")
        return ToolPicker(self._settings, mode)


class RatelCloud:
    """The Ratel Cloud API from one configuration: the Tool Picker and the management clients.

    Attributes:
        tool_picker: Ranks tools with the Tool Picker, in ``"precise"`` mode.
        skills: Skill catalog management: create, update, publish and import skills.
        suggestions: Review of the improvement suggestions that Cloud drafts.
        intents: Intent analysis of conversations and the recurring-ask ledger.
        jobs: Status of the asynchronous jobs that Cloud runs.
        runtime_catalog: The Cloud-owned retrieval descriptions of the runtime catalog.
    """

    def __init__(self, settings: _Settings, mode: ToolPickerMode) -> None:
        """Create the handle. Use `ratel_cloud` instead."""
        client = settings.client()
        self.tool_picker = ToolPicker(settings, mode)
        self.skills = client.skills
        self.suggestions = client.suggestions
        self.intents = client.intents
        self.jobs = client.jobs
        self.runtime_catalog = client.runtime_catalog


def ratel_cloud(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    transport: CloudTransport | None = None,
    env: Mapping[str, str] | None = None,
) -> RatelCloud:
    """Return the Ratel Cloud API of one configuration, with the Tool Picker in ``precise`` mode.

    Builds no connection: each client sends its first request when the caller
    calls it.

    Args:
        api_key: Project API key. Defaults to ``RATEL_API_KEY``, read on each pick.
        base_url: API root, including ``/api/v1``. Defaults to ``RATEL_BASE_URL``,
            read on each pick, then production.
        timeout: Per-pick timeout in seconds. Defaults to 15, or 60 in
            ``"exhaustive"`` mode.
        transport: The HTTP transport. Defaults to `UrllibTransport`.
        env: The environment to read. Defaults to `os.environ`.

    Returns:
        The `RatelCloud` handle.
    """
    settings = _Settings(api_key, base_url, timeout, transport, env)
    return RatelCloud(settings, "precise")


class _Settings:
    """The options of one `ratel_cloud` call. The config resolves again on each pick."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float | None,
        transport: CloudTransport | None,
        env: Mapping[str, str] | None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.transport: CloudTransport = transport if transport is not None else UrllibTransport()
        self.env = env

    def config(self) -> CloudConfig:
        return resolve_cloud_config(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, env=self.env
        )

    def client(self) -> RatelCloudClient:
        return RatelCloudClient(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
            env=self.env,
        )


def _http_error(response: TransportResponse) -> RetrieverError:
    status = response.status
    detail = f"HTTP {status}: {_error_message(response)}"
    if status in (400, 413):
        return RetrieverError(
            f"Ratel Cloud Tool Picker rejected the request ({detail})",
            "InvalidRequest",
            status=status,
        )
    if status in (401, 403):
        return RetrieverError(
            f"Ratel Cloud rejected the API key ({detail})", "Unauthorized", status=status
        )
    if status == 402:
        return RetrieverError(
            f"Ratel Cloud Tool Picker is out of credits ({detail})",
            "InsufficientCredits",
            status=status,
        )
    if status == 409:
        return RetrieverError(
            f"Ratel Cloud has no synced tools for this project ({detail}). Call attach(runtime) "
            "from ratel_ai.cloud.runtime, register your tools, then await handle.flush() so "
            "the catalog snapshot lands before the first search.",
            "NoSyncedTools",
            status=status,
        )
    if status == 429:
        return RetrieverError(
            f"Ratel Cloud Tool Picker is rate limited ({detail})",
            "RateLimited",
            transient=True,
            status=status,
            retry_after_secs=_retry_after_seconds(_header(response, "retry-after")),
        )
    if status in (502, 503):
        return RetrieverError(
            f"Ratel Cloud Tool Picker is unavailable ({detail})",
            "Unavailable",
            transient=True,
            status=status,
        )
    if status == 504:
        return RetrieverError(
            f"Ratel Cloud Tool Picker timed out ({detail})",
            "Timeout",
            transient=True,
            status=status,
        )
    return RetrieverError(
        f"Ratel Cloud Tool Picker failed ({detail})", "Http", transient=status >= 500, status=status
    )


def _error_message(response: TransportResponse) -> str:
    """Read the ``{"error": {"message", "type"}}`` envelope, else the raw text."""
    text = response.body.decode("utf-8", errors="replace")
    try:
        body: Any = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return text[:500] or _status_phrase(response.status) or "no response body"


def _parse_ranked(raw: bytes) -> list[RankedId] | None:
    try:
        body: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("tools"), list):
        return None
    ranked: list[RankedId] = []
    for tool in body["tools"]:
        if not isinstance(tool, dict):
            return None
        tool_id, score = tool.get("id"), tool.get("score")
        if (
            not isinstance(tool_id, str)
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
        ):
            return None
        ranked.append({"id": tool_id, "score": float(score)})
    return ranked


def _truncate(query: str) -> str:
    """Cut to Cloud's limit, counted in UTF-16 units as Cloud counts, without splitting a pair."""
    if len(query) <= _MAX_QUERY_CHARS // 2:
        return query
    units = 0
    for index, char in enumerate(query):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > _MAX_QUERY_CHARS:
            return query[:index]
    return query


def _retry_after_seconds(value: str | None) -> int | None:
    """Whole seconds only; an HTTP date is ignored."""
    if value is None or _WHOLE_SECONDS.fullmatch(value.strip()) is None:
        return None
    return int(value.strip())


def _header(response: TransportResponse, name: str) -> str | None:
    for key, value in response.headers.items():
        if key.lower() == name:
            return value
    return None


def _is_timeout(error: BaseException) -> bool:
    # `socket.timeout` is an alias of `TimeoutError` only from Python 3.10.
    timeouts = (TimeoutError, asyncio.TimeoutError, socket.timeout)
    if isinstance(error, timeouts):
        return True
    return isinstance(error, URLError) and isinstance(error.reason, timeouts)


def _status_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__
