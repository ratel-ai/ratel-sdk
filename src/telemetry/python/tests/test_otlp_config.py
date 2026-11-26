"""Endpoint defaults of resolve_otlp_config: the Ratel Platform base URL routes.

The Python resolver matches the TypeScript one (``src/sdk/ts/src/platform/otel/config.ts``):
traces go to ``${base_url}/traces`` and logs to ``${base_url}/logs`` unless an endpoint is
set. All cases inject ``env``, so no test reads the process environment or the network.
"""

from __future__ import annotations

from typing import Any

import pytest

from ratel_ai_telemetry.otlp import (
    init,
    ratel_log_exporter,
    ratel_span_exporter,
    resolve_otlp_config,
)

DEFAULT_TRACES_URL = "https://app.ratel.sh/api/v1/traces"
DEFAULT_LOGS_URL = "https://app.ratel.sh/api/v1/logs"


class TestBaseUrlDefault:
    def test_api_key_alone_routes_both_signals_to_ratel_platform(self) -> None:
        cfg = resolve_otlp_config(env={"RATEL_API_KEY": "rtl_env"})

        assert cfg.url == DEFAULT_TRACES_URL
        assert cfg.logs_url == DEFAULT_LOGS_URL

    def test_api_key_argument_alone_routes_both_signals_to_ratel_platform(self) -> None:
        cfg = resolve_otlp_config(api_key="rtl_arg", env={})

        assert cfg.url == DEFAULT_TRACES_URL
        assert cfg.logs_url == DEFAULT_LOGS_URL
        assert cfg.headers["Authorization"] == "Bearer rtl_arg"

    def test_ratel_base_url_moves_both_routes(self) -> None:
        cfg = resolve_otlp_config(env={"RATEL_BASE_URL": "https://staging.example/api/v1"})

        assert cfg.url == "https://staging.example/api/v1/traces"
        assert cfg.logs_url == "https://staging.example/api/v1/logs"

    def test_base_url_argument_wins_over_ratel_base_url(self) -> None:
        cfg = resolve_otlp_config(
            base_url="https://explicit.example/api/v1",
            env={"RATEL_BASE_URL": "https://staging.example/api/v1"},
        )

        assert cfg.url == "https://explicit.example/api/v1/traces"
        assert cfg.logs_url == "https://explicit.example/api/v1/logs"

    def test_trailing_slashes_are_removed_from_the_base_url(self) -> None:
        cfg = resolve_otlp_config(base_url="https://explicit.example/api/v1//", env={})

        assert cfg.url == "https://explicit.example/api/v1/traces"
        assert cfg.logs_url == "https://explicit.example/api/v1/logs"


class TestLogsEndpoint:
    def test_ratel_otlp_logs_endpoint_sets_the_logs_url(self) -> None:
        cfg = resolve_otlp_config(
            env={"RATEL_API_KEY": "rtl_env", "RATEL_OTLP_LOGS_ENDPOINT": "https://logs.example/in"},
        )

        assert cfg.url == DEFAULT_TRACES_URL
        assert cfg.logs_url == "https://logs.example/in"

    def test_logs_endpoint_argument_wins_over_ratel_otlp_logs_endpoint(self) -> None:
        cfg = resolve_otlp_config(
            logs_endpoint="https://explicit-logs.example/in",
            env={"RATEL_API_KEY": "rtl_env", "RATEL_OTLP_LOGS_ENDPOINT": "https://logs.example/in"},
        )

        assert cfg.logs_url == "https://explicit-logs.example/in"


class TestCustomTracesEndpoint:
    def test_endpoint_argument_wins_and_logs_use_the_v1_logs_sibling(self) -> None:
        cfg = resolve_otlp_config(
            endpoint="https://collector.example/v1/traces",
            base_url="https://explicit.example/api/v1",
            env={"RATEL_API_KEY": "rtl_env", "RATEL_OTLP_ENDPOINT": "https://env.example/v1/traces"},
        )

        assert cfg.url == "https://collector.example/v1/traces"
        assert cfg.logs_url == "https://collector.example/v1/logs"

    def test_ratel_otlp_endpoint_wins_and_logs_use_the_v1_logs_sibling(self) -> None:
        cfg = resolve_otlp_config(
            env={
                "RATEL_API_KEY": "rtl_env",
                "RATEL_BASE_URL": "https://staging.example/api/v1",
                "RATEL_OTLP_ENDPOINT": "https://env.example/v1/traces",
            },
        )

        assert cfg.url == "https://env.example/v1/traces"
        assert cfg.logs_url == "https://env.example/v1/logs"


class TestNoDestination:
    def test_error_names_the_key_and_base_url_settings(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            resolve_otlp_config(env={})

        message = str(excinfo.value)
        assert "RATEL_OTLP_ENDPOINT" in message
        assert "RATEL_API_KEY" in message
        assert "RATEL_BASE_URL" in message


class _RecordingExporter:
    """Stands in for an OTLP http exporter class and records the endpoint it receives."""

    endpoints: list[str]

    def __init__(self, *, endpoint: str, headers: dict[str, str]) -> None:
        type(self).endpoints.append(endpoint)

    def export(self, *_args: Any) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


@pytest.fixture()
def recorded_endpoints(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replace both OTLP exporter classes at the OpenTelemetry boundary, so no exporter
    opens a connection and each test reads the endpoint the Ratel helpers chose."""
    from opentelemetry.exporter.otlp.proto.http import _log_exporter, trace_exporter

    recorded: dict[str, list[str]] = {"traces": [], "logs": []}
    span_cls = type("SpanExporter", (_RecordingExporter,), {"endpoints": recorded["traces"]})
    log_cls = type("LogExporter", (_RecordingExporter,), {"endpoints": recorded["logs"]})
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", span_cls)
    monkeypatch.setattr(_log_exporter, "OTLPLogExporter", log_cls)
    for name in ("RATEL_OTLP_ENDPOINT", "RATEL_URL", "RATEL_OTLP_LOGS_ENDPOINT", "RATEL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return recorded


class TestBaseUrlPassThrough:
    def test_span_exporter_uses_the_base_url_route(
        self, recorded_endpoints: dict[str, list[str]]
    ) -> None:
        ratel_span_exporter(base_url="https://staging.example/api/v1")

        assert recorded_endpoints["traces"] == ["https://staging.example/api/v1/traces"]

    def test_log_exporter_uses_the_base_url_route(
        self, recorded_endpoints: dict[str, list[str]]
    ) -> None:
        ratel_log_exporter(base_url="https://staging.example/api/v1")

        assert recorded_endpoints["logs"] == ["https://staging.example/api/v1/logs"]

    def test_init_routes_both_signals_through_the_base_url(
        self, recorded_endpoints: dict[str, list[str]]
    ) -> None:
        handle = init(base_url="https://staging.example/api/v1")
        handle.shutdown()

        assert recorded_endpoints["traces"] == ["https://staging.example/api/v1/traces"]
        assert recorded_endpoints["logs"] == ["https://staging.example/api/v1/logs"]


def test_env_names_and_default_root_match_the_typescript_sdk() -> None:
    from ratel_ai_telemetry import BASE_URL_ENV, DEFAULT_BASE_URL, OTLP_LOGS_ENDPOINT_ENV

    assert OTLP_LOGS_ENDPOINT_ENV == "RATEL_OTLP_LOGS_ENDPOINT"
    assert BASE_URL_ENV == "RATEL_BASE_URL"
    assert DEFAULT_BASE_URL == "https://app.ratel.sh/api/v1"
