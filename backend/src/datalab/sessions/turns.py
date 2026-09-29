"""What a conversation's event log says about its turns, and what a turn gets.

Each turn keeps what it was asked with: its `user_message` records Express,
the rigor review switch and the effort, so a switch during a turn applies
from the next message. These read that record (and the rest of the log) to
decide what the next turn gets, whether Continue or a review run again has
anything to pick up, and how to end a turn a restart cut off. The
SessionManager (manager.py) runs the turns; nothing here starts or stops one.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from datalab.sessions import modes
from datalab.sessions.store import Conversation, ConversationStore, Event

# What the chat reads a turn from (frontend transcript.ts), for Continue.
# During a review, the review's own events aren't the turn's.
_REVIEW_TURN_EVENTS = frozenset(
    {
        "answer_started", "answer_delta", "answer", "turn_started", "turn_finished",
        "reasoning_delta", "command_started", "command_output", "command_finished",
        "tool_call", "files_changed", "web_search", "model_status", "error",
    }
)  # fmt: skip
# The agent doing anything means its model requests went through again.
_ACTIVITY = frozenset(
    {"answer_started", "answer_delta", "reasoning_delta", "command_started", "tool_call"}
)
# Shown by the chat as a turn of their own, after the one that failed. (An
# export is shown in the turn it follows, or the one it was made during.)
_OWN_TURN_EVENTS = frozenset({"files_restored", "input_attached", "input_removed"})
# What starts work that ends with turn_done: a question, or a review run again.
_TURN_STARTS = frozenset({"user_message", "review_started"})
# Model trouble that picking up again won't fix.
_NOT_CONTINUABLE = frozenset({"quota", "auth", "request"})


def last_turn_failed(store: ConversationStore, conversation_id: str) -> bool:
    """Whether the chat offers Continue for the last turn: it failed, and not
    because of model trouble that waiting can't fix (a used-up allowance, a
    refused key, a bad request). The same rule as the chat's canContinue."""
    asked = store.last(conversation_id, "user_message")
    if asked is None:
        return False
    status = None
    trouble = None
    reviewing = False
    for event in store.all_events_after(conversation_id, asked.seq):
        if reviewing and event.type in _REVIEW_TURN_EVENTS:
            reviewing = event.type != "turn_finished"
            continue
        if event.type in _ACTIVITY:
            trouble = None
        if event.type in _OWN_TURN_EVENTS:
            return False
        if event.type == "review_started":
            reviewing = True
        elif event.type == "review_finished":
            reviewing = False
        elif event.type == "model_status":
            state = event.data.get("state")
            if state in ("retrying", "recovered", "failed"):
                trouble = event.data.get("kind") if state == "failed" else None
        elif event.type == "turn_finished":
            status = event.data.get("status")
    return status == "failed" and trouble not in _NOT_CONTINUABLE


def unfinished_review(store: ConversationStore, conversation_id: str) -> Event | None:
    """The question of the last turn, if its rigor review started and didn't
    complete (so it can run again); None if there's no such review."""
    asked = store.last(conversation_id, "user_message")
    started = store.last(conversation_id, "review_started")
    finished = store.last(conversation_id, "review_finished")
    # Anchored on the review's start: one cut short by a restart never finished.
    if (
        asked is None
        or started is None
        or started.seq < asked.seq
        or (
            finished is not None
            and finished.seq > started.seq
            and finished.data.get("status") == "completed"
        )
    ):
        return None
    return asked


def work_began(store: ConversationStore, conversation_id: str, asked: Event) -> Event:
    """The question a turn answers: a turn that Continue started picks up the
    one before it (which failed), and so on back."""
    messages = [
        e
        for e in store.events_of_types_after(conversation_id, 0, ("user_message",))
        if e.seq <= asked.seq
    ]
    began = asked
    for earlier in reversed(messages[:-1]):
        if not began.data.get("continues"):
            break
        began = earlier
    return began


