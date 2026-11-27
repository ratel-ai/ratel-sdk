"""`ratel_ai.serve`: the run contract of `protocol/sandbox/v1` for a Python agent image."""

from __future__ import annotations

import ast
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import pytest

from ratel_ai.serve import (
    RunArgs,
    ServeHandle,
    ToolCallError,
    current_run,
    record_usage,
    start_server,
    tool,
    validate_run_request,
)

ROOT = Path(__file__).resolve().parents[4]
SERVE_FILE = Path(__file__).resolve().parents[1] / "ratel_ai" / "serve.py"
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.fixture
def start() -> Iterator[Callable[..., str]]:
    handles: list[ServeHandle] = []

    def _start(run: Callable[[RunArgs], Any], **options: Any) -> str:
        handle = start_server(run, port=0, host="127.0.0.1", **options)
        handles.append(handle)
        return f"http://127.0.0.1:{handle.port}"

    yield _start
    for handle in handles:
        handle.close()


def request(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "v": 1,
        "runId": "run_1",
        "input": {"message": "hi"},
        "params": {},
        "tools": {},
        "fixtures": [],
    }
    body.update(overrides)
    return body


def call(base: str, path: str, *, method: str = "GET", body: Any = None) -> tuple[int, Any]:
    data = None
    if body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
    req = urllib.request.Request(
        base + path, data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def post(base: str, body: Any) -> tuple[int, Any]:
    return call(base, "/ratel/run", method="POST", body=body)


# --- serve -------------------------------------------------------------------------------------


def test_answers_health_with_200(start: Callable[..., str]) -> None:
    base = start(lambda args: "ok")
    assert call(base, "/ratel/health") == (200, {"ok": True})


def test_reads_port_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "0")
    handle = start_server(lambda args: "ok", host="127.0.0.1")
    try:
        assert handle.port > 0
    finally:
        handle.close()


def test_refuses_a_port_that_is_not_a_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "eighty")
    with pytest.raises(ValueError, match="PORT"):
        start_server(lambda args: "ok", host="127.0.0.1")


def test_calls_run_with_input_params_tools_and_run_id(start: Callable[..., str]) -> None:
    seen: list[RunArgs] = []

    def run(args: RunArgs) -> Any:
        seen.append(args)
        return {"echo": args.input, "tone": args.params["tone"]}

    base = start(run)
    tools = {"lookup_order": {"mode": "live"}}
    status, body = post(base, request(params={"tone": "dry"}, tools=tools))

    assert status == 200
    assert body == {"output": {"echo": {"message": "hi"}, "tone": "dry"}, "toolCalls": []}
    assert seen == [
        RunArgs(
            input={"message": "hi"},
            params={"tone": "dry"},
            tools=tools,
            run_id="run_1",
            traceparent=None,
        )
    ]


def test_runs_an_async_run_function(start: Callable[..., str]) -> None:
    async def run(args: RunArgs) -> Any:
        await asyncio.sleep(0.001)
        return {"async": args.input}

    base = start(run)
    assert post(base, request()) == (200, {"output": {"async": {"message": "hi"}}, "toolCalls": []})


def test_answers_null_when_run_returns_none(start: Callable[..., str]) -> None:
    base = start(lambda args: None)
    assert post(base, request())[1]["output"] is None


def test_gives_run_id_and_traceparent_to_the_run_scope(start: Callable[..., str]) -> None:
    async def run(args: RunArgs) -> Any:
        await asyncio.sleep(0.001)
        info = current_run()
        assert info is not None
        return {"runId": info.run_id, "traceparent": info.traceparent, "args": args.traceparent}

    base = start(run)
    _, body = post(base, request(traceparent=TRACEPARENT))
    assert body["output"] == {"runId": "run_1", "traceparent": TRACEPARENT, "args": TRACEPARENT}


def test_current_run_is_none_outside_a_run() -> None:
    assert current_run() is None


def test_adds_the_usage_that_the_run_records(start: Callable[..., str]) -> None:
    def run(args: RunArgs) -> Any:
        record_usage(input_tokens=10, output_tokens=2, cost_usd=0.01)
        record_usage(input_tokens=5, output_tokens=1)
        return "done"

    base = start(run)
    assert post(base, request())[1]["usage"] == {
        "inputTokens": 15,
        "outputTokens": 3,
        "costUsd": 0.01,
    }


