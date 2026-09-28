"""SQL the agent proposes for the SQL Playground's editor (the `propose_sql` tool).

In the Playground's chat the person describes the data they want, and the
agent answers with one proposed query: its SQL, the values of its bind
variables, a title, its assumptions, and the tables and knowledge-base pages
it relied on. DataLab checks the SQL with the same check the Playground's
editor uses, and records the proposal as a `sql_proposed` event of the
conversation, in the turn it was made in. The event log is the record: the
latest proposal of a turn is the one the editor is offered.

A proposal is never run. It's a draft: the person reviews it in the editor
and runs it themselves, when the SQL check runs again as for any query.
Only this tool's proposal reaches the editor; SQL in the agent's message and
the queries it ran while exploring never do.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from datalab.data.access_log import AccessLog
from datalab.data.catalog import Catalog
from datalab.data.sqlcheck import SqlRejected, check_sql
from datalab.textcheck import lone_surrogate

EVENT = "sql_proposed"
# The SQL Playground chat's mode (sessions/modes.py).
SQL_MODE = "sql"
BindType = Literal["text", "number", "date"]
BIND_TYPES: tuple[BindType, ...] = ("text", "number", "date")

MAX_TITLE = 120
MAX_ITEMS = 12
MAX_ITEM = 400
MAX_TABLES = 40
MAX_BINDS = 50
MAX_BIND_VALUE = 1_000
# Proposals one turn may make: a follow-up is a new turn.
MAX_PER_TURN = 20
# What proposals() reads of a conversation's record (never the answer's deltas).
_READ = (
    "user_message",
    EVENT,
    "tool_call",
    "command_started",
    "turn_finished",
    "turn_done",
    "review_started",
    "review_finished",
)
# The question as shown beside the editor; the whole message is in the chat.
MAX_REQUEST = 4_000

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Files a command read in the knowledge base, as the chat shows them.
_KB_PATH = re.compile(r"/work/kb/[\w./-]*[\w-]")
# How the SQL tab's chat sends the editor along with a message (DockedChat withContext).
_CONTEXT = "\n\nThe query in the SQL editor:\n"


class DraftInvalid(ValueError):
    """The proposal can't go to the editor. The message is written for the agent."""


@dataclass(frozen=True)
class ProposedBind:
    name: str
    value: str | int | float | None
    type: BindType = "text"


@dataclass
class TurnProposal:
    """A turn's latest proposal, with what the turn's own record says about it."""

    proposal: dict[str, Any]
    seq: int
    created_at: str
    turn: int
    request: str
    # The question's event, which the chat marks its turn with.
    request_seq: int
    status: str = "running"  # completed, interrupted, failed: turn_finished's
    done: bool = False
    tables_described: list[str] = field(default_factory=list)
    kb_read: list[str] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)


