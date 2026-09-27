"""The kinds of analysis plan, and the sections each one has.

One registry, versioned. The plan check below, the `propose_plan` tool's
description, the chat's plan card (through /api/plan-schema), and the rigor
review all read it, so there's no second list of sections to drift.

Every plan has four core sections. Its type adds the sections that matter
for that kind of question, and add-on sections cover things that apply
across types (repeated observations, timing, missing data) when they apply.
Up to three sections of the agent's or the person's own hold anything else.

A plan that passes these checks is well formed and reviewable. That doesn't
make the analysis appropriate or correct: the person and the review judge
that. Safety rules (privacy, exports, the database) are enforced elsewhere
and never depend on what a plan says.

Plans approved before this registry existed are version 1: seven fixed
parts (V1_LABELS). They're read and shown exactly as they were frozen.
"""

from __future__ import annotations

import contextlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from datalab.sessions.approvals import Unshowable, visible_text

SCHEMA_VERSION = 2
ADDITIONAL = "additional"  # the kind of a section with its own title

MAX_SECTION = 2000
MAX_TITLE = 80
MAX_RATIONALE = 300
MAX_REASON = 500
MAX_ADDITIONAL = 3
MAX_SECTIONS = 20
# All of a plan's text together. One or two screens is the aim; this keeps a
# plan reviewable and lets the rigor review see every plan whole.
MAX_PLAN = 12000


class PlanInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Section:
    kind: str
    label: str
    guidance: str  # what to write in it
    review: str = ""  # for an add-on: what the rigor review checks when it's in the plan


@dataclass(frozen=True)
class AnalysisType:
    id: str
    label: str
    summary: str  # when it fits
    required: tuple[str, ...]
    optional: tuple[str, ...]
    checks: str  # what its Checks and limitations section must address
    review: str  # what the rigor review checks for this type


CORE = (
    Section(
        "question_and_purpose",
        "Question and purpose",
        "The question in plain words, and what the answer will help decide.",
    ),
    Section(
        "data_and_scope",
        "Data and scope",
        "Cohorts and population, time window, tables and columns, the unit of analysis, and "
        "who or what is included or excluded. Say which of these you've checked and which "
        "are assumptions.",
    ),
    Section(
        "checks_and_limitations",
        "Checks and limitations",
        "The checks this question needs before its results can be interpreted, the "
        "limitations you expect, and choices still open. Specific to this question, not "
        "boilerplate.",
    ),
    Section(
        "deliverables",
        "Deliverables",
        "What you'll produce (a report, figure, table, or dataset) and how you'll know it's done.",
    ),
)

TYPE_SECTIONS = (
    Section(
        "measures",
        "Measures and summaries",
        "Each measure, with its table and column, and how it's defined and summarised.",
    ),
    Section(
        "comparison",
        "Comparison groups",
        "The groups compared, the reference group, and how differences are shown.",
    ),
    Section(
        "target_quantity",
        "Target quantity (estimand)",
        "Exactly what's estimated and the claim it supports: for example, the within-person "
        "association between nightly sleep and next-day mood. Say plainly whether the "
        "question is about cause and effect.",
    ),
    Section(
        "method",
        "Method and adjustment",
        "The analysis method, each adjustment variable and why it's there, and how "
        "uncertainty is estimated.",
    ),
    Section(
        "prediction_target",
        "Prediction target and horizon",
        "What's predicted, for whom, and how far ahead.",
    ),
    Section(
        "available_information",
        "Information available at prediction time",
        "The predictors, and why each would be known at the moment a prediction is made.",
    ),
    Section(
        "validation",
        "Validation and performance",
        "How the model is validated (how the data are split, and how leakage is prevented), "
        "the performance measures, and what would count as useful.",
    ),
    Section(
        "expected_structure",
        "Expected structure and rules",
        "What the data should look like: the expected records and ranges, and the rules "
        "they're checked against.",
    ),
    Section(
        "assessment",
        "Assessment method",
        "How each rule is checked and how coverage or completeness is measured and broken "
        "down (by month, cohort, or device, for example).",
    ),
    Section(
        "flag_handling",
        "Flagged records",
        "What happens to records that break a rule: how they're counted and reported, and "
        "whether any are excluded or corrected.",
    ),
    Section(
        "approach",
        "Proposed approach",
        "The approach, and why it suits this question and these data.",
    ),
)

