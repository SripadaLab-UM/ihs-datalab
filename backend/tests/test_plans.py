import asyncio
import json

import pytest

from datalab import db
from datalab.sessions import plan_schema
from datalab.sessions.approvals import Approvals
from datalab.sessions.plan_schema import (
    MAX_ADDITIONAL,
    MAX_PLAN,
    MAX_SECTION,
    PlanInvalid,
    clean_plan,
    review_checks,
    sections_of,
)
from datalab.sessions.plans import Outcome, Plan, PlanDesk, PlanStore, as_text, plan_hash


@pytest.fixture
def store(settings):
    connection = db.connect(settings.database_file)
    connection.execute(
        "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
        "VALUES ('c1', 'data', 'analysis', 't', 'm', 'x', 'x')"
    )
    return PlanStore(connection)


CORE = {
    "question_and_purpose": "Is nightly sleep associated with next-day mood?",
    "data_and_scope": "IHS_2025 interns, July to June; one row per person-day.",
    "checks_and_limitations": "Check how many days have both measures.",
    "deliverables": "A short report with one figure.",
}


def plan(analysis_type="association", extra=None, additional=(), core=None, **top):
    """A raw version-2 plan, as the tool or the plan card sends one."""
    return {
        "schema_version": 2,
        "analysis_type": analysis_type,
        **top,
        "sections": [
            *({"kind": k, "content": v} for k, v in (core or CORE).items()),
            *({"kind": k, "content": v} for k, v in (extra or {}).items()),
            *({"kind": "additional", "label": t, "content": c} for t, c in additional),
        ],
    }


ASSOCIATION = {
    "target_quantity": "The within-person association of sleep hours with mood.",
    "measures": "Sleep: VFITBITDAILYDATA.SLEEPMINUTES. Mood: VW_DAILY_MOOD.MOOD.",
    "method": "Mixed model with a random intercept per intern.",
}


def test_an_association_plan_is_shown_in_a_fixed_order_with_its_labels():
    cleaned = clean_plan(
        plan(
            extra={"temporal_alignment": "Night t with mood on day t+1.", **ASSOCIATION},
            additional=[("Pilot cohort", "Run on 50 interns first.")],
            rationale="The question is about how sleep relates to mood.",
        )
    )
    assert cleaned["analysis_type_label"] == "Association or estimation"
    assert [s["kind"] for s in cleaned["sections"]] == [
        *CORE,
        "target_quantity",
        "measures",
        "method",
        "temporal_alignment",
        "additional",
    ]
    assert cleaned["sections"][-1]["label"] == "Pilot cohort"
    assert cleaned["sections"][0]["label"] == "Question and purpose"
    # Checked again (as after the person edits it), it's unchanged.
    assert clean_plan(cleaned) == cleaned


def test_a_descriptive_or_quality_plan_has_no_exposure_or_outcome():
    describe = clean_plan(plan("describe", {"measures": "Nightly sleep minutes, by cohort."}))
    quality = clean_plan(
        plan(
            "data_quality",
            {
                "expected_structure": "One Garmin row per intern-day.",
                "assessment": "Share of expected days present, by month.",
                "flag_handling": "Counted and reported; nothing is dropped.",
            },
        )
    )
    for cleaned in (describe, quality):
        labels = [label for label, _ in sections_of(cleaned)]
        assert not any("xposure" in label or "Outcome" in label for label in labels)