def test_refuses_an_invalid_request_with_400_and_the_error_shape(
    start: Callable[..., str],
) -> None:
    calls: list[RunArgs] = []
    base = start(calls.append)
    status, body = post(base, {"v": 1, "input": 1, "params": {}, "tools": {}, "fixtures": []})
    assert status == 400
    assert body == {"error": {"code": "invalid_request", "message": "/runId is required"}}
    assert calls == []


def test_refuses_a_body_that_is_not_json_with_400(start: Callable[..., str]) -> None:
    base = start(lambda args: "ok")
    status, body = post(base, "{not json")
    assert status == 400
    assert body["error"]["code"] == "invalid_request"


def test_refuses_a_body_that_is_too_large_with_413(start: Callable[..., str]) -> None:
    base = start(lambda args: "ok", max_body_bytes=64)
    status, body = post(base, request(input="x" * 200))
    assert status == 413
    assert body["error"]["code"] == "invalid_request"


def test_answers_500_with_the_error_shape_when_run_raises(start: Callable[..., str]) -> None:
    def run(args: RunArgs) -> Any:
        raise RuntimeError("model is down")

    base = start(run)
    assert post(base, request()) == (
        500,
        {"error": {"code": "run_failed", "message": "model is down"}},
    )


def test_keeps_the_code_of_a_raised_error_when_it_is_valid(start: Callable[..., str]) -> None:
    class Timeout(Exception):
        code = "timeout"

    def run(args: RunArgs) -> Any:
        raise Timeout("too slow")

    base = start(run)
    assert post(base, request())[1]["error"] == {"code": "timeout", "message": "too slow"}


def test_answers_500_when_the_output_is_not_json(start: Callable[..., str]) -> None:
    base = start(lambda args: {"value": object()})
    status, body = post(base, request())
    assert status == 500
    assert body["error"]["code"] == "invalid_response"


def test_answers_404_for_another_path(start: Callable[..., str]) -> None:
    base = start(lambda args: "ok")
    status, body = call(base, "/other")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_answers_405_for_a_wrong_method(start: Callable[..., str]) -> None:
    base = start(lambda args: "ok")
    assert call(base, "/ratel/run")[0] == 405
    assert call(base, "/ratel/health", method="POST", body={})[0] == 405


