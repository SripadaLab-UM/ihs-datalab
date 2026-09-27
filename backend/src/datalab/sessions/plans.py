"""Analysis plans: written before the outcome data is touched, approved, then frozen.

In Analysis mode, before looking at outcomes for a new question, the agent
proposes a short plan: four core sections, plus the sections its type of
analysis needs (plan_schema.py). The person edits and approves it in the
chat; it's then frozen with a timestamp and a hash, and later work is
labelled per plan or exploratory. Plans frozen before plan types existed
(version 1) are kept, and hashed, exactly as they were.

A plan is never changed once frozen. To change one, the agent proposes a
revision that names it (by id and hash); the person sees what changed and
approves it, and it's frozen as a new version. The earlier one stays.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from datalab.sessions.plan_schema import (
    MAX_RECORDED_TABLES,
    TYPES_BY_ID,
    PlanInvalid,
    proposed_after_text,
    review_checks,
    revision_of,
    same_plan,
    sections_of,
    type_change_note,
    type_label,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    """A plan that wasn't approved: what to tell the agent, and any edits."""

    note: str
    suggested: dict[str, Any] | None = None
    change_type: str | None = None  # the type of analysis the person asked for instead


@dataclass(frozen=True)
class Plan:
    id: str
    conversation_id: str
    approved_at: str
    content: dict[str, Any]
    sha256: str


def plan_hash(content: dict[str, Any]) -> str:
    # Unchanged since version 1, so every frozen plan's hash still checks out.
    # A version-2 plan's content holds its schema version, type, labels,
    # section order, and the plan it revises, so the hash covers those too.
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def as_text(plan: Plan, superseded_by: Plan | None = None) -> str:
    lines = [
        f"Approved analysis plan {plan.id} (frozen {plan.approved_at}, sha256 {plan.sha256[:12]})"
        + (f", superseded by {superseded_by.id}" if superseded_by else "")
        + ":"
    ]
    if revises := revision_of(plan.content):
        lines.append(
            f"- Revises: {revises['plan_id']} (sha256 {revises['sha256'][:12]}). "
            f"Why: {_indented(str(plan.content.get('revision_reason', '')))}"
        )
    lines.append(f"- Proposed after: {proposed_after_text(plan.content)}")
    if kind := type_label(plan.content):
        rationale = plan.content.get("rationale")
        lines.append(f"- Type: {kind}" + (f" ({_indented(rationale)})" if rationale else ""))
    lines.extend(f"- {label}: {_indented(text)}" for label, text in sections_of(plan.content))
    return "\n".join(lines)


def _indented(text: str) -> str:
    # Every line after a section's first is indented, so no text in a section
    # can pass for the start of another ("- Method and adjustment: ..."). A
    # blank line can't, so it's left alone: the text at most doubles.
    first, *rest = text.split("\n")
    return "\n".join([first, *(f"  {line}" if line else "" for line in rest)])


class PlanStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def approve(self, conversation_id: str, content: dict[str, Any]) -> Plan:
        plan = Plan(
            id=f"pl_{secrets.token_hex(6)}",
            conversation_id=conversation_id,
            approved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            content=content,
            sha256=plan_hash(content),
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?)",
                (plan.id, conversation_id, plan.approved_at, json.dumps(content), plan.sha256),
            )
        return plan

    def list(self, conversation_id: str) -> list[Plan]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, conversation_id, approved_at, content_json, sha256 FROM plans "
                "WHERE conversation_id = ? ORDER BY rowid",
                (conversation_id,),
            ).fetchall()
        return [Plan(r[0], r[1], r[2], json.loads(r[3]), r[4]) for r in rows]

    def superseded(self, conversation_id: str) -> dict[str, Plan]:
        """Each plan that has been revised, with the revision that replaced it."""
        replaced: dict[str, Plan] = {}
        for plan in self.list(conversation_id):
            if revises := revision_of(plan.content):
                replaced[revises["plan_id"]] = plan
        return replaced

    def for_review(self, conversation_id: str) -> tuple[list[str], list[str]]:
        """The approved plans as the rigor review is shown them, and the extra
        checks for the latest one's type and add-on sections.

        Replaced versions come first and current plans last, so when the
        review is shown only the last few, no current plan is left out for
        an old version.
        """
        plans = self.list(conversation_id)
        replaced = self.superseded(conversation_id)
        checks = review_checks(plans[-1].content) if plans else []
        ordered = [p for p in plans if p.id in replaced] + [
            p for p in plans if p.id not in replaced
        ]
        return [as_text(p, replaced.get(p.id)) for p in ordered], checks

    def current(self, conversation_id: str) -> list[Plan]:
        """The approved plans no revision has replaced."""
        replaced = self.superseded(conversation_id)
        return [p for p in self.list(conversation_id) if p.id not in replaced]


