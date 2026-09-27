"""Claim-to-evidence tracing: which numbers in an answer came from somewhere.

After each final answer, DataLab looks for the numbers in it (counts, means,
percentages, estimates) in what the turn actually produced: query results,
command output, and output files. A number that matches none of them is
flagged, so the person knows which figures to check. It's an aid, not a
proof: a number can match by coincidence, and a correct derived number
(computed in the agent's head) will be flagged.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# A number as written in prose: 1,234  12.5  -0.31  45%  3.2e-4
_NUMBER = re.compile(
    r"(?<![\w.])[-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![\w])"
)
# Parts of an answer that aren't claims: code, links, and inline code.
_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
# A reference's volume, issue, and pages aren't findings. Only forms nothing
# but a citation takes: hiding a finding from the check is worse than flagging
# a page number, so "45(12), 30", "12, 45 (2023)", "p.05" and "2024; 3:1" stay,
# and APA ("31(4), 13520") and Nature ("12, 21412 (2022)") styles are flagged.
#   2021;281:1077-1078   2024;14(2):e17545     (Vancouver: no spaces)
#   pp. 1077-1078   PMID: 34042743   arXiv:2101.01234
#   doi:10.1038/x   https://doi.org/10.1038/x   10.1038/s41598-x (a letter in it)
_RANGE = r"(?:[-\u2013]e?\d+)?"
_CITATION = re.compile(
    r"(?<![\w.])(?:1[89]|20)\d{2};\d+(?:\([\w-]+\))?:e?\d+" + _RANGE
    + r"|\bpp\. ?\d+" + _RANGE
    + r"|\bPMID:? ?\d+|\barXiv: ?\d{4}\.\d{4,5}"
    + r"|(?:\bdoi: ?|https?://(?:dx\.)?doi\.org/)10\.\d{4,9}/[^\s,;)\]\x00]+",
    re.IGNORECASE,
)  # fmt: skip
# A bare DOI, matched once, then dropped only if its suffix looks like one: a
# letter first, and a digit (s41598-022-1, jama.2020.12, jsr.13520). A rate
# such as 10.2345/100000, 10.2345/person-years or 10.1234/1000PY stays.
# (Testing for these in the pattern itself would rescan every start: quadratic.)
_BARE_DOI = re.compile(r"(?<![\w.])10\.\d{4,9}/([^\s,;)\]\x00]+)")
# What removed code, dates and citations leave behind. Not a space, so a
# number after them ("`n` 312. rows") doesn't look like it starts a line.
# (The DOI patterns above stop at it, as they stop at a space.)
_GAP = "\x00"


def _drop_bare_doi(match: re.Match[str]) -> str:
    suffix = match.group(1)
    looks_like_doi = suffix[0].isalpha() and any(c.isdigit() for c in suffix)
    return _GAP if looks_like_doi else match.group(0)


@dataclass(frozen=True)
class Claim:
    text: str  # as written in the answer
    traced: bool


def numbers_in_answer(answer: str) -> list[str]:
    """The numbers an answer states, leaving out code, links, dates, citations,
    list and heading numbers, and small integers."""
    prose = _CODE_BLOCK.sub(_GAP, answer)
    prose = _INLINE_CODE.sub(_GAP, prose)
    prose = _LINK_TARGET.sub("]", prose)
    prose = _CITATION.sub(_GAP, prose)
    prose = _BARE_DOI.sub(_drop_bare_doi, prose)
    prose = _DATE.sub(_GAP, prose)
    list_numbers = _list_numbers(prose)
    found: list[str] = []
    for match in _NUMBER.finditer(prose):
        token = match.group(0)
        value = _value(token)
        if value is None or _trivial(token, value, match.start(), list_numbers):
            continue
        after = prose[match.end() : match.end() + 12].lower().replace(_GAP, " ")
        if token.endswith("%") and after.lstrip().startswith(("ci", "confidence", "credible")):
            continue  # "95% CI" is a choice, not a finding
        if token not in found:
            found.append(token)
    return found


def trace(answer: str, evidence: list[str]) -> list[Claim]:
    """Each number in `answer`, and whether any piece of `evidence` contains it."""
    known = _evidence_values(evidence)
    return [Claim(token, _matches(token, known)) for token in numbers_in_answer(answer)]


# Clock times and dates in output (timestamps, `ls` listings) aren't findings.
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b")


def _evidence_values(evidence: list[str]) -> list[Decimal]:
    values: set[Decimal] = set()
    for text in evidence:
        text = _DATE.sub(" ", _TIME.sub(" ", text))
        for match in _NUMBER.finditer(text):
            value = _value(match.group(0))
            if value is not None:
                values.add(value)
                if match.group(0).endswith("%"):
                    values.add(value / 100)
    return sorted(values)


def _matches(token: str, known: list[Decimal]) -> bool:
    value = _value(token)
    if value is None:
        return True
    candidates = {value}
    if token.endswith("%"):
        candidates.add(value / 100)  # 12.5% written from a proportion 0.125
    for candidate in candidates:
        decimals = _decimals(token) + (2 if token.endswith("%") and candidate != value else 0)
        tolerance = Decimal(1).scaleb(-decimals) / 2  # the claim is the evidence, rounded
        # Anything within the tolerance, found by binary search in the sorted values.
        start = bisect.bisect_left(known, candidate - tolerance)
        if start < len(known) and known[start] <= candidate + tolerance:
            return True
    return False


def _value(token: str) -> Decimal | None:
    # A typographic minus counts as a minus.
    cleaned = token.replace(",", "").replace("\u2212", "-").rstrip("%")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    # Numbers too big or too precise to compare (1e2000000) are left out, so
    # one can't break the trace for the rest.
    if not value.is_finite() or value.adjusted() >= 30 or len(cleaned) > 40:
        return None
    return value


def _decimals(token: str) -> int:
    """Decimal places the number is given to: 3.2e-4 is to 5 places, 3.2e4 to -3."""
    number, _, exponent = token.rstrip("%").lower().partition("e")
    places = len(number.split(".")[1]) if "." in number else 0
    try:
        return places - int(exponent or 0)
    except ValueError:
        return places


# What a line's content may sit behind: indentation, blockquote ">"s, and a
# heading's "#"s.
_LINE_MARKERS = re.compile(r"[ \t]*((?:>[ \t]*)*)(#{1,6}[ \t]+)?")
# An ordered list's number ("12. ", "15) ", or either at a line's end), and a bullet.
_ORDERED_ITEM = re.compile(r"(\d{1,9})[.)](?:\s|\Z)")
_BULLET_ITEM = re.compile(r"[-*+](?:\s|\Z)")


def _list_numbers(prose: str) -> set[int]:
    """Where the numbers of ordered lists and numbered headings start.

    Markdown's own rule: a list can start at any number after a blank line,
    at the start of the text, or right after another ordered item, but it
    can break into a paragraph (or a bullet's text) only at 1. So "The
    cohort enrolled\n312. Of these" is a sentence, and so is "312. Of
    these" after "- The cohort enrolled": its 312 is a finding. A number
    after a bullet ("- 312 participants") is a finding too.
    """
    starts: set[int] = set()
    previous = "start"  # the line before: start, blank, ordered, bullet, or text
    previous_quoted = False
    offset = 0
    for line in prose.split("\n"):
        markers = _LINE_MARKERS.match(line)
        assert markers is not None  # every part is optional
        content = line[markers.end() :]
        quoted = bool(markers.group(1))
        ordered = _ORDERED_ITEM.match(content)
        if markers.group(2):  # a heading: "## 12. Results"
            if ordered:
                starts.add(offset + markers.end())
            kind = "blank"  # a heading ends a paragraph, like a blank line
        elif ordered:
            # A new blockquote starts a new block, whatever came before.
            fresh = previous in ("start", "blank", "ordered")
            fresh = fresh or (quoted and not previous_quoted)
            if fresh or int(ordered.group(1)) == 1:
                starts.add(offset + markers.end())
                kind = "ordered"
            else:
                kind = "text"
        elif _BULLET_ITEM.match(content):
            kind = "bullet"
        else:
            kind = "blank" if not content.strip() else "text"
        previous, previous_quoted = kind, quoted
        offset += len(line) + 1
    return starts


def _trivial(token: str, value: Decimal, start: int, list_numbers: set[int]) -> bool:
    """Numbers that aren't findings: list numbering, small counts, and years."""
    if "." not in token and "," not in token and not token.endswith("%"):
        if abs(value) <= 10:
            return True  # "two tables", "step 3"
        if 1900 <= value <= 2100:
            return True  # a year or cohort
        if start in list_numbers:
            return True  # "12." at the start of a list item or heading
    return False
