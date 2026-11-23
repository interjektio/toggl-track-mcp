"""Caching and lookup layer between the MCP tools and the Toggl API client.

Toggl allows only about 30 requests per hour to `/me` endpoints on non-Enterprise
plans, so workspaces, projects and clients are fetched with a single
`GET /me?with_related_data=true` and cached; time entry lists are cached briefly
so that, e.g., a summary followed by a detailed listing costs one request.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from typing import Any

from .client import TogglClient
from .errors import ConfigError, LookupFailed
from .text import clean
from .timeutil import load_timezone, utc_now

log = logging.getLogger(__name__)

ENTRIES_TTL = 60.0
MAX_LISTED_NAMES = 30


def _index(items: object) -> dict[int, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    return {
        item["id"]: item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class Account:
    """The parts of `GET /me?with_related_data=true` that the tools need."""

    timezone: tzinfo
    timezone_name: str
    default_workspace_id: int | None
    workspaces: dict[int, dict[str, Any]]
    projects: dict[int, dict[str, Any]]
    clients: dict[int, dict[str, Any]]

    @classmethod
    def from_me(cls, me: dict[str, Any], timezone_override: str | None = None) -> Account:
        tz_name = timezone_override or me.get("timezone") or "UTC"
        tz = load_timezone(tz_name)
        if tz is None:
            log.warning("Unknown timezone %r in Toggl profile; using UTC", tz_name)
            tz, tz_name = UTC, "UTC"
        default_ws = me.get("default_workspace_id")
        return cls(
            timezone=tz,
            timezone_name=tz_name,
            default_workspace_id=default_ws if isinstance(default_ws, int) else None,
            workspaces=_index(me.get("workspaces")),
            projects=_index(me.get("projects")),
            clients=_index(me.get("clients")),
        )

    def workspace_label(self, workspace_id: object) -> str:
        workspace = self.workspaces.get(workspace_id) if isinstance(workspace_id, int) else None
        if workspace is None:
            return f"Workspace #{workspace_id}" if workspace_id else "Unknown workspace"
        return clean(workspace.get("name"), default=f"Workspace #{workspace_id}")

    def project_label(self, project_id: object, fallback_name: object = None) -> str:
        if not project_id:
            return "No project"
        project = self.projects.get(project_id) if isinstance(project_id, int) else None
        name = fallback_name or (project or {}).get("name")
        return clean(name, default=f"Project #{project_id}")

    def client_label(self, project: dict[str, Any]) -> str | None:
        name = project.get("client_name")
        if not name:
            client = self.clients.get(project.get("client_id"))  # type: ignore[arg-type]
            name = client.get("name") if client else None
        return clean(name) or None

    # -- lookups ------------------------------------------------------------------------

    def find_workspace(self, workspace: str) -> dict[str, Any]:
        """Find a workspace by ID or (case-insensitive) name."""
        wanted = workspace.strip()
        matches = [
            ws
            for ws in self.workspaces.values()
            if str(ws.get("id")) == wanted or _norm(str(ws.get("name") or "")) == _norm(wanted)
        ]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(
            f"{clean(ws.get('name'))} (ID {ws.get('id')})" for ws in self.workspaces.values()
        )
        if not matches:
            raise LookupFailed(f"Workspace {clean(wanted)!r} not found. Workspaces: {names}.")
        raise LookupFailed(
            f"Workspace name {clean(wanted)!r} is ambiguous; pass the workspace ID instead. "
            f"Workspaces: {names}."
        )

    def find_project(self, name: str, workspace: str = "") -> dict[str, Any]:
        """Find a project by name (exact, then case-insensitive) or numeric ID.

        Raises LookupFailed when nothing matches or when the name exists in several
        workspaces and `workspace` was not given to choose between them.
        """
        wanted = name.strip()
        if not wanted:
            raise LookupFailed("project_name must not be empty.")
        candidates = list(self.projects.values())
        where = ""
        if workspace.strip():
            ws = self.find_workspace(workspace)
            candidates = [p for p in candidates if p.get("workspace_id") == ws["id"]]
            where = f" in workspace {self.workspace_label(ws['id'])!r}"

        matches = [p for p in candidates if p.get("name") == wanted]
        if not matches:
            matches = [p for p in candidates if _norm(str(p.get("name") or "")) == _norm(wanted)]
        if not matches and wanted.isdigit():
            matches = [p for p in candidates if p.get("id") == int(wanted)]

        if len(matches) == 1:
            return matches[0]
        if not matches:
            known = sorted({clean(p.get("name")) for p in candidates if p.get("name")})
            listed = ", ".join(known[:MAX_LISTED_NAMES])
            more = (
                f" and {len(known) - MAX_LISTED_NAMES} more"
                if len(known) > MAX_LISTED_NAMES
                else ""
            )
            return_hint = f" Known projects: {listed}{more}." if known else ""
            raise LookupFailed(f"Project {clean(wanted)!r} not found{where}.{return_hint}")
        options = "; ".join(
            f"{clean(p.get('name'))} in workspace {self.workspace_label(p.get('workspace_id'))!r} "
            f"(project ID {p.get('id')})"
            for p in matches
        )
        raise LookupFailed(
            f"Project name {clean(wanted)!r} matches several projects: {options}. "
            "Pass workspace_name, or the project ID as project_name, to choose one."
        )

    def pick_workspace_for_new_entry(self, workspace: str = "") -> int:
        if workspace.strip():
            return int(self.find_workspace(workspace)["id"])
        if self.default_workspace_id is not None:
            return self.default_workspace_id
        if len(self.workspaces) == 1:
            return next(iter(self.workspaces))
        raise LookupFailed(
            "No default workspace is set in your Toggl profile; pass workspace_name."
        )


class TogglService:
    """Owns the API client and caches account data and time entry lists."""

    def __init__(
        self,
        client: TogglClient | None,
        *,
        cache_ttl: float,
        entries_ttl: float = ENTRIES_TTL,
        timezone_override: str | None = None,
        now: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._cache_ttl = cache_ttl
        self._entries_ttl = entries_ttl
        self._timezone_override = timezone_override
        self._now = now
        self._monotonic = monotonic
        self._account: tuple[float, Account] | None = None
        self._account_lock = asyncio.Lock()
        self._entries: dict[tuple[datetime, datetime], tuple[float, list[dict[str, Any]]]] = {}

    @property
    def client(self) -> TogglClient:
        if self._client is None:
            raise ConfigError(
                "TOGGL_API_TOKEN is not configured. Set TOGGL_API_TOKEN (or "
                "TOGGL_API_TOKEN_FILE) in the MCP server environment; the token is on "
                "https://track.toggl.com/profile."
            )
        return self._client

    def now(self) -> datetime:
        return self._now()

    def today(self, account: Account) -> date:
        return self._now().astimezone(account.timezone).date()

    def _fresh(self, stored_at: float, ttl: float) -> bool:
        return self._monotonic() - stored_at < ttl

    def cached_account(self) -> Account | None:
        """The cached account if still fresh, without making a request."""
        if self._account and self._fresh(self._account[0], self._cache_ttl):
            return self._account[1]
        return None

    async def account(self) -> Account:
        cached = self.cached_account()
        if cached is not None:
            return cached
        async with self._account_lock:
            cached = self.cached_account()
            if cached is not None:
                return cached
            me = await self.client.get_me()
            account = Account.from_me(me, self._timezone_override)
            self._account = (self._monotonic(), account)
            return account

    async def time_entries(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        key = (start, end)
        hit = self._entries.get(key)
        if hit and self._fresh(hit[0], self._entries_ttl):
            return hit[1]
        entries = await self.client.list_time_entries(start, end)
        self._entries = {
            k: v for k, v in self._entries.items() if self._fresh(v[0], self._entries_ttl)
        }
        self._entries[key] = (self._monotonic(), entries)
        return entries

    def invalidate_time_entries(self) -> None:
        self._entries.clear()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
