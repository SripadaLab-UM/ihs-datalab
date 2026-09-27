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
