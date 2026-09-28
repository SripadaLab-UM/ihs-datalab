"""Suggested Knowledge updates: durable findings from a Workspace conversation.

When the agent confirms something durable about the data (a quirk a query
showed, a definition, a caveat), it can suggest adding it to the knowledge
base with the `suggest_kb_update` tool (data/agent_tools.py; only in the
modes that name it, sessions/modes.py). The person can also propose one
themselves ("Propose a Knowledge update" in the conversation).

A suggestion is checked here and recorded as a `kb_suggestion` event of the
conversation, then `kb_suggestion_updated` as the person accepts or
dismisses it. **It never writes the knowledge base.** Accepting it starts a
person's edit of the page (knowledge/edits.py), which they review, check and
Save & share like any other edit.

The checks:

- the page is a page of the knowledge base's layout (existing or new) that
  a person could edit;
- the evidence is queries that ran, and succeeded, in this conversation
  (DataLab's own record of them, the Data accessed log);
- the text passes the knowledge base's participant-data scans, and names no
  small counts of people;
- at most two a turn, from the agent.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable
from typing import Any

from datalab.knowledge import check as kb
from datalab.knowledge.edits import editable_problem
from datalab.textcheck import lone_surrogate

EVENT = "kb_suggestion"
UPDATED = "kb_suggestion_updated"
MAX_PER_TURN = 2
MAX_TITLE = 120
MAX_TEXT = 4_000
MAX_REASON = 600
MAX_EVIDENCE = 8
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# "3 participants", "n = 4": counts of people under the small-cell rule (11).
_SMALL_COUNT = re.compile(
    r"(?<![\w.])(?:[1-9]|10)\s+(?:participants?|people|persons?|interns?|subjects?|"
    r"individuals?|respondents?|users?)\b|\bn\s*=\s*(?:[1-9]|10)(?![\d.])",
    re.IGNORECASE,
)


class SuggestionInvalid(ValueError):
    """The suggestion can't be recorded. The message is written for its author."""


class KbSuggestions:
    def __init__(self, store: Any, access_log: Any | None = None) -> None:
        # store: sessions.store.ConversationStore; access_log: data.access_log.AccessLog.
        self._store = store
        self._access_log = access_log
        self.turn_running: Callable[[str], bool] = lambda conversation_id: True

    def suggest(
        self,
        conversation_id: str,
        *,
        page: str,
        title: str,
        text: str,
        evidence_query_ids: Iterable[str],
        reason: str,
        by: str = "agent",
    ) -> dict[str, Any]:
        """Check a suggestion and record it in the conversation (SuggestionInvalid if it
        can't be). Nothing is written to the knowledge base."""
        if by == "agent" and not self.turn_running(conversation_id):
            raise SuggestionInvalid("A Knowledge update can only be suggested during a turn.")
        if self._store.get(conversation_id) is None:
            raise SuggestionInvalid("No such conversation.")
        asked = self._store.last(conversation_id, "user_message")
        if by == "agent":
            if asked is None:
                raise SuggestionInvalid("A Knowledge update can only be suggested during a turn.")
            review = self._store.last(conversation_id, "review_started")
            if review is not None and review.seq > asked.seq:
                raise SuggestionInvalid("A Knowledge update can't be suggested during a review.")
            made = self._store.events_of_types_after(conversation_id, asked.seq, (EVENT,))
            if sum(1 for e in made if e.data.get("by") == "agent") >= MAX_PER_TURN:
                raise SuggestionInvalid(
                    f"This answer already suggests {MAX_PER_TURN} Knowledge updates, the most one "
                    "answer may. Keep the rest for the person to ask about."
                )
        path = page_path(page)
        clean_title = _line(title, "title", MAX_TITLE)
        clean_text = _block(text, "text", MAX_TEXT)
        clean_reason = _block(reason, "reason", MAX_REASON)
        evidence = self._evidence(conversation_id, evidence_query_ids)
        _no_participant_data(
            path, {"title": clean_title, "text": clean_text, "reason": clean_reason}
        )
        suggestion = {
            "id": f"ks_{secrets.token_hex(6)}",
            "by": by,
            "turn": self._store.count(conversation_id, "user_message"),
            "page": path,
            "title": clean_title,
            "text": clean_text,
            "reason": clean_reason,
            "evidence": evidence,
        }
        self._store.append(conversation_id, EVENT, suggestion)
        return suggestion

    def get(self, conversation_id: str, suggestion_id: str) -> dict[str, Any]:
        """A suggestion as it stands now (status open, accepted or dismissed)."""
        found: dict[str, Any] | None = None
        for event in self._store.events_of_types_after(conversation_id, 0, (EVENT, UPDATED)):
            if event.data.get("id") != suggestion_id:
                continue
            if event.type == EVENT:
                found = {**event.data, "status": "open"}
            elif found is not None:
                found.update({k: v for k, v in event.data.items() if k != "id"})
        if found is None:
            raise LookupError("No such suggestion in this conversation.")
        return found

    def mark(self, conversation_id: str, suggestion_id: str, status: str, **extra: Any) -> None:
        self._store.append(
            conversation_id, UPDATED, {"id": suggestion_id, "status": status, **extra}
        )

    def _evidence(self, conversation_id: str, ids: Iterable[str]) -> list[dict[str, Any]]:
        wanted = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
        if not wanted:
            raise SuggestionInvalid(
                "A Knowledge update needs evidence: the ids of the queries in this conversation "
                "that showed it (evidence_query_ids)."
            )
        if len(wanted) > MAX_EVIDENCE:
            raise SuggestionInvalid(f"Give at most {MAX_EVIDENCE} queries as evidence.")
        queries = self._access_log.for_session(conversation_id) if self._access_log else []
        by_id = {q.id: q for q in queries}
        unknown = [i for i in wanted if i not in by_id]
        if unknown:
            raise SuggestionInvalid(
                f"Not queries of this conversation: {', '.join(unknown[:5])}. Evidence must be "
                "the query_id of a query you ran here with `query`."
            )
        failed = [i for i in wanted if by_id[i].status != "succeeded"]
        if failed:
            raise SuggestionInvalid(
                f"These queries didn't succeed, so they can't be evidence: {', '.join(failed)}."
            )
        return [
            {"query_id": q.id, "tables": list(q.tables), "started_at": q.started_at}
            for q in (by_id[i] for i in wanted)
        ]


