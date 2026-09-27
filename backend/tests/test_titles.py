import json

import httpx
import pytest

from datalab.credentials import MissingCredential
from datalab.sessions.titles import TitleWriter, clean_title, fallback_title

QUESTION = "Is sleep duration associated with mood scores in the 2025 interns?"


def reply(text: str) -> dict:
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def writer(handler, key=lambda: "test-key", allowed=None) -> tuple[TitleWriter, list]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return TitleWriter(client, "https://umgpt.example/v1", key, allowed), seen


async def test_the_model_writes_a_short_title():
    titles, seen = writer(
        lambda r: httpx.Response(200, json=reply('"Sleep and mood, 2025 interns."'))
    )
    assert (
        await titles.title(QUESTION, model="gpt-5.5", kind="data") == "Sleep and mood, 2025 interns"
    )
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt-5.5" and body["input"] == QUESTION and body["store"] is False
    assert "tools" not in body  # nothing the relay policy would refuse
    assert seen[0].headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, json={}),
        lambda r: httpx.Response(200, json=reply("   ")),
        lambda r: httpx.Response(200, text="not json"),
    ],
)
async def test_without_a_good_answer_the_question_names_it(handler):
    titles, _ = writer(handler)
    assert await titles.title(QUESTION, model="gpt-5.5", kind="data") == (
        "Is sleep duration associated with mood scores in"
    )


async def test_no_key_or_an_unapproved_model_never_asks():
    def no_key() -> str:
        raise MissingCredential("none")

    titles, seen = writer(lambda r: httpx.Response(200, json=reply("x")), key=no_key)
    assert await titles.title("Steps by month", model="gpt-5.5", kind="data") == "Steps by month"
    titles, seen = writer(lambda r: httpx.Response(200, json=reply("x")), allowed=("gpt-5.5",))
    assert await titles.title("Steps by month", model="gpt-4o", kind="data") == "Steps by month"
    assert seen == []


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("Title: PHQ-9 tables across cohorts", "PHQ-9 tables across cohorts"),
        ("**Steps over the intern year**\n\nExtra line", "Steps over the intern year"),
        ("“Wearable missingness methods”", "Wearable missingness methods"),
        ("A" * 80, "A" * 59 + "…"),
        ("", None),
    ],
)
def test_clean_title(text, title):
    assert clean_title(text) == title


def test_fallback_title_uses_the_first_sentence():
    assert (
        fallback_title("Extract daily sleep. One row per night, please.") == "Extract daily sleep"
    )
    assert fallback_title("\n\n") == "New conversation"


async def test_an_odd_reply_shape_still_gets_a_title():
    odd = {"output": [{"type": "message", "content": 5}]}
    titles, _ = writer(lambda r: httpx.Response(200, json=odd))
    assert await titles.title("Steps by month", model="gpt-5.5", kind="data") == "Steps by month"


async def test_a_model_without_reasoning_is_asked_again_without_it():
    def handler(request: httpx.Request) -> httpx.Response:
        if "reasoning" in json.loads(request.content):
            return httpx.Response(400, json={"error": {"message": "reasoning not supported"}})
        return httpx.Response(200, json=reply("Monthly step counts"))

    titles, seen = writer(handler)
    assert (
        await titles.title("Steps by month", model="gpt-4.1", kind="data") == "Monthly step counts"
    )
    assert len(seen) == 2


async def test_shutdown_stops_titles_still_being_written():
    import asyncio

    started = asyncio.Event()

    async def slow() -> None:
        started.set()
        await asyncio.sleep(60)

    titles, _ = writer(lambda r: httpx.Response(200, json=reply("x")))
    titles.start("c1", slow)
    titles.start("c1", slow)  # one at a time per conversation
    await started.wait()
    await asyncio.wait_for(titles.aclose(), timeout=2)
