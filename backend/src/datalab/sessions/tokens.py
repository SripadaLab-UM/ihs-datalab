"""Per-session tokens.

Each session's Codex uses its token as the API key for the model relay and as
the bearer token for the `ihs-data` tools. A token identifies exactly one
session, so DataLab knows whose workspace a result belongs in and what that
session is allowed to do. Tokens are revoked when the session ends.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SessionKind = Literal["data", "research"]
# Tools a session has only when its mode names them (sessions/modes.py): a
# token without a tool list doesn't allow them. propose_sql fills the SQL
# Playground's editor; suggest_kb_update is for the Workspace modes whose
# findings can be durable (Analysis, Data extraction, Data engineering).
TAB_TOOLS = frozenset({"propose_sql"})
OPT_IN_TOOLS = TAB_TOOLS | {"suggest_kb_update"}
# Tools no turn asked with Express on may use, whatever its mode: each stops
# the turn to wait for the person (an analysis plan to approve, a research
# helper question to review before it goes online). Express has no such
# steps; the helper's review is a safety check, so it goes rather than being
# skipped. The data tools refuse them for as long as the turn runs.
EXPRESS_OFF_TOOLS = frozenset({"propose_plan", "ask_research_helper"})


@dataclass(frozen=True)
class SessionAccess:
    session_id: str
    kind: SessionKind
    # Where query results are written on the host, and where the agent sees them.
    results_dir: Path
    results_path_in_container: str = "/data/oracle"
    # The ihs-data tools it may use (its mode's, sessions/modes.py), or None
    # for all of them but OPT_IN_TOOLS. DataLab's data tools refuse the others.
    tools: frozenset[str] | None = None
    # Its mode's name ("Workflow authoring"), for the words of a refusal.
    mode_label: str | None = None

    def allows(self, tool: str) -> bool:
        if self.tools is None:
            return tool not in OPT_IN_TOOLS
        return tool in self.tools


class SessionTokens:
    def __init__(self) -> None:
        self._by_token: dict[str, SessionAccess] = {}
        self._lock = threading.Lock()
        # Sessions whose current turn was asked with Express on (set by the
        # session manager as each turn starts). Kept apart from the tokens,
        # which a restart mid-conversation replaces.
        self._express: set[str] = set()

    def issue(self, access: SessionAccess) -> str:
        token = "dls_" + secrets.token_urlsafe(32)
        with self._lock:
            self._by_token[token] = access
        return token

    def resolve(self, token: str | None) -> SessionAccess | None:
        if not token:
            return None
        with self._lock:
            return self._by_token.get(token)

    def set_express(self, session_id: str, on: bool) -> None:
        with self._lock:
            if on:
                self._express.add(session_id)
            else:
                self._express.discard(session_id)

    def express(self, session_id: str) -> bool:
        """Whether this session's current turn was asked with Express on."""
        with self._lock:
            return session_id in self._express

    def express_refuses(self, access: SessionAccess, tool: str) -> bool:
        """Whether `tool` is off because the session's turn is an Express one."""
        return tool in EXPRESS_OFF_TOOLS and self.express(access.session_id)

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            for token in [t for t, a in self._by_token.items() if a.session_id == session_id]:
                del self._by_token[token]


def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None