MODULES = (
    Section(
        "repeated_observations",
        "Repeated observations",
        "How several observations per person (or per day or device) are handled in the "
        "estimates and their uncertainty.",
        "Repeated observations: do the estimates and their uncertainty account for several "
        "observations per person, as the plan says?",
    ),
    Section(
        "temporal_alignment",
        "Timing and alignment",
        "How measures are lined up in time (which night goes with which day, time zones, "
        "lags), and what happens with gaps.",
        "Timing: are measures lined up in time as the plan says, with no later information "
        "used for an earlier moment?",
    ),
    Section(
        "cross_cohort",
        "Comparability across cohorts",
        "Whether measures mean the same thing in each cohort (instruments, devices, years), "
        "and how differences are handled.",
        "Cohorts: are cohorts compared only on measures the plan shows are comparable, with "
        "differences handled as it says?",
    ),
    Section(
        "missing_data",
        "Missing data",
        "How much is expected to be missing, why it may be missing, and how it's handled.",
        "Missing data: is the amount missing reported, and handled as the plan says?",
    ),
    Section(
        "sensitivity",
        "Sensitivity analyses",
        "The discretionary choices whose effect on the result will be checked, and how.",
        "Sensitivity: were the sensitivity analyses the plan names run and reported?",
    ),
    Section(
        "pilot_to_full",
        "Pilot, then full run",
        "Only when the pilot settles something this plan leaves open (a threshold, a model, "
        "whether the full run is worth doing): what it covers, and what it must show. Analysis "
        "mode always pilots first and asks before the full run, so don't add this to say that.",
        "Pilot: did the full run start only once the pilot showed what the plan asked of it?",
    ),
)

TYPES = (
    AnalysisType(
        "describe",
        "Describe or compare",
        "Describe measures, or compare them across groups, without estimating an effect or "
        "predicting.",
        required=("measures",),
        optional=("comparison",),
        checks="How the summaries could mislead (small groups, uneven coverage), and keeping "
        "comparisons descriptive.",
        review="Description: are the summaries the plan names reported, with group sizes, and "
        "without claims of association or cause?",
    ),
    AnalysisType(
        "association",
        "Association or estimation",
        "Estimate how one thing relates to another.",
        required=("target_quantity", "measures", "method"),
        optional=(),
        checks="Uncertainty that fits the data's dependence structure, and confounding. If the "
        "question implies cause and effect, what this design can and can't support.",
        review="Estimation: is what was estimated the target quantity the plan names, with the "
        "adjustment and uncertainty it describes?",
    ),
    AnalysisType(
        "prediction",
        "Prediction",
        "Predict an outcome from information available earlier.",
        required=("prediction_target", "available_information", "validation"),
        optional=(),
        checks="How validation prevents information leakage, from the future or from the "
        "test data into fitting.",
        review="Prediction: was performance measured on data kept out of fitting, as the plan "
        "says, using only information available at prediction time?",
    ),
    AnalysisType(
        "data_quality",
        "Data quality or coverage",
        "Check completeness, coverage, or whether the data follow expected rules.",
        required=("expected_structure", "assessment", "flag_handling"),
        optional=(),
        checks="What these checks can't detect.",
        review="Data quality: were the rules the plan names checked, and flagged records "
        "counted and handled as it says?",
    ),
    AnalysisType(
        "other",
        "Other analysis",
        "Anything the types above don't fit. Name what else it needs in additional sections.",
        required=("approach",),
        optional=(),
        checks="Whatever this approach needs checked before its results are interpreted.",
        review="",
    ),
)

SECTIONS: dict[str, Section] = {s.kind: s for s in (*CORE, *TYPE_SECTIONS, *MODULES)}
TYPES_BY_ID: dict[str, AnalysisType] = {t.id: t for t in TYPES}
_CORE_KINDS = tuple(s.kind for s in CORE)
_MODULE_KINDS = tuple(s.kind for s in MODULES)

# Version 1: the fixed parts every plan had, with the labels they were shown with.
V1_LABELS: dict[str, str] = {
    "question": "Question",
    "estimand": "Estimand (what exactly is estimated)",
    "exposure": "Exposure or predictor",
    "outcome": "Outcome",
    "covariates": "Covariates and adjustment",
    "cohort": "Cohort, time window, and exclusions",
    "decisions": "Decisions expected along the way",
}

