"""A person's own edits of knowledge-base pages, and suggested Knowledge updates.

The Knowledge tab's Edit page: drafts kept on this computer, the check,
status and review fields, Save & share (through share.save_and_share, to a
local bare repo standing in for GitHub), and conflicts when GitHub moved on.
And suggestions from a conversation (suggest_kb_update, also when the person
asked for one with Remember in Knowledge), which never write the knowledge base.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import db
from datalab.api.knowledge import KnowledgeServices, build_knowledge_router
from datalab.config import RepoSettings, Settings
from datalab.data.access_log import AccessLog
from datalab.knowledge import check as kb
from datalab.knowledge import share
from datalab.knowledge.edits import editable_problem, merge3
from datalab.knowledge.service import Knowledge
from datalab.knowledge.suggestions import KbSuggestions, SuggestionInvalid
from datalab.repos.github import Account, GitHubAuth, Tokens, TokenStore
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.tokens import SessionTokens
from tests.kb_fixtures import FITBIT, MIDNIGHT, Remote

ME = Account("yfang", 42, "Yu Fang")


@dataclass
class Lab:
    knowledge: Knowledge
    store: ConversationStore
    remote: Remote
    client: TestClient
    suggestions: KbSuggestions
    access_log: AccessLog


@pytest.fixture
def lab(tmp_path, github_keychain):
    remote = Remote(tmp_path)
    settings = Settings(
        profile="real",
        data_dir=tmp_path / "data",
        oracle=None,
        repos=RepoSettings(knowledge="SripadaLab-UM/ihs-knowledge", client_id="Iv23liTEST"),
    )
    TokenStore().save(Tokens("ghu_t", time.time() + 8 * 3600, "ghr_t", time.time() + 1e7, ME))

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/"):
            return httpx.Response(200, json={"permissions": {"push": True}})
        return httpx.Response(404)

    auth = GitHubAuth("Iv23liTEST", http=httpx.Client(transport=httpx.MockTransport(answer)))
    connection = db.connect(settings.database_file)
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    access_log = AccessLog(connection, tmp_path / "audit.jsonl")
    suggestions = KbSuggestions(store, access_log)
    router = build_knowledge_router(
        KnowledgeServices(
            settings, connection, store, manager, auth=auth, remote=remote.url,
            suggestions=suggestions,
        )
    )  # fmt: skip
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/knowledge/sync").json()["repo"] == "in sync"
        yield Lab(router.knowledge, store, remote, client, suggestions, access_log)  # type: ignore[attr-defined]
    connection.close()


def start(lab: Lab, path: str, *, new: bool = False) -> dict:
    response = lab.client.post("/api/knowledge/edits", json={"path": path, "new": new})
    assert response.status_code == 200, response.text
    return response.json()


def keep(lab: Lab, edit: dict, text: str) -> dict:
    response = lab.client.put(
        f"/api/knowledge/edits/{edit['id']}", json={"text": text, "version": edit["updated_at"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def checked(lab: Lab, edit: dict, text: str | None = None) -> dict:
    response = lab.client.post(f"/api/knowledge/edits/{edit['id']}/check", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def share_edit(lab: Lab, edit: dict, confirmed: list[str] | None = None) -> httpx.Response:
    now = lab.client.get(f"/api/knowledge/edits/{edit['id']}").json()
    findings = [f["id"] for f in checked(lab, now)["findings"]]
    return lab.client.post(
        f"/api/knowledge/edits/{edit['id']}/share",
        json={"confirmed": confirmed or [], "seen": now["text_sha256"], "findings": findings},
    )


def conversation(lab: Lab) -> str:
    made = lab.store.create(kind="data", mode="analysis", title="t", model="m")
    lab.store.append(made.id, "user_message", {"text": "Why are some Fitbit days zero?"})
    return made.id


def ran(lab: Lab, cid: str, query_id: str, status: str = "succeeded") -> str:
    lab.access_log.started(
        query_id=query_id, session_id=cid, sql="SELECT COUNT(*) FROM IHS_2025.VFITBITDAILYDATA",
        binds={}, tables=["IHS_2025.VFITBITDAILYDATA"],
    )  # fmt: skip
    lab.access_log.finished(query_id, status=status, row_count=1)
    return query_id


SECTION = (
    "About 4% of Fitbit days report 0 steps with some heart-rate data: the tracker was worn "
    "but not synced. Treat 0 as missing, not as no activity."
)


# What can be edited ------------------------------------------------------------


def test_pages_say_whether_they_can_be_edited_and_where_read_only_ones_come_from(lab):
    fitbit = lab.client.get("/api/knowledge/pages/sources/fitbit.md").json()
    assert (fitbit["editable"], fitbit["source_note"]) == (True, None)
    agents = lab.client.get("/api/knowledge/pages/AGENTS.md").json()
    assert agents["editable"] is True
    index = lab.client.get("/api/knowledge/pages/index.md").json()
    assert index["editable"] is False and "datalab kb-check --fix" in index["source_note"]
    # Derived from the rules for proposals, never a list of its own.
    for path in ("index.md", "generated/drift.md", "generated/README.md",
                 "generated/schema/IHS_2025/X.yml", ".github/workflows/kb-check.yml",
                 "skills/kb-use/SKILL.md", "notes.txt"):  # fmt: skip
        assert kb.proposal_problem(path) is not None
        assert kb.edit_source(path) and editable_problem(path), path
    assert "datalab catalog" in (kb.edit_source("generated/drift.md") or "")
    for path in ("sources/fitbit.md", "qc/new.md", "skills/steps-check/SKILL.md", "AGENTS.md"):
        assert kb.proposal_problem(path) is None and editable_problem(path) is None
    refused = lab.client.post("/api/knowledge/edits", json={"path": "index.md"})
    assert refused.status_code == 409 and "can't be edited here" in refused.json()["detail"]
    refused = lab.client.post("/api/knowledge/edits", json={"path": "generated/drift.md"})
    assert refused.status_code == 409 and "database catalog" in refused.json()["detail"]


# Editing, the check, status ------------------------------------------------------


def test_the_check_runs_on_the_edited_text_with_its_findings(lab):
    edit = start(lab, "sources/fitbit.md")
    assert edit["text"] == FITBIT and edit["before"] == FITBIT and edit["status"] == "draft"
    assert checked(lab, edit)["findings"] == []
    broken = FITBIT.replace("kind: source", "kind: table").replace(
        "See [the sleep rule](../qc/midnight-sleep.md).", "See [x](../qc/nowhere.md)."
    )
    rules = {f["rule"] for f in checked(lab, edit, broken)["findings"]}
    assert {"kind", "link"} <= rules
    leaky = FITBIT + "\nParticipant P12345 on 2025-03-02 had no steps.\n"
    [data] = [f for f in checked(lab, edit, leaky)["findings"] if f["severity"] == "data"]
    assert data["rule"] == "date_near_id" and data["line"] == leaky.count("\n")
    unreadable = checked(lab, edit, "---\nid: [\n---\n")["findings"]
    assert unreadable[0]["rule"] == "front_matter"
    # The check never keeps anything.
    assert lab.client.get(f"/api/knowledge/edits/{edit['id']}").json()["text"] == FITBIT


def test_a_person_may_change_status_but_datalab_stamps_who_reviewed(lab):
    edit = start(lab, "qc/midnight-sleep.md")
    reviewed = MIDNIGHT.replace("status: draft", "status: reviewed").replace(
        "cohorts: [2024, 2025]", "cohorts: [2024, 2025]\nreviewed_by: someone-else"
    )
    result = checked(lab, edit, reviewed)
    today = time.strftime("%Y-%m-%d")
    # Kept as the person wrote it (not keep_status), stamped as the person saving.
    assert "status: reviewed" in result["shared"]
    assert f"reviewed_by: yfang\nreviewed_on: {today}" in result["shared"]
    assert "someone-else" not in result["shared"]
    assert any("from draft to reviewed" in n for n in result["notes"])
    assert any("reviewed_by is set by DataLab" in n for n in result["notes"])
    edit = keep(lab, edit, reviewed)
    response = share_edit(lab, edit)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "saved"
    on_github = lab.remote.show("qc/midnight-sleep.md")
    assert "status: reviewed" in on_github
    assert f"reviewed_by: yfang\nreviewed_on: {today}" in on_github
    assert "someone-else" not in on_github
    # An agent's proposal still can't: keep_status keeps the base's.
    assert "status: draft" in kb.agents_text("qc/x.md", reviewed, MIDNIGHT)


def test_a_draft_kept_on_this_computer_never_touches_github(lab, monkeypatch):
    started = lab.remote.log()
    network: list[str] = []
    monkeypatch.setattr(lab.knowledge, "_fresh_token", lambda: network.append("token"))
    edit = start(lab, "sources/fitbit.md")
    edit = keep(
        lab, edit, FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2025 only.")
    )
    assert edit["status"] == "draft" and "2025 only" in edit["text"]
    checked(lab, edit)
    # Kept across reloads (the database), and opening the page again finds it.
    again = start(lab, "sources/fitbit.md")
    assert (again["id"], again["text"]) == (edit["id"], edit["text"])
    [listed] = lab.client.get("/api/knowledge/edits").json()
    assert listed["id"] == edit["id"]
    assert lab.remote.log() == started and network == []
    # Another window's older copy can't overwrite it silently.
    stale = lab.client.put(
        f"/api/knowledge/edits/{edit['id']}", json={"text": "x", "version": "old"}
    )
    assert stale.status_code == 409 and "another window" in stale.json()["detail"]
    assert lab.client.post(f"/api/knowledge/edits/{edit['id']}/discard").json()["status"] == (
        "discarded"
    )
    assert lab.remote.log() == started and network == []


def test_save_and_share_goes_through_the_existing_flow(lab, monkeypatch):
    calls: list[share.Share] = []
    real = share.save_and_share

    def spy(clone, request):
        calls.append(request)
        return real(clone, request)

    monkeypatch.setattr(share, "save_and_share", spy)
    started = lab.remote.head()
    edit = start(lab, "sources/fitbit.md")
    edit = keep(
        lab, edit, FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2025 only.")
    )
    saved = share_edit(lab, edit).json()
    assert saved["status"] == "saved", saved["result"]
    [request] = calls
    assert request.strict and request.base == started and request.proposal_id == edit["id"]
    assert request.reviewer == "yfang" and list(request.files) == ["sources/fitbit.md"]
    assert saved["commit"] == lab.remote.head() and lab.remote.log("%P")[0] == started
    message = "\n".join(lab.remote.log("%B"))
    assert f"DataLab-Edit: {edit['id']}" in message and "DataLab-Conversation" not in message
    assert "Wear time is in 2025 only." in lab.remote.show("sources/fitbit.md")
    assert lab.client.get("/api/knowledge/status").json()["head"] == saved["commit"]
    # Done: the page's next edit starts from the shared version.
    assert start(lab, "sources/fitbit.md")["base"] == saved["commit"]


def test_share_refuses_what_the_person_didnt_see(lab):
    edit = keep(lab, start(lab, "sources/fitbit.md"), FITBIT + "\nMore.\n")
    response = lab.client.post(
        f"/api/knowledge/edits/{edit['id']}/share",
        json={"confirmed": [], "seen": "not-it", "findings": []},
    )
    assert response.status_code == 409 and "changed since you looked" in response.json()["detail"]
    leaky = keep(lab, edit, FITBIT + "\nParticipant P12345 on 2025-03-02.\n")
    blocked = share_edit(lab, leaky).json()
    assert blocked["status"] == "check_failed" and blocked["result"]["findings"]
    assert lab.remote.log("%s")[0] == "Start the knowledge base"


def test_a_new_page_starts_as_a_draft_from_the_template(lab):
    edit = start(lab, "qc/zero-steps.md", new=True)
    assert edit["new_page"] and edit["before"] is None
    fields, _ = kb.front_matter(edit["text"])
    assert fields and (fields["id"], fields["kind"], fields["status"]) == (
        "zero-steps",
        "qc",
        "draft",
    )
    assert all(f["severity"] != "error" for f in checked(lab, edit)["findings"])
    assert (
        lab.client.post(
            "/api/knowledge/edits", json={"path": "sources/fitbit.md", "new": True}
        ).status_code
        == 409
    )
    assert share_edit(lab, edit).json()["status"] == "saved"
    assert "zero-steps" in lab.remote.show("index.md")


# Conflicts --------------------------------------------------------------------------


def test_github_moving_on_is_a_conflict_to_see_never_a_silent_overwrite(lab):
    edit = keep(
        lab, start(lab, "sources/fitbit.md"), FITBIT.replace("# Fitbit", "# Fitbit trackers")
    )
    theirs = FITBIT.replace("Wear time isn't recorded.", "Wear time is recorded from 2026.")
    lab.remote.write({"sources/fitbit.md": theirs.encode()}, "Someone else's change")
    # Not synced yet: Save & share fetches, and stops rather than merging.
    stopped = share_edit(lab, edit).json()
    assert stopped["status"] == "conflict"
    assert stopped["result"]["conflicts"] == ["sources/fitbit.md"]
    assert lab.remote.show("sources/fitbit.md") == theirs.rstrip()
    # The three versions: yours, GitHub's now, where you started.
    now = lab.client.get(f"/api/knowledge/edits/{edit['id']}").json()
    assert now["upstream_changed"] and now["theirs"] == theirs and now["before"] == FITBIT
    assert "# Fitbit trackers" in now["text"]
    # Reapply: the two changes don't overlap, so they merge onto GitHub's.
    moved = lab.client.post(
        f"/api/knowledge/edits/{edit['id']}/reapply", json={"version": now["updated_at"]}
    ).json()
    assert moved["merged"] is None
    assert moved["edit"]["base"] == lab.remote.head() and not moved["edit"]["upstream_changed"]
    assert "# Fitbit trackers" in moved["edit"]["text"] and "from 2026" in moved["edit"]["text"]
    assert share_edit(lab, moved["edit"]).json()["status"] == "saved"
    shared = lab.remote.show("sources/fitbit.md")
    assert "# Fitbit trackers" in shared and "from 2026" in shared


def test_overlapping_changes_are_marked_for_the_person_to_resolve(lab):
    mine = FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2025 only.")
    edit = keep(lab, start(lab, "sources/fitbit.md"), mine)
    theirs = FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2026 on.")
    lab.remote.write({"sources/fitbit.md": theirs.encode()}, "Someone else's change")
    lab.client.post("/api/knowledge/sync")
    now = lab.client.get(f"/api/knowledge/edits/{edit['id']}").json()
    assert now["upstream_changed"] and now["theirs"] == theirs
    tried = lab.client.post(
        f"/api/knowledge/edits/{edit['id']}/reapply", json={"version": now["updated_at"]}
    ).json()
    assert (
        "<<<<<<< Your edit" in tried["merged"]
        and ">>>>>>> The version now on GitHub" in tried["merged"]
    )
    assert tried["edit"]["text"] == mine and tried["edit"]["base"] == edit["base"]
    # Their own text for the new version.
    resolved = FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2025, and 2026 on.")
    done = lab.client.post(
        f"/api/knowledge/edits/{edit['id']}/reapply",
        json={"version": now["updated_at"], "resolution": resolved},
    ).json()
    assert done["edit"]["base"] == lab.remote.head() and done["edit"]["text"] == resolved
    assert share_edit(lab, done["edit"]).json()["status"] == "saved"
    assert "2025, and 2026 on" in lab.remote.show("sources/fitbit.md")


def test_merge3_merges_what_doesnt_overlap(lab):
    base = "a\nb\nc\nd\ne\n"
    merged, clean = merge3(lab.knowledge.clone, "A\nb\nc\nd\ne\n", base, "a\nb\nc\nd\nE\n")
    assert (merged, clean) == ("A\nb\nc\nd\nE\n", True)


# Suggested Knowledge updates -------------------------------------------------------


def test_a_suggestion_needs_real_queries_of_this_conversation_as_evidence(lab):
    cid = conversation(lab)
    other = conversation(lab)
    good = ran(lab, cid, "q_good")
    ran(lab, cid, "q_failed", status="failed")
    ran(lab, other, "q_other")
    base = {"page": "sources/fitbit", "title": "Zero-step days", "text": SECTION, "reason": "r"}
    for ids, said in (
        ([], "needs evidence"),
        (["q_nope"], "Not queries of this conversation: q_nope"),
        (["q_other"], "Not queries of this conversation: q_other"),
        (["q_failed"], "didn't succeed"),
    ):
        with pytest.raises(SuggestionInvalid, match=said):
            lab.suggestions.suggest(cid, evidence_query_ids=ids, **base)
    made = lab.suggestions.suggest(cid, evidence_query_ids=[good], **base)
    assert made["page"] == "sources/fitbit.md" and made["evidence"][0]["query_id"] == good
    [event] = [e.data for e in lab.store.events_of_types_after(cid, 0, ("kb_suggestion",))]
    assert event["id"] == made["id"] and event["by"] == "agent"


def test_a_suggestion_with_participant_like_data_is_refused(lab):
    cid = conversation(lab)
    good = ran(lab, cid, "q_1")
    base = {"page": "sources/fitbit.md", "title": "Zero-step days", "reason": "r"}
    for text in (
        "Participant P12345 on 2025-03-02 had 0 steps.",
        "Contact jane.doe@umich.edu about this.",
        "Only 3 participants had this, n = 3.",
        "| id | steps |\n|---|---|\n" + "".join(f"| {i} | {i * 7} |\n" for i in range(8)),
    ):
        with pytest.raises(SuggestionInvalid, match="participant-level data"):
            lab.suggestions.suggest(cid, text=text, evidence_query_ids=[good], **base)
    for page in ("index.md", "generated/drift.md", "notes/x.md", "skills/x/SKILL.md"):
        with pytest.raises(SuggestionInvalid, match="isn't a page"):
            lab.suggestions.suggest(
                cid, page=page, title="t", text=SECTION, evidence_query_ids=[good], reason="r"
            )
    assert lab.store.events_of_types_after(cid, 0, ("kb_suggestion",)) == []


def test_at_most_two_suggestions_an_answer_and_only_during_a_turn(lab):
    cid = conversation(lab)
    good = ran(lab, cid, "q_1")
    args = {"page": "qc/zero-steps.md", "title": "t", "text": SECTION, "reason": "r",
            "evidence_query_ids": [good]}  # fmt: skip
    lab.suggestions.suggest(cid, **args)
    lab.suggestions.suggest(cid, **args)
    with pytest.raises(SuggestionInvalid, match="already suggests 2"):
        lab.suggestions.suggest(cid, **args)
    lab.suggestions.turn_running = lambda _: False
    lab.store.append(cid, "user_message", {"text": "next"})
    with pytest.raises(SuggestionInvalid, match="during a turn"):
        lab.suggestions.suggest(cid, **args)


def test_a_suggestion_never_writes_the_knowledge_base(lab):
    cid = conversation(lab)
    before = (lab.remote.log(), lab.knowledge.clone.remote_head())
    lab.suggestions.suggest(
        cid, page="sources/fitbit.md", title="Zero-step days", text=SECTION, reason="r",
        evidence_query_ids=[ran(lab, cid, "q_1")],
    )  # fmt: skip
    assert (lab.remote.log(), lab.knowledge.clone.remote_head()) == before
    assert lab.client.get("/api/knowledge/edits").json() == []


def test_accepting_a_suggestion_starts_a_reviewable_edit_that_shares_as_usual(lab):
    cid = conversation(lab)
    made = lab.suggestions.suggest(
        cid, page="sources/fitbit.md", title="Zero-step days", text=SECTION,
        reason="Analysts keep treating 0 as no activity.",
        evidence_query_ids=[ran(lab, cid, "q_1")],
    )  # fmt: skip
    response = lab.client.post(f"/api/knowledge/suggestions/{cid}/{made['id']}/accept")
    assert response.status_code == 200, response.text
    edit = response.json()
    assert edit["status"] == "draft" and edit["path"] == "sources/fitbit.md"
    assert edit["text"].startswith(FITBIT.rstrip()) and "## Zero-step days" in edit["text"]
    assert edit["origin"]["conversation_id"] == cid
    assert edit["origin"]["evidence"][0]["query_id"] == "q_1"
    assert lab.suggestions.get(cid, made["id"])["status"] == "accepted"
    again = lab.client.post(f"/api/knowledge/suggestions/{cid}/{made['id']}/accept")
    assert again.status_code == 409
    # Nothing on GitHub until it's reviewed and shared.
    assert lab.remote.log("%s")[0] == "Start the knowledge base"
    saved = share_edit(lab, edit).json()
    assert saved["status"] == "saved"
    assert "## Zero-step days" in lab.remote.show("sources/fitbit.md")
    assert f"DataLab-Conversation: {cid}" in "\n".join(lab.remote.log("%B"))


def test_a_suggestion_can_be_dismissed(lab):
    cid = conversation(lab)
    made = lab.suggestions.suggest(
        cid, page="qc/zero-steps.md", title="t", text=SECTION, reason="r",
        evidence_query_ids=[ran(lab, cid, "q_1")],
    )  # fmt: skip
    dismissed = lab.client.post(f"/api/knowledge/suggestions/{cid}/{made['id']}/dismiss").json()
    assert dismissed["status"] == "dismissed"
    assert lab.client.get("/api/knowledge/edits").json() == []


def remember_turn(lab: Lab, text: str = "Keep this: zero-step days mean not synced.") -> str:
    """A conversation whose last message was sent with Remember in Knowledge."""
    made = lab.store.create(kind="data", mode="analysis", title="t", model="m")
    lab.store.append(made.id, "user_message", {"text": text, "kb_request": True})
    return made.id


def test_remember_in_knowledge_needs_no_query_and_becomes_a_draft_edit(lab):
    cid = remember_turn(lab)
    args = {"page": "qc/zero-steps.md", "title": "Zero-step days", "text": SECTION,
            "reason": "The person asked to keep it; no query here shows it."}  # fmt: skip
    made = lab.suggestions.suggest(cid, evidence_query_ids=[], **args)
    assert made["requested"] and made["evidence"] == []
    # Evidence it does give is still checked.
    with pytest.raises(SuggestionInvalid, match="Not queries of this conversation"):
        lab.suggestions.suggest(cid, evidence_query_ids=["q_nope"], **args)
    edit_id = lab.suggestions.accept_requested(cid, made["id"])
    assert edit_id is not None
    edit = lab.client.get(f"/api/knowledge/edits/{edit_id}").json()
    assert edit["status"] == "draft" and edit["new_page"] and "## Zero-step days" in edit["text"]
    fields, _ = kb.front_matter(edit["text"])
    assert fields and fields["status"] == "draft" and fields["summary"] == "Zero-step days"
    assert lab.suggestions.get(cid, made["id"])["status"] == "accepted"
    # A draft on this computer: nothing on GitHub until the person shares it.
    assert lab.remote.log("%s")[0] == "Start the knowledge base"


def test_remember_in_knowledge_is_still_checked_for_participant_data(lab):
    cid = remember_turn(lab)
    with pytest.raises(SuggestionInvalid, match="participant-level data"):
        lab.suggestions.suggest(
            cid, page="sources/fitbit.md", title="t", reason="r", evidence_query_ids=[],
            text="Participant P12345 on 2025-03-02 had 0 steps.",
        )  # fmt: skip
    assert lab.store.events_of_types_after(cid, 0, ("kb_suggestion",)) == []


def test_only_a_remember_in_knowledge_turn_skips_the_evidence(lab):
    cid = conversation(lab)
    with pytest.raises(SuggestionInvalid, match="needs evidence"):
        lab.suggestions.suggest(
            cid, page="qc/zero-steps.md", title="t", text=SECTION, reason="r",
            evidence_query_ids=[],
        )  # fmt: skip
    made = lab.suggestions.suggest(
        cid, page="qc/zero-steps.md", title="t", text=SECTION, reason="r",
        evidence_query_ids=[ran(lab, cid, "q_1")],
    )  # fmt: skip
    assert "requested" not in made
    # The next message, a Remember in Knowledge one, starts a new turn: the
    # earlier one's flag doesn't carry over, and nor does this one's back.
    lab.store.append(cid, "user_message", {"text": "Keep it.", "kb_request": True})
    again = lab.suggestions.suggest(
        cid, page="qc/zero-steps.md", title="t", text=SECTION, reason="r", evidence_query_ids=[]
    )
    assert again["requested"]


def test_a_requested_update_that_cant_be_accepted_stays_open(lab):
    cid = remember_turn(lab)
    made = lab.suggestions.suggest(
        cid, page="qc/zero-steps.md", title="t", text=SECTION, reason="r", evidence_query_ids=[]
    )

    def broken(conversation_id: str, suggestion_id: str) -> str:
        raise OSError("GitHub is unreachable")

    lab.suggestions.accept = broken
    assert lab.suggestions.accept_requested(cid, made["id"]) is None
    # Its card still offers Accept as proposal.
    assert lab.suggestions.get(cid, made["id"])["status"] == "open"


def test_practice_datalab_has_no_knowledge_base_to_edit(settings, tmp_path):
    connection = db.connect(tmp_path / "db.sqlite")
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    app = FastAPI()
    app.include_router(
        build_knowledge_router(KnowledgeServices(settings, connection, store, manager))
    )
    with TestClient(app) as client:
        response = client.post("/api/knowledge/edits", json={"path": "sources/fitbit.md"})
    assert response.status_code == 409 and "Practice" in response.json()["detail"]
