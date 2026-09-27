"""Revising an approved plan, and asking for another type of analysis."""

import asyncio
import json

import pytest

from datalab import db
from datalab.sessions.approvals import Approvals
from datalab.sessions.plan_schema import PlanInvalid, clean_plan, plan_answer
from datalab.sessions.plans import Outcome, Plan, PlanDesk, PlanStore, as_text, plan_hash
from tests.test_plans import ASSOCIATION, plan


@pytest.fixture
def store(settings):
    connection = db.connect(settings.database_file)
    connection.execute(
        "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
        "VALUES ('c1', 'data', 'analysis', 't', 'm', 'x', 'x')"
    )
    return PlanStore(connection)


@pytest.fixture
def desk(store):
    events: list[tuple[str, dict]] = []
    desk = PlanDesk(Approvals(), store, lambda cid, kind, data: events.append((kind, data)))
    desk.events = events  # type: ignore[attr-defined]
    desk.turn = object()  # type: ignore[attr-defined]
    desk.current_turn = lambda cid: desk.turn  # type: ignore[attr-defined]
    return desk


def answering(desk, *, approve=True, edit=None, change_type=None, cards=None):
    """What Codex and the person do: show the card, then answer it."""

    async def codex(approval_id):
        pending = desk._approvals.get(approval_id, "c1")
        pending.shown = True
        if cards is not None:
            cards.append(pending.card())
        await asyncio.sleep(0)
        edits = edit(json.loads(json.dumps(pending.plan))) if edit else None
        try:
            desk._approvals.answer("c1", approval_id, approve, plan=edits, change_type=change_type)
        except Exception:
            desk._approvals.withdraw("c1", approval_id)  # fail the test, don't wait for an answer
            raise

    return codex


def revision(desk, of: Plan, reason="Add a missing-data section.", **changes):
    changes = changes or {"missing_data": "Complete cases, with the share missing reported."}
    raw = plan("association", {**ASSOCIATION, **changes})
    raw["revises"] = desk.revision_link("c1", of.id)
    raw["revision_reason"] = reason
    return clean_plan(raw)


async def approved_plan(desk) -> Plan:
    result = await desk.propose("c1", clean_plan(plan("association", ASSOCIATION)), answering(desk))
    assert isinstance(result, Plan)
    return result


async def test_a_revision_is_a_new_version_linked_to_the_one_it_replaces(desk, store):
    first = await approved_plan(desk)
    cards: list[dict] = []
    revised = revision(desk, first, missing_data="Complete cases, with the share reported.")
    second = await desk.propose("c1", revised, answering(desk, cards=cards))
    assert isinstance(second, Plan)
    # The card showed it against the approved plan.
    assert cards[0]["compare_to"]["plan"] == first.content
    assert cards[0]["compare_to"]["sha256"] == first.sha256
    # The first version is exactly as it was frozen; the second names it by hash.
    [kept, new] = store.list("c1")
    assert kept == first and plan_hash(kept.content) == first.sha256
    assert new.content["revises"] == {"plan_id": first.id, "sha256": first.sha256}
    assert store.current("c1") == [new] and store.superseded("c1") == {first.id: new}
    assert f"Revises: {first.id}" in as_text(new)
    assert "Why: Add a missing-data section." in as_text(new)
    assert f"superseded by {new.id}" in as_text(first, new)


async def test_only_a_current_plan_can_be_revised(desk):
    with pytest.raises(PlanInvalid, match="no approved plan to revise"):
        desk.revision_link("c1", "pl_000000000000")
    first = await approved_plan(desk)
    with pytest.raises(PlanInvalid, match=f"Current: {first.id} \\(Association"):
        desk.revision_link("c1", "pl_000000000000")
    second = await desk.propose("c1", revision(desk, first), answering(desk))
    with pytest.raises(PlanInvalid, match=f"already revised by {second.id}"):
        desk.revision_link("c1", first.id)


async def test_two_revisions_of_one_plan_cant_both_be_frozen(desk, store):
    first = await approved_plan(desk)
    one, two = revision(desk, first, reason="One."), revision(desk, first, reason="Two.")
    frozen = await desk.propose("c1", one, answering(desk))
    late = await desk.propose("c1", two, answering(desk))
    assert isinstance(frozen, Plan)
    assert isinstance(late, Outcome) and "already revised" in late.note
    assert len(store.list("c1")) == 2
    # The chat is told, so the late one's card doesn't wait to be frozen.
    kind, data = desk.events[-1]
    assert kind == "plan_not_frozen" and "wasn't frozen" in data["reason"]
    # And what the person approved goes back to the agent, to carry over.
    assert late.suggested == two and "carrying over what still applies" in late.note