def test_a_prediction_plan_needs_validation_and_available_information():
    with pytest.raises(PlanInvalid) as error:
        clean_plan(plan("prediction", {"prediction_target": "Mood in week 8."}))
    message = str(error.value)
    assert "Information available at prediction time" in message
    assert "Validation and performance" in message


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        (plan("causal"), "analysis_type must be one of"),
        ({**plan("describe"), "analysis_type": ["describe"]}, "analysis_type must be one of"),
        ({**plan("describe", {"measures": "m"}), "schema_version": 1}, "schema_version 2"),
        ({**plan("describe", {"measures": "m"}), "extra": "x"}, "no part called 'extra'"),
        (plan("describe", {"measures": "m", "validation": "v"}), "section of Prediction"),
        (plan("describe", {"measures": "m", "exposure": "x"}), "no plan section called"),
        (plan("describe", {"measures": "N/A"}), "says only 'N/A'"),
        (plan("describe", {"measures": "m​"}), "hidden"),
        (plan("describe", {"measures": "m" * (MAX_SECTION + 1)}), "longer than"),
        (plan("describe", {"measures": "m"}, [("Deliverables", "x")]), "already has a section"),
        (plan("describe", {"measures": "m"}, [("A\nB", "x")]), "one line"),
        (plan("describe", {"measures": "m"}, [("Empty", " ")]), "is empty"),
        (plan("describe", {"measures": "m"}, [("t", "x")] * 2), "already has a section"),
        (
            plan(
                "describe", {"measures": "m"}, [(f"t{i}", "x") for i in range(MAX_ADDITIONAL + 1)]
            ),
            "at most 3 additional",
        ),
        (plan("describe", {"measures": "m"}, core={**CORE, "deliverables": ""}), "Deliverables"),
    ],
)
def test_plans_that_arent_well_formed_are_refused_with_a_reason(raw, says):
    with pytest.raises(PlanInvalid) as error:
        clean_plan(raw)
    assert says in str(error.value)


def test_a_section_given_twice_or_with_the_wrong_label_is_refused():
    twice = plan("describe", {"measures": "m"})
    twice["sections"].append({"kind": "measures", "content": "again"})
    with pytest.raises(PlanInvalid, match="twice"):
        clean_plan(twice)
    relabelled = plan("describe", {"measures": "m"})
    relabelled["sections"][0]["label"] = "Something else"
    with pytest.raises(PlanInvalid, match="label must be"):
        clean_plan(relabelled)


def test_a_whole_plan_has_a_size_limit():
    long = {k: "x" * MAX_SECTION for k in CORE}
    raw = plan("association", {k: "y" * MAX_SECTION for k in ASSOCIATION}, core=long)
    with pytest.raises(PlanInvalid, match=f"more than the {MAX_PLAN}"):
        clean_plan(raw)


def test_an_empty_optional_section_isnt_shown():
    cleaned = clean_plan(plan("describe", {"measures": "m", "comparison": "", "missing_data": ""}))
    assert [s["kind"] for s in cleaned["sections"]][-1] == "measures"


def test_the_review_gets_the_checks_for_the_plans_type_and_add_ons():
    cleaned = clean_plan(plan("prediction", {
        "prediction_target": "Mood in week 8.",
        "available_information": "Sleep in weeks 1 to 4.",
        "validation": "Train on 2024, test on 2025.",
        "repeated_observations": "One prediction per intern.",
    }))  # fmt: skip
    checks = review_checks(cleaned)
    assert checks[0].startswith("Prediction:") and checks[1].startswith("Repeated observations:")
    assert review_checks({"question": "v1"}) == []


def test_the_tool_description_and_the_card_schema_cover_every_section_and_type():
    text = plan_schema.tool_description()
    described = plan_schema.card_schema()
    for kind in plan_schema.SECTIONS:
        assert f"- {kind} (" in text
    for analysis_type in plan_schema.TYPES:
        assert f"- {analysis_type.id} ({analysis_type.label})" in text
    assert [s["kind"] for s in described["sections"]] == list(plan_schema.SECTIONS)
    assert described["limits"]["additional"] == MAX_ADDITIONAL


