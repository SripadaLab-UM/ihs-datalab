"""Who may reach what: every /api route's ids, checked in one place.

On the lab's shared DataLab each conversation, workflow run, knowledge or
pipeline draft (and what hangs off them) belongs to the person who made it
(migration 0017). `require_owner` runs before every route: for each id in
the route's path, `OWNERS` says whose it is, and anyone else gets 404, the
same as an id that doesn't exist, so ids can't be probed. A few ids are
administrators' only. tests/test_route_access.py fails if a route has an id
this table doesn't cover.

On a person's computer everything belongs to the local user, who is also
the administrator: nothing changes there.

Lists aren't routes with ids: each list route filters by its owner itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request

from datalab.web import current_user

# Whose an id is: a user id, ADMINS (administrators only), or None (no such id).
ADMINS = -1
Owner = Callable[[str], int | None]


@dataclass(frozen=True)
class Rule:
    # The route's path starts with this (its template: "/api/knowledge/…").
    prefix: str
    # The path parameter naming the resource.
    param: str
    owner: Owner


# Ids that name something inside a resource another id on the same path
# already guards (a conversation's approval, file, checkpoint…), or that
# are scoped by the person's own store (their Playground's runs and results).
# What only administrators may change (they may all read it): the lab's
# shared settings and connections, Docker, the Safety check, the processed
# data, export folders and workflow destinations.
ADMIN_WRITES = (
    "/api/settings",
    "/api/docker",
    "/api/safety",
    "/api/processed",
    "/api/export-destinations",
    "/api/workflows/destinations",
)
# What only administrators may even read: where the server keeps its data,
# and its diagnostics (paths, versions, logs' shape).
ADMIN_READS = ("/api/settings/storage", "/api/settings/diagnostics")
_READS = frozenset({"GET", "HEAD", "OPTIONS"})

WITHIN = frozenset(
    {"upload_id", "approval_id", "attachment_id", "number", "root", "path", "suggestion_id"}
)
OWN_STORE = {
    ("/api/sql/", "run_id"),
    ("/api/sql/", "query_id"),
    # Looked up in the signed-in person's own folder only (downloads.py).
    ("/api/downloads/", "download_id"),
}


def owner_rules(db: sqlite3.Connection) -> list[Rule]:
    """The resources and how to find whose each is."""

    def column(sql: str) -> Owner:
        def owner(value: str) -> int | None:
            row = db.execute(sql, (value,)).fetchone()
            return None if row is None else row[0]

        return owner

    def admins(_: str) -> int | None:
        return ADMINS

    conversation = column("SELECT owner_id FROM conversations WHERE id = ?")
    return [
        Rule("/api/", "conversation_id", conversation),
        Rule(
            "/api/workflows/", "run_id", column("SELECT owner_id FROM workflow_runs WHERE id = ?")
        ),
        Rule("/api/knowledge/", "edit_id", column("SELECT owner_id FROM kb_edits WHERE id = ?")),
        Rule(
            "/api/pipelines/", "edit_id", column("SELECT owner_id FROM pipeline_edits WHERE id = ?")
        ),
        Rule(
            "/api/knowledge/",
            "proposal_id",
            column(
                "SELECT c.owner_id FROM kb_proposals p JOIN conversations c "
                "ON c.id = p.conversation_id WHERE p.id = ?"
            ),
        ),
        Rule(
            "/api/pipelines/",
            "proposal_id",
            column(
                "SELECT c.owner_id FROM pipeline_proposals p JOIN conversations c "
                "ON c.id = p.conversation_id WHERE p.id = ?"
            ),
        ),
        # A test of a proposal is its conversation's; one of a draft (no
        # proposal) only an administrator's, until tests record their draft.
        Rule(
            "/api/pipelines/",
            "test_id",
            column(
                "SELECT COALESCE(c.owner_id, -1) FROM pipeline_tests t "
                "LEFT JOIN pipeline_proposals p ON p.id = t.proposal_id "
                "LEFT JOIN conversations c ON c.id = p.conversation_id WHERE t.id = ?"
            ),
        ),
        # Shared settings and lab-wide records: administrators only.
        Rule("/api/", "destination_id", admins),
        Rule("/api/workflows/", "key", admins),
        Rule("/api/workflows/", "save_id", admins),
        Rule("/api/support/", "report_id", admins),
    ]


def conversation_owner(request: Request, conversation_id: str) -> int | None:
    """Whose a conversation is (None: no such conversation)."""
    return request.app.state.services.conversations.owner_of(conversation_id)


def mine(request: Request, conversation_id: str) -> bool:
    """Whether a conversation is the signed-in person's: for lists that name
    conversations (proposals) rather than take one in their path."""
    if getattr(request.app.state, "services", None) is None:
        return True  # a single router on its own (tests), like current_user's
    return conversation_owner(request, conversation_id) == current_user(request).id


class RequireOwner:
    """A FastAPI dependency for every route (create_app's `dependencies`)."""

    def __init__(self, rules: list[Rule]) -> None:
        self._rules = rules

    def __call__(self, request: Request) -> None:
        route = request.scope.get("route")
        template: str = getattr(route, "path", request.url.path)
        user = current_user(request)
        if request.method not in _READS and template.startswith(ADMIN_WRITES) and not user.admin:
            raise HTTPException(403, "Only DataLab's administrators can change this.")
        if template.startswith(ADMIN_READS) and not user.admin:
            raise HTTPException(403, "Only DataLab's administrators can see this.")
        if not request.path_params:
            return
        for name, value in request.path_params.items():
            rule = self.rule_for(template, name)
            if rule is None:
                continue
            owner = rule.owner(str(value))
            allowed = user.admin if owner == ADMINS else owner == user.id
            if not allowed:
                raise HTTPException(404, "Not found.")

    def rule_for(self, template: str, param: str) -> Rule | None:
        found = [r for r in self._rules if r.param == param and template.startswith(r.prefix)]
        # The most specific prefix wins ("/api/workflows/" over "/api/").
        return max(found, key=lambda r: len(r.prefix)) if found else None
