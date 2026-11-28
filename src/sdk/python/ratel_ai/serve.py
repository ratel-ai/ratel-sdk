"""Make the Docker image of a Python agent answer the Ratel run contract (`protocol/sandbox/v1`).

Standard library only: this module never loads the native module of `ratel_ai`, so a slim
image can use it alone. It mirrors `@ratel-ai/sdk/serve` of the TypeScript SDK.

```python
from ratel_ai.serve import serve, tool

refund_order = tool("refund_order", execute=refund, fake=lambda order_id: {"refunded": order_id})
serve(lambda args: agent.reply(args.input, model=args.params["model"]))
```

`serve(run)` answers `GET /ratel/health` and `POST /ratel/run` on `$PORT` (default 8080). A tool
of `tool(...)` applies the mode that the run request sets: `simulated` answers from `fake`,
`recorded` from the fixture with the same arguments, `live` from `execute`. The response lists
each call with its mode.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import inspect
import json
import os
import re
import signal
import threading
import urllib.parse
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, TypeVar, cast

__all__ = [
    "ContractError",
    "RunArgs",
    "RunInfo",
    "ServeHandle",
    "ToolCallError",
    "current_run",
    "record_usage",
    "serve",
    "start_server",
    "tool",
    "validate_run_request",
]

#: The run function of the agent: sync or async. It returns the output, any JSON.
RunFn = Callable[["RunArgs"], Any]
F = TypeVar("F", bound=Callable[..., Any])

#: The three tool modes.
TOOL_MODES = ("simulated", "recorded", "live")
#: The pattern of an error code.
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: The text that replaces the value of a secret in a response.
REDACTED = "[redacted]"

DEFAULT_PORT = 8080
DEFAULT_MAX_BODY = 10 * 1024 * 1024

_REQUIRED = ("v", "runId", "input", "params", "tools", "fixtures")
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
# Shorter values are ordinary words and numbers: replacing them would destroy the output.
_MIN_SECRET_LENGTH = 4
# A line of a secret with many lines. A shorter line can be an ordinary word or a bracket.
_MIN_LINE_LENGTH = 8


@dataclass(frozen=True)
class RunArgs:
    """What `serve` gives to the run function of the agent."""

    #: Any JSON. Conversation history belongs here.
    input: Any
    #: The values of the parameters that `ratel.yaml` declares.
    params: dict[str, str | int | float | bool]
    #: The mode of each tool for this run, e.g. `{"refund_order": {"mode": "simulated"}}`.
    tools: dict[str, dict[str, Any]]
    #: The id of the run.
    run_id: str
    #: The W3C trace context of the run, when Ratel sends one.
    traceparent: str | None = None


@dataclass(frozen=True)
class RunInfo:
    """What the code of a run can read about it: see `current_run`."""

    #: The id of the run.
    run_id: str
    #: The W3C trace context of the run, when Ratel sends one.
    traceparent: str | None = None


@dataclass(frozen=True)
class ContractError:
    """A violation of the contract, at a JSON pointer."""

    #: The JSON pointer of the wrong field. `""` is the whole document.
    path: str
    #: What is wrong.
    message: str


class ToolCallError(Exception):
    """A failed tool call. `code` is `no_fake`, `no_fixture`, `no_run_scope` or `tool_failed`."""

    def __init__(self, code: str, message: str) -> None:
        """A failed call with its code."""
        super().__init__(message)
        self.code = code


class ServeHandle:
    """A running server of `start_server`."""

    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        """Wrap a server that runs on `thread`."""
        self._server = server
        self._thread = thread

    @property
    def port(self) -> int:
        """The port that the server listens on."""
        return int(self._server.server_address[1])

    def close(self) -> None:
        """Stop the server and close its socket."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


@dataclass
class _RunScope:
    info: RunInfo
    tools: Mapping[str, Any]
    next_fixture: Callable[[str, Any], dict[str, Any] | None]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, float] | None = None


_scope: contextvars.ContextVar[_RunScope | None] = contextvars.ContextVar(
    "ratel_run_scope", default=None
)

# The servers of `serve` that are open in this process. While one is open, a tool call with no
# run scope fails: it must never run the real function in place of a simulated one.
_open_servers = 0
_open_servers_lock = threading.Lock()