def test_redacts_the_values_of_the_secrets(
    start: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_SECRET_NAMES", "ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-very-secret")

    def run(args: RunArgs) -> Any:
        raise RuntimeError("bad key sk-ant-very-secret")

    base = start(run)
    assert post(base, request())[1]["error"]["message"] == "bad key [redacted]"


def test_redacts_the_variants_of_a_secret_in_the_output(
    start: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "s3cret/value+1"
    monkeypatch.setenv("SERVE_TEST_SECRET", secret)
    base = start(
        lambda args: {
            "plain": f"key={secret}",
            "url": "q=s3cret%2Fvalue%2B1",
            "base64": "czNjcmV0L3ZhbHVlKzE=",
            secret: "as a key",
        },
        secrets=["SERVE_TEST_SECRET"],
    )
    assert post(base, request())[1]["output"] == {
        "plain": "key=[redacted]",
        "url": "q=[redacted]",
        "base64": "[redacted]",
        "[redacted]": "as a key",
    }


# --- tool modes --------------------------------------------------------------------------------

real_calls: list[dict[str, Any]] = []


def refund(id: str) -> dict[str, Any]:
    real_calls.append({"id": id})
    return {"refunded": id, "real": True}


refund_order = tool("refund_order", execute=refund, fake=lambda id: {"refunded": id, "real": False})
cancel_order = tool("cancel_order", execute=lambda **args: {"cancelled": True})


def run_twice(args: RunArgs) -> list[Any]:
    results: list[Any] = []
    for _ in range(2):
        try:
            results.append(refund_order(id="42"))
        except ToolCallError as error:
            results.append({"thrown": str(error)})
    return results


def run_with(
    start: Callable[..., str], tools: dict[str, Any], fixtures: list[Any] | None = None
) -> Any:
    real_calls.clear()
    base = start(run_twice)
    return post(base, request(tools=tools, fixtures=fixtures or []))[1]


def test_simulated_answers_from_the_fake_handler(start: Callable[..., str]) -> None:
    body = run_with(start, {"refund_order": {"mode": "simulated"}})
    assert real_calls == []
    assert body["output"][0] == {"refunded": "42", "real": False}
    assert body["toolCalls"][0] == {
        "name": "refund_order",
        "args": {"id": "42"},
        "mode": "simulated",
        "result": {"refunded": "42", "real": False},
    }


def test_simulated_is_the_default_for_a_tool_the_request_does_not_name(
    start: Callable[..., str],
) -> None:
    body = run_with(start, {})
    assert real_calls == []
    assert body["toolCalls"][0]["mode"] == "simulated"


def test_recorded_answers_from_the_fixture_with_normalized_args_in_order(
    start: Callable[..., str],
) -> None:
    fixtures = [
        {"tool": "refund_order", "args": {"id": "42"}, "result": {"from": "first"}},
        {"tool": "refund_order", "args": {"id": "42"}, "result": {"from": "second"}},
        {"tool": "refund_order", "args": {"id": "7"}, "result": {"from": "other"}},
    ]
    body = run_with(start, {"refund_order": {"mode": "recorded"}}, fixtures)
    assert real_calls == []
    assert body["output"] == [{"from": "first"}, {"from": "second"}]
    assert [c["mode"] for c in body["toolCalls"]] == ["recorded", "recorded"]


def test_recorded_matches_args_whatever_the_order_of_their_keys(
    start: Callable[..., str],
) -> None:
    search = tool("search", execute=lambda query: None)
    base = start(lambda args: search({"filters": {"b": 2, "a": 1}, "q": "lamp"}))
    fixture = {"tool": "search", "args": {"q": "lamp", "filters": {"a": 1, "b": 2}}, "result": [1]}
    body = post(base, request(tools={"search": {"mode": "recorded"}}, fixtures=[fixture]))[1]
    assert body["output"] == [1]


def test_the_last_equal_fixture_repeats(start: Callable[..., str]) -> None:
    fixtures = [{"tool": "refund_order", "args": {"id": "42"}, "result": {"from": "only"}}]
    body = run_with(start, {"refund_order": {"mode": "recorded"}}, fixtures)
    assert body["output"] == [{"from": "only"}, {"from": "only"}]


def test_recorded_with_no_fixture_fails_the_call_with_no_fixture(
    start: Callable[..., str],
) -> None:
    body = run_with(start, {"refund_order": {"mode": "recorded"}})
    assert body["toolCalls"][0]["mode"] == "recorded"
    assert body["toolCalls"][0]["error"]["code"] == "no_fixture"
    assert "refund_order" in body["output"][0]["thrown"]


def test_live_runs_the_real_function(start: Callable[..., str]) -> None:
    body = run_with(start, {"refund_order": {"mode": "live"}})
    assert real_calls == [{"id": "42"}, {"id": "42"}]
    assert body["toolCalls"][0] == {
        "name": "refund_order",
        "args": {"id": "42"},
        "mode": "live",
        "result": {"refunded": "42", "real": True},
    }


def test_simulated_with_no_fake_handler_fails_with_an_error_that_names_the_tool(
    start: Callable[..., str],
) -> None:
    message = "The tool cancel_order is simulated and has no fake handler."

    def run(args: RunArgs) -> Any:
        try:
            return cancel_order(id="42")
        except ToolCallError as error:
            return {"thrown": str(error), "code": error.code}

    base = start(run)
    body = post(base, request(tools={"cancel_order": {"mode": "simulated"}}))[1]
    assert body["output"] == {"thrown": message, "code": "no_fake"}
    assert body["toolCalls"][0] == {
        "name": "cancel_order",
        "args": {"id": "42"},
        "mode": "simulated",
        "error": {"code": "no_fake", "message": message},
    }


def test_a_tool_error_that_the_run_does_not_catch_fails_the_run_with_its_code(
    start: Callable[..., str],
) -> None:
    base = start(lambda args: cancel_order(id="42"))
    status, body = post(base, request())
    assert status == 500
    assert body["error"]["code"] == "no_fake"
    assert "cancel_order" in body["error"]["message"]


def test_a_failing_real_function_is_recorded_with_tool_failed_and_reraised(
    start: Callable[..., str],
) -> None:
    def down() -> None:
        raise RuntimeError("db down")

    broken = tool("broken", execute=down)

    def run(args: RunArgs) -> Any:
        with pytest.raises(RuntimeError, match="db down"):
            broken()
        return "survived"

    base = start(run)
    body = post(base, request(tools={"broken": {"mode": "live"}}))[1]
    assert body["output"] == "survived"
    assert body["toolCalls"][0]["mode"] == "live"
    assert body["toolCalls"][0]["error"] == {"code": "tool_failed", "message": "db down"}


def test_an_async_tool_applies_its_mode(start: Callable[..., str]) -> None:
    async def lookup(order_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.001)
        return {"order_id": order_id, "real": True}

    async def fake_lookup(order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "real": False}

    lookup_order = tool("lookup_order", execute=lookup, fake=fake_lookup)

    async def run(args: RunArgs) -> Any:
        return await lookup_order(order_id="A1")

    base = start(run)
    body = post(base, request(tools={"lookup_order": {"mode": "simulated"}}))[1]
    assert body["output"] == {"order_id": "A1", "real": False}
    assert body["toolCalls"][0]["mode"] == "simulated"


def test_a_tool_refuses_a_fake_that_is_not_async_like_execute() -> None:
    async def execute() -> None:
        return None

    with pytest.raises(TypeError, match="lookup"):
        tool("lookup", execute=execute, fake=lambda: None)


def test_outside_a_run_a_tool_runs_the_real_function() -> None:
    real_calls.clear()
    assert refund_order(id="1") == {"refunded": "1", "real": True}
    assert real_calls == [{"id": "1"}]


def test_concurrent_runs_keep_their_own_modes_and_calls(start: Callable[..., str]) -> None:
    def run(args: RunArgs) -> Any:
        time.sleep(0.05 if args.input == "slow" else 0.001)
        return refund_order(id=str(args.input))

    base = start(run)
    results: dict[str, Any] = {}

    def send(run_id: str, input: str, mode: str) -> None:
        tools = {"refund_order": {"mode": mode}}
        results[run_id] = post(base, request(runId=run_id, input=input, tools=tools))[1]

    threads = [
        threading.Thread(target=send, args=("a", "slow", "live")),
        threading.Thread(target=send, args=("b", "fast", "simulated")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [c["mode"] for c in results["a"]["toolCalls"]] == ["live"]
    assert [c["mode"] for c in results["b"]["toolCalls"]] == ["simulated"]


# --- the contract and the module ---------------------------------------------------------------


def _vectors() -> list[dict[str, Any]]:
    path = ROOT / "protocol" / "sandbox" / "v1" / "conformance" / "vectors.json"
    vectors: list[dict[str, Any]] = json.loads(path.read_text())["vectors"]
    return [v for v in vectors if v["schema"] == "run-request"]


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: str(v["name"]))
def test_validate_run_request_agrees_with_the_protocol_vector(vector: dict[str, Any]) -> None:
    errors = validate_run_request(vector["document"])
    if vector["valid"]:
        assert errors == []
    else:
        assert errors[0].path == vector["path"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="sys.stdlib_module_names is 3.10+")
def test_serve_imports_only_the_standard_library() -> None:
    tree = ast.parse(SERVE_FILE.read_text())
    names = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    }
    names |= {
        n.module.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0
    }
    relative = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level > 0]
    assert relative == []
    assert names - set(sys.stdlib_module_names) - {"__future__"} == set()


def test_serve_runs_alone_without_the_native_module(tmp_path: Path) -> None:
    package = tmp_path / "ratel_ai"
    package.mkdir()
    (package / "__init__.py").write_text("")
    shutil.copy(SERVE_FILE, package / "serve.py")
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "from ratel_ai.serve import serve, tool\n"
        "assert 'ratel_ai._native' not in sys.modules\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.stdout.strip() == "ok", result.stderr
