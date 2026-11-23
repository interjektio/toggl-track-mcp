"""Exception hierarchy.

Every subclass of `UserFacingError` carries a message that is safe to return to the
MCP client verbatim: it never contains the API token and any API-supplied text in it
has been sanitised.
"""


class UserFacingError(Exception):
    """An expected failure whose message can be shown to the user."""


class ConfigError(UserFacingError):
    """Invalid or missing configuration."""


class DateRangeError(UserFacingError, ValueError):
    """A date argument could not be parsed or the range is invalid."""


class InvalidArgument(UserFacingError, ValueError):
    """A tool argument is invalid."""


class LookupFailed(UserFacingError):
    """A named project or workspace was not found, or the name is ambiguous."""


class TogglError(UserFacingError):
    """The Toggl API could not be reached or returned an error."""


class TogglAuthError(TogglError):
    """HTTP 401/403: the token is invalid or lacks access."""


class TogglQuotaExceeded(TogglError):
    """HTTP 402 caused by the hourly API request quota."""

    def __init__(self, message: str, resets_in: int | None = None) -> None:
        super().__init__(message)
        self.resets_in = resets_in


class TogglRateLimited(TogglError):
    """HTTP 429 persisted after retries."""


class TogglAPIError(TogglError):
    """Any other non-success HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status
