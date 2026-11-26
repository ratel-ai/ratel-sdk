from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import pytest

from ratel_ai.cloud import (
    CloudError,
    CloudHttpClient,
    CloudLogEvent,
    TransportRequest,
    TransportResponse,
    UrllibTransport,
    resolve_cloud_config,
)

BASE = "https://x.test/api/v1"


class StubTransport:
    """A `CloudTransport` that records each request and answers with `respond`."""

    def __init__(self, respond: Callable[[TransportRequest], TransportResponse]) -> None:
        self.respond = respond
        self.requests: list[TransportRequest] = []

    async def send(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.respond(request)


class FailingTransport:
    async def send(self, request: TransportRequest) -> TransportResponse:
        raise OSError("socket hangup")


def json_response(body: Any, status: int = 200, **headers: str) -> TransportResponse:
    return TransportResponse(status=status, headers=headers, body=json.dumps(body).encode())


def make_client(transport: Any, **kwargs: Any) -> CloudHttpClient:
    config = resolve_cloud_config(api_key="rtl_k", base_url=BASE, env={})
    return CloudHttpClient(config, transport=transport, **kwargs)


async def test_joins_url_and_query_and_sends_bearer_auth_and_json_body() -> None:
    stub = StubTransport(lambda _: json_response({"ok": True}))
    await make_client(stub).request(
        "POST", "/things", query={"a": "1", "skip": None}, body={"hello": "world"}
    )
    request = stub.requests[0]
    assert request.method == "POST"
    assert request.url == "https://x.test/api/v1/things?a=1"
    assert request.headers["authorization"] == "Bearer rtl_k"
    assert request.headers["content-type"] == "application/json"
    assert request.body == b'{"hello":"world"}'
    assert request.timeout == 30.0


async def test_a_request_without_body_sends_no_content_type() -> None:
    stub = StubTransport(lambda _: json_response({}))
    await make_client(stub).request("GET", "/catalog")
    assert stub.requests[0].body is None
    assert "content-type" not in stub.requests[0].headers


async def test_a_per_call_timeout_overrides_the_default() -> None:
    stub = StubTransport(lambda _: json_response({}))
    await make_client(stub).request("GET", "/x", timeout=2.5)
    assert stub.requests[0].timeout == 2.5


async def test_maps_a_machine_code_body_to_code_and_reason() -> None:
    stub = StubTransport(
        lambda _: json_response({"error": "conflict", "reason": "version_conflict"}, 409)
    )
    with pytest.raises(CloudError) as info:
        await make_client(stub).request("GET", "/x")
    assert info.value.status == 409
    assert info.value.code == "conflict"
    assert info.value.reason == "version_conflict"
    assert str(info.value) == "Ratel Cloud request failed: 409 conflict (version_conflict)"


async def test_keeps_a_display_sentence_as_the_message_and_maps_code_by_status() -> None:
    stub = StubTransport(lambda _: json_response({"error": "Invalid or revoked API key."}, 401))
    with pytest.raises(CloudError) as info:
        await make_client(stub).request("GET", "/x")
    assert info.value.code == "unauthorized"
    assert str(info.value) == "Invalid or revoked API key."


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid"),
        (402, "quota_exceeded"),
        (403, "forbidden"),
        (404, "not_found"),
        (418, "invalid"),
        (429, "quota_exceeded"),
        (500, "server_error"),
        (503, "unavailable"),
    ],
)
async def test_maps_a_non_json_body_by_status(status: int, code: str) -> None:
    stub = StubTransport(lambda _: TransportResponse(status, {}, b"<html>boom</html>"))
    with pytest.raises(CloudError) as info:
        await make_client(stub).request("GET", "/x")
    assert info.value.code == code
    assert info.value.status == status


async def test_wraps_a_transport_failure_as_network_error() -> None:
    with pytest.raises(CloudError) as info:
        await make_client(FailingTransport()).request("GET", "/x")
    assert info.value.code == "network_error"
    assert info.value.status is None
    assert "socket hangup" in str(info.value)


async def test_returns_accepted_statuses_instead_of_raising() -> None:
    stub = StubTransport(lambda _: TransportResponse(304, {"ETag": '"e"'}, b""))
    response = await make_client(stub).request("GET", "/catalog", accept_statuses=(304,))
    assert response.status == 304
    assert response.headers.get("etag") == '"e"'
    assert response.json is None


async def test_logs_request_and_response_events_with_the_query_in_the_path() -> None:
    events: list[CloudLogEvent] = []
    stub = StubTransport(lambda _: json_response({"ok": True}))
    await make_client(stub, logger=events.append).request(
        "GET", "/skills", query={"status": "published"}
    )
    assert [event.phase for event in events] == ["request", "response"]
    assert events[0].method == "GET"
    assert events[0].path == "/skills?status=published"
    assert events[1].status == 200
    assert events[1].body == {"ok": True}
    assert all("rtl_k" not in repr(event) for event in events)


async def test_a_throwing_logger_never_fails_the_request() -> None:
    def broken(_event: CloudLogEvent) -> None:
        raise RuntimeError("broken sink")

    stub = StubTransport(lambda _: json_response({"ok": True}))
    response = await make_client(stub, logger=broken).request("GET", "/skills")
    assert response.json == {"ok": True}


