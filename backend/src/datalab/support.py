"""Support reports: the toolbar's Send feedback, packaged (docs/SUPPORT.md).

A report is what the person wrote (a bug or a suggestion), optional files
they chose, and diagnostics DataLab collects itself. It's kept under the
data folder, `<data_dir>/support/<report-id>/`, and packaged as one ZIP:

    summary.md        what the person wrote, and the basics, to read
    diagnostics.json  the collected diagnostics, structured
    manifest.json     every other file's size and sha256, the report id,
                      when it was made, DataLab's version, the schema version
    attachments/      the files the person chose, if any

The ZIP is built from the stored report alone, with fixed timestamps and
order, so building it again gives the very same bytes (and the same hash).

What's collected is an allowlist (`collect`), field by field: versions,
the profile, the operating system, Docker, the browser's user agent, which
tab was open (never a query string, nor anything after a tab's own name
but a conversation's ID), recent operation IDs (the conversation, queries,
workflow runs, proposals, pipeline tests, model request IDs), and a bounded
trail of recent activity and errors: times, event types, statuses, error
classes and codes. Never SQL, bind values, query results or rows,
conversation or message content, titles, file contents or names from a
workspace, keys, tokens, passwords, paths, or environment values. The
only free text is DataLab's own log templates, which are scrubbed of
anything shaped like an ID, a date or an email address as well.

It goes to the maintainer one of two ways, both chosen by the person:

- saved to an export folder (practice: its own folder), for them to email;
  that says "saved to <folder> (on this computer)", never delivered, and
  for a sync folder who will upload it;
- sent to the lab's private support repository on GitHub
  (`[repos] support`, real DataLab only), through the GitHub sign-in
  DataLab already has. Only there does a report count as delivered: when
  GitHub answers with the commit. Until then it's pending, with the reason,
  and is tried again when the person asks, and once each time DataLab starts.

Every write is idempotent: a folder copy is named by the report ID and
checked by its sha256, and the repository's file by its git blob hash, so
trying again never makes a second copy or a second commit, and never
replaces a different file that's there.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import threading
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from datalab import __version__, db, exports
from datalab.config import Settings
from datalab.db.backups import applied_migrations
from datalab.diagnostics import Problem
from datalab.export_folders import PROVIDER_NAMES, Target, open_target
from datalab.exports import ExportError
from datalab.sessions.titles import scrub_title

SCHEMA_VERSION = 1
Kind = Literal["bug", "suggestion"]

# DL-20260928-7F3K: the date, and four characters of Crockford's base 32
# (no I, L, O or U, so it reads aloud and can't be misread).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
REPORT_ID = re.compile(r"DL-\d{8}-[0-9A-HJKMNP-TV-Z]{4}")

TEXT_MAX = 10_000  # characters, for each thing the person writes
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 5 * 1024**2
MAX_ATTACHMENTS_BYTES = 10 * 1024**2
TRAIL_MAX = 50  # entries in the recent trail
TRAIL_HOURS = 24
IDS_MAX = 10  # recent IDs of each kind
CLIENT_TRAIL_MAX = 50

SUMMARY = "summary.md"
DIAGNOSTICS = "diagnostics.json"
MANIFEST = "manifest.json"
ATTACHMENTS = "attachments"


class SupportError(RuntimeError):
    """Something a person can act on, said plainly."""


# The allowlist ------------------------------------------------------------------

# The tabs the web UI has; any other first part of a route is "other".
_TABS = ("workspace", "sql", "workflows", "pipelines", "knowledge", "settings", "help")
_CONVERSATION_ID = re.compile(r"c_[0-9a-f]{16}")
# An ID DataLab made (q_…, run_…, a proposal's): nothing a person typed.
_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_WORD = re.compile(r"[a-z][a-z0-9_]{0,31}")
_CODE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_SECTION = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Conversation events in the trail, by type; only these fields of each, and
# only when they're a plain word, a code, a number or an ID.
_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "turn_finished": ("status",),
    "turn_done": (),
    "error": (),
    "stop_requested": (),
    "model_status": ("state", "kind", "status", "code", "attempt", "request_id"),
    "review_started": (),
    "review_finished": ("status",),
    "approval_answered": ("decision",),
    "exported": (),
    "kb_proposal": (),
    "provenance": (),
}
_STATUSES = {
    "queries": ("running", "succeeded", "failed", "rejected", "cancelled"),
    "workflow_runs": ("queued", "running", "succeeded", "failed", "cancelled", "interrupted"),
    "workflow_run_steps": ("pending", "running", "succeeded", "failed", "skipped", "cancelled"),
    "proposals": (
        "open", "superseded", "withdrawn", "rejected", "saving", "saved", "conflict",
        "check_failed", "tests_failed", "failed",
    ),
    "pipeline_tests": ("running", "passed", "failed", "error"),
}  # fmt: skip
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS = re.compile(r"\d{9,}")
_PRINTABLE = re.compile(r"[^\x20-\x7e]")


def safe_route(route: str | None) -> tuple[str, str | None, str | None]:
    """The tab, the route as shown in a report, and a conversation's ID, from
    the page's path. Never the query string or fragment, and nothing after
    the tab's name except a conversation's ID or a Settings/Help section."""
    path = (route or "").split("?", 1)[0].split("#", 1)[0]
    parts = [part for part in path.split("/") if part]
    if not parts or parts[0] not in _TABS:
        return "other", None, None
    tab = parts[0]
    shown = f"/{tab}"
    conversation = None
    rest = parts[1:]
    if tab == "workspace" and rest and _CONVERSATION_ID.fullmatch(rest[0]):
        conversation = rest[0]
        shown += "/{conversation}"
        rest = rest[1:]
    elif tab in ("settings", "help") and rest and _SECTION.fullmatch(rest[0]):
        shown += f"/{rest[0]}"
        rest = rest[1:]
    if rest:
        shown += "/…"
    return tab, shown, conversation


def safe_user_agent(text: str | None) -> str | None:
    """The browser's user agent: printable, short, and with nothing shaped
    like an email address or a long number (a browser add-on could put one there)."""
    if not text:
        return None
    text = _PRINTABLE.sub("", text)[:300]
    text = _LONG_DIGITS.sub("…", _EMAIL.sub("…", text))
    return " ".join(text.split()) or None


def _word(value: object) -> str | None:
    return value if isinstance(value, str) and _WORD.fullmatch(value) else None


def _code(value: object) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _operation_id(value: object) -> str | None:
    return value if isinstance(value, str) and _OPERATION_ID.fullmatch(value) else None


def _status(table: str, value: object) -> str:
    return value if isinstance(value, str) and value in _STATUSES[table] else "other"


def _at(value: object) -> str | None:
    """A timestamp as ISO 8601 in UTC, to the second, or None."""
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:  # the browser's milliseconds
            when = datetime.fromtimestamp(float(value) / 1000, UTC)
    except (ValueError, OverflowError, OSError):
        return None
    if when.tzinfo is None:
        when = when.astimezone()
    return when.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class ClientEntry:
    """A failed request or an error in the page, as the browser recorded it."""

    at: object
    kind: str  # "request" or "page_error"
    method: str | None = None
    path: str | None = None
    status: int | None = None
    error: str | None = None  # an error's class, such as TypeError


def collect(
    settings: Settings,
    database: sqlite3.Connection,
    *,
    report_id: str,
    kind: Kind,
    created_at: str,
    route: str | None,
    user_agent: str | None,
    client_trail: Iterable[ClientEntry] = (),
    route_template: Callable[[str, str], str | None] = lambda method, path: None,
    problems: Iterable[Problem] = (),
    docker: str = "not checked",
) -> dict[str, Any]:
    """The report's diagnostics: the allowlisted fields and nothing else."""
    now = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    since = now - timedelta(hours=TRAIL_HOURS)
    tab, shown_route, conversation = safe_route(route)
    if conversation is not None and not _exists(database, "conversations", conversation):
        conversation = None
    trail: list[dict[str, Any]] = []
    operations = _operations(database, since, conversation, trail)
    for problem in problems:
        at = _at(problem.at)
        if at is None:
            continue
        entry: dict[str, Any] = {
            "at": at,
            "source": "log",
            "event": f"log_{problem.level.lower()}"
            if problem.level in ("WARNING", "ERROR", "CRITICAL")
            else "log",
            "logger": problem.logger if re.fullmatch(r"[\w.]{1,80}", problem.logger) else "other",
        }
        if problem.exception and re.fullmatch(r"\w{1,80}", problem.exception):
            entry["error_class"] = problem.exception
        if problem.where:
            entry["where"] = scrub_title(problem.where)[:160] or None
        if problem.template:
            # DataLab's own template, with nothing filled in; scrubbed as well.
            entry["template"] = scrub_title(problem.template)[:160] or None
        trail.append(entry)
    for item in list(client_trail)[-CLIENT_TRAIL_MAX:]:
        at = _at(item.at)
        if at is None:
            continue
        if item.kind == "request":
            method = item.method if item.method in _METHODS else None
            template = route_template(method or "GET", item.path or "") if item.path else None
            status = (
                item.status if isinstance(item.status, int) and 0 <= item.status < 600 else None
            )
            trail.append(
                {
                    "at": at,
                    "source": "browser",
                    "event": "request_failed" if status else "request_unreachable",
                    "method": method,
                    "route": template or "(other)",
                    "status": status,
                }
            )
        elif item.kind == "page_error":
            error = item.error if item.error and re.fullmatch(r"\w{1,60}", item.error) else None
            trail.append(
                {"at": at, "source": "browser", "event": "page_error", "error_class": error}
            )
    cutoff = since.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    trail = [entry for entry in trail if entry["at"] >= cutoff]
    trail.sort(key=lambda entry: (entry["at"], entry["source"], entry["event"]))
    trail = [{k: v for k, v in entry.items() if v is not None} for entry in trail[-TRAIL_MAX:]]
    applied = applied_migrations(database)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "created_at": created_at,
        "kind": kind,
        "app": {"version": __version__, "profile": settings.profile},
        "system": _system(docker),
        "browser": {"user_agent": safe_user_agent(user_agent)},
        "context": {"tab": tab, "route": shown_route},
        "operations": operations,
        "database_layout": {
            "migrations_applied": len(applied),
            "latest": applied[-1] if applied else None,
            "not_applied": len(db.known_migrations() - set(applied)),
        },
        "safety_check": _safety(settings.data_dir / "logs" / "safety-last.json"),
        "trail": {
            "window_hours": TRAIL_HOURS,
            "max_entries": TRAIL_MAX,
            "entries": trail,
        },
    }


