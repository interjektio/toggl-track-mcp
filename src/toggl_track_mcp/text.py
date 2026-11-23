"""Sanitising of untrusted text before it is placed in tool output.

Project, task, client and workspace names, time entry descriptions and API error
bodies can all be written by other members of a shared Toggl workspace. They are
rendered as a single line with control, zero-width, bidirectional-override and
Unicode tag characters removed, so they cannot fake extra lines or hide text in
the tool output that the model reads.
"""

import re

_UNSAFE_CHARS = re.compile(
    r"[\x00-\x1f\x7f-\x9f"  # C0 and C1 control characters, including newlines
    r"\u200b-\u200f"  # zero-width characters and directional marks
    r"\u2028-\u202e"  # line/paragraph separators and bidirectional embeddings/overrides
    r"\u2060-\u2064\u2066-\u206f"  # invisible operators, bidi isolates, deprecated format
    r"\ufeff"  # byte order mark / zero-width no-break space
    r"\U000e0000-\U000e007f]"  # Unicode tag characters (invisible "ASCII smuggling")
)
_WHITESPACE = re.compile(r"\s+")


def clean(value: object, *, default: str = "", max_length: int = 200) -> str:
    """Render an untrusted value as a single, bounded line of text."""
    if value is None:
        return default
    text = _WHITESPACE.sub(" ", _UNSAFE_CHARS.sub(" ", str(value))).strip()
    if not text:
        return default
    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "\u2026"
    return text