def test_one_running_query_is_worded_in_the_singular():
    from datalab.sessions.plan_schema import proposed_after_text

    record = {"queries": 1, "tables": ["IHS_2025.VW_DAILY_MOOD"], "more_tables": 0}
    assert proposed_after_text({"proposed_after": record}).startswith(
        "1 query in this conversation had returned data or was still running, reading "
        "IHS_2025.VW_DAILY_MOOD."
    )


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"revision_reason": "Why"}, "needs the plan it revises"),
        ({"revises": {"plan_id": "pl_x", "sha256": "0"}}, "by its plan_id and sha256"),
        ({"revises": {"plan_id": "pl_" + "a" * 12, "sha256": "b" * 64}}, "needs its reason"),
    ],
)
def test_a_revision_must_name_its_plan_and_say_why(change, says):
    with pytest.raises(PlanInvalid, match=says):
        clean_plan({**plan("association", ASSOCIATION), **change})


async def test_the_person_cant_unlink_a_revision_when_approving_it(desk):
    first = await approved_plan(desk)
    revised = revision(desk, first)
    unlinked = {k: v for k, v in revised.items() if k not in ("revises", "revision_reason")}
    with pytest.raises(PlanInvalid, match="can't be changed here"):
        plan_answer(revised, True, unlinked, None)
    # They can reword the reason, though.
    reworded = {**revised, "revision_reason": "Missing days were more common than expected."}
    assert json.loads(plan_answer(revised, True, reworded, None)) == reworded


async def test_asking_for_another_type_sends_back_the_persons_draft(desk):
    proposed = clean_plan(plan("association", ASSOCIATION))

    def edit(sent):
        sent["sections"][0]["content"] = "Can sleep in weeks 1 to 4 predict mood in week 8?"
        return sent

    outcome = await desk.propose(
        "c1", proposed, answering(desk, approve=False, edit=edit, change_type="prediction")
    )
    assert isinstance(outcome, Outcome) and outcome.change_type == "prediction"
    assert outcome.suggested["sections"][0]["content"].startswith("Can sleep in weeks 1 to 4")
    assert "analysis_type 'prediction'" in outcome.note
    assert "Validation and performance" in outcome.note  # what it needs
    assert "Target quantity (estimand)" in outcome.note  # what no longer belongs
    # The next proposal is shown against what the person sent back.
    cards: list[dict] = []
    await desk.propose(
        "c1",
        clean_plan(plan("describe", {"measures": "m"})),
        answering(desk, approve=False, cards=cards),
    )
    assert cards[0]["compare_to"] == {
        "label": "The version you sent back",
        "plan": outcome.suggested,
    }
    # But not a proposal in a later turn, for a new question.
    await desk.propose("c1", proposed, answering(desk, approve=False, edit=edit))
    desk.turn = object()
    cards.clear()
    await desk.propose("c1", proposed, answering(desk, approve=False, cards=cards))
    assert cards[0]["compare_to"] is None


def test_a_draft_sent_back_may_be_unfinished_but_an_approval_may_not():
    proposed = clean_plan(plan("association", ASSOCIATION))
    unfinished = json.loads(json.dumps(proposed))
    unfinished["sections"] = [s for s in unfinished["sections"] if s["kind"] != "method"]
    sent = json.loads(plan_answer(proposed, False, unfinished, "prediction"))
    assert sent["change_type"] == "prediction" and len(sent["edits"]["sections"]) == 6
    with pytest.raises(PlanInvalid, match="Method and adjustment"):
        plan_answer(proposed, True, unfinished, None)
    with pytest.raises(PlanInvalid, match="can't be approved"):
        plan_answer(proposed, True, None, "prediction")
    # A type that doesn't exist, or is the same, isn't a request; a bare no is "".
    assert plan_answer(proposed, False, None, "causal") == ""
    assert plan_answer(proposed, False, proposed, "association") == ""


async def test_the_review_sees_every_version_and_the_latest_ones_checks(desk, store):
    assert store.for_review("c1") == ([], [])
    first = await approved_plan(desk)
    second = await desk.propose(
        "c1",
        revision(desk, first, repeated_observations="Random intercept per intern."),
        answering(desk),
    )
    texts, checks = store.for_review("c1")
    assert f"superseded by {second.id}" in texts[0] and "superseded" not in texts[1]
    assert checks[0].startswith("Estimation:") and checks[1].startswith("Repeated observations:")
    # A version-1 plan approved last has no type, so no extra checks.
    store.approve("c1", {"question": "Sleep?"})
    assert store.for_review("c1")[1] == []


