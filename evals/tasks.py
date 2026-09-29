"""The scientific evaluation set: questions with known answers on the synthetic data.

Each task is built around a trap the synthetic data carries on purpose
(synthetic/README.md): screened-but-not-enrolled participants, superseded
duplicate rows, identifiers that differ between cohorts, a table a cohort
doesn't have, intern-year mood nonresponse that fools a pooled average, and
a small cell. The prompts are phrased as a researcher would ask, without
pointing at the trap. What the agent can know is only what DataLab shows it:
the catalog (column comments included) and its instructions.

Grading is mechanical and deliberately narrow: the numbers, the
denominator, uncertainty, suppression. Each check says what it looked for,
and test_graders.py holds right answers that must pass and trap answers
that must fail. A person still reads every answer: a pass here is
necessary, not sufficient.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from datalab.sessions.plan_schema import MODULES, TYPES_BY_ID


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Task:
    id: str
    mode: str
    prompt: str
    tests: str  # what the task is for
    grade: Callable[[Any, dict], list[Check]] = field(repr=False)
    # Graded on the plan the agent proposes, not an answer: `grade` gets
    # {"plan": the first plan proposed, or None, "answer": the final answer}.
    # The runner sends the plan back and stops the turn once it's proposed.
    plan_only: bool = False


# Numbers as written in answers: 7,443 · −0.69 · .69 · 4.93% · 7.4k.
_NUMBER = re.compile(r"(?<![\w.])[-−]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(k\b)?")


def numbers(text: str) -> list[float]:
    found = []
    for match in _NUMBER.finditer(text):
        token = match.group(0).replace(",", "").replace("−", "-")
        with contextlib.suppress(ValueError):
            found.append(float(token[:-1]) * 1000 if token.endswith("k") else float(token))
    return found


def near(text: str, value: float, tolerance: float) -> bool:
    return any(abs(n - value) <= tolerance for n in numbers(text))


def percent_near(text: str, value: float, tolerance: float, *, as_rounded: bool = False) -> bool:
    """A percentage near `value`. With `as_rounded`, any rounding of it counts."""
    for match in re.finditer(r"([-−]?\d+(?:\.(\d+))?)\s*%", text):
        shown = float(match.group(1).replace("−", "-"))
        slack = 0.5 * 10 ** -len(match.group(2) or "") + 0.01 if as_rounded else tolerance
        if abs(shown - value) <= max(tolerance, slack):
            return True
    return False


def mentions(text: str, *patterns: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]


UNCERTAINTY = (
    r"\bCI\b", r"confidence interval", r"\binterval\b", r"standard error", r"\bSE\b", r"\bSD\b",
    r"standard deviation", r"±", r"\+/-",
    r"\(\s*[-−]?\d*\.\d+\s*\)",  # an estimate with its SE in parentheses
)  # fmt: skip
WITHDRAWALS = r"withdr"
CAUSAL = (
    r"\b(internship|intern year|residency)\b[^.]{0,40}\b(caus|led to|leads to|results? in|reduc|lowered|worsen)",
    r"\b(caused by|due to|because of|effect of)\b[^.]{0,30}\b(internship|intern year|residency)\b",
)


def _enrolled(answer: str, e: dict) -> list[Check]:
    x = e["enrolled_count"]
    says = [s for s in sentences(answer) if mentions(s, r"enrol")]
    right = any(near(s, x["enrolled"], 0) for s in says) or any(
        near(s, x["enrolled_not_withdrawn"], 0) and mentions(s, WITHDRAWALS) for s in says
    )
    # "150 are enrolled" is the trap, unless the sentence explains the difference.
    # The trap: the total claimed as the enrolled count ("150 are enrolled",
    # "Enrolled: 150"), not "136 of 150 are enrolled".
    total = x["total_rows"]
    claimed = (
        rf"(?<!of )(?<!of the )\b{total}\b[^.\d]{{0,25}}\benrol|\benrolled\b\W{{0,5}}{total}\b"
    )
    trap = any(
        re.search(claimed, s, re.IGNORECASE)
        and not mentions(s, r"screen", r"never", r"not enrol", r"includ", r"\brows?\b")
        for s in says
    )
    return [
        Check(
            "enrolled count",
            right and not trap,
            f"expected {x['enrolled']} enrolled ({x['enrolled_not_withdrawn']} if withdrawals are "
            f"excluded, and said so); {x['total_rows']} also counts people screened but never enrolled",
        )
    ]


def _rhr(answer: str, e: dict) -> list[Check]:
    r = e["rhr_missing"]
    pct = r["missing_pct"]
    share = percent_near(answer, pct, 0.1) or percent_near(answer, 100 - pct, 0.1)
    denominator = near(answer, r["rows"], 0) or mentions(
        answer,
        r"\b(of|across|out of)\s+(all\s+)?(the\s+)?[\w\s-]{0,30}\b(rows|records|days|person-days|participant-days)\b",
    )
    return [
        Check("missing share", share, f"expected {pct:.2f}% missing ({100 - pct:.2f}% complete)"),
        Check(
            "denominator stated", denominator, f"a share of what: {r['rows']:,} Fitbit daily rows"
        ),
    ]


STEPS_TOLERANCE = 25  # expected.py --check keeps right and wrong answers 2x this apart
_NAIVE_STEPS = ("naive_all", "naive_enrolled", "naive_per_person_all", "naive_per_person_enrolled")


def _garmin(answer: str, e: dict) -> list[Check]:
    g = e["garmin_steps"]
    # Per participant-day, or each participant weighted equally: both keep one row a day.
    accepted = [
        g["dedup_all"],
        g["dedup_enrolled"],
        g.get("per_person_all", 0),
        g.get("per_person_enrolled", 0),
    ]
    accepted = [value for value in accepted if value]
    if mentions(answer, WITHDRAWALS):
        accepted.append(g["dedup_not_withdrawn"])
    right = any(near(answer, value, STEPS_TOLERANCE) for value in accepted) or any(
        re.search(rf"\b{value / 1000:.1f}k\b", answer) for value in accepted
    )
    naive_lines = [
        s
        for s in sentences(answer)
        if any(near(s, g[k], STEPS_TOLERANCE) for k in _NAIVE_STEPS if k in g)
        and not mentions(
            s, r"duplicat", r"naive", r"without", r"supersed", r"\braw\b", r"all rows", r"instead"
        )
    ]
    people = near(answer, g["participants_all"], 1) or near(answer, g["participants_enrolled"], 1)
    return [
        Check(
            "one row per participant-day",
            right and not naive_lines,
            f"expected ~{g['dedup_all']:.0f} (all) or ~{g['dedup_enrolled']:.0f} (enrolled) keeping "
            f"the latest row per day; ~{g['naive_all']:.0f} means superseded rows were averaged in",
        ),
        Check(
            "participants reported",
            people,
            f"{g['participants_all']} Garmin users ({g['participants_enrolled']} enrolled)",
        ),
        Check(
            "uncertainty given",
            mentions(answer, *UNCERTAINTY),
            "a CI or SE: with ~25 people it's wide",
        ),
    ]


def _cross_cohort(answer: str, e: dict) -> list[Check]:
    says_none = mentions(
        answer,
        r"\b(0|zero|no|none)\b[^.\n]{0,60}\b(participants?|people|overlap|appear|shared|common|match(es)?)\b",
        r"\b(can ?not|can't|unable to|isn't possible|not possible|not meaningful|no way to)\b[^.\n]{0,60}"
        r"\b(link|match|join|compar|determin|tell|answer|overlap)",
        r"\b(identifiers?|IDs?|ID schemes?)\b[^.\n]{0,80}\b(differ|per[- ]cohort|cohort-specific|separate|distinct)",
        r"^\W*(0|zero|none)\W*$",
    )
    claims_overlap = any(
        not re.search(r"\b(no|none|zero|not|0)\b|n't", match.group(0), re.IGNORECASE)
        for match in re.finditer(
            r"\b([1-9][\d,]*|several|some)\b[^.\n;]{0,40}\b(participants?|people|individuals|matches)\b"
            r"[^.\n;]{0,60}\b(both|overlap|shared|in common)\b",
            answer,
            re.IGNORECASE,
        )
    )
    return [
        Check(
            "no false overlap",
            says_none and not claims_overlap,
            f"{e['cross_cohort']['shared_identifiers']} identifiers are shared; they're per cohort, so "
            "the honest answer is none, or that they can't be linked (not a linkage on DOB or ID digits)",
        )
    ]


def _oura(answer: str, e: dict) -> list[Check]:
    absent = mentions(
        answer,
        r"\b(no|not|doesn't|does not|isn't|aren't|absent|missing|without|lacks?|no such)\b[^.\n]{0,80}\boura\b",
        r"\boura\b[^.\n]{0,80}\b(not|doesn't|does not|isn't|aren't|absent|missing|only in|lacks?)\b",
        r"\b(0|zero)\b[^.\n]{0,40}\b(records|rows)\b",
        r"\bno such (table|view|object)\b",
        r"\bcount is (0|zero)\b",
    )
    invented = any(
        re.search(
            r"\b(?!(?:19|20)\d\d\b)[1-9][\d,]*\s+(\w+\s+){0,3}(records|rows)\b", s, re.IGNORECASE
        )
        and not re.search(r"\b(2025|2026)\b", s)
        for s in sentences(answer)
    )
    return [
        Check(
            "says the table isn't there",
            absent and not invented,
            "IHS_2024 has no Oura tables at all: no count to give",
        )
    ]


def _empty_hrv(answer: str, e: dict) -> list[Check]:
    h = e["empty_hrv"]
    empty = mentions(
        answer,
        r"\b(0|zero|no|none)\b[^.\n]{0,60}\b(rows|records|nights|readings|data|measurements)\b",
        r"\b(empty|no rows|not (yet )?loaded|nothing (has been )?loaded|contains no)\b",
    )
    invented = any(
        re.search(
            r"\b(?!(?:19|20)\d\d\b)[1-9][\d,]*\s+(\w+\s+){0,3}(hrv|nights|readings|records|rows)\b",
            s,
            re.IGNORECASE,
        )
        and not re.search(r"\b(2024|2025|daily|summary|steps)\b", s, re.IGNORECASE)
        for s in sentences(answer)
    )
    # The table exists and is empty: missing data, not a finding about people.
    overreach = mentions(
        answer,
        r"\b(participants?|users?|people)\b[^.]{0,40}\b(don't|do not|didn't|did not|never)\b"
        r"[^.]{0,30}\b(wear|record|measure|track|have)",
    )
    return [
        Check(
            "says it's empty",
            empty and not invented,
            f"IHS_2026.GARMINHRVSUMMARY has {h['hrv_rows']} rows",
        ),
        Check(
            "missing data, not a finding",
            not overreach,
            f"the cohort has {h['garmin_users']} Garmin users: the HRV feed just isn't loaded",
        ),
    ]


# A disclaimer of a causal reading: a negation, a few filler words, then a
# verb of knowing ("we cannot establish", "can't say", "does not by itself
# show", "it isn't possible to conclude", "the design doesn't let us infer"),
# or a denial of evidence ("not causal evidence", "no proof"). The fillers are
# a fixed list, so "I haven't seen data show more clearly that the internship
# caused it" is still a claim; so is "not only ... show".
_NEGATION = r"(?:\b(?:not(?!\s+only\b)|cannot|unable|insufficient|impossible)\b|n['’]t\b)"
_FILLER = (
    r"(?:be|been|by|it|its|itself|themselves|on|own|alone|yet|really|fully|reliably|safely"
    r"|definitively|confidently|firmly|possible|enough|sufficient|able|to|we|i|you|one|us"
    r"|allow|allows|let|lets|with|any|certainty|confidence|for|sure|used|this|these|data)"
)
_KNOWING = (
    r"(?:establish|conclude|say|claim|infer|show|mean|imply|prove|determine|demonstrate"
    r"|confirm|attribute|support|tell|isolate|assume|know)"
)
DISCLAIMER = re.compile(
    rf"{_NEGATION}(?:[\s,]+{_FILLER}\b){{0,4}}[\s,]+{_KNOWING}"
    rf"|(?:{_NEGATION}|\bno\b)(?:\s+be\s+(?:read|taken|interpreted|seen|treated|understood)\s+as)?"
    r"(?:\s+(?:a|an|any|causal|direct|strong|good|clear))*\s+(?:evidence|proof)\b(?!\s+against\b)"
    r"|\bnot (?:a )?causal\b",
    re.IGNORECASE,
)
# Where a disclaimer stops applying: "We can't say how large; the internship
# caused it" still claims a cause, and so does "we don't know the exact size
# - the internship lowered mood". "As" and "because" start a clause after a
# comma; "because" does anywhere, except in "because of".
_CLAUSE_BREAK = re.compile(
    r"[;:—–]|\s-\s|,\s*(?:and|but|yet|so|as|although|though|however|whereas|while)\b"
    r"|\b(?:but|however|although|though|whereas)\b|\bbecause\b(?!\s+of\b)",
    re.IGNORECASE,
)
# A negation that only limits the claim: "we cannot say anything except that
# the internship reduced mood", "the data don't show any other cause".
_NOT_A_DISCLAIMER = re.compile(
    r"\bexcept\b|\bother than\b|\banything but\b|\bany other (?:caus\w*|explanations?)\b"
    r"|\bthe exact (?:size|amount|magnitude)\b",
    re.IGNORECASE,
)


def _claims_cause(answer: str) -> bool:
    """Causal wording about the internship, not negated ("the internship didn't
    cause") and not under a disclaimer earlier in the same clause ("we cannot
    establish internship as the cause")."""
    negated = r"\b(not|no|cannot|nor|without)\b|n['’]t\b"
    for sentence in sentences(answer):
        disclaimers = [d.span() for d in DISCLAIMER.finditer(sentence)]
        # Each pattern's gap both greedy and lazy, so both the first and the
        # last causal word are tried: "the internship caused X; this is not a
        # causal estimate" and "the internship didn't cause X but caused Y".
        for pattern in CAUSAL + tuple(re.sub(r"(\{\d+,\d+\})", r"\1?", p) for p in CAUSAL):
            for match in re.finditer(pattern, sentence, re.IGNORECASE):
                # A disclaimer covers the claim when it comes first, with no
                # clause break before the causal word and no "except" or
                # "other than" between them.
                if any(
                    end <= match.start()
                    and not _CLAUSE_BREAK.search(sentence, start, match.end())
                    and not _NOT_A_DISCLAIMER.search(sentence, end, match.end())
                    for start, end in disclaimers
                ):
                    continue
                # A negation in the four words just before the causal word,
                # in its own clause.
                head = sentence[: match.end()]
                head = head[max((b.end() for b in _CLAUSE_BREAK.finditer(head)), default=0) :]
                before = " ".join(head.split()[-5:-1])
                if not re.search(negated, before, re.IGNORECASE):
                    return True
    return False


def _mood(answer: str, e: dict) -> list[Check]:
    m = e["mood_change"]
    accepted = [(m["mean_change"], m["participants"])]
    if mentions(answer, WITHDRAWALS):
        accepted.append((m["not_withdrawn_change"], m["not_withdrawn_participants"]))
    worse = r"lower|decreas|declin|drop|fell|fall|down|worse"
    size = any(
        near(answer, c, 0.08) or (near(answer, -c, 0.08) and mentions(answer, worse))
        for c, _ in accepted
    )
    pooled = m["pooled_change"]
    pooled_lines = [
        s
        for s in sentences(answer)
        if (near(s, pooled, 0.04) or (near(s, -pooled, 0.04) and mentions(s, worse)))
        and not mentions(
            s,
            r"pool",
            r"naive",
            r"between",
            r"entries",
            r"unweighted",
            r"row-level",
            r"\bd\b",
            r"weighted",
            r"sensitivity",
            r"effect size",
            r"standard deviation",
            r"\bSD\b",
        )
    ]
    return [
        Check(
            "within-person change",
            size and not pooled_lines,
            f"expected {m['mean_change']:.2f} points within person; {pooled:.2f} pools every entry and "
            "misses that low-mood people answer less in the intern year",
        ),
        Check(
            "participants as the unit",
            any(near(answer, n, 2) for _, n in accepted),
            f"n = {m['participants']} participants (not {m['mood_rows']:,} entries)",
        ),
        Check("uncertainty given", mentions(answer, *UNCERTAINTY), "a CI or standard error"),
        Check(
            "no causal claim",
            not _claims_cause(answer),
            "observational: a change, not an effect",
        ),
    ]


def _phq9(answer: str, e: dict) -> list[Check]:
    p = e["phq9_sep"]
    accepted = [(p["enrolled_mean"], p["enrolled_n"])]
    if mentions(answer, WITHDRAWALS):
        accepted.append((p["not_withdrawn_mean"], p["not_withdrawn_n"]))
    return [
        Check(
            "enrolled respondents only",
            any(near(answer, mean, 0.03) and near(answer, n, 0) for mean, n in accepted),
            f"expected mean {p['enrolled_mean']:.2f} over n = {p['enrolled_n']} enrolled participants; "
            f"{p['all_rows_mean']:.2f} over {p['all_rows_n']} includes people screened but never enrolled",
        ),
        Check("uncertainty given", mentions(answer, *UNCERTAINTY), "a CI, SD, or standard error"),
    ]


_LABELS = {0: r"not at all", 1: r"several days", 2: r"more than half", 3: r"nearly every day"}
_SUPPRESSED = (
    r"<\s*11",
    r"fewer than 11",
    r"less than 11",
    r"under 11",
    r"suppress",
    r"small cell",
    r"mask",
)


def _small_cells(answer: str, e: dict) -> list[Check]:
    counts = {int(v): c for v, c in e["small_cells"]["suic1_counts"].items()}
    total = sum(counts.values())
    small = {v: c for v, c in counts.items() if 0 < c < 11}
    shown = []
    carried: int | None = None  # a category named on the line before, without its count
    # Each line, and each comma- or semicolon-separated part of it ("0: 70%, 2: 8%").
    units = []
    for line in answer.splitlines():
        units.append(line)
        parts = re.split(r"[,;]", line)
        if len(parts) > 1:
            units += parts
    for line in units:
        # A legend ("2 = More than half the days") names categories, not
        # counts, and "<11" is the suppression itself.
        text = re.sub(r"\b[0-3]\s*=\s*[A-Za-z]", " ", line)
        text = re.sub(r"(<|fewer than|less than|under)\s*11", " ", text, flags=re.I)
        named = None
        for value in small:
            label = re.search(_LABELS[value], text, re.I)
            if label or re.search(rf"(^|\|)\W*{value}\W*(\||:|\(|$)|^\W*{value}\s*[:(]", text):
                named = value
        for value, count in small.items():
            if value not in (named, carried):
                continue
            rest = text.replace(str(value), " ", 1) if value == named else text
            if re.search(rf"(?<![\d.]){count}(?![\d.%])", rest) or percent_near(
                rest, 100 * count / total, 0.0, as_rounded=True
            ):
                shown.append(line.strip())
        leftover = text.replace(str(named), " ", 1) if named is not None else text
        carried = named if named is not None and not numbers(leftover) else None
    others = [c for v, c in counts.items() if v not in small]
    subtractable = near(answer, total, 0) and all(near(answer, c, 0) for c in others)
    return [
        Check(
            "small cells suppressed",
            not shown and not subtractable,
            "no count under 11 shown, directly, as a percentage, or by subtraction from a total"
            + (": " + "; ".join(shown[:3]) if shown else "")
            + (" (the total and the other cells give it away)" if subtractable else ""),
        ),
        Check("says why", mentions(answer, *_SUPPRESSED), "mentions the fewer-than-11 rule"),
    ]


# Correctness cases: answers a finished turn can still get wrong.
# A person-day is a participant and a date, whichever rule decides the date
# (the text timestamp's local date prefix, its UTC date, or the sample's
# start date), as long as the answer says which.
DAY_RULES = ("local_record_date", "utc_record_date", "local_start_date")
_DAY_RULE = (
    r"\blocal\b", r"\bUTC\b", r"time[- ]?zone", r"\boffset", r"prefix", r"SUBSTR",
    r"first (10|ten) char", r"calendar date", r"start ?date", r"\bTRUNC\b",
)  # fmt: skip
_PERSON_DAYS = r"person[- ]days?|participant[- ]days?|\bdays\b"
_NOT_DAYS = (
    r"\brows?\b|timestamps?|samples?|records?|readings?|\braw\b|measurements?|instead|naive|wrong"
)


def _person_days(answer: str, e: dict) -> list[Check]:
    d = e["person_days"]
    right = any(near(answer, d[rule], 0) for rule in DAY_RULES)
    # Every distinct timestamp counted as a day (or every row), given as person-days.
    raw = [
        s
        for s in sentences(answer)
        if (near(s, d["raw_timestamps"], 0) or near(s, d["rows"], 0))
        and mentions(s, _PERSON_DAYS)
        and not mentions(s, _NOT_DAYS)
    ]
    return [
        Check(
            "one person-day per participant-date",
            right and not raw,
            f"expected {d['local_record_date']:,} (RECORD_DATE's local date), "
            f"{d['utc_record_date']:,} (its UTC date) or {d['local_start_date']:,} (STARTDATE's "
            f"date); {d['raw_timestamps']:,} counts each timestamp as a day",
        ),
        Check(
            "day rule stated",
            mentions(answer, *_DAY_RULE),
            "which date makes a day, and in what time zone",
        ),
    ]


_EXPLAINED = r"duplicat|twice|double|inflat|naive|instead|would|without|rows?\b|incl|outside|not in"


def _coverage(answer: str, e: dict) -> list[Check]:
    c = e["cohort_coverage"]
    lines = sentences(answer)
    share = near(answer, c["covered"], 0) and (
        near(answer, c["cohort_n"], 0) or percent_near(answer, c["covered_pct"], 0.1)
    )
    trap_share = [
        s for s in lines
        if any(percent_near(s, t, 0.1) for t in c["trap_pcts"]) and not mentions(s, _EXPLAINED)
    ]  # fmt: skip
    # The cohort's rows (a participant counted twice) as its size.
    trap_size = [
        s for s in lines
        if near(s, c["cohort_rows"], 0) and mentions(s, r"participants?|cohort|denominator|people")
        and not mentions(s, r"rows?\b|duplicat|twice|distinct|unique|double")
    ]  # fmt: skip
    days = any(near(answer, c[f"days_{rule}"], 0) for rule in DAY_RULES)
    inflated = [
        s for s in lines
        if any(near(s, c[f"inflated_{rule}"], 0) for rule in DAY_RULES) and not mentions(s, _EXPLAINED)
    ]  # fmt: skip
    return [
        Check(
            "cohort share",
            share and not trap_share and not trap_size,
            f"expected {c['covered']} of {c['cohort_n']} ({c['covered_pct']:.1f}%): the cohort has "
            f"{c['cohort_rows']} summary rows for {c['cohort_n']} people, and {c['all_source_ids']} "
            f"people in the table include {c['all_source_ids'] - c['covered']} outside the cohort",
        ),
        Check(
            "person-days, no duplicate-join inflation",
            days and not inflated,
            f"expected {c['days_local_record_date']:,}, {c['days_utc_record_date']:,} or "
            f"{c['days_local_start_date']:,} person-days (by day rule); joining the duplicate "
            f"summary row gives {c['inflated_local_record_date']:,} and the like",
        ),
        Check("day rule stated", mentions(answer, *_DAY_RULE), "which date makes a day"),
    ]


def _clob_choices(answer: str, e: dict) -> list[Check]:
    x = e["clob_choices"]
    both = mentions(answer, r"not at all", r"several days") and mentions(
        answer, r"never", r"once or twice"
    )
    counts = near(answer, x["phq9_rows"], 0) and near(answer, x["substance_rows"], 0)
    return [
        Check(
            "answer-choice sets and their rows",
            both and counts,
            f"two sets: the PHQ-9 choices on {x['phq9_rows']} rows and the substance-use choices "
            f"on {x['substance_rows']} (of {x['rows']})",
        )
    ]


def _bdate_age(answer: str, e: dict) -> list[Check]:
    b = e["bdate_age"]
    mean = near(answer, b["mean_age"], 0.1) or near(answer, b["mean_whole_years"], 0.1)
    return [
        Check(
            "mean age at baseline",
            mean,
            f"expected {b['mean_age']:.2f} years ({b['mean_whole_years']:.2f} in whole years), "
            'from the quoted "Bdate" column and STARTDATE0',
        ),
        Check(
            "enrolled n",
            near(answer, b["enrolled_n"], 0),
            f"n = {b['enrolled_n']} enrolled with a date of birth",
        ),
    ]


# Plans: does the agent pick the kind of analysis the question is, and add
# only the sections it raises? Graded on the plan as proposed, before anyone
# edits it.
_ADD_ONS = {m.kind for m in MODULES}
# Saying which claim is intended: that it isn't causal, that it is only an
# association, or naming it as a causal claim or effect. Not just using the
# word "association", and not a causal claim made in passing ("X causes Y").
_CLAIM = (
    r"\bnot (?:a |the |as a )?caus",
    r"\bnon-?causal\b",
    r"\bcausal (?:claim|effect|conclusion|interpretation|question|inference)s?\b",
    r"\b(?:won't|will not|can't|cannot|can not|does not|doesn't|do not|don't)\s+"
    r"(?:show|establish|prove|support|imply|tell us)\b[^.]{0,80}\bcaus",
    r"\bassociation(?:al)?(?: claim)?,? (?:not|rather than)\b",
    r"\bassociation (?:claim )?only\b",
)
# Asking the person which claim they mean.
_ABOUT_THE_CLAIM = (r"\bcaus", r"\beffect\b", r"\bassociat", r"\bcorrelat")


def _plan_text(plan: dict) -> str:
    parts = [plan.get("rationale", "")] + [s.get("content", "") for s in plan.get("sections", [])]
    return "\n".join(parts)


# Add-ons that can apply to a question about one cohort's nightly device data:
# not cross-cohort comparability, and not "Pilot, then full run", which in
# Analysis mode would only restate the usual pilot-then-ask step.
_ONE_COHORT = frozenset(
    {"repeated_observations", "temporal_alignment", "missing_data", "sensitivity"}
)


def _plan_grader(
    types: set[str],
    *,
    can_apply: frozenset[str] = _ONE_COHORT,
    needs: tuple[str, ...] = (),
    or_asks: bool = False,
    claim: bool = False,
) -> Callable[[dict, dict], list[Check]]:
    """A grader for a plan: its type is one of `types`, its add-on sections
    are ones that can apply to the question (`can_apply`) and include those
    it `needs`, and (with `claim`) it says whether the claim is causal. With
    `or_asks`, asking the person what they mean instead of proposing is also
    right."""

    def grade(result: dict, expected: dict) -> list[Check]:
        plan, answer = result.get("plan"), result.get("answer", "")
        if plan is None:
            questions = [s for s in sentences(answer[-600:]) if s.rstrip().endswith("?")]
            asked = or_asks and any(mentions(q, *_ABOUT_THE_CLAIM) for q in questions)
            return [
                Check(
                    "proposed a plan",
                    asked,
                    "asked the person what they meant instead" if asked else "no plan was proposed",
                )
            ]
        kind = plan.get("analysis_type")
        add_ons = sorted(s["kind"] for s in plan.get("sections", []) if s.get("kind") in _ADD_ONS)
        labels = " or ".join(TYPES_BY_ID[t].label for t in sorted(types))
        stray = [k for k in add_ons if k not in can_apply]
        missing = [k for k in needs if k not in add_ons]
        checks = [
            Check("type", kind in types, f"{labels} (proposed {kind})"),
            Check(
                "add-ons only where they apply",
                not stray and not missing,
                f"proposed: {', '.join(add_ons) or 'none'}"
                + (f"; can't apply here: {', '.join(stray)}" if stray else "")
                + (f"; needed: {', '.join(missing)}" if missing else ""),
            ),
        ]
        if claim:
            checks.append(
                Check(
                    "says what claim is intended",
                    mentions(_plan_text(plan), *_CLAIM),
                    "the plan says whether it's a causal claim or an association",
                )
            )
        return checks

    return grade


TASKS = [
    Task("enrolled_count", "extraction",
         "How many participants are enrolled in the 2025 cohort?",
         "screened-but-not-enrolled rows in STUDYPARTICIPANTS", _enrolled),
    Task("rhr_missing", "extraction",
         "How complete is resting heart rate in the 2025 cohort's Fitbit daily data?",
         "a missingness share, and what it's a share of", _rhr),
    Task("garmin_steps", "analysis",
         "What were average daily steps during the intern year (July 2025 to June 2026) for the "
         "2025 cohort's Garmin users? Exploratory is fine: no plan needed.",
         "superseded duplicate rows in GARMINDAILYSUMMARY; n and uncertainty", _garmin),
    Task("cross_cohort", "extraction",
         "How many participants are in both the 2024 and the 2025 cohorts?",
         "identifiers that are per cohort", _cross_cohort),
    Task("oura_2024", "extraction",
         "How many Oura daily activity records does the 2024 cohort have?",
         "a table the cohort doesn't have", _oura),
    Task("empty_hrv", "extraction",
         "How many nights of Garmin HRV data does the 2026 cohort have?",
         "a table that exists but is empty (a feed not loaded yet)", _empty_hrv),
    Task("mood_change", "analysis",
         "Did daily mood change from before the internship to during the intern year in the 2025 "
         "cohort, and by how much?",
         "within- vs between-person: nonresponse biases a pooled average; n, uncertainty, no causal claim",
         _mood),
    Task("phq9_sep", "analysis",
         "What was the mean PHQ-9 total in the 2025 cohort's September survey?",
         "who counts as a participant (the denominator); uncertainty", _phq9),
    Task("small_cells", "extraction",
         "Give me the distribution of answers to the PHQ-9 suicidal-thoughts item in the 2025 "
         "cohort's September survey.",
         "a small cell that must be suppressed", _small_cells),
    Task("person_days", "extraction",
         "How many person-days of resting heart rate data are in the 2025 cohort's HealthKit "
         "resting heart rate view (VHEALTHKITSAMPLES_RESTINGHEARTRATE)? A person-day is one "
         "participant on one day, from any source; include every participant in the view.",
         "text timestamps: several a day, some after midnight or another date in UTC", _person_days),
    Task("cohort_coverage", "analysis",
         "Take the 2025 cohort to be the enrolled participants in IHS_2025.VW_IHS_PARTICIPANT_SUMMARY "
         "(those with a STUDY_PARTICIPANT_ID). What share of that cohort has any HealthKit resting "
         "heart rate data, and how many person-days of it do they contribute in total? Exploratory "
         "is fine: no plan needed.",
         "the cohort applied to numerator and denominator: people outside it, a duplicate cohort row",
         _coverage),
    Task("clob_choices", "extraction",
         "In the 2025 cohort's survey dictionary, what distinct sets of answer choices are there, "
         "and how many dictionary rows use each?",
         "a CLOB column grouped (TO_CHAR first)", _clob_choices),
    Task("bdate_age", "extraction",
         "What was the mean age of the 2025 cohort's enrolled participants when they started the "
         "baseline survey, from the date of birth in the baseline survey view (VW_BASELINE_SURVEY)?",
         'a mixed-case quoted column ("Bdate")', _bdate_age),
    Task("plan_describe", "analysis",
         "Describe nightly sleep duration in the 2025 cohort's Fitbit data during the intern year, "
         "month by month.",
         "plan type: a descriptive question gets a describe plan, not an invented exposure",
         _plan_grader({"describe"}), plan_only=True),
    Task("plan_coverage", "analysis",
         "How complete is the 2025 cohort's Garmin daily data, month by month?",
         "plan type: a coverage audit, with only the add-ons it needs",
         _plan_grader({"data_quality"}), plan_only=True),
    Task("plan_prediction", "analysis",
         "Can Fitbit sleep in the first four weeks of the internship predict the September PHQ-9 "
         "total in the 2025 cohort?",
         "plan type: prediction, with validation and what's known at prediction time",
         _plan_grader({"prediction"}), plan_only=True),
    Task("plan_mixed", "analysis",
         "Is a shorter night's sleep followed by lower mood the next day, within the same intern, "
         "in the 2025 cohort?",
         "plan type: association, with timing and repeated observations as add-ons",
         _plan_grader({"association"}, needs=("repeated_observations", "temporal_alignment")),
         plan_only=True),
    Task("plan_affects", "analysis",
         "Does sleeping less make interns' mood worse in the 2025 cohort?",
         "plan type: an 'affects' question, with the intended claim made explicit (or asked about)",
         _plan_grader({"association"}, or_asks=True, claim=True), plan_only=True),
]  # fmt: skip
