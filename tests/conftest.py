import inspect
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import pytest
from aiohttp.test_utils import TestServer
from mcp import Client
from mcp.server.mcpserver import MCPServer

from toggl_track_mcp.client import TogglClient
from toggl_track_mcp.config import Config
from toggl_track_mcp.server import build_server

from .utils.fake_toggl import FakeToggl
from .utils.url_utils import API_PREFIX

TOKEN = "test-token-0123456789abcdef"
# Wednesday 2026-11-18 12:30 in Helsinki (UTC+2).
NOW = datetime(2026, 11, 18, 10, 30, tzinfo=UTC)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        function = getattr(item, "function", None)
        if function is not None and inspect.iscoroutinefunction(function):
            item.add_marker(pytest.mark.anyio)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def toggl() -> AsyncIterator[FakeToggl]:
    fake = FakeToggl()
    server = TestServer(fake.app())
    await server.start_server()
    fake.base_url = str(server.make_url(API_PREFIX))
    try:
        yield fake
    finally:
        await server.close()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_client(toggl: FakeToggl, sleeps: list[float]) -> Callable[..., TogglClient]:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def make(**kwargs: object) -> TogglClient:
        options: dict[str, object] = {"min_interval": 0, "sleep": fake_sleep}
        options.update(kwargs)
        return TogglClient(TOKEN, base_url=toggl.base_url, **options)  # type: ignore[arg-type]

    return make


@pytest.fixture
def make_server(make_client: Callable[..., TogglClient]) -> Callable[..., MCPServer]:
    def make(*, read_only: bool = False, now: datetime = NOW, timezone: str | None = None):
        config = Config(api_token=TOKEN, read_only=read_only, timezone=timezone)
        return build_server(config, client=make_client(), now=lambda: now)

    return make


async def call_text(server: MCPServer, tool: str, **arguments: object) -> str:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)
    text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
    assert not result.is_error, text
    return text


async def call_error(server: MCPServer, tool: str, **arguments: object) -> str:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)
    text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
    assert result.is_error, text
    return text
