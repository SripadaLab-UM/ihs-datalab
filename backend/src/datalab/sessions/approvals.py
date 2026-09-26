"""Approvals the person gives in the chat, kept on the host.

The research helper sends a question to the internet only after the person
approves its exact text. That decision, and the text, must live here in
DataLab: the agent's side can't be trusted to report either (code in the
container holds the session token and could answer an approval request
itself). So the tool registers the question here, the chat shows it from
here, the person's answer lands here, and the helper uses only the text
stored here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio

MAX_QUESTION = 1000
# Categories that don't show on screen: controls, format characters (zero
# width, direction marks, tag characters), private use, unassigned, and line
# and paragraph separators.
_INVISIBLE = {"Cc", "Cf", "Co", "Cn", "Zl", "Zp", "Cs"}
# Invisible characters in other categories: Unicode's Default_Ignorable_Code_Point
# (variation selectors, fillers, joiners), plus the blank braille pattern.
_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
    # Blank-looking characters that aren't formally ignorable.
    (0x1680, 0x1680), (0x303F, 0x303F), (0x13441, 0x13442), (0x16FE4, 0x16FE4),
    (0x1D159, 0x1D159),
)  # fmt: skip
# More combining marks than this on one letter is a way to hide data.
_MAX_MARKS = 2


class Unshowable(ValueError):
    """The text has something the person couldn't see, or is too long to review."""


def clean_question(text: str) -> str:
    """The text as the person will see it, or Unshowable.

    Anything invisible is refused rather than dropped, so what's approved is
    exactly what's shown.
    """
    text = visible_text(text)
    if not text:
        raise Unshowable("The question is empty.")
    if len(text) > MAX_QUESTION:
        raise Unshowable(f"The question is longer than {MAX_QUESTION} characters.")
    return text


def visible_text(text: str) -> str:
    """`text` normalized and tidied, or Unshowable if any of it wouldn't show."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n")
    marks = 0
    for char in text:
        if char in "\n\t":
            marks = 0
            continue
        code = ord(char)
        if unicodedata.category(char) in _INVISIBLE or any(a <= code <= b for a, b in _IGNORABLE):
            raise Unshowable(
                f"The text contains a hidden or joining character (U+{code:04X}), "
                "so it can't be shown for review. Emoji and special spacing aren't allowed."
            )
        marks = marks + 1 if unicodedata.category(char).startswith("M") else 0
        if marks > _MAX_MARKS:
            raise Unshowable("The text has too many accent marks stacked on one letter.")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)  # a space at a line's end doesn't show
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


@dataclass
class Pending:
    id: str
    conversation_id: str
    kind: str  # "research_helper" or "analysis_plan"
    question: str = ""  # a research-helper question
    plan: dict[str, str] | None = None  # an analysis plan
    # Set once Codex has forwarded the request: only then is it shown for review.
    shown: bool = False
    # (approved, what was approved: the question's text, or the plan as JSON)
    decision: asyncio.Future[tuple[bool, str]] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )

    def card(self) -> dict[str, Any]:
        """What the chat shows for review: always the host's stored copy."""
        if self.kind == "analysis_plan":
            return {"id": self.id, "kind": self.kind, "plan": self.plan}
        return {"id": self.id, "kind": self.kind, "question": self.question}


class Approvals:
    def __init__(self) -> None:
        self._pending: dict[str, Pending] = {}

    def open(
        self,
        conversation_id: str,
        question: str = "",
        *,
        kind: str = "research_helper",
        plan: dict[str, str] | None = None,
    ) -> Pending:
        pending = Pending(f"ap_{secrets.token_hex(8)}", conversation_id, kind, question, plan)
        self._pending[pending.id] = pending
        return pending

    def get(self, approval_id: str, conversation_id: str) -> Pending | None:
        pending = self._pending.get(approval_id)
        if pending is None or pending.conversation_id != conversation_id:
            return None
        return pending

    def answer(
        self,
        conversation_id: str,
        approval_id: str,
        approved: bool,
        text: str = "",
        plan: dict[str, Any] | None = None,
    ) -> str:
        """Record the person's decision. Returns what was approved ("" if declined)."""
        pending = self.get(approval_id, conversation_id)
        if pending is None or pending.decision.done():
            raise KeyError(approval_id)
        approved_value = ""
        if pending.kind == "analysis_plan":
            from datalab.sessions.plans import PlanInvalid, clean_plan

            if approved:
                # The plan to freeze, as the person left it.
                approved_value = json.dumps(clean_plan(plan if plan is not None else pending.plan))
            elif plan is not None:
                # A "no" always counts. Edits go to the agent as suggestions,
                # if there are any and they're a valid plan.
                with contextlib.suppress(PlanInvalid):
                    edited = clean_plan(plan)
                    if edited != clean_plan(pending.plan):
                        approved_value = json.dumps(edited)
        elif approved:
            approved_value = clean_question(text)
        pending.decision.set_result((approved, approved_value))
        return approved_value

    def withdraw(self, conversation_id: str, approval_id: str | None = None) -> list[str]:
        """Withdraw one pending approval, or all of a conversation's. Returns their ids."""
        withdrawn = []
        for pending in list(self._pending.values()):
            if pending.conversation_id != conversation_id:
                continue
            if approval_id is not None and pending.id != approval_id:
                continue
            if not pending.decision.done():
                pending.decision.set_result((False, ""))
                withdrawn.append(pending.id)
        return withdrawn

    def close(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)

    async def decide(
        self,
        pending: Pending,
        elicit: Callable[[str], Awaitable[Any]],
        emit: Callable[[str, str, dict[str, Any]], object],
        timeout: float,
    ) -> tuple[bool, str]:
        """Wait for the person's decision on `pending`, keeping Codex waiting too.

        `elicit` sends Codex the request (so its tool timeout pauses) and the
        card appears when Codex forwards it. The decision comes only from
        here; whatever Codex replies is ignored.
        """
        waiting = asyncio.ensure_future(elicit(pending.id))

        def withdraw() -> None:
            if self.withdraw(pending.conversation_id, pending.id) and pending.shown:
                emit(pending.conversation_id, "approval_withdrawn", {"id": pending.id})

        try:
            return await asyncio.wait_for(asyncio.shield(pending.decision), timeout)
        except TimeoutError:
            withdraw()
            return False, ""
        except BaseException:
            withdraw()  # the tool call was cancelled (Stop, a timeout, shutdown)
            raise
        finally:
            self.close(pending.id)
            # Codex gets its answer from the runtime; don't wait long for it here.
            # (A cancel of this call still goes through.)
            with anyio.move_on_after(5), contextlib.suppress(Exception):
                await asyncio.shield(waiting)
            waiting.cancel()
