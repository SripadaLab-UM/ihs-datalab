"""Conversation titles, written by the model from the first question.

Nobody types a title: a new conversation is "New conversation" until its first
question, then gets a short title the way Codex names a thread. The request
goes to the same approved U-M GPT model the conversation uses, which receives
that question anyway, and passes the relay's own policy check. If there's no
key or no answer, the title is the question's first words instead.
"""

from __future__ import annotations

import asyncio
import logging
import re
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
    "no filler such as 'Question about' or 'Analysis of'. Reply with the "
    "title only."
)


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


def clean_title(text: str) -> str | None:
    """One short line, without the quotes and full stops models like to add."""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^(title\s*:\s*)", "", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+", " ", line.strip(_QUOTES)).rstrip(".!?;:,")
    return _shorten(line) or None


def fallback_title(question: str) -> str:
    """The question's first words, when the model can't be asked."""
    first = next((part.strip() for part in question.splitlines() if part.strip()), "")
    first = re.split(r"(?<=[.?!])\s", first, maxsplit=1)[0].rstrip(".?!")
    return _shorten(" ".join(first.split()[:8])) or DEFAULT_TITLE


def _shorten(text: str) -> str:
    if len(text) <= MAX_LENGTH:
        return text
    cut = text[: MAX_LENGTH - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"
