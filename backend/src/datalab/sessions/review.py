"""The checks on a completed turn: DataLab's number check, and the rigor review.

The number check (tracing.py) flags numbers in the answer that nothing the
turn produced contains. The rigor review is Codex's review mode with the
lab's checklist (rigor.py). Whether a turn gets a review, and the checkpoint
after one, are the SessionManager's (manager.py: _trace_and_review).
"""

from __future__ import annotations

import asyncio
import logging
import os

from datalab.sessions import rigor
from datalab.sessions.checkpoints import Checkpoints
from datalab.sessions.plans import PlanStore
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.store import ConversationStore, Event
from datalab.sessions.tracing import trace

log = logging.getLogger(__name__)


def final_answer(
    store: ConversationStore, conversation_id: str, since: int, until: int | None = None
) -> Event | None:
    events = store.all_events_after(conversation_id, since)
    if until is not None:
        events = [e for e in events if e.seq < until]
    answers = [e for e in events if e.type == "answer" and e.data.get("text")]
    final = [e for e in answers if e.data.get("phase") == "final_answer"] or answers
    return final[-1] if final else None


async def trace_turn(
    store: ConversationStore,
    checkpoints: Checkpoints,
    conversation_id: str,
    since: int,
    evidence: list[str],
) -> list:
    """Flag numbers in the turn's answer that nothing the turn produced contains."""
    try:
        answer = final_answer(store, conversation_id, since)
        if answer is None:
            return []

        def work() -> list:
            texts = evidence + output_data(checkpoints)
            return trace(str(answer.data["text"]), texts)

        # Can be slow on big outputs: not on the event loop.
        claims = await asyncio.to_thread(work)
    except Exception:
        log.exception("tracing failed in %s", conversation_id)
        return []
    if claims:
        store.append(
            conversation_id,
            "trace",
            {
                "answer": answer.data.get("id"),
                "numbers": len(claims),
                "untraced": [c.text for c in claims if not c.traced],
            },
        )
    return claims


def output_data(checkpoints: Checkpoints, budget: int = 2 * 1024**2) -> list[str]:
    """Data files in /work/outputs as of the latest checkpoint (not prose), up to a budget.

    Reports the agent wrote aren't evidence: they'd "trace" whatever they say.
    """
    latest = checkpoints.latest()
    if latest is None:
        return []
    texts: list[str] = []
    for rel, entry in checkpoints.entries(latest.number).items():
        if budget <= 0:
            break
        if not rel.startswith("outputs/") or not rel.lower().endswith((".csv", ".tsv")):
            continue
        with os.fdopen(checkpoints.open_object(entry), "rb") as source:
            data = source.read(min(budget, 1024**2))
        budget -= len(data)
        texts.append(data.decode("utf-8", "replace"))
    return texts


async def review_turn(
    store: ConversationStore,
    plans: PlanStore | None,
    runtime: SessionRuntime,
    conversation_id: str,
    since: int,
    question: str,
) -> None:
    """The rigor review: Codex's review mode, with DataLab's checklist.

    Review mode starts without the conversation's history, so what it
    needs is given to it: the question, the approved plans, the queries
    run, and the answer.
    """
    # The turn's own work: not an earlier review of it (when run again).
    events = store.all_events_after(conversation_id, since)
    first_review = next((e.seq for e in events if e.type == "review_started"), None)
    if first_review is not None:
        events = [e for e in events if e.seq < first_review]
    answer = final_answer(store, conversation_id, since, until=first_review)
    queries = [
        str((e.data.get("arguments") or {}).get("sql", ""))
        for e in events
        if e.type == "tool_call" and e.data.get("tool") == "query"
    ]
    approved, checks = plans.for_review(conversation_id) if plans else ([], [])
    context = rigor.instructions(
        answer=str(answer.data.get("text", "")) if answer else "",
        question=question,
        plans=approved,
        checks=checks,
        queries=[q for q in queries if q],
    )
    store.append(conversation_id, "review_started", {})
    status = "failed"
    try:
        result = await runtime.review(context)
        status = result.status
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception:
        log.exception("rigor review failed in %s", conversation_id)
    finally:
        # Always, so the chat never waits for a review that ended.
        store.append(conversation_id, "review_finished", {"status": status})
