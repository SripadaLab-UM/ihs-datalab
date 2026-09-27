"""Analysis plans: written before the outcome data is touched, approved, then frozen.

In Analysis mode, before looking at outcomes for a new question, the agent
proposes a short plan: four core sections, plus the sections its type of
analysis needs (plan_schema.py). The person edits and approves it in the
chat; it's then frozen with a timestamp and a hash, and later work is
labelled per plan or exploratory. Plans frozen before plan types existed
(version 1) are kept, and hashed, exactly as they were.
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

from datalab.sessions.plan_schema import sections_of, type_label


@dataclass(frozen=True)
class Outcome:
    """A plan that wasn't approved: what to tell the agent, and any edits."""

    note: str
    suggested: dict[str, Any] | None = None


@dataclass(frozen=True)
class Plan:
    id: str
    conversation_id: str
    approved_at: str
    content: dict[str, Any]
    sha256: str


def plan_hash(content: dict[str, Any]) -> str:
    # Unchanged since version 1, so every frozen plan's hash still checks out.
    # A version-2 plan's content holds its schema version, type, labels, and
    # section order, so the hash covers all of those.
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def as_text(plan: Plan) -> str:
    lines = [f"Approved analysis plan (frozen {plan.approved_at}, sha256 {plan.sha256[:12]}):"]
    if kind := type_label(plan.content):
        rationale = plan.content.get("rationale")
        lines.append(f"- Type: {kind}" + (f" ({rationale})" if rationale else ""))
    lines.extend(f"- {label}: {text}" for label, text in sections_of(plan.content))
    return "\n".join(lines)


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


class PlanDesk:
    """Takes a proposed plan to the person, and freezes it once they approve it."""

    WAIT_SECONDS = 30 * 60

    def __init__(self, approvals: Any, store: PlanStore, emit: Any) -> None:
        self._approvals = approvals
        self._store = store
        self._emit = emit
        self.turn_running: Any = lambda conversation_id: True

    async def propose(
        self, conversation_id: str, content: dict[str, Any], elicit: Any
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
