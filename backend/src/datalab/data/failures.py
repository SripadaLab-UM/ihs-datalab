"""Why a query didn't run, as a structured tag that travels with its error.

The `query` tool's error is text the agent reads. It ends with one tag line,
`[datalab-failure category=… code=… query=…]`, so the chat can show the
failure's kind without matching words: the session runtime reads the tag
from the tool result (sessions/runtime.py) and the chat maps the category
and code to its own words (frontend activity.ts). The tag passes through the
agent's container, so it is read back strictly: only a known category, a
code of a known shape, and a query id of DataLab's shape are kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# validation: DataLab's SQL check refused it (it never reached Oracle).
# sql: Oracle refused it (a data type, a name, the SQL itself).
# permission: DataLab's account can't read something.
# timeout: a round trip, or the whole query, took too long.
# connection: DataLab couldn't reach the database (or it's not the right one).
# cancelled: stopped (Stop, or DataLab ending).
# result_limit: the result was larger than the limits allow.
CATEGORIES = frozenset(
    {"validation", "sql", "permission", "timeout", "connection", "cancelled", "result_limit"}
)
_CODE = re.compile(r"(?:ORA|DPY|DPI)-\d{4,5}|[a-z_]{1,30}")
_QUERY_ID = re.compile(r"q_\d{8}T\d{6}_[0-9a-f]{6}")
_TAG = re.compile(
    r"\[datalab-failure category=(?P<category>[a-z_]+)"
    r"(?: code=(?P<code>[A-Za-z0-9_-]+))?(?: query=(?P<query>[A-Za-z0-9_]+))?\]\s*$"
)


@dataclass(frozen=True)
class Failure:
    category: str
    code: str | None = None
    query_id: str | None = None

    def tag(self) -> str:
        parts = [f"category={self.category}"]
        if self.code and _CODE.fullmatch(self.code):
            parts.append(f"code={self.code}")
        if self.query_id and _QUERY_ID.fullmatch(self.query_id):
            parts.append(f"query={self.query_id}")
        return f"[datalab-failure {' '.join(parts)}]"


def read_tag(text: str) -> tuple[str, Failure | None]:
    """The text without its tag, and the tag's failure (None if it has none,
    or not a valid one)."""
    found = _TAG.search(text)
    if found is None:
        return text, None
    rest = text[: found.start()].rstrip()
    category = found.group("category")
    if category not in CATEGORIES:
        return rest, None
    code = found.group("code")
    query_id = found.group("query")
    return rest, Failure(
        category,
        code if code and _CODE.fullmatch(code) else None,
        query_id if query_id and _QUERY_ID.fullmatch(query_id) else None,
    )
