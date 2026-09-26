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
import re
import secrets
import unicodedata
from dataclasses import dataclass, field

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
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n")
    marks = 0
    for char in text:
        if char in "\n\t":
            marks = 0
            continue
        code = ord(char)
        if unicodedata.category(char) in _INVISIBLE or any(a <= code <= b for a, b in _IGNORABLE):
            raise Unshowable(
                f"The question contains a hidden or joining character (U+{code:04X}), "
                "so it can't be shown for review. Emoji and special spacing aren't allowed."
            )
        marks = marks + 1 if unicodedata.category(char).startswith("M") else 0
        if marks > _MAX_MARKS:
            raise Unshowable("The question has too many accent marks stacked on one letter.")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)  # a space at a line's end doesn't show
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if not text:
        raise Unshowable("The question is empty.")
    if len(text) > MAX_QUESTION:
        raise Unshowable(f"The question is longer than {MAX_QUESTION} characters.")
    return text


@dataclass
class Pending:
    id: str
    conversation_id: str
    question: str
    # Set once Codex has forwarded the request: only then is it shown for review.
    shown: bool = False
    decision: asyncio.Future[tuple[bool, str]] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class Approvals:
    def __init__(self) -> None:
        self._pending: dict[str, Pending] = {}

    def open(self, conversation_id: str, question: str) -> Pending:
        pending = Pending(f"ap_{secrets.token_hex(8)}", conversation_id, question)
        self._pending[pending.id] = pending
        return pending

    def get(self, approval_id: str, conversation_id: str) -> Pending | None:
        pending = self._pending.get(approval_id)
        if pending is None or pending.conversation_id != conversation_id:
            return None
        return pending

    def answer(self, conversation_id: str, approval_id: str, approved: bool, text: str) -> str:
        """Record the person's decision. Returns the text that will be sent."""
        pending = self.get(approval_id, conversation_id)
        if pending is None or pending.decision.done():
            raise KeyError(approval_id)
        sent = clean_question(text) if approved else ""
        pending.decision.set_result((approved, sent))
        return sent

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
