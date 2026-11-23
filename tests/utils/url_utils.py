"""URL helpers for tests: never build API paths with f-strings."""

import urllib.parse
from typing import Any

API_PREFIX = "/api/v9"


def api_path(*path_segments: str | int, **query_params: Any) -> str:
    """Path (plus optional query string) of a Toggl API v9 endpoint."""
    path = (
        API_PREFIX
        + "/"
        + "/".join(urllib.parse.quote(str(segment), safe="") for segment in path_segments)
    )
    filtered = {k: v for k, v in query_params.items() if v is not None}
    if filtered:
        return f"{path}?{urllib.parse.urlencode(filtered, quote_via=urllib.parse.quote)}"
    return path