def _system(docker: str) -> dict[str, Any]:
    os_version = platform.release()
    if sys.platform == "darwin":
        os_version = platform.mac_ver()[0] or os_version
    elif sys.platform == "win32":
        os_version = platform.version() or os_version
    return {
        "os": platform.system(),
        "os_version": os_version[:40],
        "machine": platform.machine()[:20],
        "python": platform.python_version(),
        "docker": docker[:80],
    }


def _exists(database: sqlite3.Connection, table: str, row_id: str) -> bool:
    try:
        return (
            database.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def _rows(database: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[Any]:
    try:
        return database.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []  # a table this database doesn't have yet


def _operations(
    database: sqlite3.Connection,
    since: datetime,
    conversation: str | None,
    trail: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recent operation IDs, and their trail entries: metadata columns only."""
    after = since.astimezone(UTC).isoformat()
    # Stored times are local ISO strings with an offset: compare as times.
    local_after = since.astimezone().isoformat()
    ids: dict[str, list[str]] = {
        "queries": [],
        "workflow_runs": [],
        "proposals": [],
        "pipeline_tests": [],
        "model_requests": [],
    }

    def add(name: str, row_id: object) -> str | None:
        found = _operation_id(row_id)
        if found and found not in ids[name] and len(ids[name]) < IDS_MAX:
            ids[name].append(found)
        return found

    for row in _rows(
        database,
        "SELECT id, origin, status, started_at, elapsed_ms FROM queries "
        "WHERE started_at >= ? OR started_at >= ? ORDER BY started_at DESC LIMIT ?",
        (after, local_after, TRAIL_MAX),
    ):
        query_id = add("queries", row[0])
        trail.append(
            {
                "at": _at(row[3]),
                "source": "database",
                "event": "query",
                "id": query_id,
                "origin": _word(row[1]),
                "status": _status("queries", row[2]),
                "elapsed_ms": row[4] if isinstance(row[4], int) else None,
            }
        )
    for row in _rows(
        database,
        "SELECT id, mode, status, started_at, finished_at FROM workflow_runs "
        "WHERE started_at >= ? OR started_at >= ? ORDER BY started_at DESC LIMIT ?",
        (after, local_after, TRAIL_MAX),
    ):
        run_id = add("workflow_runs", row[0])
        trail.append(
            {
                "at": _at(row[4] or row[3]),
                "source": "workflows",
                "event": "workflow_run",
                "id": run_id,
                "mode": _word(row[1]),
                "status": _status("workflow_runs", row[2]),
            }
        )
    for row in _rows(
        database,
        "SELECT run_id, kind, status, finished_at FROM workflow_run_steps "
        "WHERE status = 'failed' AND (finished_at >= ? OR finished_at >= ?) "
        "ORDER BY finished_at DESC LIMIT ?",
        (after, local_after, TRAIL_MAX),
    ):
        trail.append(
            {
                "at": _at(row[3]),
                "source": "workflows",
                "event": "workflow_step",
                "id": _operation_id(row[0]),
                "step_kind": _word(row[1]),
                "status": _status("workflow_run_steps", row[2]),
            }
        )
    for table, area in (("kb_proposals", "knowledge"), ("pipeline_proposals", "pipelines")):
        for row in _rows(
            database,
            f"SELECT id, status, updated_at FROM {table} "
            "WHERE updated_at >= ? OR updated_at >= ? ORDER BY updated_at DESC LIMIT ?",
            (after, local_after, TRAIL_MAX),
        ):
            trail.append(
                {
                    "at": _at(row[2]),
                    "source": area,
                    "event": "proposal",
                    "id": add("proposals", row[0]),
                    "status": _status("proposals", row[1]),
                }
            )
    for row in _rows(
        database,
        "SELECT id, status, started_at FROM pipeline_tests "
        "WHERE started_at >= ? OR started_at >= ? ORDER BY started_at DESC LIMIT ?",
        (after, local_after, TRAIL_MAX),
    ):
        trail.append(
            {
                "at": _at(row[2]),
                "source": "pipelines",
                "event": "pipeline_test",
                "id": add("pipeline_tests", row[0]),
                "status": _status("pipeline_tests", row[1]),
            }
        )
    for row in _rows(database, "SELECT repo, synced_at, error_at FROM repo_sync", ()):
        area = row[0] if row[0] in ("knowledge", "pipelines") else "other"
        failed = bool(row[2]) and (not row[1] or str(row[2]) >= str(row[1]))
        trail.append(
            {
                "at": _at(row[2] if failed else row[1]),
                "source": area,
                "event": "repo_sync",
                "status": "failed" if failed else "succeeded",
            }
        )
    if conversation is not None:
        types = list(_EVENT_FIELDS)
        marks = ",".join("?" * len(types))
        for row in _rows(
            database,
            f"SELECT created_at, type, data_json FROM events WHERE conversation_id = ? "
            f"AND type IN ({marks}) ORDER BY seq DESC LIMIT ?",
            (conversation, *types, TRAIL_MAX),
        ):
            try:
                data = json.loads(row[2])
            except (ValueError, TypeError):
                data = {}
            entry: dict[str, Any] = {
                "at": _at(row[0]),
                "source": "conversation",
                "event": row[1],
            }
            for field in _EVENT_FIELDS[row[1]]:
                value = data.get(field) if isinstance(data, dict) else None
                if field == "request_id":
                    entry[field] = add("model_requests", value)
                elif field == "attempt" or (field == "status" and isinstance(value, int)):
                    entry[field] = value if isinstance(value, int) and 0 <= value < 1000 else None
                elif field == "code":
                    entry[field] = _code(value)
                else:
                    entry[field] = _word(value)
            if row[1] == "error":
                entry["status"] = "failed"
            trail.append(entry)
    for entry in trail:
        entry.setdefault("at", None)
    trail[:] = [entry for entry in trail if entry["at"]]
    return {"conversation_id": conversation, **ids}


def _safety(file: Path) -> dict[str, Any] | None:
    """The last Safety check: when, whether it passed, and each check's id and status."""
    try:
        report = json.loads(file.read_text(encoding="utf-8"))
        results = report["results"]
        if not isinstance(results, list):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    counts: dict[str, int] = {}
    not_passed = []
    for result in results:
        if not isinstance(result, dict):
            continue
        status = _word(result.get("status")) or "other"
        counts[status] = counts.get(status, 0) + 1
        check = _code(result.get("id"))
        if status != "pass" and check:
            not_passed.append({"id": check, "status": status})
    return {
        "finished_at": _at(report.get("finished_at")),
        "passed": bool(report.get("passed")),
        "counts": dict(sorted(counts.items())),
        "not_passed": not_passed[:30],
    }


# The report and its bundle -----------------------------------------------------------


@dataclass(frozen=True)
class NewAttachment:
    name: str
    data: bytes


def attachment_name(name: str, taken: set[str]) -> str:
    """A safe, unique file name for an attachment: no folders, no characters
    Windows or a Mac refuse, and `.txt` added to a type that would run."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = exports.inert_name(exports.effective_name(exports.safe_name(base, 80, 120)))
    if base in ("export", ""):
        base = "attachment"
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    candidate, number = base, 2
    while candidate.casefold() in taken:
        candidate = f"{stem} ({number}).{suffix}" if suffix else f"{stem} ({number})"
        number += 1
    taken.add(candidate.casefold())
    return candidate


def check_attachments(attachments: list[NewAttachment]) -> list[NewAttachment]:
    """The person's files, named safely, or SupportError for too many or too large."""
    if len(attachments) > MAX_ATTACHMENTS:
        raise SupportError(f"Attach at most {MAX_ATTACHMENTS} files.")
    total = 0
    taken: set[str] = set()
    named = []
    for attachment in attachments:
        if len(attachment.data) > MAX_ATTACHMENT_BYTES:
            raise SupportError(
                f"{attachment.name[:80]} is larger than {MAX_ATTACHMENT_BYTES // 1024**2} MB."
            )
        total += len(attachment.data)
        named.append(NewAttachment(attachment_name(attachment.name, taken), attachment.data))
    if total > MAX_ATTACHMENTS_BYTES:
        raise SupportError(
            f"The attachments come to more than {MAX_ATTACHMENTS_BYTES // 1024**2} MB together."
        )
    return named


def new_report(
    *,
    report_id: str,
    created_at: str,
    kind: Kind,
    happened: str,
    expected: str,
    steps: str,
    attachments: list[NewAttachment],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """The report as it's stored (report.json): everything the bundle is built from."""
    return {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "created_at": created_at,
        "kind": kind,
        "happened": _tidy(happened),
        "expected": _tidy(expected) if kind == "bug" else "",
        "steps": _tidy(steps),
        "attachments": [
            {"name": a.name, "bytes": len(a.data), "sha256": hashlib.sha256(a.data).hexdigest()}
            for a in attachments
        ],
        "diagnostics": diagnostics,
    }


def _tidy(text: str) -> str:
    text = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
    return "\n".join(line.rstrip() for line in text.strip().splitlines())[:TEXT_MAX]


def new_report_id(created_at: str, taken: Callable[[str], bool]) -> str:
    day = created_at[:10].replace("-", "")
    for _ in range(200):
        candidate = f"DL-{day}-{''.join(secrets.choice(_ALPHABET) for _ in range(4))}"
        if not taken(candidate):
            return candidate
    raise SupportError("DataLab couldn't choose a report ID. Try again.")


def kind_label(kind: str) -> str:
    return "Bug report" if kind == "bug" else "Suggestion"


def summary_text(report: dict[str, Any]) -> str:
    """summary.md: what the person wrote, and the basics, to read (also on GitHub)."""
    diagnostics = report.get("diagnostics") or {}
    app = diagnostics.get("app") or {}
    system = diagnostics.get("system") or {}
    context = diagnostics.get("context") or {}
    operations = diagnostics.get("operations") or {}
    lines = [
        f"# DataLab report {report['report_id']}",
        "",
        f"- Type: {kind_label(report['kind'])}",
        f"- Made: {report['created_at']}",
        f"- DataLab: {app.get('version', '?')} ({app.get('profile', '?')})",
        f"- System: {system.get('os', '?')} {system.get('os_version', '')}".rstrip(),
        f"- Browser: {(diagnostics.get('browser') or {}).get('user_agent') or 'not given'}",
        f"- Tab: {context.get('route') or context.get('tab') or 'not given'}",
    ]
    if operations.get("conversation_id"):
        lines.append(f"- Conversation: {operations['conversation_id']}")
    sections = (
        [("What happened", report["happened"]), ("What I expected", report["expected"])]
        if report["kind"] == "bug"
        else [("Suggestion", report["happened"])]
    )
    sections.append(("Steps to reproduce", report["steps"]))
    for title, text in sections:
        if text or title != "Steps to reproduce":
            lines += ["", f"## {title}", "", text or "(not given)"]
    if report["attachments"]:
        lines += ["", "## Attachments", ""]
        lines += [
            f"- {ATTACHMENTS}/{a['name']} ({a['bytes']:,} bytes)" for a in report["attachments"]
        ]
    trail = (diagnostics.get("trail") or {}).get("entries") or []
    problems = [e for e in trail if e.get("status") in ("failed", "rejected", "error")]
    problems += [
        e for e in trail if e.get("event") in ("log_error", "page_error", "request_failed")
    ]
    lines += [
        "",
        "## Diagnostics",
        "",
        f"{len(trail)} recent entries in {DIAGNOSTICS} (last {TRAIL_HOURS} hours), "
        f"{len(problems)} of them failures or errors. Metadata only: no SQL, results, "
        "conversation content, keys or paths.",
        "",
    ]
    return "\n".join(lines)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def bundle_files(report: dict[str, Any], attachments: dict[str, bytes]) -> list[tuple[str, bytes]]:
    """The bundle's files, in order, manifest last."""
    files = [
        (SUMMARY, summary_text(report).encode()),
        (DIAGNOSTICS, _json_bytes(report["diagnostics"])),
    ]
    for entry in report["attachments"]:
        data = attachments[entry["name"]]
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SupportError(f"The attachment {entry['name']} has changed since it was saved.")
        files.append((f"{ATTACHMENTS}/{entry['name']}", data))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report["report_id"],
        "created_at": report["created_at"],
        "kind": report["kind"],
        "app_version": (report["diagnostics"].get("app") or {}).get("version"),
        "files": [
            {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in files
        ],
    }
    files.append((MANIFEST, _json_bytes(manifest)))
    return files


def zip_bytes(files: list[tuple[str, bytes]], created_at: str) -> bytes:
    """The ZIP, the same bytes every time for the same files: fixed times,
    order, permissions and compression."""
    when = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(UTC)
    stamp = (max(when.year, 1980), when.month, when.day, when.hour, when.minute, when.second)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files:
            info = zipfile.ZipInfo(path, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data, compresslevel=6)
    return buffer.getvalue()


def git_blob_sha(data: bytes) -> str:
    """The hash git (and GitHub's Contents API) gives a file with these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def zip_name(report_id: str) -> str:
    return f"{report_id}.zip"


# Where reports are kept -------------------------------------------------------------

State = Literal[
    "saved_locally", "saved_to_folder", "pending_retry", "confirmed_delivery", "refused"
]


@dataclass(frozen=True)
class Preview:
    report: dict[str, Any]
    files: list[tuple[str, bytes]]
    zip: bytes


def preview(report: dict[str, Any], attachments: dict[str, bytes]) -> Preview:
    files = bundle_files(report, attachments)
    return Preview(report, files, zip_bytes(files, report["created_at"]))


class ReportStore:
    """`<data_dir>/support/<report-id>/`: report.json, attachments/, the ZIP, status.json.

    A report is written into a hidden folder and renamed into place, so a
    half-saved report is never listed. The status file is replaced whole.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()

    def folder(self, report_id: str) -> Path:
        if not REPORT_ID.fullmatch(report_id):
            raise LookupError("No such report.")
        return self.root / report_id

    def taken(self, report_id: str) -> bool:
        return (self.root / report_id).exists()

    def save(self, report: dict[str, Any], attachments: dict[str, bytes], now: str) -> None:
        built = preview(report, attachments)
        report_id = report["report_id"]
        with self._lock:
            final = self.folder(report_id)
            if final.exists():
                raise SupportError("This report is already saved.")
            self.root.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                self.root.chmod(0o700)
            work = self.root / f".{report_id}.{secrets.token_hex(4)}"
            work.mkdir(mode=0o700)
            try:
                (work / ATTACHMENTS).mkdir()
                for name, data in attachments.items():
                    (work / ATTACHMENTS / name).write_bytes(data)
                (work / "report.json").write_bytes(_json_bytes(report))
                (work / zip_name(report_id)).write_bytes(built.zip)
                status = {
                    "saved_at": now,
                    "zip_bytes": len(built.zip),
                    "zip_sha256": hashlib.sha256(built.zip).hexdigest(),
                    "folders": [],
                    "github": None,
                }
                (work / "status.json").write_bytes(_json_bytes(status))
                os.rename(work, final)
            except BaseException:
                shutil.rmtree(work, ignore_errors=True)
                raise

    def ids(self) -> list[str]:
        try:
            names = [p.name for p in self.root.iterdir() if REPORT_ID.fullmatch(p.name)]
        except OSError:
            return []
        return sorted(names, reverse=True)

    def report(self, report_id: str) -> dict[str, Any]:
        try:
            return json.loads((self.folder(report_id) / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LookupError("No such report.") from error

    def status(self, report_id: str) -> dict[str, Any]:
        try:
            return json.loads((self.folder(report_id) / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LookupError("No such report.") from error

    def attachments(self, report: dict[str, Any]) -> dict[str, bytes]:
        folder = self.folder(report["report_id"]) / ATTACHMENTS
        return {a["name"]: (folder / a["name"]).read_bytes() for a in report["attachments"]}

    def bundle(self, report_id: str) -> bytes:
        """The saved ZIP, checked against the hash recorded when it was saved;
        rebuilt from the report (the same bytes) if it's gone."""
        status = self.status(report_id)
        path = self.folder(report_id) / zip_name(report_id)
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        if hashlib.sha256(data).hexdigest() != status["zip_sha256"]:
            report = self.report(report_id)
            data = preview(report, self.attachments(report)).zip
            if hashlib.sha256(data).hexdigest() != status["zip_sha256"]:
                raise SupportError("This report's files have changed since it was saved.")
            path.write_bytes(data)
        return data

    def update_status(self, report_id: str, change: Callable[[dict[str, Any]], None]) -> dict:
        with self._lock:
            status = self.status(report_id)
            change(status)
            folder = self.folder(report_id)
            temporary = folder / f".status.{secrets.token_hex(4)}"
            temporary.write_bytes(_json_bytes(status))
            os.replace(temporary, folder / "status.json")
            return status

    def delete(self, report_id: str) -> None:
        with self._lock:
            folder = self.folder(report_id)
            if not folder.is_dir():
                raise LookupError("No such report.")
            shutil.rmtree(folder)


def states(status: dict[str, Any]) -> list[State]:
    """Where a report stands, most recent news last. Only a GitHub commit is a delivery."""
    shown: list[State] = ["saved_locally"]
    if status.get("folders"):
        shown.append("saved_to_folder")
    github = status.get("github") or {}
    state = github.get("state")
    if state == "pending":
        shown.append("pending_retry")
    elif state == "confirmed":
        shown.append("confirmed_delivery")
    elif state == "refused":
        shown.append("refused")
    return shown


# A. Saving to an export folder ------------------------------------------------------


@dataclass(frozen=True)
class FolderCopy:
    file: str  # the full path, for the person to find it
    name: str  # the folder's friendly name
    sync_provider: str | None
    already_there: bool  # an identical copy was there: nothing was written


_PARTIAL = re.compile(r"\.(DL-\d{8}-[0-9A-HJKMNP-TV-Z]{4})\.zip\.partial-[0-9a-f]{8}")


def save_to_folder(store: ReportStore, report_id: str, target: Target) -> FolderCopy:
    """Copy the report's ZIP into a checked export folder, as `<report-id>.zip`.

    Through the folder opened when it was checked (never by path). Written
    to a hidden partial file first and then linked into place, which never
    replaces a file: if one is there with the same sha256, the report is
    already there and nothing is written; if a different one is there, it's
    left alone and SupportError says so. A partial file an interrupted save
    left behind is removed.
    """
    data = store.bundle(report_id)
    digest = hashlib.sha256(data).hexdigest()
    name = zip_name(report_id)
    with open_target(target) as folder:
        _remove_partials(folder, report_id)
        existing = _sha256_of(folder, name)
        if existing is not None:
            if existing != digest:
                raise SupportError(
                    f"A different file called {name} is already in {target.name}. DataLab left "
                    "it as it is: move or rename it, then save again."
                )
            return FolderCopy(str(folder.path / name), target.name, target.sync_provider, True)
        partial = f".{report_id}.zip.partial-{secrets.token_hex(4)}"
        fd = folder.open_file(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            try:
                folder.link_new(partial, name)
            except FileExistsError:
                # Saved meanwhile (another window): the same file counts.
                if _sha256_of(folder, name) != digest:
                    raise SupportError(
                        f"A different file called {name} appeared in {target.name} while "
                        "DataLab was saving. It was left as it is."
                    ) from None
        finally:
            with contextlib.suppress(OSError, ExportError):
                folder.unlink(partial)
        exports.mark_downloaded(folder.path / name)
        return FolderCopy(str(folder.path / name), target.name, target.sync_provider, False)


def _sha256_of(folder: exports.Folder, name: str) -> str | None:
    """A regular file's sha256 in the folder, or None when nothing is there."""
    try:
        info = folder.lstat(name)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return "not a file"
    digest = hashlib.sha256()
    fd = folder.open_file(name, os.O_RDONLY)
    with os.fdopen(fd, "rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_partials(folder: exports.Folder, report_id: str) -> None:
    """Remove partial files this report's earlier saves left (only those)."""
    try:
        names = os.listdir(folder.path)
    except OSError:
        return
    for name in names:
        match = _PARTIAL.fullmatch(name)
        if match and match.group(1) == report_id:
            with contextlib.suppress(OSError, ExportError):
                if stat.S_ISREG(folder.lstat(name).st_mode):
                    folder.unlink(name)


def sync_note(provider: str | None) -> str | None:
    if provider not in PROVIDER_NAMES:
        return None
    app = PROVIDER_NAMES[provider]
    return (
        f"{app} will upload it when its app is running and signed in. DataLab can't confirm "
        "the upload, and it isn't sent to anyone until you email it."
    )


# B. Sending to the lab's private support repository ---------------------------------


class GitHubApi(Protocol):
    """What sending needs from GitHub: the sign-in's own client (GitHubAuth.request)."""

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response: ...


class Pending(Exception):
    """Not sent now, but may be later: the reason, in plain words."""


class Refused(Exception):
    """Not sent, and trying again won't help until something changes."""


def repo_paths(report: dict[str, Any]) -> tuple[str, str]:
    year, month = report["created_at"][:4], report["created_at"][5:7]
    base = f"reports/{year}/{month}/{report['report_id']}"
    return f"{base}.zip", f"{base}.md"


def check_repo(api: GitHubApi, repo: str) -> None:
    """The support repository must be private, and the person able to write to it."""
    response = _call(api, "GET", f"/repos/{repo}")
    if _rate_limited(response):
        _raise_for(response)
    if response.status_code in (403, 404):
        raise Pending(
            f"GitHub says your account can't open {repo}. Ask the maintainer to give you "
            "access, or to install the lab's GitHub App on it."
        )
    _raise_for(response)
    info = response.json()
    if not info.get("private"):
        raise Refused(
            f"{repo} is a public repository, so DataLab won't send reports there. Ask the "
            "maintainer to make it private, or to set a private one in settings.toml."
        )
    if not (info.get("permissions") or {}).get("push"):
        raise Pending(f"Your GitHub account can read {repo} but not add files to it.")


@dataclass(frozen=True)
class Sent:
    commit_sha: str | None
    html_url: str | None
    already_there: bool


def put_file(api: GitHubApi, repo: str, path: str, data: bytes, message: str) -> Sent:
    """Add one file to the repository unless the same file is there already.

    Never replaces a file: the request carries no `sha`, so GitHub refuses
    to overwrite. A file already there with the same git blob hash counts
    as sent, without a second commit; a different one is Refused.
    """
    blob = git_blob_sha(data)
    for _ in range(2):
        existing = _call(api, "GET", f"/repos/{repo}/contents/{path}")
        if existing.status_code == 200:
            found = existing.json()
            if not isinstance(found, dict) or found.get("sha") != blob:
                raise Refused(
                    f"A different file is already at {path} in {repo}, so DataLab didn't "
                    "replace it. Ask the maintainer to look."
                )
            return Sent(_last_commit(api, repo, path), found.get("html_url"), True)
        if existing.status_code != 404:
            _raise_for(existing)
        created = _call(
            api,
            "PUT",
            f"/repos/{repo}/contents/{path}",
            json={"message": message, "content": base64.b64encode(data).decode()},
        )
        if created.status_code in (200, 201):
            body = created.json()
            content = body.get("content") or {}
            if content.get("sha") not in (None, blob):
                raise Refused(f"GitHub saved something other than the report at {path}.")
            return Sent((body.get("commit") or {}).get("sha"), content.get("html_url"), False)
        if created.status_code == 422:
            continue  # it appeared meanwhile: look again
        _raise_for(created)
    raise Pending("GitHub didn't settle whether the file is there. Try again.")


def _last_commit(api: GitHubApi, repo: str, path: str) -> str | None:
    with contextlib.suppress(Pending, Refused, ValueError, KeyError, TypeError, IndexError):
        response = _call(api, "GET", f"/repos/{repo}/commits", params={"path": path, "per_page": 1})
        if response.status_code == 200:
            return str(response.json()[0]["sha"])
    return None


def _call(api: GitHubApi, method: str, path: str, **kwargs: Any) -> httpx.Response:
    from datalab.repos.github import GitHubUnavailable, SignInNeeded

    try:
        return api.request(method, path, **kwargs)
    except SignInNeeded as error:
        raise Pending(f"{error} (Settings → Connections → GitHub)") from None
    except GitHubUnavailable:
        raise Pending(
            "GitHub couldn't be reached: this computer may be offline. DataLab will try "
            "again when it next starts, or press Retry."
        ) from None


def _rate_limited(response: httpx.Response) -> bool:
    code = response.status_code
    return code == 429 or (code == 403 and response.headers.get("x-ratelimit-remaining") == "0")


def _raise_for(response: httpx.Response) -> None:
    code = response.status_code
    if code < 400:
        return
    if code == 401:
        raise Pending("GitHub didn't accept the sign-in. Sign in again in Settings → Connections.")
    if _rate_limited(response):
        raise Pending("GitHub is limiting requests for now. Try again in a while.")
    if code in (403, 404):
        raise Pending("GitHub says your account can't add files there. Ask the maintainer.")
    if code >= 500:
        raise Pending(f"GitHub had a problem ({code}). Try again later.")
    raise Pending(f"GitHub refused the request ({code}).")


def send_to_repo(store: ReportStore, report_id: str, api: GitHubApi, repo: str, now: str) -> dict:
    """Send a saved report to the support repository, and record how it went.

    The ZIP first, then the summary beside it, to read on GitHub. Confirmed
    only when GitHub has both (a commit, or the identical files already
    there); otherwise pending with the reason, or refused. The report is
    kept whatever happens.
    """
    report = store.report(report_id)
    data = store.bundle(report_id)
    zip_path, md_path = repo_paths(report)
    message = f"Report {report_id} ({report['kind']})"
    outcome: dict[str, Any] = {"repo": repo, "last_attempt_at": now, "reason": None}
    try:
        check_repo(api, repo)
        sent = put_file(api, repo, zip_path, data, message)
        put_file(api, repo, md_path, summary_text(report).encode(), message)
    except Pending as reason:
        outcome.update(state="pending", reason=str(reason))
    except Refused as reason:
        outcome.update(state="refused", reason=str(reason))
    else:
        outcome.update(
            state="confirmed",
            confirmed_at=now,
            commit_sha=sent.commit_sha,
            html_url=sent.html_url,
            path=zip_path,
            already_there=sent.already_there,
        )

    def record(status: dict[str, Any]) -> None:
        before = status.get("github") or {}
        status["github"] = {**outcome, "attempts": int(before.get("attempts") or 0) + 1}

    return store.update_status(report_id, record)


def pending_sends(store: ReportStore) -> list[str]:
    found = []
    for report_id in store.ids():
        with contextlib.suppress(LookupError):
            if (store.status(report_id).get("github") or {}).get("state") == "pending":
                found.append(report_id)
    return found


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
