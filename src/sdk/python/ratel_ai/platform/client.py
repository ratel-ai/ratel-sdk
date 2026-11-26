"""`RatelPlatformClient`: the management and supervised self-improvement client."""

from __future__ import annotations

from collections.abc import Mapping

from .config import resolve_platform_config
from .intents import IntentsClient
from .jobs import JobsClient
from .runtime_catalog import RuntimeCatalogClient
from .skills import SkillsClient
from .suggestions import SuggestionsClient
from .transport import PlatformHttpClient, PlatformLogger, PlatformTransport

__all__ = ["RatelPlatformClient"]


class RatelPlatformClient:
    """The Ratel Platform v1 HTTP API: catalog writes, intent analysis, suggestion review.

    Standard library only: no native code runs here, and all analysis and
    drafting stay on the server. This client does not sync skills into an agent
    at runtime.

    Attributes:
        config: The resolved settings. Its `repr` masks the API key.
        skills: The managed-catalog write surface.
        suggestions: The review surface for machine-drafted proposals.
        intents: Conversation analysis and the intent ledger.
        jobs: Read and wait for async jobs.
        runtime_catalog: The runtime-catalog overrides overlay.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: PlatformTransport | None = None,
        logger: PlatformLogger | None = None,
        debug: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """Create the client. With no arguments it reads ``RATEL_API_KEY`` and ``RATEL_BASE_URL``.

        Args:
            api_key: Project API key. Defaults to ``RATEL_API_KEY``.
            base_url: API root. Defaults to ``RATEL_BASE_URL``, then the production root.
            timeout: Per-request timeout in seconds. Defaults to 30.
            transport: The `PlatformTransport` to send through. Defaults to `UrllibTransport`.
            logger: A log sink for each request, response and error.
            debug: Print each request and its outcome to stderr. Ignored when ``logger`` is set.
            env: The environment to read. Defaults to `os.environ`.
        """
        self.config = resolve_platform_config(
            api_key=api_key, base_url=base_url, timeout=timeout, env=env
        )
        http = PlatformHttpClient(self.config, transport=transport, logger=logger, debug=debug)
        self.skills = SkillsClient(http)
        self.suggestions = SuggestionsClient(http)
        self.intents = IntentsClient(http)
        self.jobs = JobsClient(http)
        self.runtime_catalog = RuntimeCatalogClient(http)
