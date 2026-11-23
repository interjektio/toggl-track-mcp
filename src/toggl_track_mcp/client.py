"""Async client for the Toggl Track API v9.

Toggl enforces an hourly request quota (HTTP 402) and a per-second leaky bucket
(HTTP 429); see https://engineering.toggl.com/docs/track/. This client spaces
requests out, retries 429 responses, and turns 402 into a readable error that
says when the quota resets.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
from yarl import URL

from . import __version__
from .errors import (
    TogglAPIError,
    TogglAuthError,
    TogglError,
    TogglQuotaExceeded,
    TogglRateLimited,
)
from .text import clean
from .timeutil import format_rfc3339_utc

API_BASE = URL("https://api.track.toggl.com/api/v9")
USER_AGENT = f"toggl-track-mcp/{__version__}"
CREATED_WITH = "toggl-track-mcp"

MAX_RETRY_WAIT = 30.0

log = logging.getLogger(__name__)

type JSON = Any


@dataclass
class QuotaState:
    """Quota as last reported by Toggl's response headers."""

    remaining: int | None = None
    resets_in: int | None = None


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _items(data: JSON, what: str) -> list[dict[str, Any]]:
    """Normalise a list response; Toggl returns null for empty lists."""
    if data is None:
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise TogglError(f"Unexpected response from Toggl when listing {what}.")
    return [item for item in data if isinstance(item, dict)]