def page_path(page: str) -> str:
    """The page a suggestion is for, as a path: "sources/fitbit" or
    "/work/kb/sources/fitbit.md" is sources/fitbit.md. Only pages."""
    raw = str(page).strip().removeprefix("/work/kb/").removeprefix("kb/").strip("/")
    if raw and "/" in raw and not raw.endswith(".md"):
        raw += ".md"
    if kb.place(raw) != "page" or editable_problem(raw) is not None:
        folders = ", ".join(f"{f}/" for f in kb.FOLDERS)
        raise SuggestionInvalid(
            f"{page!r} isn't a page of the knowledge base. Give its path, in one of {folders}: "
            "for example sources/fitbit.md, or tables/IHS_2025.VFITBITDAILYDATA.md for a new page."
        )
    return raw


def _no_participant_data(path: str, fields: dict[str, str]) -> None:
    """Refuse what the knowledge base's scans would flag, and small counts of people."""
    problems = [f"the page's name: {f.message}" for f in kb.name_findings(path)]
    for name, value in fields.items():
        problems += [
            f"{name}, line {f.line}: {f.message}" for f in kb.data_findings(path, value) if f.line
        ]
        if _SMALL_COUNT.search(value):
            problems.append(f"{name}: a count of fewer than 11 people")
    if problems:
        raise SuggestionInvalid(
            "This may hold participant-level data, so it wasn't suggested: "
            + "; ".join(problems[:5])
            + ". A Knowledge update states a general, durable fact: no IDs, per-person dates, "
            "rows or tables of values, or small counts."
        )


def _line(value: str, what: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise SuggestionInvalid(f"The {what} is empty.")
    if len(text) > limit:
        raise SuggestionInvalid(f"Keep the {what} under {limit} characters.")
    _plain(text, what)
    return text


def _block(value: str, what: str, limit: int) -> str:
    text = str(value).replace("\r\n", "\n").strip()
    if not text:
        raise SuggestionInvalid(f"The {what} is empty.")
    if len(text) > limit:
        raise SuggestionInvalid(f"Keep the {what} under {limit} characters.")
    _plain(text, what)
    return text


def _plain(text: str, what: str) -> None:
    if lone_surrogate(text) is not None or _CONTROL.search(text):
        raise SuggestionInvalid(f"The {what} has characters that aren't plain text.")
