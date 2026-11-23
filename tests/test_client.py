"""HTTP client behaviour: auth, retries, quota and error handling."""

import base64
from datetime import UTC, datetime

import pytest

from toggl_track_mcp.errors import (
    TogglAPIError,
    TogglAuthError,
    TogglQuotaExceeded,
    TogglRateLimited,
)

from .conftest import TOKEN
from .utils.url_utils import api_path

ME = api_path("me")


async def test_basic_auth_and_user_agent(toggl, make_client) -> None:
    client = make_client()
    try:
        await client.get_me()
    finally:
        await client.close()
    (request,) = toggl.requests_to(ME)
    expected = base64.b64encode(f"{TOKEN}:api_token".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert request.headers["User-Agent"].startswith("toggl-track-mcp/")
    assert request.query == {"with_related_data": "true"}


async def test_429_is_retried_honouring_retry_after(toggl, make_client, sleeps) -> None:
    toggl.fail_next(429, headers={"Retry-After": "2"})
    client = make_client()
    try:
        me = await client.get_me()
    finally:
        await client.close()
    assert me["id"] == 1
    assert len(toggl.requests_to(ME)) == 2
    assert sleeps == [2.0]


async def test_429_gives_up_after_max_retries(toggl, make_client) -> None:
    toggl.fail_next(429, times=3)
    client = make_client(max_retries=2)
    try:
        with pytest.raises(TogglRateLimited):
            await client.get_me()
    finally:
        await client.close()
    assert len(toggl.requests_to(ME)) == 3


async def test_402_quota_is_not_retried(toggl, make_client) -> None:
    toggl.fail_next(
        402,
        body="You have hit your hourly limit for API calls.",
        headers={"X-Toggl-Quota-Remaining": "0", "X-Toggl-Quota-Resets-In": "780"},
    )
    client = make_client()
    try:
        with pytest.raises(TogglQuotaExceeded, match="about 13 minute") as excinfo:
            await client.get_me()
    finally:
        await client.close()
    assert excinfo.value.resets_in == 780
    assert len(toggl.requests_to(ME)) == 1
    assert client.quota.remaining == 0


async def test_402_without_quota_headers_is_a_plan_error(toggl, make_client) -> None:
    toggl.fail_next(402, body="Tasks are not available on this plan")
    client = make_client()
    try:
        with pytest.raises(TogglAPIError, match="paid Toggl plan"):
            await client.list_project_tasks(200, 3)
    finally:
        await client.close()


async def test_403_error_does_not_leak_token(toggl, make_client) -> None:
    toggl.fail_next(403, body="Incorrect username and/or password")
    client = make_client()
    try:
        with pytest.raises(TogglAuthError) as excinfo:
            await client.get_me()
    finally:
        await client.close()
    assert TOKEN not in str(excinfo.value)
    assert "Incorrect username" in str(excinfo.value)


async def test_get_is_retried_on_server_error(toggl, make_client, sleeps) -> None:
    toggl.fail_next(503)
    client = make_client()
    try:
        await client.get_me()
    finally:
        await client.close()
    assert len(toggl.requests_to(ME)) == 2
    assert sleeps == [1.0]


async def test_post_is_never_retried_on_server_error(toggl, make_client) -> None:
    toggl.fail_next(500, body="boom", method="POST")
    client = make_client()
    path = api_path("workspaces", 200, "time_entries")
    try:
        with pytest.raises(TogglAPIError, match="HTTP 500"):
            await client.create_time_entry(200, {"description": "x"})
    finally:
        await client.close()
    assert len(toggl.requests_to(path, "POST")) == 1


async def test_null_and_wrapped_list_responses(toggl, make_client) -> None:
    client = make_client()
    try:
        toggl.my_tasks = None
        assert await client.list_my_tasks() == []
        toggl.wrap_items = True
        toggl.my_tasks = [{"id": 1, "name": "a"}]
        assert await client.list_my_tasks() == [{"id": 1, "name": "a"}]
        assert await client.get_current_time_entry() is None
    finally:
        await client.close()


async def test_time_entry_bounds_are_sent_as_utc_z(toggl, make_client) -> None:
    client = make_client()
    start = datetime(2026, 11, 17, 22, tzinfo=UTC)
    end = datetime(2026, 11, 18, 22, tzinfo=UTC)
    try:
        await client.list_time_entries(start, end)
    finally:
        await client.close()
    (request,) = toggl.requests_to(api_path("me", "time_entries"))
    assert request.query == {
        "start_date": "2026-11-17T22:00:00Z",
        "end_date": "2026-11-18T22:00:00Z",
        "meta": "true",
    }


async def test_requests_are_spaced_by_min_interval(toggl, make_client) -> None:
    clock = [100.0]
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    client = make_client(min_interval=1.0, sleep=sleep, monotonic=lambda: clock[0])
    try:
        await client.get_me()
        clock[0] += 0.25
        await client.get_me()
    finally:
        await client.close()
    assert waits == [0.75]


async def test_url_segments_cannot_escape_the_api_path(make_client) -> None:
    client = make_client()
    for bad in ("../../evil", "..", ""):
        with pytest.raises(ValueError, match="segment"):
            client._url("workspaces", bad)


async def test_redirects_are_not_followed(toggl, make_client) -> None:
    toggl.fail_next(302, headers={"Location": "https://attacker.invalid/steal"})
    client = make_client()
    try:
        with pytest.raises(TogglAPIError, match="HTTP 302"):
            await client.get_me()
    finally:
        await client.close()
    assert len(toggl.requests) == 1
