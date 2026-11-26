"""The one place that resolves where Ratel Platform is and how to authenticate.

Every Platform feature reads ``api_key`` and ``base_url`` through
`resolve_platform_config`, so one env variable moves all of them. This module
imports nothing from the rest of the Platform code, so any module can import it
without an import cycle.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "API_KEY_ENV",
    "BASE_URL_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "PlatformConfig",
    "PlatformSwitch",
    "platform_switch_on",
    "resolve_platform_config",
]

#: Default production API root. All paths are relative to it.
DEFAULT_BASE_URL = "https://app.ratel.sh/api/v1"

#: Default per-request timeout in seconds.
DEFAULT_TIMEOUT = 30.0

#: Env variable that holds the project API key used when ``api_key`` is omitted.
API_KEY_ENV = "RATEL_API_KEY"

#: Env variable that holds the API root used when ``base_url`` is omitted.
BASE_URL_ENV = "RATEL_BASE_URL"

#: A platform feature that an env switch can turn off.
PlatformSwitch = Literal["events", "intent_graph"]

#: The env switch of each feature. The first name wins when it is set. The
#: second is the deprecated name that the SDK still reads.
_SWITCH_ENV: dict[str, tuple[str, str]] = {
    "events": ("RATEL_PLATFORM_EVENTS", "RATEL_CLOUD_EVENTS"),
    "intent_graph": ("RATEL_PLATFORM_INTENT_GRAPH", "RATEL_CLOUD_INTENT_GRAPH"),
}


@dataclass(frozen=True)
class PlatformConfig:
    """The resolved settings that every Ratel Platform feature shares.

    Attributes:
        api_key: Project API key (``rtl_…``), sent as ``Authorization: Bearer <key>``.
            `None` when no key is set. Never shown by `repr`.
        base_url: API root, including the ``/api/v1`` prefix. Never ends with a slash.
        timeout: Per-request timeout in seconds.
    """

    api_key: str | None
    base_url: str
    timeout: float

    def __repr__(self) -> str:
        """Show the settings with the API key masked."""
        key = "None" if self.api_key is None else "'***'"
        return (
            f"PlatformConfig(api_key={key}, base_url={self.base_url!r}, "
            f"timeout={self.timeout!r})"
        )


def resolve_platform_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> PlatformConfig:
    """Resolve the Platform settings into concrete values.

    Precedence for each value: the argument, then the env variable
    (``RATEL_API_KEY``, ``RATEL_BASE_URL``), then the default. An empty env value
    counts as unset.

    Args:
        api_key: Explicit project API key.
        base_url: Explicit API root.
        timeout: Explicit per-request timeout in seconds.
        env: The environment to read. Defaults to `os.environ`.

    Returns:
        The resolved `PlatformConfig`. ``api_key`` stays `None` when no key is set.
    """
    source = os.environ if env is None else env
    resolved_url = base_url if base_url is not None else _non_empty(source.get(BASE_URL_ENV))
    return PlatformConfig(
        api_key=api_key if api_key is not None else _non_empty(source.get(API_KEY_ENV)),
        base_url=(resolved_url if resolved_url is not None else DEFAULT_BASE_URL).rstrip("/"),
        timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
    )


def platform_switch_on(
    feature: PlatformSwitch, *, env: Mapping[str, str] | None = None
) -> bool:
    """Return whether the env leaves a platform feature on.

    ``off`` (any case, trimmed) turns it off. The ``RATEL_PLATFORM_*`` name wins
    when it is set. Else the deprecated ``RATEL_CLOUD_*`` name applies.
    """
    source = os.environ if env is None else env
    current, deprecated = _SWITCH_ENV[feature]
    value = _non_empty(source.get(current))
    if value is None:
        value = source.get(deprecated)
    return (value or "").strip().lower() != "off"


def _non_empty(value: str | None) -> str | None:
    return None if value is None or value == "" else value
