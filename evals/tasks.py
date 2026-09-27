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
    rf"|(?:{_NEGATION}|\bno\b)(?:\s+(?:a|an|any|causal|direct|strong|good|clear))*\s+(?:evidence|proof)\b"
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


# Plans: does the agent pick the kind of analysis the question is, and add
# only the sections it raises? Graded on the plan as proposed, before anyone
# edits it.
_ADD_ONS = {m.kind for m in MODULES}
_CAUSAL = (r"\bcaus", r"\bnot (?:a )?(?:causal|cause)", r"\bassociation\b", r"\bconfound")


def _plan_text(plan: dict) -> str:
    parts = [plan.get("rationale", "")] + [s.get("content", "") for s in plan.get("sections", [])]
    return "\n".join(parts)


def _plan_grader(
    types: set[str],
    *,
    max_add_ons: int = 2,
    needs: tuple[str, ...] = (),
    or_asks: bool = False,
    claim: bool = False,
) -> Callable[[dict, dict], list[Check]]:
    """A grader for a plan: its type is one of `types`, it has the add-on
    sections in `needs` and no more than `max_add_ons` in all, and (with
    `claim`) it says whether the claim is causal. With `or_asks`, asking the
    person what they mean instead of proposing is also right."""

    def grade(result: dict, expected: dict) -> list[Check]:
        plan, answer = result.get("plan"), result.get("answer", "")
        if plan is None:
            asked = or_asks and "?" in answer[-600:]
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
        checks = [
            Check("type", kind in types, f"{labels} (proposed {kind})"),
            Check(
                "add-ons only where they apply",
                len(add_ons) <= max_add_ons and all(k in add_ons for k in needs),
                f"at most {max_add_ons}"
                + (f", including {', '.join(needs)}" if needs else "")
                + f" (proposed: {', '.join(add_ons) or 'none'})",
            ),
        ]
        if claim:
            checks.append(
                Check(
                    "says what claim is intended",
                    mentions(_plan_text(plan), *_CAUSAL),
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
         _plan_grader({"association"}, max_add_ons=4,
                      needs=("repeated_observations", "temporal_alignment")), plan_only=True),
    Task("plan_affects", "analysis",
         "Does sleeping less make interns' mood worse in the 2025 cohort?",
         "plan type: an 'affects' question, with the intended claim made explicit (or asked about)",
         _plan_grader({"association"}, or_asks=True, claim=True), plan_only=True),
]  # fmt: skip