async def test_a_plan_approved_just_before_stop_is_still_frozen(desk, store):
    """Stop can land after the person approves, while the tool waits for
    Codex's side of the request to finish: the approved plan is frozen."""
    approved_now = asyncio.Event()

    async def codex(approval_id):
        desk._approvals.get(approval_id, "c1").shown = True
        desk._approvals.answer("c1", approval_id, True)
        approved_now.set()
        await asyncio.sleep(3600)  # Codex's reply, still on its way

    proposing = asyncio.ensure_future(
        desk.propose("c1", clean_plan(plan("association", ASSOCIATION)), codex)
    )
    await approved_now.wait()
    await asyncio.sleep(0)
    proposing.cancel()  # what Stop does
    with pytest.raises(asyncio.CancelledError):
        await proposing
    [frozen] = store.list("c1")
    assert desk.events[-1][0] == "plan_approved" and desk.events[-1][1]["plan_id"] == frozen.id


async def test_a_revision_must_change_something(desk):
    first = await approved_plan(desk)
    same = clean_plan(
        {
            **first.content,
            "revises": desk.revision_link("c1", first.id),
            "revision_reason": "No change.",
        }
    )
    with pytest.raises(PlanInvalid, match="doesn't change anything"):
        desk.check_revision("c1", same)
    # Nor can the person approve it after editing it back to the original.
    revised = revision(desk, first)
    with pytest.raises(PlanInvalid, match="doesn't change anything"):
        plan_answer(revised, True, same, None, first.content)


async def test_a_revision_cant_name_another_conversations_plan(desk, store):
    store._db.execute(
        "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
        "VALUES ('c2', 'data', 'analysis', 't', 'm', 'x', 'x')"
    )
    elsewhere = store.approve("c2", clean_plan(plan("describe", {"measures": "m"})))
    with pytest.raises(PlanInvalid, match="no approved plan to revise"):
        desk.revision_link("c1", elsewhere.id)


async def test_the_review_keeps_current_plans_when_old_versions_pile_up(desk, store):
    a = await approved_plan(desk)
    b = await desk.propose("c1", clean_plan(plan("describe", {"measures": "B"})), answering(desk))
    b2 = await desk.propose("c1", _revise(desk, b, "B2"), answering(desk))
    b3 = await desk.propose("c1", _revise(desk, b2, "B3"), answering(desk))
    texts, _ = store.for_review("c1")
    shown = [t.split()[3] for t in texts[-3:]]  # "Approved analysis plan <id> ..."
    assert shown == [b2.id, a.id, b3.id]


def _revise(desk, of, measures):
    raw = plan("describe", {"measures": measures})
    raw["revises"] = desk.revision_link("c1", of.id)
    raw["revision_reason"] = f"Now {measures}."
    return clean_plan(raw)


async def test_a_failure_freezing_as_the_turn_stops_doesnt_swallow_the_stop(desk, monkeypatch):
    approved_now = asyncio.Event()

    async def codex(approval_id):
        desk._approvals.answer("c1", approval_id, True)
        approved_now.set()
        await asyncio.sleep(3600)

    def broken(*args):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(desk, "_freeze", broken)
    proposing = asyncio.ensure_future(
        desk.propose("c1", clean_plan(plan("association", ASSOCIATION)), codex)
    )
    await approved_now.wait()
    await asyncio.sleep(0)
    proposing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await proposing


class Query:
    count = 0

    def __init__(self, status, tables):
        Query.count += 1
        self.id, self.status, self.tables = f"q{Query.count}", status, tables


async def test_a_plan_records_what_had_returned_data_when_it_was_proposed(desk, store):
    assert desk.planning_record("c1") == {"queries": 0, "tables": [], "more_tables": 0}
    so_far = [
        Query("succeeded", ["IHS_2025.VW_DAILY_MOOD"]),
        Query("succeeded", ["IHS_2025.VW_DAILY_MOOD", "IHS_2025.STUDYPARTICIPANTS"]),
        Query("rejected", ["IHS_2025.SECRET"]),  # never returned anything
        Query("failed", ["IHS_2025.OTHER"]),
    ]
    desk.queries_so_far = lambda cid: so_far
    record = desk.planning_record("c1")
    assert record == {
        "queries": 2,
        "tables": ["IHS_2025.STUDYPARTICIPANTS", "IHS_2025.VW_DAILY_MOOD"],
        "more_tables": 0,
    }
    proposed = clean_plan({**plan("association", ASSOCIATION), "proposed_after": record})
    frozen = await desk.propose("c1", proposed, answering(desk))
    # Part of what was approved and hashed, and of what the review reads.
    assert frozen.content["proposed_after"] == record
    assert "2 queries in this conversation had returned data or were still running" in as_text(
        frozen
    )
    assert "aren't prespecified" in as_text(frozen)