async def test_logs_an_error_event_when_no_response_arrives() -> None:
    events: list[CloudLogEvent] = []
    with pytest.raises(CloudError):
        await make_client(FailingTransport(), logger=events.append).request("GET", "/x")
    assert [event.phase for event in events] == ["request", "error"]
    assert events[1].error is not None and "socket hangup" in events[1].error


async def test_no_api_key_raises_a_config_error_naming_the_env_variable() -> None:
    stub = StubTransport(lambda _: json_response({}))
    config = resolve_cloud_config(base_url=BASE, env={})
    with pytest.raises(CloudError) as info:
        await CloudHttpClient(config, transport=stub).request("GET", "/catalog")
    assert "RATEL_API_KEY" in str(info.value)
    assert info.value.code == "config"
    assert info.value.status is None
    assert stub.requests == []


# — the default urllib transport, against a local http.server ——————————————


class _Recorded:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []


@pytest.fixture
def local_server() -> Iterator[tuple[str, _Recorded]]:
    recorded = _Recorded()

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("content-length") or "0")
            body = self.rfile.read(length) if length else b""
            recorded.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            route = self.path.removeprefix("/api/v1")
            if route.startswith("/slow"):
                time.sleep(0.5)
            if route.startswith("/not-modified"):
                self.send_response(304)
                self.send_header("ETag", '"abc"')
                self.end_headers()
                return
            status = 409 if route.startswith("/conflict") else 200
            payload = (
                {"error": "conflict", "reason": "version_conflict"}
                if status == 409
                else {"ok": True}
            )
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("ETag", '"abc"')
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _handle
        do_POST = _handle
        do_PATCH = _handle

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1", recorded
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def urllib_client(base_url: str, timeout: float = 5.0) -> CloudHttpClient:
    config = resolve_cloud_config(api_key="rtl_k", base_url=base_url, timeout=timeout, env={})
    return CloudHttpClient(config, transport=UrllibTransport())


async def test_urllib_sends_auth_and_json_and_returns_the_body(
    local_server: tuple[str, _Recorded],
) -> None:
    base_url, recorded = local_server
    response = await urllib_client(base_url).request(
        "PATCH", "/skills/sk_1", query={"a": "b c"}, body={"name": "x"}
    )
    assert response.status == 200
    assert response.json == {"ok": True}
    assert response.headers.get("etag") == '"abc"'
    request = recorded.requests[0]
    assert request["method"] == "PATCH"
    assert request["path"] == "/api/v1/skills/sk_1?a=b+c"
    assert request["headers"]["authorization"] == "Bearer rtl_k"
    assert request["headers"]["content-type"] == "application/json"
    assert request["body"] == b'{"name":"x"}'


async def test_urllib_maps_an_error_status_to_a_cloud_error(
    local_server: tuple[str, _Recorded],
) -> None:
    base_url, _ = local_server
    with pytest.raises(CloudError) as info:
        await urllib_client(base_url).request("POST", "/conflict")
    assert info.value.status == 409
    assert info.value.code == "conflict"
    assert info.value.reason == "version_conflict"


async def test_urllib_returns_an_accepted_304_with_its_etag(
    local_server: tuple[str, _Recorded],
) -> None:
    base_url, recorded = local_server
    response = await urllib_client(base_url).request(
        "GET", "/not-modified", headers={"if-none-match": '"abc"'}, accept_statuses=(304,)
    )
    assert response.status == 304
    assert response.json is None
    assert response.headers.get("etag") == '"abc"'
    assert recorded.requests[0]["headers"]["if-none-match"] == '"abc"'


async def test_urllib_timeout_is_a_network_error(local_server: tuple[str, _Recorded]) -> None:
    base_url, _ = local_server
    with pytest.raises(CloudError) as info:
        await urllib_client(base_url, timeout=0.1).request("GET", "/slow")
    assert info.value.code == "network_error"
    assert info.value.status is None


async def test_urllib_connection_refused_is_a_network_error() -> None:
    with pytest.raises(CloudError) as info:
        await urllib_client("http://127.0.0.1:9/api/v1").request("GET", "/x")
    assert info.value.code == "network_error"


async def test_mock_cloud_serves_an_added_route_and_records_requests() -> None:
    from ratel_ai.cloud import RatelCloudClient
    from ratel_ai.cloud.testing import MockCloud, MockRequest, json_response

    mock = MockCloud()
    seen: list[MockRequest] = []

    def receive_events(request: MockRequest) -> TransportResponse:
        seen.append(request)
        return json_response({"accepted": len(request.body["events"])}, status=202)

    mock.add_route("POST", r"/events", receive_events)
    client = RatelCloudClient(
        api_key=mock.api_key, base_url="https://mock.test/api/v1", transport=mock
    )
    http = CloudHttpClient(client.config, transport=mock)
    response = await http.request("POST", "/events", query={"x": "1"}, body={"events": [1, 2]})

    assert response.status == 202
    assert response.json == {"accepted": 2}
    assert seen[0].path == "/events"
    assert seen[0].query == {"x": "1"}
    assert mock.requests[-1].url == "https://mock.test/api/v1/events?x=1"