def review_wanted(store: ConversationStore, conversation_id: str, started: Event | None) -> bool:
    """Whether the turn that `started` began was asked with the rigor review
    on. A question from before DataLab recorded it: the switch as it is now."""
    if started is not None and "rigor_review" in started.data:
        return bool(started.data["rigor_review"])
    conversation = store.get(conversation_id)
    return conversation is not None and conversation.rigor_review


def default_effort(store: ConversationStore, conversation: Conversation, continues: bool) -> str:
    """The effort for a message sent without one: Continue keeps the effort of
    the turn it picks up; otherwise low with Express on, else the usual one."""
    if continues:
        asked = store.last(conversation.id, "user_message")
        if asked is not None and asked.data.get("effort"):
            return str(asked.data["effort"])
    return "low" if conversation.express else modes.DEFAULT_EFFORT


def express_note(store: ConversationStore, conversation: Conversation) -> str:
    """Tell the agent this message is an Express one, or that Express is off
    again after one (modes.py: EXPRESS). Before the message is logged."""
    if conversation.express:
        return modes.express_note(conversation.mode)
    asked = store.last(conversation.id, "user_message")
    return modes.EXPRESS_OFF if asked is not None and asked.data.get("express") else ""


def workspace_note(store: ConversationStore, conversation_id: str) -> str:
    """Tell the agent what changed in its files since its last turn."""
    asked = store.last(conversation_id, "user_message")
    changes = store.events_of_types_after(
        conversation_id,
        asked.seq if asked else 0,
        ("files_restored", "input_attached", "input_removed", "input_unavailable"),
    )
    notes = [_note(event.type, event.data) for event in changes]
    return "".join(f"[DataLab: {note}]\n" for note in notes if note) + ("\n" if notes else "")


def end_cut_off_turn(store: ConversationStore, conversation_id: str) -> bool:
    """End a turn the log shows still going: what was running (the agent's
    turn, or its review) is marked stopped, and the turn done. Nothing is
    written twice: a turn Stop already ended keeps its own end. Whether
    there was a turn to end."""
    done = store.last(conversation_id, "turn_done")
    events = store.all_events_after(conversation_id, done.seq if done else 0)
    if not any(event.type in _TURN_STARTS for event in events):
        return False
    # Whether the agent's turn, or the review's own turn, is still to finish.
    answering = reviewing = review_running = False
    for event in events:
        if event.type == "user_message":
            answering = True
        elif event.type == "review_started":
            reviewing = review_running = True
        elif event.type == "review_finished":
            reviewing = review_running = False
        elif event.type == "turn_finished":
            if reviewing:
                review_running = False
            else:
                answering = False
    if answering and not reviewing:
        store.append(
            conversation_id, "notice", {"text": "DataLab closed while the agent was working."}
        )
    if review_running if reviewing else answering:
        store.append(conversation_id, "turn_finished", {"status": "interrupted"})
    store.append(conversation_id, "turn_done", {})
    return True


def turn_status(status: str) -> Literal["completed", "interrupted", "failed"]:
    if status in ("completed", "interrupted"):
        return status  # type: ignore[return-value]
    return "failed"


def _note(kind: str, data: dict[str, Any]) -> str:
    if kind == "files_restored":
        label = str(data.get("label", "an earlier checkpoint")).lower()
        if data.get("failed"):
            return (
                f"the user tried to restore the files in /work to how they were {label}, but "
                "it failed partway, so some files may be restored and others not. Check the "
                "files before relying on them."
            )
        return (
            f"the user restored the files in /work to how they were {label}. Changes made "
            "to /work since then were undone. Check the files before relying on anything you "
            "remember about them."
        )
    items = data.get("items") or []
    # Names come from files, so they're quoted: they can't pose as DataLab's words.
    listed = ", ".join(f"{json.dumps(str(i.get('path')))} ({i.get('kind')})" for i in items)
    if kind == "input_attached":
        return f"the user attached {listed}, read-only (names are the files' own)."
    if kind == "input_removed":
        return f"the user removed {listed}; no longer available."
    if kind == "input_unavailable":
        path = str(data.get("path"))
        reason = str(data.get("reason", "")).replace(path, "it")
        return f"{json.dumps(path)} isn't available this time ({reason})."
    return ""