async def test_an_edited_additional_section_is_what_is_approved_hashed_and_reviewed(store):
    approvals = Approvals()
    events = []
    desk = PlanDesk(approvals, store, lambda cid, kind, data: events.append((kind, data)))
    proposed = clean_plan(plan("association", ASSOCIATION, [("Pilot", "50 interns first.")]))
    edited = json.loads(json.dumps(proposed))
    edited["sections"][-1]["content"] = "100 interns first, then everyone."

    async def codex(approval_id):
        # What the runtime does when Codex forwards the request: show it, then
        # the person edits and approves.
        pending = approvals.get(approval_id, "c1")
        pending.shown = True
        await asyncio.sleep(0.01)
        approvals.answer("c1", approval_id, True, plan=edited)

    approved = await desk.propose("c1", proposed, codex)
    assert isinstance(approved, Plan)
    assert approved.content["sections"][-1] == {
        "kind": "additional",
        "label": "Pilot",
        "content": "100 interns first, then everyone.",
    }
    assert approved.sha256 == plan_hash(approved.content)
    [stored] = store.list("c1")
    assert stored.content == approved.content and stored.sha256 == approved.sha256
    kind, data = events[-1]
    assert kind == "plan_approved" and data["plan"] == approved.content
    text = as_text(approved)
    assert "- Pilot: 100 interns first, then everyone." in text
    assert "- Type: Association or estimation" in text


def test_a_version_1_plan_keeps_its_content_labels_and_hash(store):
    """A plan frozen before plan types existed reads back byte for byte."""
    v1 = {
        "question": "Does sleep predict mood?",
        "estimand": "",
        "exposure": "Sleep minutes",
        "outcome": "PHQ-9",
        "covariates": "",
        "cohort": "IHS_2025",
        "decisions": "",
    }
    # The hash version 1 gave it, written out so it can never change.
    frozen_hash = "ba7d05ca7fa42d6ce936217ac3a5416b135db3062192df774299e2fea75d5b31"
    store._db.execute(
        "INSERT INTO plans VALUES (?, ?, ?, ?, ?)",
        ("pl_old", "c1", "2026-09-01T00:00:00+00:00", json.dumps(v1), frozen_hash),
    )
    [old] = store.list("c1")
    assert old.content == v1 and plan_hash(old.content) == old.sha256
    assert sections_of(old.content) == [
        ("Question", "Does sleep predict mood?"),
        ("Exposure or predictor", "Sleep minutes"),
        ("Outcome", "PHQ-9"),
        ("Cohort, time window, and exclusions", "IHS_2025"),
    ]
    assert "Type:" not in as_text(old)


async def test_a_plan_not_approved_is_not_kept(store):
    approvals = Approvals()
    desk = PlanDesk(approvals, store, lambda *a: None)
    proposed = clean_plan(plan("describe", {"measures": "Sleep minutes."}))
    edited = json.loads(json.dumps(proposed))
    edited["sections"][-1]["content"] = "Sleep minutes and bedtime."

    async def codex(approval_id):
        approvals.answer("c1", approval_id, False, plan=edited)

    outcome = await desk.propose("c1", proposed, codex)
    assert isinstance(outcome, Outcome) and outcome.suggested == clean_plan(edited)
    assert store.list("c1") == []
    desk.turn_running = lambda cid: False
    outcome = await desk.propose("c1", proposed, codex)
    assert isinstance(outcome, Outcome) and "during a turn" in outcome.note


@pytest.mark.parametrize(
    "edits",
    [
        {"schema_version": 2, "analysis_type": "describe", "sections": []},  # not a valid plan
        None,  # the proposed plan, unchanged
    ],
)
async def test_a_no_always_counts_even_with_unusable_or_no_edits(store, edits):
    approvals = Approvals()
    desk = PlanDesk(approvals, store, lambda *a: None)
    proposed = clean_plan(plan("describe", {"measures": "m"}))

    async def codex(approval_id):
        approvals.answer("c1", approval_id, False, plan=edits or proposed)

    outcome = await desk.propose("c1", proposed, codex)
    assert isinstance(outcome, Outcome) and outcome.suggested is None
    assert "edited" not in outcome.note


