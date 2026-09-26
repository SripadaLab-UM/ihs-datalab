import asyncio

import pytest

from datalab.sessions.approvals import Approvals, Unshowable, clean_question


@pytest.mark.parametrize(
    "text",
    [
        "ok" + chr(0xE0053) + chr(0xE0059),  # tag characters: invisible ASCII
        "ok\u200bSYN25",  # zero-width space
        "ok\u202eevil",  # right-to-left override
        "ok\x00",
        "ok\u2028more",
        "ok\ufe01hidden",  # variation selector
        "ok\u034f",  # combining grapheme joiner
        "ok\u3164",  # Hangul filler
        "ok\u2800",  # blank braille
        "ok\u3164x\u16fe4",  # fillers
        "ok\U000e0101",  # variation selector supplement
        "e\u0301\u0302\u0303\u0304",  # stacked accents
        "",
        "   \n  ",
        "x" * 1001,
    ],
)
def test_text_that_cant_be_seen_or_reviewed_is_refused(text):
    with pytest.raises(Unshowable):
        clean_question(text)


def test_ordinary_accents_and_other_scripts_are_fine():
    assert clean_question("P\u00f3lya\u2013Kolmogorov, caf\u00e9, \u4e2d\u6587") == (
        "P\u00f3lya\u2013Kolmogorov, caf\u00e9, \u4e2d\u6587"
    )


def test_text_is_normalized_and_padding_collapsed():
    assert clean_question("  a\t\tb\n\n\n\n\nc  ") == "a b\n\nc"
    assert clean_question("one \ntwo") == "one\ntwo"  # trailing spaces don't show
    assert clean_question("\uff21\uff22") == "AB"  # full-width letters, as shown


async def test_answers_are_recorded_once_and_only_for_their_conversation():
    approvals = Approvals()
    pending = approvals.open("c1", "q")
    with pytest.raises(KeyError):
        approvals.answer("c2", pending.id, True, "q")
    assert approvals.answer("c1", pending.id, True, " edited ") == "edited"
    assert await asyncio.wait_for(pending.decision, 1) == (True, "edited")
    with pytest.raises(KeyError):
        approvals.answer("c1", pending.id, False, "")
    other = approvals.open("c1", "q2")
    assert approvals.withdraw("c1") == [other.id]
