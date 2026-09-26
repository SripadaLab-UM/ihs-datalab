"""Analysis plans: written before the outcome data is touched, approved, then frozen.

In Analysis mode, before looking at outcomes for a new question, the agent
proposes a short plan (the question and estimand, exposure, outcome,
covariates, cohort and exclusions, and the decisions it expects to make).
The person edits and approves it in the chat; it's then frozen with a
timestamp and a hash, and later work is labelled per plan or exploratory.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from datalab.sessions.approvals import Unshowable, visible_text

# The plan's parts, in order, with the label the card shows.
FIELDS: dict[str, str] = {
    "question": "Question",
    "estimand": "Estimand (what exactly is estimated)",
    "exposure": "Exposure or predictor",
    "outcome": "Outcome",
    "covariates": "Covariates and adjustment",
    "cohort": "Cohort, time window, and exclusions",
    "decisions": "Decisions expected along the way",
}
MAX_FIELD = 2000


class PlanInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Outcome:
    """A plan that wasn't approved: what to tell the agent, and any edits."""

    note: str
    suggested: dict[str, str] | None = None


@dataclass(frozen=True)
class Plan:
    id: str
    conversation_id: str
    approved_at: str
    content: dict[str, str]
    sha256: str


def clean_plan(raw: Any) -> dict[str, str]:
    """A plan with exactly the known fields, as trimmed text."""
    if not isinstance(raw, dict):
        raise PlanInvalid("A plan must be a set of named parts.")
    plan: dict[str, str] = {}
    for name in FIELDS:
        value = raw.get(name, "")
        if not isinstance(value, str):
            raise PlanInvalid(f"The plan's {name} must be text.")
        # The person reviews this text, so all of it must be visible.
        try:
            value = visible_text(value)
        except Unshowable as error:
            raise PlanInvalid(f"The plan's {name}: {error}") from error
        if len(value) > MAX_FIELD:
            raise PlanInvalid(f"The plan's {name} is longer than {MAX_FIELD} characters.")
        plan[name] = value
    if not plan["question"]:
        raise PlanInvalid("A plan needs its question.")
    return plan


def plan_hash(content: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def as_text(plan: Plan) -> str:
    lines = [f"Approved analysis plan (frozen {plan.approved_at}, sha256 {plan.sha256[:12]}):"]
    for name, label in FIELDS.items():
        if plan.content.get(name):
            lines.append(f"- {label}: {plan.content[name]}")
    return "\n".join(lines)


class PlanStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def approve(self, conversation_id: str, content: dict[str, str]) -> Plan:
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


class PlanDesk:
    """Takes a proposed plan to the person, and freezes it once they approve it."""

    WAIT_SECONDS = 30 * 60

    def __init__(self, approvals: Any, store: PlanStore, emit: Any) -> None:
        self._approvals = approvals
        self._store = store
        self._emit = emit
        self.turn_running: Any = lambda conversation_id: True

    async def propose(
        self, conversation_id: str, content: dict[str, str], elicit: Any
    ) -> Plan | Outcome:
        """The approved (maybe edited) plan, or why there isn't one."""
        if not self.turn_running(conversation_id):
            return Outcome("Plans can only be proposed during a turn.")
        pending = self._approvals.open(conversation_id, kind="analysis_plan", plan=content)
        approved, value = await self._approvals.decide(
            pending, elicit, self._emit, self.WAIT_SECONDS
        )
        if not approved:
            if value:
                return Outcome(
                    "The person didn't approve this plan, and edited it as below. Take their "
                    "changes into account, and propose again if a plan is still needed.",
                    json.loads(value),
                )
            return Outcome(
                "The person didn't approve this plan (or didn't answer). Ask what they'd "
                "change. If they'd rather explore without a plan, go ahead and label all "
                "of the work exploratory."
            )
        plan = self._store.approve(conversation_id, json.loads(value))
        self._emit(
            conversation_id,
            "plan_approved",
            {
                "approval": pending.id,
                "plan_id": plan.id,
                "approved_at": plan.approved_at,
                "sha256": plan.sha256,
                "plan": plan.content,
            },
        )
        return plan
