"""The one place that resolves where Ratel Cloud is and how to authenticate.

Every Cloud feature reads ``api_key`` and ``base_url`` through
`resolve_cloud_config`, so one env variable moves all of them. This module
imports nothing from the rest of the Cloud code, so any module can import it
without an import cycle.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "API_KEY_ENV",
    "BASE_URL_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "CloudConfig",
    "resolve_cloud_config",
]

#: Default production API root. All paths are relative to it.
DEFAULT_BASE_URL = "https://cloud.ratel.sh/api/v1"

#: Default per-request timeout in seconds.
DEFAULT_TIMEOUT = 30.0

#: Env variable that holds the project API key used when ``api_key`` is omitted.
API_KEY_ENV = "RATEL_API_KEY"

#: Env variable that holds the API root used when ``base_url`` is omitted.
BASE_URL_ENV = "RATEL_BASE_URL"


@dataclass(frozen=True)
class CloudConfig:
    """The resolved settings that every Ratel Cloud feature shares.

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
        return f"CloudConfig(api_key={key}, base_url={self.base_url!r}, timeout={self.timeout!r})"


def resolve_cloud_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> CloudConfig:
    """Resolve the Cloud settings into concrete values.

    Precedence for each value: the argument, then the env variable
    (``RATEL_API_KEY``, ``RATEL_BASE_URL``), then the default. An empty env value
    counts as unset.

    Args:
        api_key: Explicit project API key.
        base_url: Explicit API root.
        timeout: Explicit per-request timeout in seconds.
        env: The environment to read. Defaults to `os.environ`.

    Returns:
        The resolved `CloudConfig`. ``api_key`` stays `None` when no key is set.
    """
    source = os.environ if env is None else env
    resolved_url = base_url if base_url is not None else _non_empty(source.get(BASE_URL_ENV))
    return CloudConfig(
        api_key=api_key if api_key is not None else _non_empty(source.get(API_KEY_ENV)),
        base_url=(resolved_url if resolved_url is not None else DEFAULT_BASE_URL).rstrip("/"),
        timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
    )


def _non_empty(value: str | None) -> str | None:
    return None if value is None or value == "" else value