_TOP_FIELDS = {
    "schema_version",
    "analysis_type",
    "analysis_type_label",
    "rationale",
    "sections",
    "revises",
    "revision_reason",
    "proposed_after",
}
# How many of the tables queried before a plan it names (the rest are counted).
MAX_RECORDED_TABLES = 30
_SECTION_FIELDS = {"kind", "label", "content"}
# Row titles the card, the export, and the review's text use for a plan's own
# parts, so no section of the agent's or person's can take them.
RESERVED_TITLES = (
    "Type",
    "Revises",
    "Why this kind of analysis",
    "Why it changed",
    "What changes, and why",
)
_PLAN_ID = re.compile(r"pl_[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# Content that says nothing: a required section needs a reason instead.
_PLACEHOLDER = re.compile(r"(n/?a|none|not applicable|tbd|to be decided|-+|\.+)\.?", re.I)


def allowed_kinds(analysis_type: AnalysisType) -> tuple[str, ...]:
    """The registered sections a plan of this type can have, in the order they're shown."""
    return (*_CORE_KINDS, *analysis_type.required, *analysis_type.optional, *_MODULE_KINDS)


def required_kinds(analysis_type: AnalysisType) -> tuple[str, ...]:
    return (*_CORE_KINDS, *analysis_type.required)


def clean_plan(raw: Any, *, draft: bool = False) -> dict[str, Any]:
    """A version-2 plan as it will be shown, approved, and hashed, or PlanInvalid.

    The result has the registry's labels written in, so a frozen plan shows
    exactly as it was approved even if the registry changes later, and its
    sections in a fixed order: core, the type's own, add-ons, then the
    additional sections in the order given.

    A revision names the approved plan it replaces, by id and hash, so the
    hash of each version covers the version before it. A `draft` (a plan the
    person sends back unapproved) may leave sections unwritten.
    """
    if not isinstance(raw, dict):
        raise PlanInvalid("A plan must be a set of named parts.")
    unknown = sorted(set(raw) - _TOP_FIELDS)
    if unknown:
        raise PlanInvalid(f"A plan has no part called {unknown[0]!r}.")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        raise PlanInvalid(f"A new plan must have schema_version {SCHEMA_VERSION}.")
    analysis_type = TYPES_BY_ID.get(str(raw.get("analysis_type")))
    if analysis_type is None:
        raise PlanInvalid(f"A plan's analysis_type must be one of: {', '.join(TYPES_BY_ID)}.")
    if raw.get("analysis_type_label", analysis_type.label) != analysis_type.label:
        raise PlanInvalid("The plan's analysis_type_label doesn't match its type.")
    rationale = _text(raw.get("rationale", ""), "rationale")
    if len(rationale) > MAX_RATIONALE:
        raise PlanInvalid(f"The plan's rationale is longer than {MAX_RATIONALE} characters.")
    revises, reason = _revision(raw, draft)
    proposed_after = _proposed_after(raw.get("proposed_after"))

    sections = raw.get("sections", [])
    if not isinstance(sections, list):
        raise PlanInvalid("A plan's sections must be a list.")
    if len(sections) > MAX_SECTIONS:
        raise PlanInvalid(f"A plan can have at most {MAX_SECTIONS} sections.")
    allowed = allowed_kinds(analysis_type)
    registered: dict[str, str] = {}
    additional: list[dict[str, str]] = []
    for section in sections:
        kind, label, content = _section_parts(section)
        if kind == ADDITIONAL:
            if draft and not _text(content, "additional section").strip():
                continue  # started, not written: nothing to send back
            additional.append(_additional(label, content, additional))
            continue
        known = SECTIONS.get(kind)
        if known is None:
            raise PlanInvalid(
                f"There's no plan section called {kind!r}. Use an additional section, with "
                "its own title, for anything the registered sections don't cover."
            )
        if kind not in allowed:
            owners = [t.label for t in TYPES if kind in (*t.required, *t.optional)]
            raise PlanInvalid(
                f"{known.label} is a section of {' and '.join(owners)} plans, not "
                f"{analysis_type.label}. Change the type, or use an additional section."
            )
        if kind in registered:
            raise PlanInvalid(f"The plan has its {known.label} section twice.")
        if label is not None and label != known.label:
            raise PlanInvalid(f"The {kind} section's label must be {known.label!r}.")
        registered[kind] = _content(content, known.label)

    missing = [SECTIONS[k].label for k in required_kinds(analysis_type) if not registered.get(k)]
    if missing and not draft:
        raise PlanInvalid(
            f"A {analysis_type.label} plan needs these sections written: {', '.join(missing)}. "
            "If one can't be settled yet, say so there and say what it depends on."
        )
    ordered = [
        {"kind": k, "label": SECTIONS[k].label, "content": registered[k]}
        for k in allowed
        if registered.get(k)  # an optional section left empty isn't shown
    ]
    plan = {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": analysis_type.id,
        "analysis_type_label": analysis_type.label,
        "rationale": rationale,
        "sections": ordered + additional,
    }
    if revises:
        plan["revises"] = revises
        plan["revision_reason"] = reason
    if proposed_after is not None:
        plan["proposed_after"] = proposed_after
    size = len(rationale) + len(reason)
    size += sum(len(s["label"]) + len(s["content"]) for s in plan["sections"])
    if size > MAX_PLAN:
        raise PlanInvalid(
            f"The plan is {size} characters, more than the {MAX_PLAN} a plan can be. Keep "
            "each section to what the person needs to review."
        )
    return plan


def _proposed_after(record: Any) -> dict[str, Any] | None:
    """What had run in the conversation when the plan was proposed, as DataLab
    recorded it: {queries, tables, more_tables}, or None if it wasn't recorded."""
    if record is None:
        return None
    fields = {"queries", "tables", "more_tables"}
    counts = [record.get(k) for k in ("queries", "more_tables")] if isinstance(record, dict) else []
    if (
        not isinstance(record, dict)
        or set(record) != fields
        or not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts)
        or not isinstance(record["tables"], list)
        or len(record["tables"]) > MAX_RECORDED_TABLES
        or not all(isinstance(t, str) and 0 < len(t) <= 128 for t in record["tables"])
    ):
        raise PlanInvalid("A plan's record of what ran before it isn't in the expected form.")
    return {
        "queries": record["queries"],
        "tables": list(record["tables"]),
        "more_tables": record["more_tables"],
    }


