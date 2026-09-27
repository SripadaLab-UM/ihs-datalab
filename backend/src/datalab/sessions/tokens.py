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


@dataclass(frozen=True)
class SessionAccess:
    session_id: str
    kind: SessionKind
    # Where query results are written on the host, and where the agent sees them.
    results_dir: Path
    results_path_in_container: str = "/data/oracle"
    # The ihs-data tools it may use (its mode's, sessions/modes.py), or None
    # for all of them. DataLab's data tools refuse the others.
    tools: frozenset[str] | None = None

    def allows(self, tool: str) -> bool:
        return self.tools is None or tool in self.tools


class SessionTokens:
    def __init__(self) -> None:
        self._by_token: dict[str, SessionAccess] = {}
        self._lock = threading.Lock()

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

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            for token in [t for t, a in self._by_token.items() if a.session_id == session_id]:
                del self._by_token[token]


def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None
