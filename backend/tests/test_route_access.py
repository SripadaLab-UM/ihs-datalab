"""On the lab's shared DataLab, each person reaches only their own things
(api/access.py), and every route's ids are covered by a rule."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from datalab.api.access import OWN_STORE, WITHIN, RequireOwner
from datalab.app import create_app
from datalab.auth.users import Users
from datalab.config import ServerSettings
from datalab.repos.github import Account
from tests.conftest import FakeDatabase

SITE = "https://lab.example.edu"
JSON = {"content-type": "application/json", "origin": SITE}


@pytest.fixture
def app(settings, catalog):
    server = ServerSettings(public_url=SITE, admins=(1001,))
    return create_app(
        replace(settings, deployment="server", server=server),
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
    )


def signed_in(app, client: TestClient, github_id: int, admin: bool) -> TestClient:
    users = Users(app.state.services.conversations._db)
    user = users.signed_in_with_github(Account(f"person{github_id}", github_id), admin=admin)
    client.cookies.set("datalab_session", app.state.browser.start(user))
    return client


def test_every_routes_ids_are_covered(app):
    check = next(
        d.dependency for d in app.router.dependencies if isinstance(d.dependency, RequireOwner)
    )
    uncovered = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        for param in re.findall(r"\{(\w+)(?::\w+)?\}", route.path):
            if check.rule_for(route.path, param) is not None:
                continue
            if param in WITHIN and "{conversation_id}" in route.path:
                continue  # inside a conversation, which its own rule guards
            if any(route.path.startswith(prefix) and param == name for prefix, name in OWN_STORE):
                continue
            uncovered.append(f"{route.path} {{{param}}}")
    assert not uncovered, uncovered


def test_one_person_cant_reach_anothers_conversation(app):
    with TestClient(app) as a:
        b = TestClient(app)  # the same app: only its cookies differ
        signed_in(app, a, 1001, admin=True)
        signed_in(app, b, 1002, admin=False)
        made = a.post("/api/conversations", json={"mode": "research"}, headers=JSON)
        assert made.status_code == 201
        cid = made.json()["id"]
        assert [c["id"] for c in a.get("/api/conversations").json()] == [cid]
        # B: an empty list, and the same "not found" as an id that doesn't exist.
        assert b.get("/api/conversations").json() == []
        for path in (
            f"/api/conversations/{cid}",
            f"/api/conversations/{cid}/events",
            f"/api/conversations/{cid}/files",
            f"/api/conversations/{cid}/data-accessed",
            "/api/conversations/c_doesnotexist",
        ):
            assert b.get(path).status_code == 404, path
        assert b.delete(f"/api/conversations/{cid}", headers=JSON).status_code == 404
        assert a.get(f"/api/conversations/{cid}").status_code == 200


def test_each_person_has_their_own_playground(app):
    with TestClient(app) as a:
        b = TestClient(app)  # the same app: only its cookies differ
        signed_in(app, a, 1001, admin=True)
        signed_in(app, b, 1002, admin=False)
        mine = a.get("/api/sql/status").json()["playground_id"]
        theirs = b.get("/api/sql/status").json()["playground_id"]
        assert mine != theirs and a.get("/api/sql/status").json()["playground_id"] == mine


def test_only_administrators_change_the_labs_settings(app):
    with TestClient(app) as a:
        b = TestClient(app)  # the same app: only its cookies differ
        signed_in(app, a, 1001, admin=True)
        signed_in(app, b, 1002, admin=False)
        body = {"key": "not-a-real-key"}
        refused = b.put("/api/settings/connections/model-key", json=body, headers=JSON)
        assert refused.status_code == 403 and "administrators" in refused.json()["detail"]
        # Reading is fine; an administrator gets past the check (here practice's
        # own rule answers: it never saves a key).
        assert b.get("/api/settings/connections").status_code == 200
        allowed = a.put("/api/settings/connections/model-key", json=body, headers=JSON)
        assert "administrators" not in allowed.json()["detail"]


def test_the_audit_names_whose_session_it_was(app):
    with TestClient(app) as a:
        signed_in(app, a, 1001, admin=True)
        cid = a.post("/api/conversations", json={"mode": "research"}, headers=JSON).json()["id"]
        playground = a.get("/api/sql/status").json()["playground_id"]
        who = app.state.services.access_log.who
        mine = who(cid)
        assert mine is not None and mine[1] == "person1001"
        assert who(playground) == mine
        assert who("c_nobody") is None
