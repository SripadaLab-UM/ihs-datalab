"""Settings → Copy diagnostics: a metadata-only report to paste into an email
or a GitHub issue when something goes wrong (docs/DISTRIBUTION.md).

The app repo is public, so the report may end up somewhere public. It holds
only what helps find a fault in DataLab itself:

- versions (DataLab, Python, the OS, Docker), the profile, and the agent image;
- the data folder's paths, with the home folder shortened to `~`;
- whether a database and keys are set up (never the keys, the password, the
  server, or the account);
- the last Safety check's result, by check id and status (never its details);
- the database layout (migrations), the update marker's state and recent
  updates (versions and times);
- counts of recent failures, and the latest problems DataLab logged: the
  logger, the level, DataLab's own message template (never the values
  filled into it), and the exception's type and where it was raised.

Never included: secrets, SQL text, bind values, query results, conversation
content or titles, file names from a workspace, or export destinations.
`tests/test_settings_api.py` checks this with canaries planted everywhere
those could come from.
"""

from __future__ import annotations

import contextlib
import json
import logging
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from datalab import __version__, db, setup, updates
from datalab.config import Settings
from datalab.db.backups import applied_migrations

_KEEP = 40
_DOCKER_FORMAT = "{{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})"
_TEMPLATE_CHARS = 160


@dataclass(frozen=True)
class Problem:
    at: str
    level: str
    logger: str
    # DataLab's own message template, with nothing filled in; other
    # libraries' messages can carry values, so theirs are left out.
    template: str | None
    exception: str | None
    where: str | None


class RecentProblems(logging.Handler):
    """Keeps the latest warnings and errors, as metadata only."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._problems: deque[Problem] = deque(maxlen=_KEEP)
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        own = record.name == "datalab" or record.name.startswith("datalab.")
        template = None
        if own and isinstance(record.msg, str):
            template = " ".join(record.msg.split())[:_TEMPLATE_CHARS]
        exception = where = None
        if record.exc_info and record.exc_info[0] is not None:
            exception = record.exc_info[0].__name__
            where = _where(record.exc_info[2])
        problem = Problem(
            datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            record.levelname,
            record.name,
            template,
            exception,
            where,
        )
        with self._guard:
            self._problems.append(problem)

    def latest(self) -> list[Problem]:
        with self._guard:
            return list(self._problems)


_handler: RecentProblems | None = None


def recent_problems() -> RecentProblems:
    """The one handler, attached to the root and uvicorn loggers the first time."""
    global _handler
    if _handler is None:
        _handler = RecentProblems()
        logging.getLogger().addHandler(_handler)
        # uvicorn's loggers don't pass their records up to the root logger.
        logging.getLogger("uvicorn").addHandler(_handler)
    return _handler


def build(
    settings: Settings,
    database: sqlite3.Connection,
    *,
    problems: list[Problem],
    recovery: updates.Recovery | None = None,
    docker: str | None = None,
) -> str:
    """The diagnostics text. `docker` is Docker's version, or None to ask it."""
    lines: list[str] = []

    def section(title: str) -> None:
        lines.extend(["", f"## {title}"])

    def fact(name: str, value: object) -> None:
        lines.append(f"{name + ':':<22} {value}")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    lines.append(f"DataLab diagnostics, {now}")
    lines.append("Metadata only: no keys, SQL, query results or conversation content.")

    section("Versions")
    fact("DataLab", __version__)
    fact("Python", platform.python_version())
    fact("OS", f"{platform.system()} {platform.release()} ({platform.machine()})")
    if sys.platform == "darwin":
        fact("macOS", platform.mac_ver()[0] or "unknown")
    fact("Docker", docker if docker is not None else docker_version())
    fact("Agent image", settings.agent_image)

    section("This DataLab")
    fact("Profile", settings.profile)
    fact("Data folder", home_as_tilde(settings.data_dir))
    fact("Catalog folder", home_as_tilde(settings.catalog_dir) if settings.catalog_dir else "none")
    if settings.workflows.folder:
        fact("Workflows folder", home_as_tilde(Path(settings.workflows.folder)))
    with contextlib.suppress(OSError):
        fact("Free disk", f"{shutil.disk_usage(settings.data_dir).free // 1024**2:,} MB")

    section("Connections")
    oracle = settings.oracle
    if oracle is None:
        fact("Database", "not configured")
    else:
        fact("Database", "practice (synthetic)" if settings.profile == "practice" else "configured")
        fact("Read-only roles", f"{len(oracle.read_only_roles)} set")
        lab = setup.asks_for_oracle_password(settings)
        if lab is not None:
            fact("Database password", _source(lambda: setup.oracle_password_source(lab)))
    fact("U-M GPT key", _source(setup.model_key_source))
    fact("Knowledge repo", "configured" if settings.repos.knowledge else "not configured")
    fact("Pipelines repo", "configured" if settings.repos.pipelines else "not configured")

    section("Safety check (last run)")
    lines.extend(_safety(settings.data_dir / "logs" / "safety-last.json"))

    section("Database layout")
    applied = applied_migrations(database)
    known = db.known_migrations()
    fact("Migrations applied", len(applied))
    fact("Latest", applied[-1] if applied else "none")
    pending = sorted(known - set(applied))
    unknown = sorted(set(applied) - known)
    fact("Not applied yet", ", ".join(pending) or "none")
    fact("Unknown (newer)", ", ".join(unknown) or "none")

    section("Updates")
    try:
        marker = updates.read_marker(settings.data_dir)
        fact(
            "Update in progress",
            f"{marker.from_version} → {marker.to_version}, {marker.state}" if marker else "none",
        )
    except updates.UnreadableMarker:
        fact("Update in progress", "a marker DataLab can't read")
    if recovery is not None:
        fact("At this start", recovery.outcome)
    fact("Set-aside notes", len(updates.set_aside_notes(settings.data_dir)))
    for entry in updates.history(settings.data_dir, limit=5):
        lines.append(
            f"  {str(entry.get('at', ''))[:19]}  {entry.get('outcome', '?')}  "
            f"{entry.get('from_version', '')} → {entry.get('to_version', '')}".rstrip(" →")
        )

    section("Recent failures (counts, last 7 days)")
    for name, count in _failure_counts(database):
        fact(name, count)

    section("Recent problems DataLab logged (since it started)")
    if not problems:
        lines.append("none")
    for problem in problems[-15:]:
        parts = [problem.at, problem.level, problem.logger]
        if problem.exception:
            parts.append(problem.exception + (f" at {problem.where}" if problem.where else ""))
        if problem.template:
            parts.append(repr(problem.template))
        lines.append("  " + "  ".join(parts))
    return "\n".join(lines) + "\n"