def proposed_after_text(content: dict[str, Any]) -> str:
    """What had run when the plan was proposed, in a sentence."""
    record = content.get("proposed_after")
    if not isinstance(record, dict):
        return "Not recorded (this plan is from an earlier version of DataLab)."
    queries = record["queries"]
    if not queries:
        return "No queries had returned data in this conversation."
    tables = ", ".join(record["tables"]) or "no tables"
    if record["more_tables"]:
        tables += f", and {record['more_tables']} more"
    return (
        f"{queries} {'query' if queries == 1 else 'queries'} had already returned data in this "
        f"conversation, from {tables}. Results seen before a plan aren't prespecified by it."
    )


def _revision(raw: dict[str, Any], draft: bool) -> tuple[dict[str, str] | None, str]:
    """The approved plan this one revises, and why, if it's a revision."""
    revises = raw.get("revises")
    reason = _text(raw.get("revision_reason", ""), "reason for the revision")
    if revises is None:
        if reason:
            raise PlanInvalid("A reason for a revision needs the plan it revises.")
        return None, ""
    if (
        not isinstance(revises, dict)
        or set(revises) != {"plan_id", "sha256"}
        or not _PLAN_ID.fullmatch(str(revises["plan_id"]))
        or not _SHA256.fullmatch(str(revises["sha256"]))
    ):
        raise PlanInvalid("A revision names the plan it revises by its plan_id and sha256.")
    if len(reason) > MAX_REASON:
        raise PlanInvalid(f"The reason for the revision is longer than {MAX_REASON} characters.")
    if not reason and not draft:
        raise PlanInvalid("A revision needs its reason: what changes, and why.")
    return {"plan_id": revises["plan_id"], "sha256": revises["sha256"]}, reason


