"""Ratel Cloud API client: skills, suggestions, intents, jobs and runtime catalog overrides."""

from __future__ import annotations

from .client import RatelCloudClient
from .config import (
    API_KEY_ENV,
    BASE_URL_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    CloudConfig,
    resolve_cloud_config,
)
from .errors import CloudError, CloudErrorCode
from .intents import IntentsClient
from .jobs import JobsClient
from .runtime_catalog import RuntimeCatalogClient
from .skills import SkillsClient
from .suggestions import SuggestionsClient
from .transport import (
    CloudHttpClient,
    CloudLogEvent,
    CloudLogger,
    CloudResponse,
    CloudTransport,
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
    "CloudConfig",
    "CloudError",
    "CloudErrorCode",
    "CloudHttpClient",
    "CloudLogEvent",
    "CloudLogger",
    "CloudResponse",
    "CloudTransport",
    "IntentsClient",
    "JobsClient",
    "RatelCloudClient",
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
    "resolve_cloud_config",
    "resolve_v2",
    *_types_all,
]
