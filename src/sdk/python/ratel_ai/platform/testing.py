"""`MockPlatform`: an in-process Ratel Platform for tests, used as a `PlatformTransport`.

Routes and error bodies mirror the v1 handlers of the server. The intent
*extraction* is a deterministic fixture (one intent per unique user message,
covered when every name token of a published skill appears in it), not a
reimplementation of the server pipeline.

Extend it with more endpoints through `MockPlatform.add_route`. Every request it
receives is kept in `MockPlatform.requests`. `MockPlatform.script_responses` queues
the answers of one endpoint (e.g. 503, then 200) before its normal handler.

`ManualClock` is a fake ``clock`` and ``sleep`` for the runtime publishers, so a
test moves time forward with `ManualClock.advance` instead of a real wait.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Union, cast
from urllib.parse import parse_qsl, urlsplit

from .transport import TransportRequest, TransportResponse
from .types import (
    SKILL_STATUSES,
    SUGGESTION_STATUSES,
    SUGGESTION_TYPES,
    AnalyzeResult,
    Catalog,
    CatalogV2,
    ExtractedIntent,
    ImportReport,
    PlatformSkill,
    PlatformSuggestion,
    RuntimeCatalogOverride,
    SkillStatus,
    WireSkill,
)
from .wire import canonical_set, if_none_match_matches, resolve

__all__ = [
    "ManualClock",
    "MockPlatform",
    "MockHandler",
    "MockIntentGraph",
    "MockRequest",
    "MockToolPick",
    "ScriptedResponse",
    "json_response",
]

_PROJECT_ID = "proj_mock"
_KEBAB_CASE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_V1_PREFIX = re.compile(r"^.*?/(?:api/)?v1")


@dataclass(frozen=True)
class MockRequest:
    """One request as a `MockPlatform` route handler receives it.

    Attributes:
        method: The upper-case HTTP method.
        path: The path after the ``/v1`` (or ``/api/v1``) prefix, e.g. ``"/skills"``.
        query: The query parameters, first value per name.
        headers: The request headers with lowercase names.
        body: The JSON object body, or ``{}`` when there is none.
        params: The groups that the route pattern captured.
        raw: The `TransportRequest` as it arrived.
    """

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: dict[str, Any]
    params: tuple[str, ...]
    raw: TransportRequest


@dataclass(frozen=True)
class MockIntentGraph:
    """An intent graph that `MockPlatform` stores for one source.

    Attributes:
        rev: The ``rev`` of the stored graph.
        graph: The graph as a JSON object. `MockPlatform` never reads more than ``rev``.
        etag: The quoted ETag that ``GET`` returns and ``If-Match`` must name.
    """

    rev: int
    graph: dict[str, Any]
    etag: str


@dataclass(frozen=True)
class MockToolPick:
    """One request that `MockPlatform` received on ``POST /tools/pick``.

    Attributes:
        query: The query.
        mode: The Tool Picker mode, e.g. ``"precise"``.
        top_k: The requested number of tools.
    """

    query: str
    mode: str
    top_k: int


#: A route handler: it receives the parsed request and returns the response.
MockHandler = Callable[[MockRequest], TransportResponse]

#: One scripted answer: a status (2xx runs the normal handler), a full response,
#: or an exception that `MockPlatform.send` raises as a network failure.
ScriptedResponse = Union[int, TransportResponse, BaseException]


@dataclass(frozen=True)
class _Route:
    method: str
    pattern: re.Pattern[str]
    handler: MockHandler


class MockPlatform:
    """An in-process Ratel Platform, passed as ``transport=`` to `RatelPlatformClient`.

    Attributes:
        api_key: The Bearer key it expects. Default ``"rtl_test_key"``.
        skills: The managed skills by id.
        suggestions: The suggestions by id.
        requests: Every request received, in arrival order (also unauthorized ones).
        received_event_batches: The ``events`` of every authorized ``POST /events``,
            in arrival order. Retries, scripted failures and the empty batch of
            ``verify()`` are included.
        runtime_events: The events that ``POST /events`` accepted, in arrival order.
        received_snapshots: The body of every authorized ``PUT /catalog/snapshot``,
            in arrival order, also of scripted failures.
        catalog_snapshots: The snapshots that ``PUT /catalog/snapshot`` accepted.
        intent_graphs: The stored intent graphs by source id, from ``PUT /intent-graph``
            or `seed_intent_graph`.
        tool_picks: The requests that ``POST /tools/pick`` received, in arrival order.
    """

    def __init__(
        self,
        *,
        api_key: str = "rtl_test_key",
        catalog: CatalogV2 | Catalog | None = None,
        suggestions: Iterable[Mapping[str, Any]] = (),
        now: Callable[[], str] | None = None,
    ) -> None:
        """Create the mock.

        Args:
            api_key: The Bearer key to expect.
            catalog: Published skills to seed (a global layer and per-subject layers).
            suggestions: Suggestions to seed, as partial wire objects.
            now: The clock, returning ISO 8601 text. Defaults to the current UTC time.
        """
        self.api_key = api_key
        self.skills: dict[str, PlatformSkill] = {}
        self.suggestions: dict[str, PlatformSuggestion] = {}
        self.requests: list[TransportRequest] = []
        self.received_event_batches: list[list[dict[str, Any]]] = []
        self.runtime_events: list[dict[str, Any]] = []
        self.received_snapshots: list[dict[str, Any]] = []
        self.catalog_snapshots: list[dict[str, Any]] = []
        self.intent_graphs: dict[str, MockIntentGraph] = {}
        self.tool_picks: list[MockToolPick] = []
        self._tool_ranking: list[tuple[str, float]] | None = None
        self._scripts: dict[tuple[str, str], deque[ScriptedResponse]] = {}
        self._now = now if now is not None else _utc_now
        self._seq = 0
        # Keeps source layers even when a conformance vector reuses an id across scopes.
        self._catalog_rows: list[PlatformSkill] = []
        self._analyze_cache: dict[str, AnalyzeResult] = {}
        self._query_intents: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._runtime_catalog_overrides: dict[tuple[str, str], RuntimeCatalogOverride] = {}
        self._routes: list[_Route] = []
        self._add_default_routes()
        if catalog is not None:
            self._seed_catalog(catalog)
        for partial in suggestions:
            self.seed_suggestion(partial)

    async def send(self, request: TransportRequest) -> TransportResponse:
        """Serve one request (the `PlatformTransport` protocol).

        Args:
            request: The request.

        Returns:
            The response. An unknown route is a 404 ``{"error": "not_found"}``.
        """
        self.requests.append(request)
        headers = {name.lower(): value for name, value in request.headers.items()}
        if headers.get("authorization") != f"Bearer {self.api_key}":
            return json_response({"error": "Invalid or revoked API key."}, status=401)

        url = urlsplit(request.url)
        path = _V1_PREFIX.sub("", url.path, count=1) or url.path
        query: dict[str, str] = {}
        for name, value in parse_qsl(url.query, keep_blank_values=True):
            query.setdefault(name, value)
        parsed = json.loads(request.body.decode("utf-8")) if request.body else {}
        body = parsed if isinstance(parsed, dict) else {}
        method = request.method.upper()
        self._record_received(method, path, body)

        scripted = self._next_scripted(method, path)
        if isinstance(scripted, BaseException):
            raise scripted
        if isinstance(scripted, TransportResponse):
            return scripted
        if scripted is not None and not 200 <= scripted < 300:
            return json_response({"error": f"scripted HTTP {scripted}"}, status=scripted)

        for route in self._routes:
            match = route.pattern.fullmatch(path)
            if route.method == method and match is not None:
                return route.handler(
                    MockRequest(
                        method=method,
                        path=path,
                        query=query,
                        headers=headers,
                        body=body,
                        params=match.groups(),
                        raw=request,
                    )
                )
        return _not_found()

    def add_route(
        self, method: str, pattern: str, handler: MockHandler, *, first: bool = False
    ) -> None:
        """Serve one more endpoint.

        Routes match in order, and the first match serves the request.

        Args:
            method: The HTTP method, e.g. ``"POST"``.
            pattern: A regular expression that must match the whole path after the
                ``/v1`` prefix, e.g. ``r"/jobs/([^/]+)"``. Its groups become
                `MockRequest.params`.
            handler: The handler.
            first: Put the route before the existing ones, to override one.
        """
        route = _Route(method.upper(), re.compile(pattern), handler)
        if first:
            self._routes.insert(0, route)
        else:
            self._routes.append(route)

    def script_responses(self, method: str, path: str, *responses: ScriptedResponse) -> None:
        """Queue the next answers of one endpoint, before its normal handler.

        Each request to the endpoint takes the next answer. When the queue is
        empty, the normal handler serves the request again. For example,
        ``script_responses("POST", "/events", 503, 200)`` fails the first batch
        and accepts its retry.

        Args:
            method: The HTTP method, e.g. ``"POST"``.
            path: The exact path after the ``/v1`` prefix, e.g. ``"/events"``.
            *responses: The answers, in order. A 2xx status runs the normal
                handler. Another status returns ``{"error": ...}`` with that
                status. A `TransportResponse` is returned as it is (use
                `json_response` to add a ``Retry-After`` header). An exception
                is raised, as a network failure.
        """
        self._scripts.setdefault((method.upper(), path), deque()).extend(responses)

    def next_id(self, prefix: str) -> str:
        """Return a new id such as ``"sk_3"``. Ids share one counter.

        Args:
            prefix: The id prefix.

        Returns:
            The new id.
        """
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def now(self) -> str:
        """Return the current time of the mock clock as ISO 8601 text."""
        return self._now()

    def seed_suggestion(self, overrides: Mapping[str, Any] | None = None) -> PlatformSuggestion:
        """Store a pending ``new_skill`` suggestion, with ``overrides`` applied.

        Args:
            overrides: Wire fields to set, e.g. ``{"type": "edit_skill"}``.

        Returns:
            The stored suggestion.
        """
        ts = self._now()
        suggestion: dict[str, Any] = {
            "id": self.next_id("sug"),
            "projectId": _PROJECT_ID,
            "type": "new_skill",
            "signalKind": "coverage_gap",
            "status": "pending",
            "rationale": "seeded",
            "evidence": {},
            "targetSkillId": None,
            "targetSkillExpectedVersion": None,
            "sourceQueryIntentId": None,
            "endUserId": None,
            "patch": {},
            "retrievabilityPreview": {},
            "createdSkillId": None,
            "model": None,
            "createdAt": ts,
            "updatedAt": ts,
            "reviewedAt": None,
            "appliedAt": None,
            **(overrides or {}),
        }
        stored = cast(PlatformSuggestion, suggestion)
        self.suggestions[stored["id"]] = stored
        return stored

    def seed_runtime_catalog_override(self, override: RuntimeCatalogOverride) -> None:
        """Store or replace one runtime-catalog override.

        Args:
            override: The override. Its ``kind`` and ``entryId`` identify it.
        """
        self._runtime_catalog_overrides[(override["kind"], override["entryId"])] = {
            "kind": override["kind"],
            "entryId": override["entryId"],
            "searchableDescription": override["searchableDescription"],
        }

    def clear_runtime_catalog_overrides(self) -> None:
        """Remove every runtime-catalog override."""
        self._runtime_catalog_overrides.clear()

    def seed_intent_graph(self, source_id: str, graph: Mapping[str, Any]) -> MockIntentGraph:
        """Store an intent graph for ``source_id``, as if a runtime had pushed it.

        Args:
            source_id: The source id (the ``source`` query parameter of ``GET``).
            graph: The graph JSON object, e.g. ``json.loads(IntentGraph().to_json())``.

        Returns:
            The stored graph with its new ETag.
        """
        return self._store_intent_graph(source_id, dict(graph))

    def seed_tool_ranking(self, ranked: Iterable[tuple[str, float]]) -> None:
        """Make ``POST /tools/pick`` return this ranking, cut to the requested ``top_k``.

        Without a seeded ranking, the pick ranks the tools of the latest catalog
        snapshot by how many query words their name and description contain.

        Args:
            ranked: ``(tool id, score)`` pairs, best first.
        """
        self._tool_ranking = list(ranked)

    # — routing ———————————————————————————————————————————————————————————

    def _add_default_routes(self) -> None:
        routes: list[tuple[str, str, MockHandler]] = [
            ("GET", r"/skills", self._list_skills),
            ("POST", r"/skills", self._create_skill),
            ("POST", r"/skills/import", self._import_skills),
            ("GET", r"/skills/([^/]+)", self._get_skill),
            ("PATCH", r"/skills/([^/]+)", self._update_skill),
            ("POST", r"/skills/([^/]+)/([^/]+)", self._transition_skill),
            ("GET", r"/suggestions", self._list_suggestions),
            ("POST", r"/suggestions/generate", self._generate_suggestions),
            ("GET", r"/suggestions/([^/]+)", self._get_suggestion),
            ("POST", r"/suggestions/([^/]+)/([^/]+)", self._review_suggestion),
            ("POST", r"/intents/analyze", self._analyze),
            ("GET", r"/intents", self._list_intents),
            ("POST", r"/intents/([^/]+)/suggest", self._suggest_intent),
            ("GET", r"/jobs/([^/]+)", self._get_job),
            ("GET", r"/runtime-catalog/overrides", self._get_runtime_catalog_overrides),
            ("POST", r"/events", self._receive_events),
            ("PUT", r"/catalog/snapshot", self._receive_snapshot),
            ("GET", r"/intent-graph", self._get_intent_graph),
            ("PUT", r"/intent-graph", self._put_intent_graph),
            ("POST", r"/tools/pick", self._pick_tools),
        ]
        for method, pattern, handler in routes:
            self.add_route(method, pattern, handler)

    def _record_received(self, method: str, path: str, body: dict[str, Any]) -> None:
        if method == "POST" and path == "/events":
            events = body.get("events")
            self.received_event_batches.append(list(events) if isinstance(events, list) else [])
        elif method == "PUT" and path == "/catalog/snapshot":
            self.received_snapshots.append(body)

    def _next_scripted(self, method: str, path: str) -> ScriptedResponse | None:
        queue = self._scripts.get((method, path))
        return queue.popleft() if queue else None

    # — catalog —————————————————————————————————————————————————————————————

    def _seed_catalog(self, catalog: Mapping[str, Any]) -> None:
        for skill in catalog.get("global") or []:
            self._insert_skill(skill, None, "published")
        for subject, skills in (catalog.get("subjects") or {}).items():
            for skill in skills:
                self._insert_skill(skill, subject, "published")

    def _insert_skill(
        self, source: Mapping[str, Any], end_user_id: str | None, status: SkillStatus
    ) -> PlatformSkill:
        ts = self._now()
        skill: PlatformSkill = {
            "id": source["id"] if source.get("id") is not None else self.next_id("sk"),
            "name": source["name"],
            "description": source["description"],
            "searchableDescription": source.get("searchableDescription"),
            "tags": list(source.get("tags") or []),
            "tools": list(source.get("tools") or []),
            "metadata": dict(source.get("metadata") or {}),
            "body": source["body"],
            "status": status,
            "version": 1,
            "endUserId": end_user_id,
            "model": None,
            "createdAt": ts,
            "updatedAt": ts,
            "publishedAt": ts if status == "published" else None,
        }
        self.skills[skill["id"]] = skill
        self._catalog_rows.append(skill)
        return skill

    def _published_catalog(self) -> Catalog:
        """The published set as a layered v1 source catalog."""
        global_layer: list[WireSkill] = []
        subjects: dict[str, list[WireSkill]] = {}
        for skill in self._catalog_rows:
            if skill["status"] != "published":
                continue
            wire: WireSkill = {
                "id": skill["id"],
                "name": skill["name"],
                "description": skill["description"],
                "tags": skill["tags"],
                "tools": skill["tools"],
                "metadata": skill["metadata"],
                "body": skill["body"],
            }
            if skill["endUserId"] is None:
                global_layer.append(wire)
            else:
                subjects.setdefault(skill["endUserId"], []).append(wire)
        return {"global": global_layer, "subjects": subjects}

    # — skills ——————————————————————————————————————————————————————————————

    def _list_skills(self, request: MockRequest) -> TransportResponse:
        status = request.query.get("status")
        if status and status not in SKILL_STATUSES:
            return _error(400, "invalid", "invalid_status")
        end_user_id = request.query.get("endUserId")
        skills = [
            skill
            for skill in self.skills.values()
            if (not status or skill["status"] == status)
            and (not end_user_id or skill["endUserId"] == end_user_id)
        ]
        return json_response({"count": len(skills), "skills": skills})

    def _get_skill(self, request: MockRequest) -> TransportResponse:
        skill = self.skills.get(request.params[0])
        return _not_found() if skill is None else json_response({"skill": skill})

    def _active_names(self) -> set[str]:
        return {skill["name"] for skill in self.skills.values() if skill["status"] != "archived"}

    def _create_skill(self, request: MockRequest) -> TransportResponse:
        body = request.body
        name = body.get("name")
        if not isinstance(name, str) or not _KEBAB_CASE.fullmatch(name):
            return _error(400, "invalid", "invalid_name")
        if name in self._active_names():
            return _error(409, "conflict", "name_conflict")
        skill = self._insert_skill(
            {**body, "id": None}, body.get("endUserId"), body.get("status") or "draft"
        )
        return json_response({"skill": skill}, status=201)

    def _update_skill(self, request: MockRequest) -> TransportResponse:
        skill = self.skills.get(request.params[0])
        if skill is None:
            return _not_found()
        body = request.body
        # The version guard is opt-in: absent means an unconditional edit, like the server.
        if "expectedVersion" in body and skill["version"] != body["expectedVersion"]:
            return _error(409, "conflict", "version_conflict")
        name = body.get("name")
        if name is not None and name != skill["name"] and name in self._active_names():
            return _error(409, "conflict", "name_conflict")
        patch = {key: value for key, value in body.items() if key != "expectedVersion"}
        skill.update(cast(Any, patch))
        skill["version"] += 1
        skill["updatedAt"] = self._now()
        return json_response({"skill": skill})

    def _transition_skill(self, request: MockRequest) -> TransportResponse:
        skill_id, action = request.params
        if action not in ("publish", "archive"):
            return _not_found()
        skill = self.skills.get(skill_id)
        if skill is None:
            return _not_found()
        body = request.body
        if "expectedVersion" in body and skill["version"] != body["expectedVersion"]:
            return _error(409, "conflict", "version_conflict")
        skill["status"] = "published" if action == "publish" else "archived"
        if action == "publish":
            skill["publishedAt"] = self._now()
        skill["version"] += 1
        skill["updatedAt"] = self._now()
        return json_response({"skill": skill})

    def _import_skills(self, request: MockRequest) -> TransportResponse:
        report: ImportReport = {"created": [], "updated": [], "unchanged": []}
        for item in request.body.get("skills") or []:
            existing = next(
                (
                    skill
                    for skill in self.skills.values()
                    if skill["status"] != "archived" and skill["name"] == item["name"]
                ),
                None,
            )
            if existing is None:
                self._insert_skill(
                    {**item, "id": None}, item.get("endUserId"), item.get("status") or "draft"
                )
                report["created"].append(item["name"])
                continue
            same = (
                existing["description"] == item["description"]
                and existing["searchableDescription"] == item.get("searchableDescription")
                and existing["body"] == item["body"]
                and existing["tags"] == (item.get("tags") or [])
                and existing["tools"] == (item.get("tools") or [])
            )
            if same:
                report["unchanged"].append(item["name"])
                continue
            existing.update(
                {
                    "description": item["description"],
                    "searchableDescription": item.get("searchableDescription"),
                    "body": item["body"],
                    "tags": item.get("tags") or [],
                    "tools": item.get("tools") or [],
                    "version": existing["version"] + 1,
                    "updatedAt": self._now(),
                }
            )
            report["updated"].append(item["name"])
        return json_response(report)

    # — suggestions —————————————————————————————————————————————————————————

    def _get_suggestion(self, request: MockRequest) -> TransportResponse:
        suggestion = self.suggestions.get(request.params[0])
        return _not_found() if suggestion is None else json_response({"suggestion": suggestion})

    def _list_suggestions(self, request: MockRequest) -> TransportResponse:
        status = request.query.get("status")
        if status and status not in SUGGESTION_STATUSES:
            return _error(400, "invalid", "invalid_status")
        kind = request.query.get("type")
        if kind and kind not in SUGGESTION_TYPES:
            return _error(400, "invalid", "invalid_type")
        end_user_id = request.query.get("endUserId")
        limit = min(max(_js_number(request.query.get("limit")) or 50, 1), 100)
        suggestions = [
            suggestion
            for suggestion in self.suggestions.values()
            if (not status or suggestion["status"] == status)
            and (not kind or suggestion["type"] == kind)
            and (not end_user_id or suggestion["endUserId"] == end_user_id)
        ][: int(limit)]
        return json_response({"count": len(suggestions), "suggestions": suggestions})

    def _review_suggestion(self, request: MockRequest) -> TransportResponse:
        suggestion_id, action = request.params
        if action not in ("approve", "reject"):
            return _not_found()
        suggestion = self.suggestions.get(suggestion_id)
        if suggestion is None:
            return _not_found()
        if suggestion["status"] != "pending":
            return _error(409, "conflict", "not_pending")
        ts = self._now()
        if action == "reject":
            suggestion.update({"status": "rejected", "reviewedAt": ts, "updatedAt": ts})
            return json_response({"suggestion": suggestion})
        if suggestion["type"] == "new_skill":
            patch = suggestion["patch"]
            names = self._active_names()
            name = patch["name"]
            suffix = 2
            while name in names:
                name = f"{patch['name']}-{suffix}"
                suffix += 1
            skill = self._insert_skill(
                {
                    "name": name,
                    "description": patch["description"],
                    "tags": patch["tags"],
                    "body": patch["body"],
                },
                suggestion["endUserId"],
                "draft",
            )
            suggestion.update(
                {
                    "status": "approved",
                    "createdSkillId": skill["id"],
                    "reviewedAt": ts,
                    "appliedAt": ts,
                    "updatedAt": ts,
                }
            )
            return json_response({"suggestion": suggestion})
        # edit_skill: apply through the version compare-and-swap.
        target_id = suggestion["targetSkillId"]
        target = self.skills.get(target_id) if target_id else None
        if target is None:
            return _not_found()
        if target["version"] != suggestion["targetSkillExpectedVersion"]:
            return _error(409, "conflict", "version_conflict")
        target.update(cast(Any, dict(suggestion["patch"])))
        target["version"] += 1
        target["updatedAt"] = ts
        suggestion.update(
            {"status": "approved", "reviewedAt": ts, "appliedAt": ts, "updatedAt": ts}
        )
        return json_response({"suggestion": suggestion})

    def _generate_suggestions(self, _request: MockRequest) -> TransportResponse:
        """``POST /suggestions/generate``: an inert run that settles at once."""
        job: dict[str, Any] = {
            "id": self.next_id("job"),
            "kind": "generate_suggestions",
            "status": "done",
            "result": {
                "coverageGapsFound": 0,
                "surfacedNotInvokedFound": 0,
                "toolErrorsFound": 0,
                "proposalsCreated": 0,
                "proposalsSkipped": 0,
                "proposalsFailed": 0,
                "usedLLM": False,
            },
            "error": None,
        }
        self._jobs[job["id"]] = job
        return json_response({"jobId": job["id"], "coalesced": False})

    # — intent analysis (async: analyze, then suggest, then poll the job) ————

    def _upsert_query_intent(self, text: str, end_user_id: str | None) -> dict[str, Any]:
        for row in self._query_intents.values():
            if row["text"] == text and row["endUserId"] == end_user_id:
                row["occurrences"] += 1
                row["lastSeenAt"] = self._now()
                return row
        ts = self._now()
        row = {
            "id": self.next_id("qi"),
            "text": text,
            "occurrences": 1,
            "firstSeenAt": ts,
            "lastSeenAt": ts,
            "endUserId": end_user_id,
        }
        self._query_intents[row["id"]] = row
        return row

    def _analyze(self, request: MockRequest) -> TransportResponse:
        """Extract intents and score coverage. It does not draft skills."""
        body = request.body
        messages = body.get("messages") or []
        end_user_id = body.get("endUserId") if isinstance(body.get("endUserId"), str) else None
        published = resolve(self._published_catalog(), end_user_id)
        digest = hashlib.sha256(canonical_set(published).encode("utf-8")).hexdigest()

        # Keyed by conversation, scope and catalog version, like the server.
        cache_key = json.dumps([messages, end_user_id, digest])
        if body.get("noCache") is not True:
            cached = self._analyze_cache.get(cache_key)
            if cached is not None:
                return json_response({**cached, "cached": True})

        texts: list[str] = []
        for message in messages:
            text = message["content"].strip()
            if message["role"] == "user" and text and text not in texts:
                texts.append(text)
        intents: list[ExtractedIntent] = []
        for text in texts:
            row = self._upsert_query_intent(text, end_user_id)
            lower = text.lower()
            match = next(
                (
                    skill
                    for skill in published
                    if all(token in lower for token in skill["name"].split("-"))
                ),
                None,
            )
            intents.append(
                {
                    "id": row["id"],
                    "text": text,
                    "covered": match is not None,
                    "matchedSkillId": match["id"] if match is not None else None,
                    "score": 1 if match is not None else None,
                }
            )
        result: AnalyzeResult = {
            "runId": self.next_id("run"),
            "cached": False,
            "catalogVersion": digest,
            "intents": intents,
        }
        self._analyze_cache[cache_key] = result
        return json_response(result)

    def _list_intents(self, request: MockRequest) -> TransportResponse:
        """``GET /intents``: the recurring-ask ledger, most frequent first."""
        page_size = 50
        page = max(0, _js_parse_int(request.query.get("page", "0")))
        ranked = sorted(self._query_intents.values(), key=lambda row: -row["occurrences"])
        window = ranked[page * page_size : page * page_size + page_size]
        intents = [
            {key: row[key] for key in ("id", "text", "occurrences", "firstSeenAt", "lastSeenAt")}
            for row in window
        ]
        return json_response(
            {
                "count": len(intents),
                "page": page,
                "pageSize": page_size,
                "total": len(ranked),
                "intents": intents,
            }
        )

    def _suggest_intent(self, request: MockRequest) -> TransportResponse:
        """``POST /intents/{id}/suggest``: a drafting job that settles at once."""
        intent_id = request.params[0]
        row = self._query_intents.get(intent_id)
        if row is None:
            return _not_found()
        # A pending or approved draft for the intent suppresses drafting again.
        # A rejected one does not: the reviewer said no, so draft again.
        exists = any(
            suggestion["type"] == "new_skill"
            and suggestion["status"] in ("pending", "approved")
            and suggestion["sourceQueryIntentId"] == intent_id
            for suggestion in self.suggestions.values()
        )
        result: dict[str, Any]
        if exists:
            result = {"suggestionId": None, "reason": "exists"}
        else:
            name = _slugify(row["text"])
            suggestion = self.seed_suggestion(
                {
                    "type": "new_skill",
                    "signalKind": "coverage_gap",
                    "status": "pending",
                    "rationale": f'Recurring ask: "{row["text"]}"',
                    "sourceQueryIntentId": intent_id,
                    "endUserId": row["endUserId"],
                    "patch": {
                        "name": name,
                        "description": row["text"],
                        "tags": [],
                        "body": f"# {name}\n\n{row['text']}\n",
                        "model": "mock",
                    },
                }
            )
            result = {"suggestionId": suggestion["id"]}
        job: dict[str, Any] = {
            "id": self.next_id("job"),
            "kind": "suggest_skill",
            "status": "done",
            "result": result,
            "error": None,
        }
        self._jobs[job["id"]] = job
        return json_response({"jobId": job["id"]}, status=202)

    def _get_job(self, request: MockRequest) -> TransportResponse:
        job = self._jobs.get(request.params[0])
        return _not_found() if job is None else json_response(job)

    # — runtime catalog overrides ———————————————————————————————————————————

    def _get_runtime_catalog_overrides(self, request: MockRequest) -> TransportResponse:
        overrides = sorted(
            self._runtime_catalog_overrides.values(),
            key=lambda override: (override["kind"], override["entryId"]),
        )
        body = {"overrides": overrides}
        digest = hashlib.sha256(_compact_json(body).encode("utf-8")).hexdigest()
        headers = {"etag": f'"{digest}"', "cache-control": "no-cache"}
        if if_none_match_matches(request.headers.get("if-none-match"), headers["etag"]):
            return TransportResponse(status=304, headers=headers, body=b"")
        return json_response(body, headers=headers)

    # — runtime events and catalog snapshots ——————————————————————————————————

    def _receive_events(self, request: MockRequest) -> TransportResponse:
        events = request.body.get("events")
        accepted = list(events) if isinstance(events, list) else []
        self.runtime_events.extend(accepted)
        return json_response(
            {"accepted": len(accepted), "duplicates": 0, "rejected": []}, status=202
        )

    def _receive_snapshot(self, request: MockRequest) -> TransportResponse:
        snapshot = request.body
        self.catalog_snapshots.append(snapshot)
        catalog_version = self.next_id("cv")
        tools = snapshot.get("tools")
        return json_response(
            {
                "sourceId": snapshot.get("source_id"),
                "catalogVersion": catalog_version,
                "tools": len(tools) if isinstance(tools, list) else 0,
                "unchanged": False,
            },
            headers={"etag": f'"{catalog_version}"'},
        )

    # — intent graph ————————————————————————————————————————————————————————

    def _get_intent_graph(self, request: MockRequest) -> TransportResponse:
        source_id = request.query.get("source", "")
        stored = self.intent_graphs.get(source_id)
        if stored is None:
            return _not_found()
        headers = {"etag": stored.etag}
        if request.headers.get("if-none-match") == stored.etag:
            return TransportResponse(status=304, headers=headers, body=b"")
        return json_response(
            {"sourceId": source_id, "rev": stored.rev, "graph": stored.graph}, headers=headers
        )

    def _put_intent_graph(self, request: MockRequest) -> TransportResponse:
        source_id = str(request.body.get("source_id", ""))
        graph = request.body.get("graph")
        stored = self.intent_graphs.get(source_id)
        # Optimistic concurrency: a write must name the ETag of the stored graph.
        if stored is not None and request.headers.get("if-match") != stored.etag:
            return json_response({"error": "stale_graph", "rev": stored.rev}, status=409)
        if not isinstance(graph, dict):
            return _error(400, "invalid_graph", "graph must be a JSON object")
        saved = self._store_intent_graph(source_id, graph)
        return json_response(
            {"sourceId": source_id, "rev": saved.rev}, headers={"etag": saved.etag}
        )

    def _store_intent_graph(self, source_id: str, graph: dict[str, Any]) -> MockIntentGraph:
        rev = graph.get("rev")
        stored = MockIntentGraph(
            rev=rev if isinstance(rev, int) and not isinstance(rev, bool) else 0,
            graph=graph,
            etag=f'"{self.next_id("ig")}"',
        )
        self.intent_graphs[source_id] = stored
        return stored

    # — Tool Picker ———————————————————————————————————————————————————————————

    def _pick_tools(self, request: MockRequest) -> TransportResponse:
        top_k = request.body.get("top_k")
        pick = MockToolPick(
            query=str(request.body.get("query", "")),
            mode=str(request.body.get("mode", "precise")),
            top_k=top_k if isinstance(top_k, int) and not isinstance(top_k, bool) else 5,
        )
        self.tool_picks.append(pick)
        ranked = (
            self._tool_ranking
            if self._tool_ranking is not None
            else self._rank_snapshot_tools(pick.query)
        )
        tools = [{"id": tool_id, "score": score} for tool_id, score in ranked[: pick.top_k]]
        return json_response(
            {
                "mode": pick.mode,
                "tools": tools,
                "confident": bool(tools),
                "usage": {"candidates": len(tools), "questions": 0, "input_tokens": 0},
            }
        )

    def _rank_snapshot_tools(self, query: str) -> list[tuple[str, float]]:
        words = [word for word in re.split(r"\W+", query.lower()) if word]
        tools = self.catalog_snapshots[-1].get("tools", []) if self.catalog_snapshots else []
        scored: list[tuple[str, float]] = []
        for tool in tools if isinstance(tools, list) else []:
            text = f"{tool.get('name', '')} {tool.get('description') or ''}".lower()
            hits = sum(1 for word in words if word in text)
            score = hits / len(words) if words else 0.0
            if score > 0:
                scored.append((str(tool.get("id")), score))
        return sorted(scored, key=lambda pair: -pair[1])


class ManualClock:
    """A fake ``clock`` and ``sleep`` that move only when a test calls `advance`.

    Pass `now` as ``clock=`` and `sleep` as ``sleep=``. A sleep resolves when
    `advance` reaches its deadline. Sleeps of zero resolve at the next `advance`.

    Attributes:
        sleeps: Every requested sleep in seconds, in call order.
    """

    def __init__(self, start: float = 1_750_000_000.0) -> None:
        """Create the clock.

        Args:
            start: The start time in seconds since the epoch.
        """
        self._now = start
        self._seq = 0
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self.sleeps: list[float] = []

    def now(self) -> float:
        """Return the current fake time in seconds since the epoch."""
        return self._now

    async def sleep(self, seconds: float) -> None:
        """Wait until `advance` reaches ``now() + seconds``.

        Args:
            seconds: The delay.
        """
        self.sleeps.append(seconds)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._sleepers, (self._now + max(seconds, 0.0), self._seq, future))
        await future

    @property
    def pending(self) -> list[float]:
        """The remaining seconds of each sleep that has not resolved, soonest first."""
        return sorted(
            deadline - self._now for deadline, _, future in self._sleepers if not future.done()
        )

    async def advance(self, seconds: float = 0.0) -> None:
        """Move the time forward and resolve each sleep whose deadline it reaches.

        Sleeps resolve in deadline order, and the tasks they wake run before
        the next one resolves. A sleep that starts meanwhile resolves too when
        its deadline is in the window.

        Args:
            seconds: How far to move. Zero resolves only the due sleeps.
        """
        target = self._now + seconds
        await _settle()
        while self._sleepers and self._sleepers[0][0] <= target:
            deadline, _, future = heapq.heappop(self._sleepers)
            if future.done():
                continue
            self._now = max(self._now, deadline)
            future.set_result(None)
            await _settle()
        self._now = target
        await _settle()


async def _settle() -> None:
    """Let the woken tasks run until they wait again."""
    for _ in range(50):
        await asyncio.sleep(0)


def json_response(
    body: Any, *, status: int = 200, headers: Mapping[str, str] | None = None
) -> TransportResponse:
    """Build a JSON `TransportResponse`, as a route handler returns it.

    Args:
        body: The JSON-serializable body.
        status: The HTTP status.
        headers: Extra response headers.

    Returns:
        The response, with ``content-type: application/json``.
    """
    return TransportResponse(
        status=status,
        headers={"content-type": "application/json", **(headers or {})},
        body=_compact_json(body).encode("utf-8"),
    )


def _not_found() -> TransportResponse:
    return json_response({"error": "not_found"}, status=404)


def _error(status: int, error: str, reason: str) -> TransportResponse:
    return json_response({"error": error, "reason": reason}, status=status)


def _compact_json(value: Any) -> str:
    """``JSON.stringify``: compact separators, raw UTF-8."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _utc_now() -> str:
    """``new Date().toISOString()``."""
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return now.replace("+00:00", "Z")


def _js_number(text: str | None) -> float:
    """``Number(text)``, with NaN and a missing value as 0."""
    if text is None or text.strip() == "":
        return 0
    try:
        value = float(text)
    except ValueError:
        return 0
    return 0 if value != value else value


def _js_parse_int(text: str) -> int:
    """``Number.parseInt(text, 10) || 0``."""
    match = re.match(r"\s*([+-]?\d+)", text)
    return int(match.group(1)) if match else 0


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].rstrip("-")
    return slug or "new-skill"