class PlanDesk:
    """Takes a proposed plan to the person, and freezes it once they approve it."""

    WAIT_SECONDS = 30 * 60

    def __init__(self, approvals: Any, store: PlanStore, emit: Any) -> None:
        self._approvals = approvals
        self._store = store
        self._emit = emit
        self.turn_running: Any = lambda conversation_id: True
        # Which turn is running (any value that's the same only within one turn).
        self.current_turn: Any = lambda conversation_id: None
        # The conversation's queries so far (access_log.AccessLog.for_session).
        self.queries_so_far: Any = lambda conversation_id: []
        # The queries a plan's record counted, per conversation, until it's proposed.
        self._recorded: dict[str, set[str]] = {}
        # The version of a plan the person last sent back, per conversation,
        # and the turn it was sent back in, so the agent's next proposal in
        # that turn can be shown against it. For display only.
        self._returned: dict[str, tuple[Any, dict[str, Any], str]] = {}

    def revision_link(self, conversation_id: str, plan_id: str) -> dict[str, str]:
        """How a revision names the approved plan it revises, or PlanInvalid."""
        current = self._store.current(conversation_id)
        for plan in current:
            if plan.id == plan_id:
                return {"plan_id": plan.id, "sha256": plan.sha256}
        replaced = self._store.superseded(conversation_id).get(plan_id)
        if replaced is not None:
            raise PlanInvalid(f"Plan {plan_id} was already revised by {replaced.id}: revise that.")
        if not current:
            raise PlanInvalid("There's no approved plan to revise: propose a new plan instead.")
        listed = "; ".join(f"{p.id} ({_summary(p)})" for p in current)
        raise PlanInvalid(f"There's no approved plan {plan_id!r} to revise. Current: {listed}.")

    def planning_record(self, conversation_id: str) -> dict[str, Any]:
        """What had run in the conversation so far, for a plan proposed now:
        the queries that returned data or are still running (their results may
        arrive before the plan is approved), and the tables they read.
        Recorded by DataLab, not said by the agent, so it holds whatever the
        plan says. The plan is only frozen if no more ran while it waited."""
        ran = self._ran(conversation_id)
        self._recorded[conversation_id] = {q.id for q in ran}
        tables = sorted({t for q in ran for t in q.tables})
        return {
            "queries": len(ran),
            "tables": tables[:MAX_RECORDED_TABLES],
            "more_tables": max(0, len(tables) - MAX_RECORDED_TABLES),
        }

    def _ran(self, conversation_id: str) -> list[Any]:
        return [
            q for q in self.queries_so_far(conversation_id) if q.status in ("succeeded", "running")
        ]

    def check_revision(self, conversation_id: str, content: dict[str, Any]) -> None:
        """PlanInvalid if a revision changes nothing in the plan it revises."""
        if revises := revision_of(content):
            earlier = self._plan(conversation_id, revises["plan_id"])
            if earlier is not None and same_plan(earlier.content, content):
                raise PlanInvalid(
                    f"This revision doesn't change anything in plan {earlier.id}. Change what "
                    "needs changing, or keep following the approved plan."
                )

    async def propose(
        self, conversation_id: str, content: dict[str, Any], elicit: Any
    ) -> Plan | Outcome:
        """The approved (maybe edited) plan, or why there isn't one."""
        counted = self._recorded.pop(conversation_id, None)
        if not self.turn_running(conversation_id):
            return Outcome("Plans can only be proposed during a turn.")
        pending = self._approvals.open(
            conversation_id,
            kind="analysis_plan",
            plan=content,
            compare_to=self._compare_to(conversation_id, content),
        )
        try:
            approved, value = await self._approvals.decide(
                pending, elicit, self._emit, self.WAIT_SECONDS
            )
        except asyncio.CancelledError:
            # Stopped after the person approved, while the tool was finishing:
            # what they approved is still frozen, so the card isn't left waiting.
            if pending.decision.done() and not pending.decision.cancelled():
                approved, value = pending.decision.result()
                if approved:
                    try:
                        self._freeze(conversation_id, pending.id, json.loads(value), counted)
                    except Exception:
                        # The Stop still goes through; this is only logged.
                        log.exception("couldn't freeze a plan approved as the turn stopped")
            raise
        if not approved:
            return self._not_approved(conversation_id, content, value)
        return self._freeze(conversation_id, pending.id, json.loads(value), counted)

    def _freeze(
        self,
        conversation_id: str,
        approval_id: str,
        content: dict[str, Any],
        counted: set[str] | None = None,
    ) -> Plan | Outcome:
        if counted is not None and (since := {q.id for q in self._ran(conversation_id)} - counted):
            # Results that arrived while the plan waited would make its record untrue.
            self._emit(
                conversation_id,
                "plan_not_frozen",
                {
                    "approval": approval_id,
                    "reason": f"{len(since)} more {'query' if len(since) == 1 else 'queries'} "
                    "ran while the plan waited, so its record of what had already run was out "
                    "of date. The agent will propose it again.",
                },
            )
            return self._approved_not_frozen(
                conversation_id,
                content,
                "This plan wasn't frozen: queries ran while it waited for approval, so its "
                "record of what had already run is out of date. The person approved it as below "
                "(persons_edits): propose that again, and don't run queries while a plan waits.",
            )
        revises = revision_of(content)
        if revises and revises["plan_id"] in self._store.superseded(conversation_id):
            # Another revision of the same plan was approved while this one waited.
            # The card says so, rather than waiting to be frozen.
            self._emit(
                conversation_id,
                "plan_not_frozen",
                {
                    "approval": approval_id,
                    "reason": "Another revision of the same plan was "
                    "approved first, so this one wasn't frozen.",
                },
            )
            return self._approved_not_frozen(
                conversation_id,
                content,
                f"This revision wasn't frozen: plan {revises['plan_id']} was already revised "
                "while it waited. The person approved it as below (persons_edits): revise the "
                "latest plan instead, carrying over what still applies.",
            )
        plan = self._store.approve(conversation_id, content)
        self._emit(
            conversation_id,
            "plan_approved",
            {
                "approval": approval_id,
                "plan_id": plan.id,
                "approved_at": plan.approved_at,
                "sha256": plan.sha256,
                "plan": plan.content,
            },
        )
        return plan

    def _approved_not_frozen(
        self, conversation_id: str, approved: dict[str, Any], note: str
    ) -> Outcome:
        """An approved plan that couldn't be frozen: what the person approved goes
        back to the agent, and the next card is shown against it."""
        turn = self.current_turn(conversation_id)
        self._returned[conversation_id] = (turn, approved, "The version you approved")
        return Outcome(note, approved)

    def _plan(self, conversation_id: str, plan_id: str) -> Plan | None:
        return next((p for p in self._store.list(conversation_id) if p.id == plan_id), None)

    def _compare_to(self, conversation_id: str, content: dict[str, Any]) -> dict[str, Any] | None:
        """What a proposal is shown against: the plan it revises, or what the
        person sent back (or approved, when it couldn't be frozen) earlier in the
        same turn (a new question is a new turn)."""
        turn, returned, label = self._returned.pop(conversation_id, (None, None, ""))
        if revises := revision_of(content):
            earlier = self._plan(conversation_id, revises["plan_id"])
            if earlier is not None:
                return {
                    "label": "The approved plan it revises",
                    "plan": earlier.content,
                    "plan_id": earlier.id,
                    "approved_at": earlier.approved_at,
                    "sha256": earlier.sha256,
                }
        if returned is not None and turn is not None and turn is self.current_turn(conversation_id):
            return {"label": label, "plan": returned}
        return None

    def _not_approved(self, conversation_id: str, proposed: dict[str, Any], value: str) -> Outcome:
        if not value:
            return Outcome(
                "The person didn't approve this plan (or didn't answer). Ask what they'd "
                "change. If they'd rather explore without a plan, go ahead and label all "
                "of the work exploratory."
            )
        answer = json.loads(value)
        edits, change_type = answer.get("edits"), answer.get("change_type")
        turn = self.current_turn(conversation_id)
        self._returned[conversation_id] = (turn, edits or proposed, "The version you sent back")
        if change_type:
            return Outcome(type_change_note(edits or proposed, change_type), edits, change_type)
        return Outcome(
            "The person didn't approve this plan, and edited it as below. Take their "
            "changes into account, and propose again if a plan is still needed.",
            edits,
        )


def _summary(plan: Plan) -> str:
    """A plan in a few words: its type and the start of its question."""
    question = next((text for _, text in sections_of(plan.content)), "")
    words = question if len(question) <= 60 else question[:57] + "..."
    kind = TYPES_BY_ID.get(str(plan.content.get("analysis_type")))
    return f"{kind.label}: {words}" if kind else words
