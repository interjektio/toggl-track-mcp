"""An in-process fake of the parts of the Toggl Track API v9 the server uses."""

import copy
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aiohttp import web

from .url_utils import API_PREFIX

DEFAULT_ME: dict[str, Any] = {
    "id": 1,
    "fullname": "Test User",
    "timezone": "Europe/Helsinki",
    # Deliberately not the first workspace in the list.
    "default_workspace_id": 200,
    "workspaces": [
        {"id": 100, "name": "Alpha", "organization_id": 10},
        {"id": 200, "name": "Interjektio", "organization_id": 20},
    ],
    "projects": [
        {"id": 1, "name": "Website", "workspace_id": 100, "client_id": 7, "active": True},
        {"id": 2, "name": "Website", "workspace_id": 200, "active": True},
        {"id": 3, "name": "Internal", "workspace_id": 200, "client_name": "Acme", "active": True},
        {"id": 4, "name": "Old stuff", "workspace_id": 200, "active": False},
    ],
    "clients": [{"id": 7, "name": "Client Seven", "workspace_id": 100}],
    "tasks": None,
    "tags": None,
    "time_entries": None,
}


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    json: Any


@dataclass
class Failure:
    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    method: str | None = None


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class FakeToggl:
    def __init__(self) -> None:
        self.me: dict[str, Any] = copy.deepcopy(DEFAULT_ME)
        self.time_entries: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.project_tasks: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self.my_tasks: Any = []
        self.quota: list[dict[str, Any]] = []
        self.requests: list[Recorded] = []
        self.failures: deque[Failure] = deque()
        self.wrap_items = False
        self.base_url = ""
        self._next_id = 9000

    # -- helpers for tests ----------------------------------------------------------

    def fail_next(
        self,
        status: int,
        body: str = "",
        headers: dict[str, str] | None = None,
        *,
        times: int = 1,
        method: str | None = None,
    ) -> None:
        for _ in range(times):
            self.failures.append(Failure(status, body, headers or {}, method))

    def requests_to(self, path: str, method: str = "GET") -> list[Recorded]:
        return [r for r in self.requests if r.path == path and r.method == method]

    # -- server -----------------------------------------------------------------------

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._middleware])
        p = API_PREFIX
        app.router.add_get(f"{p}/me", self._me)
        app.router.add_get(f"{p}/me/time_entries", self._list_entries)
        app.router.add_get(f"{p}/me/time_entries/current", self._current)
        app.router.add_post(f"{p}/workspaces/{{wid}}/time_entries", self._create_entry)
        app.router.add_patch(f"{p}/workspaces/{{wid}}/time_entries/{{eid}}/stop", self._stop)
        app.router.add_get(f"{p}/workspaces/{{wid}}/projects/{{pid}}/tasks", self._tasks)
        app.router.add_post(f"{p}/workspaces/{{wid}}/projects/{{pid}}/tasks", self._create_task)
        app.router.add_get(f"{p}/me/tasks", self._my_tasks)
        app.router.add_get(f"{p}/me/quota", self._quota)
        return app

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Any) -> web.StreamResponse:
        body = await request.json() if request.can_read_body else None
        self.requests.append(
            Recorded(
                method=request.method,
                path=request.path,
                query=dict(request.query),
                headers=dict(request.headers),
                json=body,
            )
        )
        if self.failures and self.failures[0].method in (None, request.method):
            failure = self.failures.popleft()
            return web.Response(status=failure.status, text=failure.body, headers=failure.headers)
        return await handler(request)

    def _list(self, items: Any) -> web.Response:
        if self.wrap_items and isinstance(items, list):
            return web.json_response({"items": items})
        return web.json_response(items)

    async def _me(self, request: web.Request) -> web.Response:
        assert request.query.get("with_related_data") == "true"
        return web.json_response(self.me)

    async def _list_entries(self, request: web.Request) -> web.Response:
        lower = _parse(request.query["start_date"])
        upper = _parse(request.query["end_date"])
        found = [e for e in self.time_entries if lower <= _parse(e["start"]) < upper]
        return self._list(found)

    async def _current(self, request: web.Request) -> web.Response:
        return web.json_response(self.current)

    async def _create_entry(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._next_id += 1
        entry = {**body, "id": self._next_id, "workspace_id": int(request.match_info["wid"])}
        self.current = entry
        return web.json_response(entry)

    async def _stop(self, request: web.Request) -> web.Response:
        assert self.current is not None
        assert int(request.match_info["eid"]) == self.current["id"]
        stopped = {**self.current, "stop": "2026-11-18T10:30:00Z", "duration": 5400}
        self.current = None
        return web.json_response(stopped)

    async def _tasks(self, request: web.Request) -> web.Response:
        key = (int(request.match_info["wid"]), int(request.match_info["pid"]))
        return self._list(self.project_tasks.get(key))

    async def _create_task(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._next_id += 1
        return web.json_response(
            {**body, "id": self._next_id, "project_id": int(request.match_info["pid"])}
        )

    async def _my_tasks(self, request: web.Request) -> web.Response:
        return self._list(self.my_tasks)

    async def _quota(self, request: web.Request) -> web.Response:
        return self._list(self.quota)
