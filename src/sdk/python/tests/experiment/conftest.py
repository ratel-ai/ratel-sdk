"""Shared fixtures of the experiment tests: in-memory OTel exporters and a runtime."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry._logs import _internal as _logs_internal
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# OpenTelemetry sets its global providers only once per process, and other test
# modules set their own. Each test here swaps in these providers for its duration
# instead, so the global set-once slot stays free for those modules.
_SPANS = InMemorySpanExporter()
_LOGS = InMemoryLogRecordExporter()
_TRACER_PROVIDER = TracerProvider()
_TRACER_PROVIDER.add_span_processor(SimpleSpanProcessor(_SPANS))
_LOGGER_PROVIDER = LoggerProvider()
_LOGGER_PROVIDER.add_log_record_processor(SimpleLogRecordProcessor(_LOGS))


@dataclass
class Otel:
    spans: InMemorySpanExporter
    logs: InMemoryLogRecordExporter

    def spans_named(self, name: str) -> list[Any]:
        return [span for span in self.spans.get_finished_spans() if span.name == name]

    def logs_named(self, name: str) -> list[Any]:
        return [
            readable.log_record
            for readable in self.logs.get_finished_logs()
            if readable.log_record.event_name == name
        ]


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> Iterator[Otel]:
    monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", _TRACER_PROVIDER)
    monkeypatch.setattr(_logs_internal, "_LOGGER_PROVIDER", _LOGGER_PROVIDER)
    _SPANS.clear()
    _LOGS.clear()
    yield Otel(_SPANS, _LOGS)
