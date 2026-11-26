"""The Ratel Platform client error taxonomy.

The v1 surface puts a machine code in ``error`` (``"not_found"``, ``"conflict"``,
``"invalid"``, …) and an optional finer-grained code in ``reason``
(``"version_conflict"``, ``"not_pending"``, …). Auth failures carry a display
sentence instead. A code-shaped ``error`` body wins; otherwise the HTTP status
decides the code.
"""

from __future__ import annotations

from typing import Any, Literal, cast

__all__ = ["PlatformError", "PlatformErrorCode", "error_from_response"]

PlatformErrorCode = Literal[
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "invalid",
    "quota_exceeded",
    "unavailable",
    "server_error",
    "network_error",
    # The client is not configured: no request was sent (for example, no API key).
    "config",
]

_CODES_BY_STATUS: dict[int, PlatformErrorCode] = {
    400: "invalid",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    402: "quota_exceeded",
    429: "quota_exceeded",
    503: "unavailable",
}

_KNOWN_BODY_CODES = frozenset(
    {
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
        "invalid",
        "quota_exceeded",
        "unavailable",
        "server_error",
    }
)


class PlatformError(Exception):
    """A failed Ratel Platform call.

    Attributes:
        status: HTTP status, or `None` when the request never got a response.
        code: The machine code, one of `PlatformErrorCode`.
        reason: Finer-grained server code, e.g. ``"version_conflict"``, or `None`.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None,
        code: PlatformErrorCode,
        reason: str | None = None,
    ) -> None:
        """Create the error.

        Args:
            message: Human-readable message.
            status: HTTP status, or `None` when no response arrived.
            code: The machine code.
            reason: The finer-grained server code, if any.
        """
        super().__init__(message)
        self.status = status
        self.code: PlatformErrorCode = code
        self.reason = reason


def error_from_response(status: int, body: Any) -> PlatformError:
    """Build the typed error for a non-2xx response.

    Args:
        status: The HTTP status.
        body: The parsed JSON body, or `None`.

    Returns:
        The `PlatformError` for this response.
    """
    body_error: str | None = None
    reason: str | None = None
    if isinstance(body, dict):
        if isinstance(body.get("error"), str):
            body_error = body["error"]
        if isinstance(body.get("reason"), str):
            reason = body["reason"]
    known = body_error is not None and body_error in _KNOWN_BODY_CODES
    code = cast(PlatformErrorCode, body_error) if known else _code_for_status(status)
    detail = f" ({reason})" if reason else ""
    # A non-code `error` is a display sentence: keep it as the message.
    if body_error is not None and not known:
        message = body_error
    else:
        message = f"Ratel Platform request failed: {status} {code}{detail}"
    return PlatformError(message, status=status, code=code, reason=reason)


def _code_for_status(status: int) -> PlatformErrorCode:
    code = _CODES_BY_STATUS.get(status)
    if code is not None:
        return code
    return "server_error" if status >= 500 else "invalid"
