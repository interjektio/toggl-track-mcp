# Toggl Track MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude and
other MCP clients read and update your [Toggl Track](https://toggl.com/track/) time
tracking: list and summarise time entries, start and stop timers, and manage project tasks.

This is Interjektio's hardened fork of
[vontell/toggl-track-mcp](https://github.com/vontell/toggl-track-mcp). See
[Changes from upstream](#changes-from-upstream).

![Claude Code using the server](example.png)

## Quick start (Docker)

1. Copy your API token from your [Toggl Track profile](https://track.toggl.com/profile).

2. Put it in a private env file outside the repository:

   ```bash
   mkdir -p ~/.config/toggl-track-mcp
   install -m 600 .env.example ~/.config/toggl-track-mcp/env
   $EDITOR ~/.config/toggl-track-mcp/env   # set TOGGL_API_TOKEN=...
   ```

3. Build the image:

   ```bash
   docker build -t toggl-track-mcp .
   ```

4. Register the server with your MCP client.

   **Claude Code**:

   ```bash
   claude mcp add toggl-track -- \
     docker run -i --rm --env-file /home/YOU/.config/toggl-track-mcp/env toggl-track-mcp
   ```

   **Claude Desktop** (`claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "toggl-track": {
         "command": "docker",
         "args": [
           "run", "-i", "--rm",
           "--env-file", "/Users/YOU/.config/toggl-track-mcp/env",
           "toggl-track-mcp"
         ]
       }
     }
   }
   ```

   Using `--env-file` keeps the token out of the client configuration file.

### Without Docker

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv sync --locked
uv run --locked --env-file ~/.config/toggl-track-mcp/env toggl-track-mcp
```

For an MCP client, use the command
`uv run --locked --project /path/to/toggl-track-mcp --env-file /path/to/env toggl-track-mcp`.

## Configuration

| Variable               | Meaning                                                                       |
|------------------------|-------------------------------------------------------------------------------|
| `TOGGL_API_TOKEN`      | Toggl Track API token (required).                                             |
| `TOGGL_API_TOKEN_FILE` | Path to a file containing the token; used when `TOGGL_API_TOKEN` is unset.    |
| `TOGGL_READ_ONLY`      | `true` exposes only the read-only tools.                                      |
| `TOGGL_TIMEZONE`       | IANA timezone (e.g. `Europe/Helsinki`) overriding your Toggl profile timezone. |
| `TOGGL_CACHE_TTL`      | Seconds to cache workspaces, projects and clients. Default `900`.             |

## Tools

Dates are `YYYY-MM-DD` in your Toggl profile timezone, and both `start_date` and
`end_date` are inclusive, so `start_date == end_date` means a single day. Project names
match case-insensitively; when a name exists in several workspaces, pass `workspace_name`
or use the numeric project ID.

| Tool                  | Kind  | Description                                                    |
|-----------------------|-------|----------------------------------------------------------------|
| `get_workspaces`      | read  | Workspaces and IDs; marks your default workspace.              |
| `get_projects`        | read  | Projects by workspace, with client, archived and private flags. |
| `get_time_entries`    | read  | Entries grouped by day with daily totals (default: last 7 days). |
| `get_time_summary`    | read  | Time per project with percentages (default: last 7 days).      |
| `search_time_entries` | read  | Entries whose description contains a text (default: 30 days).  |
| `get_current_timer`   | read  | The running timer and its elapsed time.                        |
| `get_project_tasks`   | read  | Tasks of one project.                                          |
| `get_all_tasks`       | read  | Tasks across all your projects.                                |
| `get_api_quota`       | read  | Remaining Toggl API requests in the current hour.              |
| `start_timer`         | write | Start a timer, in the project's workspace or your default one. |
| `stop_current_timer`  | write | Stop the running timer.                                        |
| `create_project_task` | write | Create a task, optionally with an estimate.                    |

Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, ...), so clients
can ask for confirmation before the write tools run. With `TOGGL_READ_ONLY=true`, the
write tools and their prompts are not registered at all.

The server also provides prompt templates such as `weekly_time_report`,
`project_deep_dive` and `quick_start_timer`.

## Toggl API limits

Toggl [limits API usage](https://engineering.toggl.com/docs/track/): requests to `/me`
endpoints are capped at about **30 per hour per user** on Free, Starter and Premium
plans, and workspace requests at 30/240/600 per hour by plan. When the quota is used up
Toggl answers HTTP 402; there is also a per-second limit (HTTP 429).

The server is built around this:

- workspaces, projects and clients come from a single `GET /me?with_related_data=true`
  and are cached (`TOGGL_CACHE_TTL`);
- time entry lists are cached for 60 seconds and include project names, so a summary
  followed by a detailed listing costs one request;
- `get_all_tasks` uses a single `GET /me/tasks` request rather than one per project;
- requests are spaced at least one second apart, 429 responses are retried with
  backoff (honouring `Retry-After`), and a 402 quota error says when the quota resets.

`GET /me/time_entries` only reaches back about three months and returns at most 1000
entries; the tools say so when a result may be truncated.

## Security notes

- The token is sent only to `https://api.track.toggl.com` over HTTPS. Redirects are
  never followed, so the `Authorization` header cannot be forwarded elsewhere.
- Project, task, client and workspace names and entry descriptions can be written by
  other members of a shared workspace. They are rendered as single lines with control,
  zero-width, bidirectional-override and Unicode tag characters removed, and the server
  instructions tell the model to treat them as data. This reduces prompt-injection risk
  but cannot remove it; use `TOGGL_READ_ONLY=true` if you don't need the write tools.
- All dependencies are pinned in `uv.lock`; the Docker base images are pinned by digest
  and the container runs as an unprivileged user.

## Development

```bash
uv sync --locked
uv run --locked pytest
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
```

The tests run the tools through an in-process MCP client against a fake Toggl API,
plus one test that starts the real stdio entry point.

To update dependencies, move `exclude-newer` in `pyproject.toml` to a date at least
two weeks in the past, then run `uv lock --upgrade` and commit `uv.lock`. Update the
image digests in the `Dockerfile` the same way.

## Changes from upstream

- **Works with current dependencies.** Upstream imports `mcp.server.fastmcp`, which
  no longer exists in `mcp` 2.x, so a fresh build of upstream fails at startup. This fork
  uses `MCPServer` from `mcp` 2.x.
- **Pinned supply chain.** `requirements.txt` with `>=` ranges replaced by
  `pyproject.toml` + `uv.lock` (`uv sync --locked`); base images pinned by digest;
  container runs as a non-root user; committed `.claude/settings.local.json` removed.
- **Correct dates.** Dates were naive and in the container's UTC clock, and `end_date`
  was effectively exclusive, so "today" was missing from the default range. Dates are now
  inclusive days in your Toggl timezone. The duplicate `get_time_entries_fixed` tool is
  gone.
- **Correct workspace.** `start_timer` used the first workspace in the list, not the
  project's or your default one.
- **Quota-aware.** Upstream made 2-3 requests per call with no caching, and
  `get_all_tasks` made one per project while silently hiding errors. See
  [Toggl API limits](#toggl-api-limits).
- **Errors are errors.** Failures come back as MCP tool errors (`isError`) instead of
  ordinary text, and null descriptions no longer crash searches.
- **Untrusted text sanitised**, tool annotations added, optional read-only mode, and
  nothing is printed to stdout, which is the MCP protocol channel.