def plan_answer(
    proposed: dict[str, Any],
    approved: bool,
    edits: Any,
    change_type: str | None,
    revises_plan: dict[str, Any] | None = None,
) -> str:
    """What the person's answer on a proposed plan hands back, as JSON.

    Approved: the plan to freeze, as they left it; it must be complete, and
    still revise the plan it was proposed to revise (`revises_plan`), and
    change something in it. Not approved: "" for a
    bare no, or {edits, change_type}: their edits, if they're a usable draft
    that differs from the proposal, and the type they asked for instead. A
    "no" always counts, whatever comes with it.
    """
    if approved:
        if change_type:
            raise PlanInvalid("A plan sent back for another type of analysis can't be approved.")
        plan = clean_plan(edits if edits is not None else proposed)
        if revision_of(plan) != revision_of(proposed):
            raise PlanInvalid("Which approved plan this revises can't be changed here.")
        if plan.get("proposed_after") != proposed.get("proposed_after"):
            raise PlanInvalid("The record of what ran before the plan can't be changed.")
        if revises_plan is not None and same_plan(revises_plan, plan):
            raise PlanInvalid(
                "This revision doesn't change anything in the approved plan. Change what needs "
                "changing, or choose Not yet to keep the approved plan."
            )
        return json.dumps(plan)
    edited = None
    if edits is not None:
        with contextlib.suppress(PlanInvalid):
            draft = clean_plan(edits, draft=True)
            nothing_new = draft == proposed or not draft["sections"]
            same_links = revision_of(draft) == revision_of(proposed)
            same_record = draft.get("proposed_after") == proposed.get("proposed_after")
            if not nothing_new and same_links and same_record:
                edited = draft
    if change_type not in TYPES_BY_ID or change_type == proposed.get("analysis_type"):
        change_type = None
    if edited is None and change_type is None:
        return ""
    return json.dumps({"edits": edited, "change_type": change_type})


def revision_of(content: dict[str, Any]) -> dict[str, str] | None:
    """The plan this one revises ({plan_id, sha256}), or None."""
    revises = content.get("revises")
    return revises if isinstance(revises, dict) else None


def type_change_note(content: dict[str, Any], new_type: str) -> str:
    """What the agent needs to rewrite a plan as another type."""
    old, new = TYPES_BY_ID[content["analysis_type"]], TYPES_BY_ID[new_type]
    needed = [SECTIONS[k].label for k in new.required]
    kept = set(allowed_kinds(new))
    misfits = [s["label"] for s in content["sections"] if s["kind"] not in {*kept, ADDITIONAL}]
    note = (
        f"The person asked for a {new.label} plan instead of {old.label}. Propose it again "
        f"with analysis_type {new.id!r}, keeping their edits (persons_edits). A {new.label} "
        f"plan needs: {', '.join(needed)}."
    )
    if misfits:
        note += (
            f" These sections don't belong in it: {', '.join(misfits)}. Carry what still "
            "matters into the core sections or an additional section, and say in the "
            "rationale what you left out."
        )
    return note


def _section_parts(section: Any) -> tuple[str, str | None, Any]:
    if not isinstance(section, dict):
        raise PlanInvalid("Each plan section must have a kind and its content.")
    unknown = sorted(set(section) - _SECTION_FIELDS)
    if unknown:
        raise PlanInvalid(f"A plan section has no part called {unknown[0]!r}.")
    kind, label = section.get("kind"), section.get("label")
    if not isinstance(kind, str):
        raise PlanInvalid("Each plan section needs its kind.")
    if label is not None and not isinstance(label, str):
        raise PlanInvalid("A plan section's label must be text.")
    return kind, label, section.get("content", "")


def _additional(label: str | None, content: Any, earlier: list[dict[str, str]]) -> dict[str, str]:
    title = _text(label or "", "additional section's title")
    if not title:
        raise PlanInvalid("An additional section needs a title.")
    if "\n" in title or len(title) > MAX_TITLE:
        raise PlanInvalid(f"A section title must be one line of at most {MAX_TITLE} characters.")
    # Checked as typed too: normalizing turns some rare Greek letters into basic ones.
    typed = label or ""
    if any(c.isalpha() and not _title_letter(c) for c in title) or any(map(_rare_greek, typed)):
        # Letters from other scripts, and Greek's rarer letters (yot looks like j),
        # can look like Latin ones, and aren't needed here.
        raise PlanInvalid(
            f"The section title {title!r} has letters from outside the Latin alphabet and "
            "the basic Greek letters. Write it in those letters (accents are fine)."
        )
    taken = {_title_key(t) for t in (*(s.label for s in SECTIONS.values()), *RESERVED_TITLES)}
    taken |= {_title_key(s["label"]) for s in earlier}
    if _title_key(title) in taken:
        registered = next(
            (s for s in SECTIONS.values() if _title_key(s.label) == _title_key(title)), None
        )
        if registered is not None:
            raise PlanInvalid(
                f"{registered.label} is one of the plan's own sections: give it in `sections` "
                f"as {registered.kind!r}, not as an additional section."
            )
        raise PlanInvalid(f"The plan already has a section called {title!r}, or one like it.")
    if len(earlier) >= MAX_ADDITIONAL:
        raise PlanInvalid(f"A plan can have at most {MAX_ADDITIONAL} additional sections.")
    body = _content(content, title)
    if not body:
        raise PlanInvalid(f"The section {title!r} is empty. Write it, or remove it.")
    return {"kind": ADDITIONAL, "label": title, "content": body}


