"""Conversation titles, written by the model from the first question.

Nobody types a title: a new conversation is "New conversation" until its first
question, then gets a short title the way Codex names a thread. The request
goes to the same approved U-M GPT model the conversation uses, which receives
that question anyway, and passes the relay's own policy check. If there's no
key or no answer, the title is the question's first words instead.

A title shows in the sidebar and names export folders, so it's kept free of
study identifiers: the model is told to leave them out, and whatever comes
back (or the question's words, as a fallback) is scrubbed of anything shaped
like a participant ID, an email address, or a date before it's stored.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable, Coroutine
from typing import Any

import httpx

from datalab.credentials import MissingCredential
from datalab.relay.policy import Refused, check_responses_request
from datalab.sessions.tokens import SessionKind

log = logging.getLogger(__name__)

DEFAULT_TITLE = "New conversation"
MAX_LENGTH = 60
# Straight and curly quotes, backticks, and Markdown emphasis.
_QUOTES = " \t\"'`*#\u201c\u201d\u2018\u2019"

_INSTRUCTIONS = (
    "Write a title for a research conversation that starts with the question "
    "below. At most six words, specific to the question (name the measure, "
    "cohort, or method), in sentence case. No quotes, no ending punctuation, "
    "no filler such as 'Question about' or 'Analysis of'. Never include "
    "participant IDs or record numbers, people's names, email addresses, or "
    "specific dates (a cohort year is fine). Reply with the title only."
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
)
_WEEKDAY = (
    r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b\.?"
)
_MM = r"(?:0?[1-9]|1[0-2])"
_DD = r"(?:0?[1-9]|[12]\d|3[01])"
_DAY = rf"{_DD}(?:st|nd|rd|th)?\b"
_DATES = re.compile(
    "|".join(
        [
            # 2025-03-14, 2025/3/14, 2025.03.14, with a time or not
            r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?\b",
            # 2026-09, but not 2024-2025 or 2024-25
            r"\b\d{4}[-/.](?:0[1-9]|1[0-2])\b(?![-/.]?\d)",
            # 03/14/2025, 3-14-25
            rf"\b{_MM}[-/.]{_DD}[-/.](?:\d{{4}}|\d{{2}})\b",
            # 9/26, but not 30/60/90 or 24/7
            rf"(?<![\d/]){_MM}/{_DD}(?![\d/])",
            # Monday 26th, Mon, 26 September 2026
            rf"\b{_WEEKDAY},?\s+(?:{_DAY}|{_MONTH}\s+{_DAY})(?:\s+{_MONTH})?(?:,?\s+\d{{4}}\b)?",
            # March 14, Sep 26th, 2025 (March 2026 stays)
            rf"\b{_MONTH}\s+{_DAY}(?:,?\s+\d{{4}}\b)?",
            # 14 March, 26 Sep 2026
            rf"\b{_DAY}\s+{_MONTH}(?:,?\s+\d{{4}}\b)?",
        ]
    ),
    re.IGNORECASE,
)
# Digits in groups, as phone numbers and formatted record numbers are
# written: 734-555-1234, (734) 555-1234, +1 734 555 1234, 12-34-567.
_DIGIT_GROUPS = re.compile(
    r"(?<![\w+.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)|\d{1,6})(?:[ ./-]\d{1,6})+(?![\w])"
)
# A number after a word that labels it as someone's: "participant 0001",
# "ID 1234", "#1234". A bare "ID" or "MRN" reads oddly, so it goes too.
_LABELS = ("participant", "subject", "patient", "respondent", "record", "id", "mrn", "pt")
_BARE_LABELS = {"id", "mrn", "pt"}
_LABELLED = re.compile(
    rf"\b({'|'.join(_LABELS)})\b\.?(?:\s*(?:#|no\.?|number|:))?\s*\d+(?:[./-]\d+)*\b", re.IGNORECASE
)
_HASH_NUMBER = re.compile(r"(?<!\w)#\s*\d{2,}\b")
# A study code followed by a number: "SYN24 0001" (not "Q3 2025").
_CODED = re.compile(r"\b([^\W\d_]+\d+)\s+(?!(?:19|20)\d\d\b)\d{3,}\b")
# A word, with its possessive: "P-0001's" goes as a whole.
_WORD = re.compile(r"\w+(?:[-_]\w+)*(?:['\u2019]s\b)?")
# Punctuation a removed identifier can leave stranded.
_EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")
_STRANDED = re.compile(r"\s+([,;:.!?)\]])|([(\[])\s+|([,;:])(?:\s*[,;:])+")
_LOOSE_ENDS = " ,;:-_/+\u2013\u2014"
# Words left hanging ("Sleep for P-0001 on 2025-03-14", "P-0001 on sleep").
_CONNECTORS = "on|for|of|in|at|from|to|and|or|with|by|about|since|until"
_DANGLING = re.compile(rf"(?:(?:^|[\s,;:]+)(?:{_CONNECTORS}))+$", re.IGNORECASE)
_LEADING = re.compile(rf"^(?:(?:{_CONNECTORS})\b[\s,;:]*)+", re.IGNORECASE)
_NOT_MEANINGFUL = {*_CONNECTORS.split("|"), *_LABELS}


class TitleWriter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: Callable[[], str],
        allowed_models: tuple[str, ...] | None,
    ) -> None:
        self._client = client
        self._url = base_url.rstrip("/") + "/responses"
        self._api_key = api_key
        self._allowed_models = allowed_models
        self._naming: dict[str, asyncio.Task[None]] = {}

    def start(self, conversation_id: str, work: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Run `work` in the background, once per conversation at a time."""
        if conversation_id in self._naming:
            return
        task = asyncio.create_task(work())
        self._naming[conversation_id] = task
        task.add_done_callback(lambda _: self._naming.pop(conversation_id, None))

    async def aclose(self) -> None:
        """Stop titles still being written: DataLab is shutting down."""
        tasks = list(self._naming.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def title(self, question: str, *, model: str, kind: SessionKind) -> str:
        """A title for a conversation that starts with this question."""
        try:
            written = await self._ask(question, model, kind)
        except (httpx.HTTPError, ValueError, Refused, MissingCredential) as error:
            log.info("no model title (%s); using the question", type(error).__name__)
            written = None
        except Exception:  # an odd reply shape: never leave the conversation unnamed
            log.exception("writing a title failed; using the question")
            written = None
        return written or fallback_title(question)

    async def _ask(self, question: str, model: str, kind: SessionKind) -> str | None:
        key = await asyncio.to_thread(self._api_key)
        body: dict[str, Any] = {
            "model": model,
            "instructions": _INSTRUCTIONS,
            "input": question[:4000],
            "store": False,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 600,  # reasoning models count their thinking here
        }
        check_responses_request(body, kind, self._allowed_models)
        response = await self._post(body, key)
        if response.status_code == 400:
            # A model without reasoning refuses the setting: ask once without it.
            body.pop("reasoning")
            response = await self._post(body, key)
        response.raise_for_status()
        return clean_title(_output_text(response.json()))

    async def _post(self, body: dict[str, Any], key: str) -> httpx.Response:
        return await self._client.post(
            self._url,
            json=body,
            headers={"authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(30),
        )


def _output_text(reply: Any) -> str:
    """The text of a Responses API reply."""
    if not isinstance(reply, dict):
        return ""
    if isinstance(reply.get("output_text"), str):
        return reply["output_text"]
    parts: list[str] = []
    for item in reply.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for content in item.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
    return "".join(parts)


def normalize_title(text: str) -> str:
    """One line of visible text: every title, typed or written, goes through this.

    Control characters become spaces, and invisible formatting characters
    (bidi overrides such as U+202E, zero-width spaces) are dropped, so a
    title can't hide or reorder what's shown in the sidebar or a folder name.
    """
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf":
            continue
        kept.append(" " if category == "Cc" else char)
    return " ".join("".join(kept).split())


def scrub_title(text: str) -> str:
    """The title without anything that looks like a study identifier.

    Removes email addresses; dates (ISO, US, 9/26, 2026-09, "March 14",
    "Monday 26th"); phone-like digit groups; numbers labelled as someone's
    ("participant 0001", "ID 1234", "#1234", "SYN24 0001"); runs of five or
    more digits that aren't round; and words mixing letters and digits the
    way IDs do ("P-0001", "IHS2025_00123", "A1B2C3", 32-digit hex). A year
    such as 2025, 10000, or a measure such as PHQ-9, COVID-19, CYP2D6 or
    Q1-Q4, stays. If nothing meaningful is left, the result is empty. Names
    can't be told apart from other words: the model is asked to leave them
    out.
    """
    original = normalize_title(text)
    text = _EMAIL.sub(" ", original)
    text = _DATES.sub(" ", text)
    text = _LABELLED.sub(
        lambda m: " " if m.group(1).lower() in _BARE_LABELS else f"{m.group(1)} ", text
    )
    text = _DIGIT_GROUPS.sub(
        lambda m: " " if _looks_like_digit_groups(m.group()) else m.group(), text
    )
    text = _HASH_NUMBER.sub(" ", text)
    text = _CODED.sub(r"\1 ", text)
    text = _WORD.sub(lambda m: "" if _looks_like_an_id(m.group()) else m.group(), text)
    if text == original:
        return original
    text = _EMPTY_BRACKETS.sub(" ", text)
    text = _STRANDED.sub(lambda m: m.group(1) or m.group(2) or m.group(3), " ".join(text.split()))
    text = " ".join(text.split()).strip(_LOOSE_ENDS)
    text = _LEADING.sub("", _DANGLING.sub("", text).rstrip(_LOOSE_ENDS)).strip(_LOOSE_ENDS)
    words = re.findall(r"\w+", text.lower())
    if not any(word not in _NOT_MEANINGFUL for word in words):
        return ""
    return text


def _looks_like_digit_groups(text: str) -> bool:
    """A phone or record number: three groups of seven or more digits in all,
    or three digits then four ("555 1234", but not "500-1000")."""
    groups = re.findall(r"\d+", text)
    if len(groups) >= 3:
        return sum(len(group) for group in groups) >= 7
    lengths = [len(group) for group in groups]
    if lengths == [3, 4]:
        return not all(int(group) % 100 == 0 for group in groups)
    return False


def _looks_like_an_id(word: str) -> bool:
    digit_runs = re.findall(r"\d+", word)
    if not digit_runs:
        return False
    if any(len(run) >= 5 and not _round(run) for run in digit_runs):
        return True
    if not re.search(r"[^\W\d_]", word):
        return False  # only digits (a year, a count) and separators
    hex_digits = word.replace("-", "")
    if len(hex_digits) >= 16 and re.fullmatch(r"[0-9a-fA-F]+", hex_digits):
        return True  # a hash or UUID
    if "_" in word or max(len(run) for run in digit_runs) >= 3:
        return True
    # Letters and digits taking turns, as in A1B2C3, judged a part at a time:
    # Q1-Q4, CYP2D6 and H1N1 have too few digits.
    for part in word.split("-"):
        runs = re.findall(r"\d+", part)
        if len(runs) >= 2 and sum(len(run) for run in runs) >= 3:
            return True
    return False


def _round(run: str) -> bool:
    """A round number (10000, 250000), which IDs rarely are."""
    return not run.startswith("0") and int(run) % 1000 == 0 and int(run) <= 1_000_000


def clean_title(text: str) -> str | None:
    """One short line, without the quotes and full stops models like to add."""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^(title\s*:\s*)", "", normalize_title(line), flags=re.IGNORECASE)
    line = scrub_title(line.strip(_QUOTES)).strip(_QUOTES).rstrip(".!?;:,")
    return _shorten(line) or None


def fallback_title(question: str) -> str:
    """The question's first words, when the model can't be asked."""
    first = next((part.strip() for part in question.splitlines() if part.strip()), "")
    first = re.split(r"(?<=[.?!])\s", normalize_title(first), maxsplit=1)[0]
    first = scrub_title(first.rstrip(".?!")).rstrip(".?!,;:")
    return _shorten(" ".join(first.split()[:8])) or DEFAULT_TITLE


def _shorten(text: str) -> str:
    if len(text) <= MAX_LENGTH:
        return text
    cut = text[: MAX_LENGTH - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"
