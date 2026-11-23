"""The real entry point over stdio: nothing but JSON-RPC may reach stdout."""

import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def test_stdio_entry_point_lists_tools() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "toggl_track_mcp"],
        env={"TOGGL_API_TOKEN": "dummy-token", "TOGGL_READ_ONLY": "false"},
    )
    async with Client(params, read_timeout_seconds=30) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
    assert {"get_time_entries", "start_timer", "get_api_quota"} <= tools
    assert len(tools) == 12