def serve(
    run: RunFn,
    *,
    port: int | None = None,
    host: str = "0.0.0.0",
    secrets: Sequence[str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY,
) -> None:
    """Answer the run contract (sandbox/v1, HTTP mode) until the process stops.

    One line in the entry point of the image: `serve(run)`. It answers `GET /ratel/health` and
    `POST /ratel/run`. `run` gets one `RunArgs` and can be sync or async. SIGTERM stops it.

    Args:
        run: The run function of the agent. It returns the output: any JSON.
        port: Default: `$PORT`, else 8080. 0 picks a free port.
        host: Default: `0.0.0.0`.
        secrets: The names of the environment variables whose values never leave the container
            in a response. Default: the names in `$RATEL_SECRET_NAMES` (comma-separated).
        max_body_bytes: The largest request body, in bytes. Default: 10 MiB.
    """
    server = _make_server(run, port, host, secrets, max_body_bytes)
    if threading.current_thread() is threading.main_thread():

        def stop(signum: int, frame: Any) -> None:
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def start_server(
    run: RunFn,
    *,
    port: int | None = None,
    host: str = "0.0.0.0",
    secrets: Sequence[str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY,
) -> ServeHandle:
    """Start the server of `serve` on a background thread and return at once.

    For tests and for a process that does other work. The arguments are those of `serve`.
    """
    server = _make_server(run, port, host, secrets, max_body_bytes)
    thread = threading.Thread(
        target=server.serve_forever, args=(0.05,), name="ratel-serve", daemon=True
    )
    thread.start()
    return ServeHandle(server, thread)


def tool(name: str, execute: F, fake: Callable[..., Any] | None = None) -> F:
    """Wrap a tool so each run applies its mode.

    Inside a run of `serve`, the request sets the mode (default `simulated`):

    - `simulated` answers from `fake`. With no `fake`, the call fails with the code `no_fake`.
    - `recorded` answers from the fixture with the same tool name and normalized arguments. With
      no fixture, the call fails with the code `no_fixture`.
    - `live` runs `execute`.

    The response lists each call with its mode. Call the tool with keyword arguments or with one
    mapping. When `execute` is async, the tool is async and `fake` must be async too.

    In a process with no server of `serve`, the tool runs `execute`. While a server is open, a
    call with no run scope fails with the code `no_run_scope` and never runs `execute`. A worker
    thread does not get the scope of the run: carry it with `contextvars.copy_context().run`.

    Args:
        name: The name of the tool, as `ratel.yaml` and the run request name it.
        execute: The real function. It runs in mode `live`, and in a process with no server.
        fake: The fake handler. It answers in mode `simulated` and never has side effects.
    """
    if not callable(execute):
        raise TypeError(f"ratel: the tool {name} needs an execute function")
    is_async = inspect.iscoroutinefunction(execute)
    if fake is not None and inspect.iscoroutinefunction(fake) != is_async:
        kind = "async" if is_async else "sync"
        raise TypeError(f"ratel: the fake handler of the tool {name} must be {kind}, as execute")

    if is_async:

        async def call_async(*args: Any, **kwargs: Any) -> Any:
            scope = _scope.get()
            if scope is None:
                _refuse_without_scope(name)
                return await execute(*args, **kwargs)
            call = _ToolCall(name, scope, _call_args(name, args, kwargs))
            try:
                result = await _answer_async(call, execute, fake, args, kwargs)
            except BaseException as error:
                call.failed(error)
                raise
            return call.answered(result)

        return cast(F, _named(call_async, execute, name))

    def call_sync(*args: Any, **kwargs: Any) -> Any:
        scope = _scope.get()
        if scope is None:
            _refuse_without_scope(name)
            return execute(*args, **kwargs)
        call = _ToolCall(name, scope, _call_args(name, args, kwargs))
        try:
            result = _answer_sync(call, execute, fake, args, kwargs)
        except BaseException as error:
            call.failed(error)
            raise
        return call.answered(result)

    return cast(F, _named(call_sync, execute, name))


def current_run() -> RunInfo | None:
    """The run that the caller is inside, or `None` outside a run of `serve`."""
    scope = _scope.get()
    return scope.info if scope else None


def record_usage(
    *,
    input_tokens: float | None = None,
    output_tokens: float | None = None,
    cost_usd: float | None = None,
) -> None:
    """Add model usage to the current run. The response sums each call. Outside a run, nothing."""
    scope = _scope.get()
    if scope is None:
        return
    total = dict(scope.usage or {})
    for key, value in (
        ("inputTokens", input_tokens),
        ("outputTokens", output_tokens),
        ("costUsd", cost_usd),
    ):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            total[key] = total.get(key, 0) + value
    scope.usage = total


def validate_run_request(doc: Any) -> list[ContractError]:
    """The violations of `run-request.schema.json` in a document.

    They come in the order of the reference verifier, so the first error has the same path.
    Empty: the document is a valid request.
    """
    if not isinstance(doc, dict):
        return [ContractError("", "must be object")]
    errors: list[ContractError] = []

    def fail(path: str, message: str) -> None:
        errors.append(ContractError(path, message))

    for name in _REQUIRED:
        if name not in doc:
            fail(f"/{name}", "is required")
    if "v" in doc and not (_is_number(doc["v"]) and doc["v"] == 1):
        fail("/v", "must be 1")
    if "runId" in doc and not (isinstance(doc["runId"], str) and doc["runId"]):
        fail("/runId", "must be a non-empty string")
    if "params" in doc:
        if not isinstance(doc["params"], dict):
            fail("/params", "must be object")
        else:
            for name, value in doc["params"].items():
                if not _PARAM_NAME.match(name):
                    fail(f"/params/{_esc(name)}", "is not a valid name")
                elif not isinstance(value, (str, int, float)):
                    fail(f"/params/{_esc(name)}", "must be string or number or boolean")
    if "tools" in doc:
        if not isinstance(doc["tools"], dict):
            fail("/tools", "must be object")
        else:
            for name, value in doc["tools"].items():
                path = f"/tools/{_esc(name)}"
                if not _TOOL_NAME.match(name):
                    fail(path, "is not a valid name")
                elif not isinstance(value, dict):
                    fail(path, "must be object")
                elif "mode" not in value:
                    fail(f"{path}/mode", "is required")
                elif value["mode"] not in TOOL_MODES:
                    fail(f"{path}/mode", f"must be one of {', '.join(TOOL_MODES)}")
    if "fixtures" in doc:
        if not isinstance(doc["fixtures"], list):
            fail("/fixtures", "must be array")
        else:
            for i, fixture in enumerate(doc["fixtures"]):
                path = f"/fixtures/{i}"
                if not isinstance(fixture, dict):
                    fail(path, "must be object")
                    continue
                for name in ("tool", "args", "result"):
                    if name not in fixture:
                        fail(f"{path}/{name}", "is required")
                tool_name = fixture.get("tool")
                if "tool" in fixture and not (
                    isinstance(tool_name, str) and _TOOL_NAME.match(tool_name)
                ):
                    fail(f"{path}/tool", "is not a valid tool name")
                if "args" in fixture and not isinstance(fixture["args"], dict):
                    fail(f"{path}/args", "must be object")
    if "traceparent" in doc and not (
        isinstance(doc["traceparent"], str) and _TRACEPARENT.match(doc["traceparent"])
    ):
        fail("/traceparent", "must be a W3C traceparent")
    return errors


# --- the server --------------------------------------------------------------------------------


def _make_server(
    run: RunFn,
    port: int | None,
    host: str,
    secrets: Sequence[str] | None,
    max_body_bytes: int,
) -> ThreadingHTTPServer:
    redactor = _Redactor(_secret_values(secrets))

    class Handler(_RunHandler):
        pass

    Handler.run = staticmethod(run)
    Handler.redactor = redactor
    Handler.max_body = max_body_bytes
    server = _Server((host, _port_from_env() if port is None else port), Handler)
    server.daemon_threads = True
    return server


class _Server(ThreadingHTTPServer):
    """A server of `serve`. It counts as open from its start to `server_close`."""

    _counted = False

    def server_activate(self) -> None:
        super().server_activate()
        global _open_servers
        with _open_servers_lock:
            _open_servers += 1
            self._counted = True

    def server_close(self) -> None:
        global _open_servers
        with _open_servers_lock:
            if self._counted:
                _open_servers -= 1
                self._counted = False
        super().server_close()


class _RunHandler(BaseHTTPRequestHandler):
    run: RunFn
    redactor: _Redactor
    max_body: int
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: D102
        self._handle()

    def do_HEAD(self) -> None:  # noqa: D102
        self._handle()

    def do_POST(self) -> None:  # noqa: D102
        self._handle()

    def do_PUT(self) -> None:  # noqa: D102
        self._handle()

    def do_PATCH(self) -> None:  # noqa: D102
        self._handle()

    def do_DELETE(self) -> None:  # noqa: D102
        self._handle()

    def log_message(self, format: str, *args: Any) -> None:
        """Log nothing: a request line can carry a secret."""

    def _handle(self) -> None:
        path = self.path.split("?")[0]
        if path == "/ratel/health":
            if self.command not in ("GET", "HEAD"):
                return self._refuse()
            return self._send(200, {"ok": True})
        if path != "/ratel/run":
            return self._send(404, _error("not_found", f"No route {path}."))
        if self.command != "POST":
            return self._refuse()

        length = int(self.headers.get("content-length") or 0)
        if length > self.max_body:
            self.close_connection = True
            return self._send(
                413, _error("invalid_request", f"The body is larger than {self.max_body} bytes.")
            )
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return self._send(400, _error("invalid_request", "The body is not JSON."))
        errors = validate_run_request(body)
        if errors:
            first = errors[0]
            return self._send(
                400, _error("invalid_request", f"{first.path or '/'} {first.message}")
            )

        traceparent = body.get("traceparent")
        args = RunArgs(
            input=body["input"],
            params=body["params"],
            tools=body["tools"],
            run_id=body["runId"],
            traceparent=traceparent,
        )
        scope = _RunScope(
            info=RunInfo(run_id=body["runId"], traceparent=traceparent),
            tools=body["tools"],
            next_fixture=_fixture_reader(body["fixtures"]),
        )
        try:
            output = contextvars.copy_context().run(_call_run, type(self).run, args, scope)
        except Exception as error:
            return self._send(500, _failure("run_failed", error))
        response: dict[str, Any] = {"output": output, "toolCalls": scope.tool_calls}
        if scope.usage:
            response["usage"] = scope.usage
        self._send(200, response)

    def _refuse(self) -> None:
        message = f"{self.command} is not allowed on {self.path}."
        self._send(405, _error("method_not_allowed", message))

    def _send(self, status: int, body: Any) -> None:
        try:
            text = _dumps(self.redactor.json(body))
        except (TypeError, ValueError) as error:
            status = 500
            text = _dumps(self.redactor.json(_failure("invalid_response", error)))
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def _call_run(run: RunFn, args: RunArgs, scope: _RunScope) -> Any:
    _scope.set(scope)
    result = run(args)
    if inspect.isawaitable(result):
        result = asyncio.run(_await(result))
    return result


async def _await(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _failure(fallback: str, error: BaseException) -> dict[str, Any]:
    code = getattr(error, "code", None)
    if not (isinstance(code, str) and ERROR_CODE.match(code)):
        code = fallback
    return _error(code, str(error) or "The run failed.")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _port_from_env() -> int:
    raw = os.environ.get("PORT", "")
    if raw == "":
        return DEFAULT_PORT
    if not raw.isdigit() or int(raw) > 65535:
        raise ValueError(f"ratel: PORT must be a port number, got {raw!r}")
    return int(raw)


def _secret_values(names: Sequence[str] | None) -> list[str]:
    if names is None:
        raw = os.environ.get("RATEL_SECRET_NAMES", "")
        names = [name.strip() for name in raw.split(",") if name.strip()]
    return [value for value in (os.environ.get(name) for name in names) if value]


# --- the tool calls ----------------------------------------------------------------------------


class _ToolCall:
    """One call of a tool inside a run: its mode, and its record in the response."""

    def __init__(self, name: str, scope: _RunScope, args: dict[str, Any]) -> None:
        self.name = name
        self.scope = scope
        setting = scope.tools.get(name)
        self.mode: str = setting["mode"] if isinstance(setting, dict) else "simulated"
        # A copy, so later changes to the arguments do not change the record.
        self.args = _to_json(args)

    def answered(self, result: Any) -> Any:
        self._record({"result": _to_json(result)})
        return result

    def failed(self, error: BaseException) -> None:
        code = error.code if isinstance(error, ToolCallError) else "tool_failed"
        self._record({"error": {"code": code, "message": str(error)}})

    def fixture_result(self) -> Any:
        fixture = self.scope.next_fixture(self.name, self.args)
        if fixture is None:
            raise ToolCallError(
                "no_fixture",
                f"The tool {self.name} is recorded and has no fixture for these arguments.",
            )
        return json.loads(_dumps(fixture["result"]))

    def no_fake(self) -> ToolCallError:
        return ToolCallError(
            "no_fake", f"The tool {self.name} is simulated and has no fake handler."
        )

    def _record(self, outcome: dict[str, Any]) -> None:
        self.scope.tool_calls.append(
            {"name": self.name, "args": self.args, "mode": self.mode, **outcome}
        )


def _refuse_without_scope(name: str) -> None:
    """Fail a call with no run scope while a server is open. With no server, do nothing."""
    if _open_servers == 0:
        return
    raise ToolCallError(
        "no_run_scope",
        f"The tool {name} was called with no run scope while a server of serve is open. "
        "A worker thread does not get the scope of the run: "
        "call the tool through contextvars.copy_context().run.",
    )


def _answer_sync(
    call: _ToolCall,
    execute: Callable[..., Any],
    fake: Callable[..., Any] | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    if call.mode == "live":
        return execute(*args, **kwargs)
    if call.mode == "recorded":
        return call.fixture_result()
    if fake is None:
        raise call.no_fake()
    return fake(*args, **kwargs)


async def _answer_async(
    call: _ToolCall,
    execute: Callable[..., Any],
    fake: Callable[..., Any] | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    if call.mode == "live":
        return await execute(*args, **kwargs)
    if call.mode == "recorded":
        return call.fixture_result()
    if fake is None:
        raise call.no_fake()
    return await fake(*args, **kwargs)


def _call_args(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    if not args:
        return dict(kwargs)
    if len(args) == 1 and not kwargs and isinstance(args[0], Mapping):
        return dict(args[0])
    raise TypeError(f"ratel: call the tool {name} with keyword arguments or with one mapping")


def _named(wrapper: Callable[..., Any], execute: Callable[..., Any], name: str) -> Any:
    wrapper.__name__ = getattr(execute, "__name__", name)
    wrapper.__qualname__ = getattr(execute, "__qualname__", name)
    wrapper.__doc__ = execute.__doc__
    wrapper.__wrapped__ = execute  # type: ignore[attr-defined]
    return wrapper


def _fixture_reader(
    fixtures: Sequence[dict[str, Any]],
) -> Callable[[str, Any], dict[str, Any] | None]:
    """The fixture for a call. Equal fixtures answer in order, and the last one repeats."""
    queues: dict[str, list[dict[str, Any]]] = {}
    for fixture in fixtures:
        queues.setdefault(f"{fixture['tool']}\n{_normalize(fixture['args'])}", []).append(fixture)
    lock = threading.Lock()

    def next_fixture(tool: str, args: Any) -> dict[str, Any] | None:
        with lock:
            queue = queues.get(f"{tool}\n{_normalize(args)}")
            if not queue:
                return None
            return queue.pop(0) if len(queue) > 1 else queue[0]

    return next_fixture


def _normalize(value: Any) -> str:
    """JSON with sorted object keys: equal arguments give equal text."""
    return json.dumps(_to_json(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _to_json(value: Any) -> Any:
    """The value as JSON carries it. What JSON cannot carry becomes its text."""
    try:
        return json.loads(_dumps(value))
    except (TypeError, ValueError):
        return str(value)


# --- redaction ---------------------------------------------------------------------------------


class _Redactor:
    """Replace the values of the secrets in each string of a JSON value, object keys too."""

    def __init__(self, secrets: Sequence[str]) -> None:
        variants = {v for secret in secrets for v in _variants(secret)}
        self.values = sorted(
            (v for v in variants if len(v) >= _MIN_SECRET_LENGTH), key=len, reverse=True
        )

    def text(self, value: str) -> str:
        for secret in self.values:
            value = value.replace(secret, REDACTED)
        return value

    def json(self, value: Any) -> Any:
        if not self.values:
            return value
        if isinstance(value, str):
            return self.text(value)
        if _is_number(value) and json.dumps(value) in self.values:
            return REDACTED
        if isinstance(value, (list, tuple)):
            return [self.json(item) for item in value]
        if isinstance(value, dict):
            return {
                self.text(k) if isinstance(k, str) else k: self.json(v) for k, v in value.items()
            }
        return value


def _variants(secret: str) -> list[str]:
    """A secret as it is, trimmed, inside JSON text, in a URL, in base64, and each of its lines."""
    return [
        secret,
        secret.strip(),
        json.dumps(secret, ensure_ascii=False)[1:-1],
        urllib.parse.quote(secret, safe="-_.!~*'()"),
        base64.b64encode(secret.encode("utf-8")).decode("ascii"),
        *(line for line in re.split(r"\r?\n", secret) if len(line) >= _MIN_LINE_LENGTH),
    ]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _esc(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")
