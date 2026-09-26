import asyncio

import pytest

from datalab import db
from datalab.sessions.approvals import Approvals
from datalab.sessions.plans import (
    Outcome,
    PlanDesk,
    PlanInvalid,
    PlanStore,
    as_text,
    clean_plan,
)


@pytest.fixture
def store(settings):
    connection = db.connect(settings.database_file)
    connection.execute(
        "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
        "VALUES ('c1', 'data', 'analysis', 't', 'm', 'x', 'x')"
    )
    return PlanStore(connection)


def test_plans_have_known_parts_and_a_question():
    plan = clean_plan({"question": " Does sleep predict mood? ", "junk": "x"})
    assert plan["question"] == "Does sleep predict mood?" and "junk" not in plan
    with pytest.raises(PlanInvalid):
        clean_plan({"question": ""})
    with pytest.raises(PlanInvalid):
        clean_plan({"question": "q", "outcome": 3})


async def test_an_approved_plan_is_the_persons_edit_and_is_frozen(store):
    approvals = Approvals()
    events = []
    desk = PlanDesk(approvals, store, lambda cid, kind, data: events.append((kind, data)))
    proposed = clean_plan({"question": "Sleep and mood?", "outcome": "PHQ-9"})

    async def codex(approval_id):
        # What the runtime does when Codex forwards the request: show it, then
        # the person edits and approves.
        pending = approvals.get(approval_id, "c1")
        pending.shown = True
        await asyncio.sleep(0.01)
        approvals.answer("c1", approval_id, True, plan={**proposed, "outcome": "PHQ-9 total"})

    plan = await desk.propose("c1", proposed, codex)
    assert plan.content["outcome"] == "PHQ-9 total"
    assert [p.id for p in store.list("c1")] == [plan.id]
    assert events[-1][0] == "plan_approved" and events[-1][1]["sha256"] == plan.sha256
    assert "PHQ-9 total" in as_text(plan)


async def test_a_plan_not_approved_is_not_kept(store):
    approvals = Approvals()
    desk = PlanDesk(approvals, store, lambda *a: None)

    async def codex(approval_id):
        approvals.answer("c1", approval_id, False, plan={"question": "q", "outcome": "PHQ-9"})

    outcome = await desk.propose("c1", clean_plan({"question": "q"}), codex)
    assert isinstance(outcome, Outcome) and outcome.suggested["outcome"] == "PHQ-9"
    assert store.list("c1") == []
    desk.turn_running = lambda cid: False
    outcome = await desk.propose("c1", clean_plan({"question": "q"}), codex)
    assert "during a turn" in outcome.note


@pytest.mark.parametrize("edits", [{"question": ""}, {"question": "q\u200b"}, {"question": "q"}])
async def test_a_no_always_counts_even_with_unusable_or_no_edits(store, edits):
    approvals = Approvals()
    desk = PlanDesk(approvals, store, lambda *a: None)

    async def codex(approval_id):
        approvals.answer("c1", approval_id, False, plan=edits)

    outcome = await desk.propose("c1", clean_plan({"question": "q"}), codex)
    assert isinstance(outcome, Outcome) and outcome.suggested is None
    assert "edited" not in outcome.note


def test_hidden_text_in_a_plan_is_refused():
    with pytest.raises(PlanInvalid):
        clean_plan({"question": "Sleep?\u200b"})