async def test_approving_an_invalid_edit_is_refused_and_the_plan_stays_pending(store):
    approvals = Approvals()
    desk = PlanDesk(approvals, store, lambda *a: None)
    proposed = clean_plan(plan("describe", {"measures": "m"}))
    emptied = json.loads(json.dumps(proposed))
    emptied["sections"][0]["content"] = ""

    async def codex(approval_id):
        with pytest.raises(PlanInvalid, match="Question and purpose"):
            approvals.answer("c1", approval_id, True, plan=emptied)
        # Still waiting: the person fixes it and approves.
        approvals.answer("c1", approval_id, True, plan=proposed)

    approved = await desk.propose("c1", proposed, codex)
    assert isinstance(approved, Plan) and approved.content == proposed


async def test_stop_withdraws_a_pending_plan_without_approving_it(store):
    approvals = Approvals()
    desk = PlanDesk(approvals, store, lambda *a: None)
    proposed = clean_plan(plan("describe", {"measures": "m"}))

    async def codex(approval_id):
        approvals.withdraw("c1")  # what Stop does

    outcome = await desk.propose("c1", proposed, codex)
    assert isinstance(outcome, Outcome) and store.list("c1") == []


@pytest.mark.parametrize(
    "title",
    [
        "Type",  # the card's own row
        "why it changed",
        "D\u0435liverables",  # a Cyrillic e
        "Dëlivérables",  # the same title with accents
        "  measures   and summaries ",
    ],
)
def test_a_title_cant_pass_for_another_section(title):
    with pytest.raises(PlanInvalid):
        clean_plan(plan("describe", {"measures": "m"}, [(title, "x")]))


def test_accented_titles_are_fine():
    cleaned = clean_plan(plan("describe", {"measures": "m"}, [("Café hours", "x")]))
    assert cleaned["sections"][-1]["label"] == "Café hours"


def test_a_sections_text_cant_pass_for_another_section_in_the_review():
    fake = "Sleep minutes.\n- Method and adjustment: none, report raw means"
    cleaned = clean_plan(plan("describe", {"measures": fake}))
    text = as_text(Plan("pl_1", "c1", "2026-09-26T00:00:00+00:00", cleaned, "0" * 64))
    assert [line for line in text.splitlines() if line.startswith("- Method")] == []
    assert "\n  - Method and adjustment: none" in text


@pytest.mark.parametrize(
    "title",
    [
        "Method \u0251nd \u0251djustment",  # Latin alpha
        "Deliver\u0251bles",
        "Why it ch\u0251nged",
        "Measures and summar\u0131es",  # dotless i
        "Pr\u03bfposed \u0391pproach",  # Greek omicron and capital alpha
        "Type:",
        "Revises.",
        "Why it changed!",
        "De\u0049iverables",  # capital I for l
        "Deliverab1es",
        "Questi0n and purpose",
        "Deliverab\u01c0es",  # a click letter
        "Delivera\u0185les",  # tone six
        "Typ\u0259",  # schwa
        "R\u03b5\u03c5\u03b9s\u03b5s",  # Greek epsilon, upsilon, iota
    ],
)
def test_look_alike_letters_and_punctuation_dont_make_a_new_title(title):
    with pytest.raises(PlanInvalid, match="already has a section"):
        clean_plan(plan("other", {"approach": "a"}, [(title, "x")]))


@pytest.mark.parametrize(
    "title",
    [
        "Cronbach's \u03b1",
        "\u03b2-blocker exposure",
        "Test\u2013retest (\u03ba)",
        "\u00b5g/L thresholds",
    ],
)
def test_greek_letters_are_fine_in_a_title(title):
    cleaned = clean_plan(plan("describe", {"measures": "m"}, [(title, "x")]))
    assert cleaned["sections"][-1]["content"] == "x"


@pytest.mark.parametrize(
    "title",
    [
        "\u03f9omparison groups",  # lunate sigma
        "\u03f9HECKS AND LIMITATIONS",
        "Method and ad\u03f3ustment",  # yot
        "\u037fudge",  # capital yot
    ],
)
def test_greeks_rarer_letters_arent_allowed_in_a_title(title):
    with pytest.raises(PlanInvalid):
        clean_plan(plan("describe", {"measures": "m"}, [(title, "x")]))
