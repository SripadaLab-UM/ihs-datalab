"""Send feedback: packaged support reports (support.py, docs/SUPPORT.md).

- `POST /api/support/preview` collects the diagnostics and builds the bundle
  from what the person wrote and the files they chose, without saving it:
  the page shows exactly what's in it. `POST /api/support/reports` then
  saves that very draft (by its `draft_id`) under the data folder.
- `GET /api/support/reports` lists saved reports; `GET …/{id}` reopens one
  with its contents; `GET …/{id}/bundle` downloads its ZIP; `DELETE` removes it.
- `POST …/{id}/save-to-folder` copies the ZIP to an export folder (practice:
  its own), and says what to do next: email it to the maintainer.
- `POST …/{id}/send` sends it to the lab's private support repository
  (`[repos] support`, real DataLab only), the one way a report is delivered.
  Pending sends are tried again once each time DataLab starts.

Like every /api route, these need the sign-in cookie, and each POST and
DELETE must be JSON from DataLab's own page (web.py ApiProtection). The
person's files come as base64 in the JSON: there are no uploads.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from datalab import diagnostics, export_folders, support
from datalab.api.exports import export_target
from datalab.api.settings import FeedbackContactOut, feedback_contact
from datalab.config import Settings
from datalab.exports import DestinationStore, ExportError
from datalab.repos.github import GitHubAuth
from datalab.sessions.titles import normalize_title, scrub_title

log = logging.getLogger(__name__)

_B64_MAX = (support.MAX_ATTACHMENT_BYTES * 4) // 3 + 8
_DRAFTS_KEPT = 5
_DRAFT_SECONDS = 30 * 60
MAILTO_LIMIT = 1800  # some mail apps cut mailto: links at about 2,000 characters


@dataclass(frozen=True)
class SupportServices:
    settings: Settings
    database: Any  # sqlite3.Connection
    destinations: DestinationStore
    auth: GitHubAuth | None
    docker_version: Callable[[], str] = diagnostics.docker_version


# ------------------------------------------------------------------ in and out


class SupportClientEntryIn(BaseModel):
    """A failed request or an error in the page, as the browser recorded it:
    the method, the path (only its route's shape is kept) and the status, or
    an error's class. Never a message or a response."""

    at: str | float
    kind: Literal["request", "page_error"]
    method: str | None = Field(default=None, max_length=10)
    path: str | None = Field(default=None, max_length=2000)
    status: int | None = None
    error: str | None = Field(default=None, max_length=100)


class SupportAttachmentIn(BaseModel):
    name: str = Field(max_length=300)
    data_base64: str = Field(max_length=_B64_MAX)


class SupportDraftIn(BaseModel):
    kind: Literal["bug", "suggestion"]
    happened: str = Field(max_length=support.TEXT_MAX)
    expected: str = Field(default="", max_length=support.TEXT_MAX)
    steps: str = Field(default="", max_length=support.TEXT_MAX)
    # The page's path (location.pathname): only the tab and a conversation's ID are kept.
    route: str | None = Field(default=None, max_length=2000)
    user_agent: str | None = Field(default=None, max_length=1000)
    client_trail: list[SupportClientEntryIn] = Field(default_factory=list, max_length=200)
    attachments: list[SupportAttachmentIn] = Field(
        default_factory=list, max_length=support.MAX_ATTACHMENTS
    )


class SupportBundleFileOut(BaseModel):
    path: str
    bytes: int
    sha256: str


class SupportContentsOut(BaseModel):
    """Exactly what's in the bundle, to read before saving or sharing."""

    files: list[SupportBundleFileOut]  # every file in the ZIP, manifest last
    summary: str  # summary.md
    diagnostics: str  # diagnostics.json, as it is in the ZIP
    manifest: str  # manifest.json, as it is in the ZIP


class SupportPreviewOut(BaseModel):
    draft_id: str  # save this very draft with POST /api/support/reports
    report_id: str  # DL-20260928-7F3K
    created_at: str
    zip_name: str
    zip_bytes: int
    zip_sha256: str
    contents: SupportContentsOut
    # Worth checking before saving: lines that look like they hold an ID, a
    # date or an email address, and the files chosen.
    warnings: list[str]


class SupportSaveIn(BaseModel):
    draft_id: str = Field(max_length=64)


class SupportFolderCopyOut(BaseModel):
    name: str  # the folder's friendly name
    file: str  # the full path of the saved ZIP
    where: str  # the same, with the home folder as ~
    saved_at: str
    saved_to: str  # "Saved to Lab Dropbox (on this computer)"
    sync_provider: str | None
    sync_note: str | None  # "Dropbox will upload it … DataLab can't confirm the upload …"
    practice: bool


class SupportGitHubSendOut(BaseModel):
    # pending: not sent yet, `reason` says why, and Retry (or the next start)
    # tries again. confirmed: GitHub has it (commit_sha). refused: won't be
    # sent as things are (a public repo, a different file at the path).
    state: Literal["pending", "confirmed", "refused"]
    repo: str
    reason: str | None = None
    attempts: int = 0
    last_attempt_at: str | None = None
    confirmed_at: str | None = None
    commit_sha: str | None = None
    html_url: str | None = None
    path: str | None = None


class SupportEmailOut(BaseModel):
    """ "Email this file to …": a mailto: link can't attach the file, so it says to."""

    contact: str | None  # "Ali <ali@umich.edu>", as the lab settings say
    to: str | None
    subject: str  # "DataLab report DL-…"
    body: str  # the report ID, the file to attach, and the summary: never the diagnostics
    mailto: str | None  # None without an address


State = Literal[
    "saved_locally", "saved_to_folder", "pending_retry", "confirmed_delivery", "refused"
]


class SupportReportOut(BaseModel):
    report_id: str
    kind: Literal["bug", "suggestion"]
    created_at: str
    headline: str  # the first line the person wrote
    saved_at: str
    zip_name: str
    zip_bytes: int
    zip_sha256: str
    attachments: list[SupportBundleFileOut]
    # Where it stands, oldest first. "confirmed_delivery" only ever comes
    # from the support repository's commit, never from a folder copy.
    states: list[State]
    folders: list[SupportFolderCopyOut]
    github: SupportGitHubSendOut | None
    email: SupportEmailOut


class SupportReportDetailOut(SupportReportOut):
    contents: SupportContentsOut


class SupportRepoOut(BaseModel):
    """Send to the lab: the private support repository, when it can be used here."""

    configured: bool  # [repos] support is set (and this isn't practice)
    repo: str | None
    available: bool  # configured, signed in to GitHub: the button can show
    signed_in: bool
    message: str | None  # why not, or what's wrong
    # Checked with GitHub when asked (?check=true): True only for a private repository.
    private: bool | None = None
    can_write: bool | None = None


class SupportStatusOut(BaseModel):
    profile: str
    practice: bool
    contact: FeedbackContactOut
    github: SupportRepoOut


class SupportSaveToFolderIn(BaseModel):
    destination_id: str = Field(max_length=100)


class SupportSendIn(BaseModel):
    # The person saw where it goes ("SripadaLab-UM/ihs-support (private)") and pressed Send.
    confirmed: bool = False


# ------------------------------------------------------------------ router


def build_support_router(services: SupportServices) -> APIRouter:
    settings = services.settings
    practice = settings.profile == "practice"
    store = support.ReportStore(settings.data_dir / "support")
    problems = diagnostics.recent_problems()
    drafts: dict[str, tuple[float, dict[str, Any], dict[str, bytes]]] = {}
    drafts_lock = threading.Lock()
    router = APIRouter(prefix="/api/support", tags=["support"])
    repo = None if practice else settings.repos.support
    auth = None if practice else services.auth

    def repo_state(check: bool = False) -> SupportRepoOut:
        if practice:
            return SupportRepoOut(
                configured=False, repo=None, available=False, signed_in=False,
                message="Practice DataLab saves reports to its own folder only.",
            )  # fmt: skip
        if repo is None:
            return SupportRepoOut(
                configured=False, repo=None, available=False, signed_in=False,
                message="Ask the maintainer to set up the support repo ([repos] support in the "
                "lab's settings.toml), for sending reports in one click.",
            )  # fmt: skip
        if auth is None:
            return SupportRepoOut(
                configured=True, repo=repo, available=False, signed_in=False,
                message="The lab's GitHub App isn't set ([repos] client_id), so DataLab can't "
                "sign in to GitHub.",
            )  # fmt: skip
        if not auth.signed_in():
            return SupportRepoOut(
                configured=True, repo=repo, available=False, signed_in=False,
                message="Sign in to GitHub (Settings → Connections) to send reports to the lab.",
            )  # fmt: skip
        out = SupportRepoOut(
            configured=True, repo=repo, available=True, signed_in=True, message=None
        )
        if check:
            try:
                support.check_repo(auth, repo)
            except support.Refused as refused:
                out.private, out.available, out.message = False, False, str(refused)
            except support.Pending as pending:
                out.message = str(pending)
                out.can_write = (
                    False if "can't" in str(pending) or "not add" in str(pending) else None
                )
            else:
                out.private, out.can_write = True, True
        return out

    def find(report_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            return store.report(report_id), store.status(report_id)
        except LookupError as error:
            raise HTTPException(404, "No such report.") from error

    def report_out(report: dict[str, Any], status: dict[str, Any]) -> SupportReportOut:
        return SupportReportOut(**_report_fields(report, status, settings))

    @router.get("/status")
    def status_route() -> SupportStatusOut:
        return SupportStatusOut(
            profile=settings.profile,
            practice=practice,
            contact=feedback_contact(settings.repos.access_contact),
            github=repo_state(),
        )

    @router.get("/github")
    async def github_route(check: bool = False) -> SupportRepoOut:
        """Whether Send to the lab can be used; with check=true, asks GitHub
        whether the repository is private and the person can add files."""
        return await asyncio.to_thread(repo_state, check)

    @router.post("/preview")
    async def preview_route(body: SupportDraftIn, request: Request) -> SupportPreviewOut:
        if not body.happened.strip():
            raise HTTPException(
                422, "Say what happened." if body.kind == "bug" else "Say what would help."
            )
        attachments = []
        for item in body.attachments:
            try:
                data = base64.b64decode(item.data_base64, validate=True)
            except (binascii.Error, ValueError) as error:
                raise HTTPException(422, f"{item.name[:80]} couldn't be read.") from error
            attachments.append(support.NewAttachment(item.name, data))
        try:
            attachments = support.check_attachments(attachments)
        except support.SupportError as error:
            raise HTTPException(422, str(error)) from error
        created_at = support.now_utc()
        report_id = support.new_report_id(
            created_at, lambda candidate: store.taken(candidate) or _drafted(drafts, candidate)
        )
        app = request.app

        def build() -> support.Preview:
            collected = support.collect(
                settings,
                services.database,
                report_id=report_id,
                kind=body.kind,
                created_at=created_at,
                route=body.route,
                user_agent=body.user_agent,
                client_trail=[
                    support.ClientEntry(e.at, e.kind, e.method, e.path, e.status, e.error)
                    for e in body.client_trail
                ],
                route_template=lambda method, path: _route_template(app, method, path),
                problems=problems.latest(),
                docker=services.docker_version(),
            )
            report = support.new_report(
                report_id=report_id,
                created_at=created_at,
                kind=body.kind,
                happened=body.happened,
                expected=body.expected,
                steps=body.steps,
                attachments=attachments,
                diagnostics=collected,
            )
            return support.preview(report, {a.name: a.data for a in attachments})

        built = await asyncio.to_thread(build)
        draft_id = secrets.token_urlsafe(16)
        with drafts_lock:
            now = time.monotonic()
            for key in [k for k, (at, _, _) in drafts.items() if now - at > _DRAFT_SECONDS]:
                drafts.pop(key)
            while len(drafts) >= _DRAFTS_KEPT:
                drafts.pop(next(iter(drafts)))
            drafts[draft_id] = (now, built.report, {a.name: a.data for a in attachments})
        return SupportPreviewOut(
            draft_id=draft_id,
            report_id=report_id,
            created_at=created_at,
            zip_name=support.zip_name(report_id),
            zip_bytes=len(built.zip),
            zip_sha256=_sha256(built.zip),
            contents=_contents(built.files),
            warnings=_warnings(body, len(attachments)),
        )

    @router.post("/reports", status_code=201)
    async def save_route(body: SupportSaveIn) -> SupportReportDetailOut:
        """Save the previewed draft, as it was shown, under the data folder."""
        with drafts_lock:
            draft = drafts.pop(body.draft_id, None)
        if draft is None:
            raise HTTPException(
                404, "That preview is no longer here (it was saved, or it's too old). "
                "Preview the report again."
            )  # fmt: skip
        _, report, attachments = draft
        try:
            await asyncio.to_thread(store.save, report, attachments, support.now_utc())
        except (support.SupportError, OSError) as error:
            raise HTTPException(422, f"The report wasn't saved: {error}") from error
        return await asyncio.to_thread(detail, report["report_id"])

    def detail(report_id: str) -> SupportReportDetailOut:
        report, status = find(report_id)
        built = support.preview(report, store.attachments(report))
        return SupportReportDetailOut(
            **_report_fields(report, status, settings), contents=_contents(built.files)
        )

    @router.get("/reports")
    def list_route() -> list[SupportReportOut]:
        found = []
        for report_id in store.ids():
            try:
                found.append(report_out(store.report(report_id), store.status(report_id)))
            except (LookupError, KeyError, TypeError):
                continue  # not a report DataLab can read: left as it is
        return found

    @router.get("/reports/{report_id}")
    async def get_route(report_id: str) -> SupportReportDetailOut:
        return await asyncio.to_thread(detail, report_id)

    @router.get("/reports/{report_id}/bundle")
    def bundle_route(report_id: str) -> Response:
        """The ZIP, as a download to the browser's own downloads folder (the
        browser marks it as downloaded)."""
        find(report_id)
        try:
            data = store.bundle(report_id)
        except support.SupportError as error:
            raise HTTPException(409, str(error)) from error
        name = support.zip_name(report_id)
        return Response(
            data,
            media_type="application/zip",
            headers={
                "content-disposition": f'attachment; filename="{name}"',
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            },
        )

    @router.delete("/reports/{report_id}", status_code=204)
    def delete_route(report_id: str) -> None:
        """Remove a saved report. Copies saved to folders or sent to the lab stay."""
        try:
            store.delete(report_id)
        except LookupError as error:
            raise HTTPException(404, "No such report.") from error

    @router.post("/reports/{report_id}/save-to-folder")
    async def save_to_folder_route(report_id: str, body: SupportSaveToFolderIn) -> SupportReportOut:
        """Copy the ZIP to an export folder (practice: its own) as <report-id>.zip.
        Trying again never makes a second copy."""
        find(report_id)
        target = export_target(settings, services.destinations, body.destination_id)
        try:
            copy = await asyncio.to_thread(support.save_to_folder, store, report_id, target)
        except (support.SupportError, ExportError, OSError) as error:
            raise HTTPException(422, f"The report wasn't saved there: {error}") from error
        entry = {
            "name": copy.name,
            "file": copy.file,
            "saved_at": support.now_utc(),
            "sync_provider": copy.sync_provider,
            "practice": practice,
        }

        def record(status: dict[str, Any]) -> None:
            folders = [f for f in status.get("folders") or [] if f.get("file") != copy.file]
            status["folders"] = [*folders, entry]

        status = await asyncio.to_thread(store.update_status, report_id, record)
        return report_out(store.report(report_id), status)

    @router.post("/reports/{report_id}/send")
    async def send_route(report_id: str, body: SupportSendIn) -> SupportReportOut:
        """Send the report to the lab's private support repository (Retry too).
        Once GitHub has it, sending again changes nothing."""
        report, status = find(report_id)
        if not body.confirmed:
            raise HTTPException(400, "Sending a report needs your confirmation.")
        state = repo_state()
        if not state.available or repo is None or auth is None:
            raise HTTPException(409, state.message or "Send to the lab isn't available here.")
        if (status.get("github") or {}).get("state") == "confirmed":
            return report_out(report, status)
        status = await asyncio.to_thread(
            support.send_to_repo, store, report_id, auth, repo, support.now_utc()
        )
        return report_out(report, status)

    def retry_pending() -> int:
        """Try each pending send once (at start). How many were confirmed."""
        if repo is None or auth is None or not auth.signed_in():
            return 0
        done = 0
        for report_id in support.pending_sends(store):
            try:
                status = support.send_to_repo(store, report_id, auth, repo, support.now_utc())
            except Exception as error:  # never stop DataLab starting
                log.warning("a pending support report wasn't retried: %s", type(error).__name__)
                continue
            done += (status.get("github") or {}).get("state") == "confirmed"
        return done

    router.retry_pending = retry_pending  # type: ignore[attr-defined]
    return router


# ------------------------------------------------------------------ helpers


def _drafted(drafts: dict[str, tuple[float, dict[str, Any], dict[str, bytes]]], report_id: str):
    return any(report["report_id"] == report_id for _, report, _ in list(drafts.values()))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _route_template(app: Any, method: str, path: str) -> str | None:
    """A request's path by its shape, such as "/api/conversations/{…}/files/{…}/{…}":
    only the fixed words of DataLab's own API routes are kept (from its
    OpenAPI schema), and every other part, an ID, a name or a file's path,
    becomes {…}. None for a path that isn't one of DataLab's API routes."""
    del method  # the shape is the same whatever the method
    parts = path.split("?", 1)[0].split("#", 1)[0].split("/")[1:]
    words = _api_words(app)
    if len(parts) < 2 or parts[0] != "api" or parts[1] not in words:
        return None
    shown = [part if part in words else "{…}" for part in parts[:12]]
    return "/" + "/".join(shown) + ("/…" if len(parts) > 12 else "")


def _api_words(app: Any) -> frozenset[str]:
    cached = getattr(app.state, "support_api_words", None)
    if cached is None:
        try:
            paths = app.openapi().get("paths", {})
        except Exception:  # a schema that can't be built: keep nothing but "api"
            paths = {}
        cached = frozenset(
            part
            for template in paths
            for part in template.split("/")
            if part and not part.startswith("{")
        ) | {"api"}
        app.state.support_api_words = cached
    return cached


def _contents(files: list[tuple[str, bytes]]) -> SupportContentsOut:
    texts = dict(files)
    return SupportContentsOut(
        files=[SupportBundleFileOut(path=p, bytes=len(d), sha256=_sha256(d)) for p, d in files],
        summary=texts[support.SUMMARY].decode(),
        diagnostics=texts[support.DIAGNOSTICS].decode(),
        manifest=texts[support.MANIFEST].decode(),
    )


def _warnings(body: SupportDraftIn, attachments: int) -> list[str]:
    found = []
    fields = [("What happened" if body.kind == "bug" else "What would help", body.happened)]
    if body.kind == "bug":
        fields.append(("What you expected", body.expected))
    fields.append(("Steps", body.steps))
    for label, text in fields:
        for number, line in enumerate(text.splitlines(), start=1):
            shown = normalize_title(line)
            if shown and scrub_title(shown) != shown:
                found.append(
                    f"{label}, line {number}, may hold an ID, a date or an email address. "
                    "Check it isn't participant data."
                )
    if attachments:
        them = "this file" if attachments == 1 else f"these {attachments} files"
        found.append(
            f"You chose {them}: DataLab doesn't look inside them. Check they show no "
            "participant data, query results or conversations."
        )
    return found


def _report_fields(
    report: dict[str, Any], status: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    report_id = report["report_id"]
    folders = [
        SupportFolderCopyOut(
            name=f["name"],
            file=f["file"],
            where=export_folders.display_path(Path(f["file"])),
            saved_at=f["saved_at"],
            saved_to=export_folders.saved_to(f["name"]),
            sync_provider=f.get("sync_provider"),
            sync_note=support.sync_note(f.get("sync_provider")),
            practice=bool(f.get("practice")),
        )
        for f in status.get("folders") or []
    ]
    github = status.get("github")
    return {
        "report_id": report_id,
        "kind": report["kind"],
        "created_at": report["created_at"],
        "headline": (report["happened"].splitlines() or [""])[0][:120],
        "saved_at": status["saved_at"],
        "zip_name": support.zip_name(report_id),
        "zip_bytes": status["zip_bytes"],
        "zip_sha256": status["zip_sha256"],
        "attachments": [
            SupportBundleFileOut(
                path=f"{support.ATTACHMENTS}/{a['name']}", bytes=a["bytes"], sha256=a["sha256"]
            )
            for a in report["attachments"]
        ],
        "states": support.states(status),
        "folders": folders,
        "github": SupportGitHubSendOut(**_known(github, SupportGitHubSendOut)) if github else None,
        "email": email_for(report, folders[-1] if folders else None, settings.repos.access_contact),
    }


def _known(raw: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k in model.model_fields}


def email_for(
    report: dict[str, Any], folder: SupportFolderCopyOut | None, contact: str | None
) -> SupportEmailOut:
    """The email to the maintainer: the report ID, the file to attach, and the
    summary (what the person wrote). Never the diagnostics: they're in the file."""
    who = feedback_contact(contact)
    report_id = report["report_id"]
    subject = f"DataLab report {report_id}"
    where = f", saved in {folder.name}" if folder else ""
    head = (
        f"Hello,\n\nPlease find DataLab report {report_id} ({support.kind_label(report['kind'])}) "
        f"attached.\n\nAttach the file: {support.zip_name(report_id)}{where}. (An email link "
        "can't attach it for you.)\n\n"
    )
    summary = support.summary_text(report)
    summary = summary.split("\n## Diagnostics", 1)[0].rstrip() + "\n"
    body = head + summary

    def link(text: str) -> str:
        return f"mailto:{who.email}?subject={quote(subject)}&body={quote(text)}"

    mailto = None
    if who.email:
        mailto = link(body)
        if len(mailto) > MAILTO_LIMIT:
            cut = head + "(The summary is cut short here: all of it is in the attached file.)\n"
            room = MAILTO_LIMIT - len(link(cut))
            text = summary
            while text and len(quote(text)) > room:
                text = text[: max(0, len(text) - max(20, (len(quote(text)) - room) // 3))]
            body = (
                head
                + text.rstrip()
                + "\n\n(The summary is cut short here: all of it is in the attached file.)\n"
            )
            mailto = link(body)
    return SupportEmailOut(
        contact=who.contact, to=who.email, subject=subject, body=body, mailto=mailto
    )
