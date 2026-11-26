"""Ratel Platform API client: skills, suggestions, intents, jobs and runtime catalog overrides."""

from __future__ import annotations

from .client import RatelPlatformClient
from .config import (
    API_KEY_ENV,
    BASE_URL_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    PlatformConfig,
    PlatformSwitch,
    platform_switch_on,
    resolve_platform_config,
)
from .errors import PlatformError, PlatformErrorCode
from .intents import IntentsClient
from .jobs import JobsClient
from .runtime_catalog import RuntimeCatalogClient
from .skills import SkillsClient
from .suggestions import SuggestionsClient
from .transport import (
    PlatformHttpClient,
    PlatformLogEvent,
    PlatformLogger,
    PlatformResponse,
    PlatformTransport,
    TransportRequest,
    TransportResponse,
    UrllibTransport,
    console_log_event,
)
from .types import *  # noqa: F403 - the wire types are part of the public API
from .types import __all__ as _types_all
from .wire import (
    canonical_set,
    canonical_set_v2,
    canonical_skill,
    canonical_skill_v2,
    if_none_match_matches,
    resolve,
    resolve_v2,
)

__all__ = [
    "API_KEY_ENV",
    "BASE_URL_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "PlatformConfig",
    "PlatformSwitch",
    "PlatformError",
    "PlatformErrorCode",
    "PlatformHttpClient",
    "PlatformLogEvent",
    "PlatformLogger",
    "PlatformResponse",
    "PlatformTransport",
    "IntentsClient",
    "JobsClient",
    "RatelPlatformClient",
    "RuntimeCatalogClient",
    "SkillsClient",
    "SuggestionsClient",
    "TransportRequest",
    "TransportResponse",
    "UrllibTransport",
    "canonical_set",
    "canonical_set_v2",
    "canonical_skill",
    "canonical_skill_v2",
    "console_log_event",
    "if_none_match_matches",
    "resolve",
    "platform_switch_on",
    "resolve_platform_config",
    "resolve_v2",
    *_types_all,
]