def _title_key(title: str) -> str:
    """A title as it reads, to compare with others: accents and punctuation
    dropped, case folded, and each letter that looks like a Latin letter (a
    Latin alpha, a dotless i, small capitals, Greek alpha or omicron) taken as
    that letter."""
    # The table first: decomposing can turn a look-alike into another letter.
    looked = "".join(_GREEK_LOOKALIKES.get(c, c) for c in title)
    bare = (c for c in unicodedata.normalize("NFKD", looked) if not unicodedata.combining(c))
    words = "".join(_skeleton(c) if c.isalnum() else " " for c in bare)
    # And the plain letters and digits that pass for each other.
    return " ".join(words.translate(_ASCII_LOOKALIKES).split())


# Plain letters and digits that pass for each other: I, l, 1; O, 0; u, v.
_ASCII_LOOKALIKES = str.maketrans("i1|0v", "lllou")


def _title_letter(char: str) -> bool:
    """Whether a title may use this letter: Latin, or basic Greek (U+0386 to U+03CE)."""
    if unicodedata.name(char, "").startswith("LATIN"):
        return True
    return "\u0386" <= char <= "\u03ce"


def _rare_greek(char: str) -> bool:
    """Greek letters outside the basic ones: archaic, symbol, and Coptic-era forms."""
    return "\u0370" <= char <= "\u0385" or "\u03cf" <= char <= "\u03ff"


# Greek letters that look like a Latin one.
_GREEK_LOOKALIKES = dict(
    zip(
        "αβγδεζηικμνοπρστυχωςϲϹϳͿϒϱϐϰΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",
        "abydeznikuvonpotuxwcccjjypbkabezhikmnoptyx",
        strict=True,
    )
)
# Latin letters named after the Greek letter they look like (Latin alpha, iota, ...).
_GREEK_NAMES = {"ALPHA": "a", "IOTA": "i", "UPSILON": "u", "OMEGA": "w", "GAMMA": "y"}


# Latin letters whose names don't say which letter they look like (a click,
# tone six, schwa).
_LATIN_LOOKALIKES = {"\u01c0": "l", "\u0185": "b", "\u0184": "b", "\u0259": "e", "\u018f": "e"}


def _skeleton(char: str) -> str:
    """The plain lower-case Latin letter a letter looks like, or the letter itself."""
    if char.isascii():
        return char.lower()
    if char in _GREEK_LOOKALIKES:
        return _GREEK_LOOKALIKES[char]
    if char in _LATIN_LOOKALIKES:
        return _LATIN_LOOKALIKES[char]
    name = unicodedata.name(char, "")
    if name.startswith("LATIN"):
        # "LATIN SMALL LETTER DOTLESS I", "LATIN LETTER SMALL CAPITAL A",
        # "LATIN SMALL LETTER L WITH STROKE": the last word before any "WITH".
        base = name.split(" WITH ")[0].split()[-1]
        if len(base) == 1:
            return base.lower()
        return _GREEK_NAMES.get(base, char.casefold())
    return char.casefold()


def same_plan(earlier: dict[str, Any], later: dict[str, Any]) -> bool:
    """Whether two version-2 plans say the same thing (ignoring any revision link)."""

    def said(plan: dict[str, Any]) -> tuple[Any, ...]:
        sections = [(s["kind"], s["label"], s["content"]) for s in plan.get("sections", [])]
        return plan.get("analysis_type"), plan.get("rationale"), sections

    return said(earlier) == said(later)


def _content(value: Any, label: str) -> str:
    text = _text(value, label)
    if len(text) > MAX_SECTION:
        raise PlanInvalid(f"The plan's {label} is longer than {MAX_SECTION} characters.")
    if _PLACEHOLDER.fullmatch(text):
        raise PlanInvalid(
            f"The plan's {label} says only {text!r}. Say why it doesn't apply, or leave it out "
            "if it's optional."
        )
    return text


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PlanInvalid(f"The plan's {label} must be text.")
    # The person reviews this text, so all of it must be visible.
    try:
        return visible_text(value)
    except Unshowable as error:
        raise PlanInvalid(f"The plan's {label}: {error}") from error