def test_the_record_of_what_ran_before_cant_be_edited_or_malformed():
    record = {"queries": 2, "tables": ["IHS_2025.X"], "more_tables": 0}
    proposed = clean_plan({**plan("association", ASSOCIATION), "proposed_after": record})
    hidden = {**proposed, "proposed_after": {**record, "queries": 0}}
    with pytest.raises(PlanInvalid, match="can't be changed"):
        plan_answer(proposed, True, hidden, None)
    # Edits sent back that change it aren't passed on.
    assert plan_answer(proposed, False, {**hidden, "rationale": "x"}, None) == ""
    for bad in ({"queries": -1, "tables": [], "more_tables": 0}, {"queries": 1}, "3 queries"):
        with pytest.raises(PlanInvalid, match="expected form"):
            clean_plan({**plan("association", ASSOCIATION), "proposed_after": bad})


def test_a_plan_from_before_the_record_says_it_wasnt_recorded():
    old = Plan("pl_1", "c1", "2026-09-01T00:00:00+00:00", {"question": "Sleep?"}, "0" * 64)
    assert "- Proposed after: Not recorded" in as_text(old)


def test_a_running_query_counts_and_other_conversations_queries_dont(desk):
    queries = {
        "c1": [Query("running", ["IHS_2025.VW_DAILY_MOOD"])],
        "c2": [Query("succeeded", ["X"])],
    }
    desk.queries_so_far = lambda cid: queries[cid]
    assert desk.planning_record("c1") == {
        "queries": 1,
        "tables": ["IHS_2025.VW_DAILY_MOOD"],
        "more_tables": 0,
    }


def test_many_tables_are_counted_and_long_names_fit(desk):
    from datalab.sessions.plan_schema import MAX_RECORDED_TABLES, MAX_TABLE_NAME

    longest = "S" * 128 + "." + "T" * 128
    assert len(longest) == MAX_TABLE_NAME
    tables = [f"IHS_2025.T{i:02}" for i in range(MAX_RECORDED_TABLES + 5)] + [longest]
    desk.queries_so_far = lambda cid: [Query("succeeded", tables)]
    record = desk.planning_record("c1")
    assert len(record["tables"]) == MAX_RECORDED_TABLES and record["more_tables"] == 6
    record = {"queries": 1, "tables": [longest], "more_tables": 0}
    clean_plan({**plan("association", ASSOCIATION), "proposed_after": record})  # accepted


async def test_a_plan_isnt_frozen_if_queries_ran_while_it_waited(desk, store):
    """Codex can call query alongside propose_plan: results that arrive while
    the plan waits would make its record untrue, so it isn't frozen."""
    ran: list = []
    desk.queries_so_far = lambda cid: ran
    record = desk.planning_record("c1")
    proposed = clean_plan({**plan("association", ASSOCIATION), "proposed_after": record})

    edited = {**proposed, "rationale": "Edited by the person."}

    async def codex(approval_id):
        ran.append(Query("succeeded", ["IHS_2025.VW_DAILY_MOOD"]))  # a query in parallel
        desk._approvals.answer("c1", approval_id, True, plan=edited)

    outcome = await desk.propose("c1", proposed, codex)
    assert isinstance(outcome, Outcome) and "wasn't frozen" in outcome.note
    # What the person approved isn't lost: it goes back to the agent.
    assert outcome.suggested == clean_plan(edited)
    assert store.list("c1") == []
    kind, data = desk.events[-1]
    assert kind == "plan_not_frozen" and "1 more query ran while the plan waited" in data["reason"]
    # Proposed again, now with that query in its record, it's frozen.
    again = clean_plan(
        {**plan("association", ASSOCIATION), "proposed_after": desk.planning_record("c1")}
    )
    cards: list[dict] = []
    frozen = await desk.propose("c1", again, answering(desk, cards=cards))
    assert isinstance(frozen, Plan) and frozen.content["proposed_after"]["queries"] == 1
    # And the new card was shown against the version the person approved.
    assert cards[0]["compare_to"] == {
        "label": "The version you approved",
        "plan": clean_plan(edited),
    }


def test_removing_the_record_on_approval_is_refused():
    record = {"queries": 0, "tables": [], "more_tables": 0}
    proposed = clean_plan({**plan("association", ASSOCIATION), "proposed_after": record})
    without = {k: v for k, v in proposed.items() if k != "proposed_after"}
    with pytest.raises(PlanInvalid, match="can't be changed"):
        plan_answer(proposed, True, without, None)


def test_the_record_says_what_it_doesnt_count():
    from datalab.sessions.plan_schema import proposed_after_text

    text = proposed_after_text({"proposed_after": {"queries": 0, "tables": [], "more_tables": 0}})
    assert "aren't counted" in text and "Attached files" in text