class SqlDrafts:
    """Checks and records the agent's proposals; lists them for the editor."""

    def __init__(
        self,
        store: Any,  # sessions.store.ConversationStore
        catalog: Catalog,
        allowed_schemas: Callable[[], frozenset[str]],
        access_log: AccessLog | None = None,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._allowed_schemas = allowed_schemas
        self._access_log = access_log
        self.turn_running: Callable[[str], bool] = lambda conversation_id: True

    def propose(
        self,
        conversation_id: str,
        *,
        sql: str,
        title: str,
        binds: Iterable[ProposedBind] = (),
        assumptions: Iterable[str] = (),
        tables: Iterable[str] = (),
        knowledge: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Check a proposal and record it in the running turn, or DraftInvalid. Nothing runs."""
        if not self.turn_running(conversation_id):
            raise DraftInvalid("A query can only be proposed during a turn.")
        asked = self._store.last(conversation_id, "user_message")
        review = self._store.last(conversation_id, "review_started")
        if asked is None:
            raise DraftInvalid("A query can only be proposed in answer to a question.")
        if review is not None and review.seq > asked.seq:
            # The rigor review reads the turn's work; it doesn't add to it.
            raise DraftInvalid("A query can't be proposed during the review of a turn.")
        made = self._store.events_of_types_after(conversation_id, asked.seq, (EVENT,))
        if len(made) >= MAX_PER_TURN:
            raise DraftInvalid(
                f"This turn has already proposed {MAX_PER_TURN} queries. Stop, and say what's "
                "left to decide."
            )
        if (not_text := lone_surrogate(sql)) is not None:
            raise DraftInvalid(f"The SQL check refused this query: {not_text.message}")
        try:
            checked = check_sql(
                sql, allowed_schemas=self._allowed_schemas(), columns=self._catalog.column_index()
            )
        except SqlRejected as rejection:
            raise DraftInvalid(
                f"The SQL check refused this query, so it wasn't proposed: {rejection} "
                "Fix it and call propose_sql again."
            ) from rejection
        bound = _binds(list(binds), _in_order(checked.sql, checked.binds))
        proposal = {
            "proposal_id": f"sp_{secrets.token_hex(6)}",
            "turn": self._store.count(conversation_id, "user_message"),
            "title": _line(title, "title", MAX_TITLE, required=True),
            "sql": checked.sql,
            "binds": bound,
            "assumptions": _items(assumptions, "assumptions", MAX_ITEMS, MAX_ITEM),
            "tables_named": _items(tables, "tables", MAX_TABLES, 128),
            "knowledge": _items(knowledge, "knowledge", MAX_ITEMS, MAX_ITEM),
            # What the SQL check found, not what the agent said.
            "tables": [str(t) for t in checked.tables],
            "warnings": list(checked.warnings),
        }
        self._store.append(conversation_id, EVENT, proposal)
        return proposal

    def is_sql_conversation(self, conversation_id: str) -> bool:
        """Whether this is a SQL Playground chat (the only kind that proposes queries)."""
        conversation = self._store.get(conversation_id)
        return conversation is not None and conversation.mode == SQL_MODE

    def proposals(self, conversation_id: str) -> list[TurnProposal]:
        """Each turn's latest proposal, oldest turn first, from the conversation's record."""
        found: dict[int, TurnProposal] = {}
        turn = 0
        request = ""
        request_seq = 0
        described: dict[int, list[str]] = {}
        kb: dict[int, list[str]] = {}
        status: dict[int, str] = {}
        done: set[int] = set()
        starts: list[str] = []
        # A rigor review's events (its own turn_finished too) aren't the turn's.
        reviewing = False
        for event in self._store.events_of_types_after(conversation_id, 0, _READ):
            if event.type == "review_started":
                reviewing = True
                continue
            if event.type == "review_finished":
                reviewing = False
                continue
            if event.type == "user_message":
                reviewing = False
                turn += 1
                starts.append(event.created_at)
                text = str(event.data.get("text", ""))
                # Continue picks up the question before; it isn't a new one.
                if not event.data.get("continues"):
                    request = _request(text)
                    request_seq = event.seq
            elif turn == 0 or reviewing:
                continue
            elif event.type == EVENT:
                found[turn] = TurnProposal(
                    proposal=event.data,
                    seq=event.seq,
                    created_at=event.created_at,
                    turn=turn,
                    request=request,
                    request_seq=request_seq,
                )
            elif event.type == "tool_call" and event.data.get("server") == "ihs-data":
                arguments = event.data.get("arguments") or {}
                if event.data.get("tool") == "describe_table" and isinstance(arguments, dict):
                    _add(described.setdefault(turn, []), arguments.get("table"))
            elif event.type == "command_started":
                for path in _KB_PATH.findall(str(event.data.get("command", ""))):
                    _add(kb.setdefault(turn, []), path)
            elif event.type == "turn_finished":
                status[turn] = str(event.data.get("status") or "failed")
            elif event.type == "turn_done":
                done.add(turn)
        queries = self._access_log.for_session(conversation_id) if self._access_log else []
        for number, proposal in found.items():
            proposal.status = status.get(number, "running")
            proposal.done = number in done
            proposal.tables_described = described.get(number, [])[:MAX_TABLES]
            proposal.kb_read = kb.get(number, [])[:MAX_TABLES]
            began = starts[number - 1]
            ended = starts[number] if number < len(starts) else None
            proposal.queries = [
                {
                    "id": q.id,
                    "status": q.status,
                    "tables": q.tables,
                    "row_count": q.row_count,
                    "started_at": q.started_at,
                }
                for q in queries
                if q.started_at >= began and (ended is None or q.started_at < ended)
            ]
        return [found[n] for n in sorted(found)]


def _in_order(sql: str, names: tuple[str, ...]) -> tuple[str, ...]:
    """The bind variables in the order the SQL first uses them (the check sorts them)."""

    def first(name: str) -> int:
        found = re.search(rf":{re.escape(name)}\b", sql, re.IGNORECASE)
        return found.start() if found else len(sql)

    return tuple(sorted(names, key=first))


def _binds(given: list[ProposedBind], needed: tuple[str, ...]) -> list[dict[str, Any]]:
    """The bind values, one for each bind variable the SQL uses, in its order."""
    if len(given) > MAX_BINDS:
        raise DraftInvalid(f"At most {MAX_BINDS} bind variables.")
    by_name: dict[str, ProposedBind] = {}
    for bind in given:
        name = bind.name.strip().lstrip(":")
        if name.upper() in {n.upper() for n in by_name}:
            raise DraftInvalid(f"The bind variable :{name} is given twice.")
        by_name[name] = bind
    wanted = {n.upper(): n for n in needed}
    extra = [f":{n}" for n in by_name if n.upper() not in wanted]
    if extra:
        raise DraftInvalid(
            f"The SQL doesn't use {', '.join(extra)}. Give a value for each bind variable "
            "the SQL uses, and no others."
        )
    given_upper = {n.upper(): b for n, b in by_name.items()}
    missing = [f":{n}" for n in needed if n.upper() not in given_upper]
    if missing:
        raise DraftInvalid(
            f"Give a value for {', '.join(missing)}: every bind variable the SQL uses needs "
            "one (name, value, type), so the person can review and change it."
        )
    return [_bind(name, given_upper[name.upper()]) for name in needed]


def _bind(name: str, bind: ProposedBind) -> dict[str, Any]:
    if bind.type not in BIND_TYPES:
        raise DraftInvalid(f":{name}: the type must be one of {', '.join(BIND_TYPES)}.")
    value = bind.value
    if isinstance(value, bool):
        raise DraftInvalid(f":{name}: give a number or text, not true or false.")
    if isinstance(value, str):
        value = _value(value, f":{name}")
    if bind.type == "date":
        if not isinstance(value, str) or not _is_date(value):
            raise DraftInvalid(
                f":{name} is a date: give it as YYYY-MM-DD text, and write "
                f"TO_DATE(:{name}, 'YYYY-MM-DD') in the SQL."
            )
    elif bind.type == "number":
        if isinstance(value, str):
            try:
                value = float(value) if "." in value or "e" in value.lower() else int(value)
            except ValueError:
                raise DraftInvalid(f":{name} is a number, but {value!r} isn't one.") from None
        if value is not None and not isinstance(value, int | float):
            raise DraftInvalid(f":{name} is a number, but its value isn't one.")
        if isinstance(value, float) and not math.isfinite(value):
            raise DraftInvalid(f":{name} is a number, but it isn't a finite one.")
    elif value is not None and not isinstance(value, str):
        value = str(value)
    return {"name": name, "value": value, "type": bind.type}


def _is_date(value: str) -> bool:
    if not _DATE.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _value(text: str, what: str) -> str:
    """A text bind's value: control characters out and the ends trimmed, the rest as given."""
    if (not_text := lone_surrogate(text)) is not None:
        raise DraftInvalid(f"{what}: {not_text.message}")
    text = _CONTROL.sub("", text).strip()
    if len(text) > MAX_BIND_VALUE:
        raise DraftInvalid(f"{what} is longer than {MAX_BIND_VALUE} characters.")
    return text


def _line(value: Any, what: str, limit: int, required: bool = False) -> str:
    text = value if isinstance(value, str) else ""
    if (not_text := lone_surrogate(text)) is not None:
        raise DraftInvalid(f"{what}: {not_text.message}")
    text = " ".join(_CONTROL.sub(" ", text).split())
    if required and not text:
        raise DraftInvalid(f"Give a {what}: one line saying what the query returns.")
    if len(text) > limit:
        raise DraftInvalid(f"{what} is longer than {limit} characters.")
    return text


def _items(values: Iterable[str], what: str, count: int, limit: int) -> list[str]:
    items = [_line(v, what, limit) for v in values]
    items = [i for i in items if i]
    if len(items) > count:
        raise DraftInvalid(f"At most {count} {what}: keep the most important.")
    return list(dict.fromkeys(items))


def _request(text: str) -> str:
    """The person's question, without the editor's SQL the chat may have sent with it."""
    cut = text.find(_CONTEXT)
    question = text[:cut] if cut >= 0 else text
    return question.strip()[:MAX_REQUEST]


def _add(values: list[str], value: Any) -> None:
    if isinstance(value, str) and value and value not in values:
        values.append(value[:200])