def sections_of(content: dict[str, Any]) -> list[tuple[str, str]]:
    """A frozen plan of either version, as (label, text) pairs, in order."""
    if content.get("schema_version") == SCHEMA_VERSION:
        return [(s["label"], s["content"]) for s in content.get("sections", [])]
    return [(label, content[name]) for name, label in V1_LABELS.items() if content.get(name)]


def type_label(content: dict[str, Any]) -> str:
    """The plan's type, as shown ("" for a version-1 plan, which had none)."""
    return str(content.get("analysis_type_label", ""))


def review_checks(content: dict[str, Any]) -> list[str]:
    """What the rigor review checks for this plan's type and add-on sections."""
    analysis_type = TYPES_BY_ID.get(str(content.get("analysis_type")))
    if content.get("schema_version") != SCHEMA_VERSION or analysis_type is None:
        return []
    kinds = {s["kind"] for s in content.get("sections", [])}
    checks = [analysis_type.review] if analysis_type.review else []
    return checks + [m.review for m in MODULES if m.kind in kinds]


def card_schema() -> dict[str, Any]:
    """The registry, for the chat's plan card."""
    return {
        "schema_version": SCHEMA_VERSION,
        "sections": [
            {"kind": s.kind, "label": s.label, "guidance": s.guidance} for s in SECTIONS.values()
        ],
        "core": list(_CORE_KINDS),
        "modules": list(_MODULE_KINDS),
        "types": [
            {
                "id": t.id,
                "label": t.label,
                "summary": t.summary,
                "required": list(t.required),
                "optional": list(t.optional),
                "checks": t.checks,
            }
            for t in TYPES
        ],
        "limits": {
            "section": MAX_SECTION,
            "title": MAX_TITLE,
            "rationale": MAX_RATIONALE,
            "reason": MAX_REASON,
            "additional": MAX_ADDITIONAL,
            "plan": MAX_PLAN,
        },
    }


def tool_description() -> str:
    """How to write a plan, for the propose_plan tool."""

    def line(kind: str) -> str:
        section = SECTIONS[kind]
        return f"  - {kind} ({section.label}): {section.guidance}"

    lines = [
        "Propose an analysis plan for the person to approve, before touching outcome data.",
        "",
        "Pick the one type that fits the question best, and say why in `rationale` (one "
        "sentence). Don't invent parts that don't apply: a descriptive or data-quality "
        "question has no exposure or outcome. If the question asks whether X affects Y, "
        "decide what claim is intended, and ask the person first when that changes the work.",
        "",
        "Every plan has four core sections, each its own argument:",
        *(line(k) for k in _CORE_KINDS),
        "",
        "Types (analysis_type), and the sections each adds, given in `sections` by kind:",
    ]
    for t in TYPES:
        lines.append(f"- {t.id} ({t.label}): {t.summary}")
        lines.extend(line(k) + " (required)" for k in t.required)
        lines.extend(line(k) + " (when it applies)" for k in t.optional)
        lines.append(f"  Checks and limitations must address: {t.checks}")
    lines += [
        "",
        "Add-on sections for any type, in `sections` too. Add one only when this question "
        "raises that issue; most plans need none, one, or two, and each is a commitment the "
        "person has to review:",
        *(line(k) for k in _MODULE_KINDS),
        "",
        f"additional_sections: up to {MAX_ADDITIONAL}, each a title and content, for what the "
        "sections above don't cover.",
        f"Write short prose or bullets. Limits: {MAX_SECTION} characters a section, "
        f"{MAX_PLAN} for the whole plan, plain visible text only. A required section that "
        "can't be settled yet says so and what it depends on; \"N/A\" alone isn't accepted.",
        "",
        "The person may edit the plan before approving it. Once approved it is frozen: label "
        "any later work outside it as exploratory.",
        "",
        "To change an approved plan, propose a revision: the whole plan as it should now be, "
        "with `revises` set to the approved plan's plan_id and `revision_reason` saying what "
        "changes and why (including anything you've already seen in the data that prompted "
        "it). The person sees what changed and approves the revision; the earlier version is "
        "kept as it was. For a new question, propose a new plan instead.",
    ]
    return "\n".join(lines)
