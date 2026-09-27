import json

import httpx
import pytest

from datalab.credentials import MissingCredential
from datalab.sessions.titles import (
    TitleWriter,
    clean_title,
    fallback_title,
    normalize_title,
    scrub_title,
)

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
    assert "participant IDs" in body["instructions"]  # told to leave identifiers out
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


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("Sleep for P-0001 on 2025-03-14", "Sleep"),
        ("IHS2025_00123 mood (P-0001)", "mood"),
        ("Email jane.doe@umich.edu about 03/14/2025 visit", "Email about visit"),
        ("MRN 12345678 steps", "steps"),
        ("Mood on March 14th, 2025", "Mood"),
        ("Heart rate, 14 Mar 2025, participant A1B2C3", "Heart rate, participant"),
        ("Visits for S123 and s-0042", "Visits"),
        ("Sleep at 2025-03-14T09:30 and 3-14-25", "Sleep"),
        # Phone and formatted record numbers: three digit groups, or 3 then 4.
        ("Call 734-555-1234", "Call"),
        ("Call (734) 555-1234 now", "Call now"),
        ("Call +1 734 555 1234", "Call"),
        ("Call 734.555.1234", "Call"),
        ("Visits 12-34-567", "Visits"),
        ("MRN 12-34-567 visits", "visits"),
        ("Phone 123 4567", "Phone"),
        # Numbers labelled as someone's: the number goes, a bare label too.
        ("Sleep for participant 0001", "Sleep for participant"),
        ("Mood of subject 12", "Mood of subject"),
        ("Steps for ID 1234", "Steps"),
        ("Labs for record 5678", "Labs for record"),
        ("Visits for MRN 123456", "Visits"),
        ("Notes on #1234", "Notes"),
        ("Sleep of SYN24 0001", "Sleep of SYN24"),
        # The study's own ID shapes.
        ("Sleep for SYN24-0001", "Sleep"),
        ("Steps for 9240001", "Steps"),
        ("Run e3b0c44298fc1c149afbf4c8996fb924", "Run"),
        ("Run 3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6a7b", "Run"),
        # More dates.
        ("Mood on 9/26", "Mood"),
        ("Visit 9/26/26", "Visit"),
        ("Enrolment 2026-09", "Enrolment"),
        ("Mood on 26 Sep 2026", "Mood"),
        ("Mood on Sep 26", "Mood"),
        ("Mood on Monday 26th", "Mood"),
        # Nothing meaningful left.
        ("P-0001 on 2025-03-14", ""),
        ("participant 0001", ""),
        # What a title should name stays.
        ("Top 10000 steps", "Top 10000 steps"),
        ("500-1000 steps", "500-1000 steps"),
        ("CYP2D6 metabolism", "CYP2D6 metabolism"),
        ("Q1-Q4 trends", "Q1-Q4 trends"),
        ("30/60/90-day retention", "30/60/90-day retention"),
        ("24/7 monitoring", "24/7 monitoring"),
        ("COVID-19 and IL-6", "COVID-19 and IL-6"),
        ("T2D and H1N1", "T2D and H1N1"),
        ("2024-2025 cohort", "2024-2025 cohort"),
        ("Q3 2025 sleep", "Q3 2025 sleep"),
        ("PHQ-9 and SF-36", "PHQ-9 and SF-36"),
        ("HbA1c, BRCA1, APOE4", "HbA1c, BRCA1, APOE4"),
        ("ICD-10 codes", "ICD-10 codes"),
        ("PM2.5 exposure", "PM2.5 exposure"),
        ("Sleep in March 2026", "Sleep in March 2026"),
        ("Participants over 65", "Participants over 65"),
        ("PHQ-9 and GAD-7 in 2025 interns", "PHQ-9 and GAD-7 in 2025 interns"),
        ("COVID-19, HbA1c, SF-36, ICD-10", "COVID-19, HbA1c, SF-36, ICD-10"),
        ("Days over 10,000 steps in March", "Days over 10,000 steps in March"),
        ("T2D in the 2024-25 cohort, 3rd year", "T2D in the 2024-25 cohort, 3rd year"),
        ("Top decile of markers 12", "Top decile of markers 12"),
        ("P-0001", ""),
    ],
)
def test_scrub_title_takes_out_study_identifiers(text, title):
    assert scrub_title(text) == title


def test_normalize_title_makes_one_line_of_visible_text():
    assert normalize_title("  Sleep\n\tand\x00mood\x1b ") == "Sleep and mood"
    # Bidi overrides and isolates, and zero-width characters, can hide or
    # reorder what a title shows.
    hidden = "\u202eSleep\u202c \u2066fdp.exe\u2069\u200b \u200fmood"
    assert normalize_title(hidden) == "Sleep fdp.exe mood"


async def test_a_model_title_naming_a_participant_is_scrubbed():
    titles, _ = writer(lambda r: httpx.Response(200, json=reply("Sleep for P-0001 on 2025-03-14")))
    assert await titles.title(QUESTION, model="gpt-5.5", kind="data") == "Sleep"
    # Nothing left once scrubbed: the question's words, scrubbed too.
    titles, _ = writer(lambda r: httpx.Response(200, json=reply("\u202eP-0001\u202c")))
    question = "What was IHS2025_00123's heart rate on 3/14/2025?"
    assert await titles.title(question, model="gpt-5.5", kind="data") == "What was heart rate"


def test_the_fallback_is_scrubbed():
    assert fallback_title("Pull jane@umich.edu and P-0001 steps. Thanks") == "Pull and steps"
    assert fallback_title("P-0001?") == "New conversation"
    # A lone connecting word is no title.
    assert fallback_title("P-0001 on 2025-03-14?") == "New conversation"
