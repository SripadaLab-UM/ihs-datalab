"""Text from outside that can't be written as UTF-8, and sizes said honestly.

JSON can carry a lone UTF-16 surrogate (`"\\ud800"`), and so can a YAML
double-quoted escape, and Python keeps it in a `str`. But `str.encode()`
raises on it, so text from an agent or a request is checked with
`lone_surrogate` before anything encodes, hashes or measures it, and is
refused with a message rather than failing with an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SURROGATE = re.compile("[\ud800-\udfff]")


@dataclass(frozen=True)
class NotText:
    """The first lone surrogate: its 1-based line, and what it is."""

    line: int
    what: str  # "a character that isn't text (U+D800, …)"

    @property
    def message(self) -> str:
        return f"It has {self.what}. Remove it."


def lone_surrogate(text: str) -> NotText | None:
    """The first lone surrogate in `text`, or None if it encodes as UTF-8."""
    found = _SURROGATE.search(text)
    if found is None:
        return None
    return NotText(
        line=text.count("\n", 0, found.start()) + 1,
        what=(
            f"a character that isn't text (U+{ord(found.group()):04X}, "
            "half of a UTF-16 pair on its own)"
        ),
    )


def size_text(size: int) -> str:
    """A size in bytes, as people read it, never rounded down to less than it is:
    `800 bytes`, `2 KB`, `1,500 bytes`, `1 MB`."""
    if size >= 1024**2 and size % 1024**2 == 0:
        return f"{size // 1024**2} MB"
    if size >= 1024 and size % 1024 == 0:
        return f"{size // 1024} KB"
    return f"{size:,} byte" + ("" if size == 1 else "s")