def _object(data: JSON, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TogglError(f"Unexpected response from Toggl for {what}.")
    return data


class TogglClient:
    """Minimal Toggl Track API client with throttling, retries and quota tracking."""

    def __init__(
        self,
        api_token: str,
        *,
        base_url: URL | str = API_BASE,
        min_interval: float = 1.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_token:
            raise ValueError("api_token must not be empty")
        # Toggl expects HTTP Basic auth with the token as user and "api_token" as password.
        self._authorization = aiohttp.encode_basic_auth(api_token, "api_token")
        self._base = URL(base_url)
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._sleep = sleep
        self._monotonic = monotonic
        self._session: aiohttp.ClientSession | None = None
        self._throttle_lock = asyncio.Lock()
        self._last_request: float | None = None
        self.quota = QuotaState()

    def __repr__(self) -> str:
        return f"TogglClient(base_url={str(self._base)!r})"

    # -- plumbing -----------------------------------------------------------------

    def _url(self, *segments: str | int) -> URL:
        url = self._base
        for segment in segments:
            text = str(segment)
            if not text or "/" in text or text in {".", ".."}:
                raise ValueError(f"invalid URL path segment {text!r}")
            url = url / text
        return url

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": self._authorization,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            if self._last_request is not None and self._min_interval > 0:
                wait = self._last_request + self._min_interval - self._monotonic()
                if wait > 0:
                    await self._sleep(wait)
            self._last_request = self._monotonic()

    def _record_quota(self, headers: Mapping[str, str]) -> None:
        remaining = _int_header(headers, "X-Toggl-Quota-Remaining")
        resets_in = _int_header(headers, "X-Toggl-Quota-Resets-In")
        if remaining is not None:
            self.quota.remaining = remaining
        if resets_in is not None:
            self.quota.resets_in = resets_in

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** (attempt - 1), 8.0)

    def _retry_after(self, headers: Mapping[str, str], attempt: int) -> float:
        value = _int_header(headers, "Retry-After")
        if value is None or value < 0:
            return self._backoff(attempt)
        return min(float(value), MAX_RETRY_WAIT)

    @staticmethod
    def _error_for(status: int, body: str, headers: Mapping[str, str]) -> TogglError:
        detail = clean(body, default="(no details)", max_length=300)
        if status in (401, 403):
            return TogglAuthError(
                f"Toggl rejected the request (HTTP {status}): {detail}. Check that the API "
                "token is valid and has access to this resource."
            )
        if status == 402:
            resets_in = _int_header(headers, "X-Toggl-Quota-Resets-In")
            remaining = _int_header(headers, "X-Toggl-Quota-Remaining")
            if resets_in is not None or remaining is not None or "limit" in detail.lower():
                when = (
                    f"it resets in about {max(1, round(resets_in / 60))} minute(s)"
                    if resets_in is not None
                    else "try again later"
                )
                return TogglQuotaExceeded(
                    f"The Toggl API hourly request quota is used up (HTTP 402); {when}. "
                    "Do not retry until then.",
                    resets_in=resets_in,
                )
            return TogglAPIError(
                f"Toggl returned HTTP 402 Payment Required: {detail}. This feature may "
                "need a paid Toggl plan.",
                status,
            )
        if status == 429:
            return TogglRateLimited(
                "Toggl is rate limiting requests (HTTP 429) and retries did not help; "
                "wait a minute and try again."
            )
        return TogglAPIError(f"Toggl API error (HTTP {status}): {detail}", status)

    async def request(
        self,
        method: str,
        *segments: str | int,
        params: Mapping[str, str] | None = None,
        json_body: JSON = None,
    ) -> JSON:
        """Send a request and return the decoded JSON body (None for an empty body).

        429 responses are retried for every method. Connection errors and 5xx
        responses are retried only for GET, so a POST is never sent twice.
        """
        url = self._url(*segments)
        idempotent = method.upper() == "GET"
        attempt = 0
        while True:
            await self._throttle()
            session = self._get_session()
            status: int
            body: str
            headers: Mapping[str, str]
            try:
                # Redirects are refused so the Authorization header can never be
                # forwarded to another host; a 3xx is reported as an API error.
                async with session.request(
                    method, url, params=params, json=json_body, allow_redirects=False
                ) as resp:
                    self._record_quota(resp.headers)
                    status, headers = resp.status, resp.headers
                    body = await resp.text()
            except (aiohttp.ClientError, TimeoutError) as exc:
                if idempotent and attempt < self._max_retries:
                    attempt += 1
                    log.warning("Toggl request failed (%s); retrying", type(exc).__name__)
                    await self._sleep(self._backoff(attempt))
                    continue
                raise TogglError(f"Could not reach the Toggl API ({type(exc).__name__}).") from exc

            if 200 <= status < 300:
                if not body.strip():
                    return None
                try:
                    return json.loads(body)
                except ValueError as exc:
                    raise TogglError("Toggl returned a response that is not valid JSON.") from exc
            if status == 429 and attempt < self._max_retries:
                attempt += 1
                wait = self._retry_after(headers, attempt)
                log.warning("Toggl rate limit (429); retrying in %.1f s", wait)
                await self._sleep(wait)
                continue
            if status >= 500 and idempotent and attempt < self._max_retries:
                attempt += 1
                log.warning("Toggl server error %d; retrying", status)
                await self._sleep(self._backoff(attempt))
                continue
            raise self._error_for(status, body, headers)

    # -- endpoints ------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """The current user, with workspaces, projects, clients and tasks (one request)."""
        data = await self.request("GET", "me", params={"with_related_data": "true"})
        return _object(data, "the current user")

    async def list_time_entries(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Time entries that start in [start, end), with project/client names (meta)."""
        params = {
            "start_date": format_rfc3339_utc(start),
            "end_date": format_rfc3339_utc(end),
            "meta": "true",
        }
        data = await self.request("GET", "me", "time_entries", params=params)
        return _items(data, "time entries")

    async def get_current_time_entry(self) -> dict[str, Any] | None:
        data = await self.request("GET", "me", "time_entries", "current")
        if data is None:
            return None
        return _object(data, "the running time entry")

    async def create_time_entry(self, workspace_id: int, body: dict[str, Any]) -> dict[str, Any]:
        data = await self.request(
            "POST", "workspaces", int(workspace_id), "time_entries", json_body=body
        )
        return _object(data, "the new time entry")

    async def stop_time_entry(self, workspace_id: int, entry_id: int) -> dict[str, Any]:
        data = await self.request(
            "PATCH", "workspaces", int(workspace_id), "time_entries", int(entry_id), "stop"
        )
        return _object(data, "the stopped time entry")

    async def list_project_tasks(self, workspace_id: int, project_id: int) -> list[dict[str, Any]]:
        data = await self.request(
            "GET", "workspaces", int(workspace_id), "projects", int(project_id), "tasks"
        )
        return _items(data, "tasks")

    async def list_my_tasks(self) -> list[dict[str, Any]]:
        """All tasks in projects the user participates in, including done ones."""
        data = await self.request(
            "GET", "me", "tasks", params={"include_not_active": "true", "meta": "true"}
        )
        return _items(data, "tasks")

    async def create_task(
        self,
        workspace_id: int,
        project_id: int,
        name: str,
        estimated_seconds: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "active": True}
        if estimated_seconds:
            body["estimated_seconds"] = estimated_seconds
        data = await self.request(
            "POST",
            "workspaces",
            int(workspace_id),
            "projects",
            int(project_id),
            "tasks",
            json_body=body,
        )
        return _object(data, "the new task")

    async def get_quota(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "me", "quota")
        return _items(data, "API quotas")