def docker_version() -> str:
    """Docker Engine's version, or why there isn't one."""
    if shutil.which("docker") is None:
        return "not installed"
    try:
        done = subprocess.run(
            ["docker", "version", "--format", _DOCKER_FORMAT],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "didn't answer"
    version = done.stdout.strip().splitlines()[0] if done.stdout.strip() else ""
    if done.returncode != 0 or not version or version.startswith("("):
        return "installed, not running"
    return version[:80]


def home_as_tilde(path: Path) -> str:
    """A path with the home folder shortened to `~`, so no user name shows."""
    text = str(path)
    with contextlib.suppress(RuntimeError, KeyError):
        home = str(Path.home())
        if text == home or text.startswith(home.rstrip("/\\") + ("\\" if "\\" in home else "/")):
            return "~" + text[len(home) :]
    return text


def _source(get) -> str:
    try:
        return {"keychain": "saved in the keychain", "environment": "from the environment"}.get(
            get(), "not saved"
        )
    except Exception:  # a keychain that can't be read
        return "couldn't check"


def _safety(file: Path) -> list[str]:
    try:
        report = json.loads(file.read_text(encoding="utf-8"))
        results = report["results"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["not run yet"]
    counts: dict[str, int] = {}
    lines = [
        f"{'Finished:':<22} {str(report.get('finished_at', ''))[:19]}",
        f"{'Passed:':<22} {'yes' if report.get('passed') else 'no'}"
        f"{'' if report.get('passed_strict') else ' (some required checks unverified)'}",
    ]
    for result in results:
        status = str(result.get("status"))
        counts[status] = counts.get(status, 0) + 1
        if status != "pass":
            # The check's id and status only: its details can name paths or hosts.
            required = "" if result.get("required", True) else " (optional)"
            lines.append(f"  {status}: {str(result.get('id'))[:60]}{required}")
    lines.insert(2, f"{'Results:':<22} " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return lines


_COUNT_QUERIES = "SELECT COUNT(*) FROM queries WHERE status = '{status}' AND started_at >= ?"


def _failure_counts(database: sqlite3.Connection) -> list[tuple[str, int]]:
    queries = [
        ("Failed queries", _COUNT_QUERIES.format(status="failed")),
        ("Rejected queries", _COUNT_QUERIES.format(status="rejected")),
        (
            "Failed workflow runs",
            "SELECT COUNT(*) FROM workflow_runs WHERE status IN ('failed', 'interrupted') "
            "AND started_at >= ?",
        ),
    ]
    since = datetime.fromtimestamp(datetime.now(UTC).timestamp() - 7 * 86400, UTC).isoformat()
    counts = []
    for name, sql in queries:
        try:
            (count,) = database.execute(sql, (since,)).fetchone()
        except sqlite3.Error:
            continue
        counts.append((name, int(count)))
    return counts


def _where(tb) -> str | None:
    """Where the exception was raised: the innermost frame in DataLab's own code."""
    frames = traceback.extract_tb(tb) if tb is not None else []
    for frame in reversed(frames):
        path = Path(frame.filename)
        if "datalab" in path.parts:
            parts = path.parts[path.parts.index("datalab") :]
            return f"{'/'.join(parts)}:{frame.lineno} in {frame.name}"
    if frames:
        last = frames[-1]
        return f"{Path(last.filename).name}:{last.lineno} in {last.name}"
    return None
