"""The knowledge-base check (docs/KNOWLEDGE_BASE.md, "The check").

DataLab's own code: it runs before every save, again after a rebase, and in
GitHub Actions after every push (`datalab kb-check <path>`), so an edit to
the knowledge base can never change what runs on anyone's computer.

It checks a knowledge base given as its files (`path -> bytes`):

- the layout: only the known folders and files, text only, within size
  limits, and no links;
- pages: front matter, ids that are unique and match file names, typed
  evidence, limitations, the cohorts a page applies to, and links (`related`,
  evidence, and Markdown links) that resolve;
- tables and columns pages mention exist in `generated/schema`, which is
  metadata only (catalog.py's format, nothing else);
- lab skills: `skills/<name>/SKILL.md` with a name and a description;
- participant-data heuristics, on file names (which also end up in the
  commit message and index.md) as well as contents: things that look like
  study IDs, dates next to IDs, long numeric lists, pasted tables of values,
  and email addresses. A hit is a "data" finding: it blocks a save until a
  person confirms it's a false positive. The scan is best effort, an aid to
  the person's review, which is the control: it doesn't catch names, phone
  numbers written with dashes, an ID written as "Participant 1234", or a
  small table.

It also writes `index.md`, one line per page, from the pages' front matter.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import posixpath
import re
import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from datalab.repos.git import name_problem

Severity = Literal["error", "data", "warning"]

# Page folders, and the `kind` their pages have.
FOLDERS = {
    "sources": "source",
    "tables": "table",
    "features": "feature",
    "qc": "qc",
    "cohorts": "cohort",
    "queries": "query",
    "decisions": "decision",
    "papers": "paper",
}
_TITLES = {
    "sources": "Data sources",
    "tables": "Tables and views",
    "features": "Derived features",
    "qc": "QC rules",
    "cohorts": "Cohorts",
    "queries": "Verified queries",
    "decisions": "Decisions",
    "papers": "Papers",
}
TOP_FILES = ("AGENTS.md", "README.md", "index.md", ".gitignore")
STATUSES = ("draft", "reviewed", "deprecated")
EVIDENCE_TYPES = ("schema", "code", "legacy", "paper", "query")
_FIELDS = (
    "id", "kind", "status", "summary", "evidence", "limitations", "related", "cohorts",
    "reviewed_by", "reviewed_on",
)  # fmt: skip
_REQUIRED = ("id", "kind", "status", "summary", "evidence", "limitations", "cohorts")
REVIEW_FIELDS = ("reviewed_by", "reviewed_on")
_SCHEMA_KEYS = {"schema", "name", "type", "comment", "columns", "primary_key"}
_COLUMN_KEYS = {"name", "type", "nullable", "comment"}
_SKILL_KEYS = {"name", "description", "metadata", "license", "allowed-tools"}

# Skill names the lab's skills can't take. Codex (0.157.1) lists a lab skill
# and an app skill of the same name side by side, the lab's first, so one
# could stand in for DataLab's own: the image's app skills
# (images/agent/skills), Codex's own system skills, and any kb-* name.
APP_SKILLS = frozenset({
    "academic-figures", "data-analysis", "reproducible-report", "research-helper",
    "sql-extraction", "statistical-review", "kb-use", "kb-propose", "kb-maintain",
})  # fmt: skip
CODEX_SKILLS = frozenset({
    "imagegen", "openai-docs", "plugin-creator", "review-agent", "skill-creator",
    "skill-installer",
})  # fmt: skip
# The files a lab skill may hold besides SKILL.md: text and scripts.
SKILL_EXTENSIONS = (".md", ".txt", ".r", ".py", ".sql", ".yml", ".yaml", ".json")

MAX_TEXT_BYTES = 256 * 1024
MAX_SCHEMA_BYTES = 1024 * 1024

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_ORACLE_NAME = re.compile(r"[A-Z][A-Z0-9_$#]*")
_SCHEMA_REF = re.compile(
    r"(?<![\w.])(IHS_\d{4})\.([A-Za-z][A-Za-z0-9_$#]*)(?:\.([A-Za-z][A-Za-z0-9_$#]*))?"
)
_FILE_EXTENSIONS = {"md", "yml", "yaml", "csv", "r", "sql", "py", "json", "txt", "html"}
_CODE_REF = re.compile(r"[A-Za-z0-9._-]+@[0-9a-f]{7,40} \S+")
_LEGACY_REF = re.compile(r"[^\s#]+(#L\d+(-L?\d+)?)?")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    severity: Severity
    message: str
    line: int | None = None
    # What the finding is about (the line's text for a data finding), so its
    # id stays the same when lines elsewhere move. Never shown on its own.
    subject: str = ""

    @property
    def id(self) -> str:
        """Stable across edits elsewhere in the file: a person confirms a data
        finding as a false positive by this id."""
        key = f"{self.path}\0{self.rule}\0{self.subject or self.message}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    # index.md as it should be.
    index: str = ""

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def data(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "data"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def blocking(self, confirmed: Collection[str] = ()) -> list[Finding]:
        """What stops a save: every error, and each data finding a person
        hasn't confirmed is a false positive."""
        return self.errors + [f for f in self.data if f.id not in confirmed]


# Layout -------------------------------------------------------------------

Place = Literal["top", "page", "skill", "skill_file", "schema", "generated", "github"]


def reserved_skill(path: str) -> bool:
    """Whether `path` is in a lab skill folder named like one of DataLab's or Codex's."""
    parts = path.split("/")
    if len(parts) < 2 or parts[0] != "skills":
        return False
    name = parts[1].lower()
    return name in APP_SKILLS or name in CODEX_SKILLS or name.startswith("kb-")


def place(path: str) -> Place | None:
    """What a path is in the knowledge base's layout, or None if it has no place
    (including every name git or a disk would treat specially)."""
    if name_problem(path):
        return None
    parts = path.split("/")
    if len(parts) == 1:
        return "top" if path in TOP_FILES else None
    top = parts[0]
    if top in FOLDERS:
        return "page" if len(parts) == 2 and path.endswith(".md") else None
    if top == "skills" and len(parts) >= 3 and _SKILL_NAME.fullmatch(parts[1]):
        if len(parts) == 3 and parts[2] == "SKILL.md":
            return "skill"
        if len(parts) <= 4 and parts[-1].lower().endswith(SKILL_EXTENSIONS):
            return "skill_file"
        return None
    if top == "generated":
        if path in ("generated/drift.md", "generated/README.md"):
            return "generated"
        if len(parts) == 4 and parts[1] == "schema" and path.endswith(".yml"):
            return "schema"
        return None
    if top == ".github":
        return "github"
    return None


def proposal_problem(path: str) -> str | None:
    """Why an agent's edit to `path` can't be proposed, or None if it can."""
    problem = name_problem(path)
    if problem:
        return problem
    if reserved_skill(path):
        return "that skill name is DataLab's or Codex's own"
    where = place(path)
    if where is None:
        return "it isn't part of the knowledge base's layout (see AGENTS.md)"
    if path == "index.md":
        return "index.md is written by DataLab's check from the pages"
    if where in ("schema", "generated"):
        return "generated/ is refreshed by DataLab from the database catalog"
    if where == "github":
        return ".github/ holds the repo's automation, which is changed outside DataLab"
    return None


def size_limit(path: str) -> int:
    return MAX_SCHEMA_BYTES if place(path) == "schema" else MAX_TEXT_BYTES


def as_text(content: bytes) -> str | None:
    """The file as text, or None if it's binary (not UTF-8, or has NULs)."""
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


# Front matter ---------------------------------------------------------------


def split_front_matter(text: str) -> tuple[str | None, str, int]:
    """(front matter text or None, body, the body's first line number)."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None, text, 1
    lines = text.splitlines(keepends=True)
    for number, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return "".join(lines[1:number]), "".join(lines[number + 1 :]), number + 2
    return None, text, 1


def front_matter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """The parsed front matter, or None and why not."""
    raw, _, _ = split_front_matter(text)
    if raw is None:
        return None, "it doesn't start with front matter between --- lines"
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark is not None else ""
        return None, f"its front matter isn't valid YAML{where}"
    if not isinstance(value, dict):
        return None, "its front matter isn't a set of fields"
    return value, None


def review_changes(old: str | None, new: str) -> list[str]:
    """What a change does to the fields only people may set: its status,
    and who reviewed it and when. Shown on the proposal so it can't slip by."""
    before = (front_matter(old)[0] or {}) if old is not None else {}
    after = front_matter(new)[0] or {}
    changes = []
    if old is not None and before.get("status") != after.get("status"):
        was = before.get("status", "none")
        changes.append(
            f"status: {was} → {after.get('status', 'none')} in the agent's text; kept as "
            f"{was}: only a person changes a page's status"
        )
    elif old is None and after.get("status") not in (None, "draft"):
        changes.append(
            f"a new page with status {after.get('status')} in the agent's text; kept as "
            "draft: only a person changes a page's status"
        )
    for key in REVIEW_FIELDS:
        if str(before.get(key, "")) != str(after.get(key, "")):
            changes.append(f"{key} changed (DataLab fills it in when a person saves)")
    return changes


def keep_review_fields(new: str, old: str | None) -> str:
    """`new` with `reviewed_by` and `reviewed_on` as they were in `old`
    (or absent): only DataLab's save flow sets them."""
    old_lines = _front_lines(old) if old is not None else {}
    text = new
    for key in REVIEW_FIELDS:
        text = _set_field(text, key, old_lines.get(key))
    return text


def keep_status(new: str, old: str | None) -> str:
    """`new` with the status `old` had (`draft` for a new page), so an agent
    can't mark a page reviewed: only a person's own edit changes it."""
    if split_front_matter(new)[0] is None:
        return new
    if old is None:
        return _set_field(new, "status", "status: draft") if "status" in _front_lines(new) else new
    return _set_field(new, "status", _front_lines(old).get("status"))


def agents_text(path: str, new: str, old: str | None) -> str:
    """What DataLab proposes of the agent's text for `path`: never its
    review fields, nor, on a page, its status."""
    text = keep_review_fields(new, old)
    return keep_status(text, old) if place(path) == "page" else text


def stamp_review(text: str, reviewer: str, day: datetime.date) -> str:
    """Record who saved a reviewed page, and when."""
    fields, _ = front_matter(text)
    if not fields or fields.get("status") != "reviewed":
        return text
    text = _set_field(text, "reviewed_by", f"reviewed_by: {reviewer}")
    return _set_field(text, "reviewed_on", f"reviewed_on: {day.isoformat()}")


def _front_lines(text: str) -> dict[str, str]:
    raw, _, _ = split_front_matter(text)
    found: dict[str, str] = {}
    for line in (raw or "").splitlines():
        match = re.match(r"([A-Za-z_]+)\s*:", line)
        if match:
            found.setdefault(match.group(1), line)
    return found


def _set_field(text: str, key: str, line: str | None) -> str:
    """Replace (or add, or with None remove) one top-level front-matter line."""
    raw, _, _ = split_front_matter(text)
    if raw is None:
        return text
    lines = text.splitlines(keepends=True)
    end = next(i for i, ln in enumerate(lines[1:], start=1) if ln.rstrip("\r\n") == "---")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    for i in range(1, end):
        if re.match(rf"{re.escape(key)}\s*:", lines[i]):
            if line is None:
                del lines[i]
            else:
                lines[i] = line + newline
            return "".join(lines)
    if line is not None:
        lines.insert(end, line + newline)
    return "".join(lines)


# The check ----------------------------------------------------------------


def check(
    files: Mapping[str, bytes],
    *,
    others: Iterable[str] = (),
    only: Collection[str] | None = None,
) -> Report:
    """Check a knowledge base given as its regular files. `others` are paths
    that aren't regular files (links, say). With `only`, findings are limited
    to those paths (a proposal's changed files), and to problems between
    pages that involve one of them."""
    wanted = (lambda p: True) if only is None else (lambda p: p in only)
    findings: list[Finding] = []
    texts: dict[str, str] = {}
    for path in others:
        if wanted(path):
            findings.append(_error(path, "not_a_file", "Links and special files aren't allowed."))
    for path, content in sorted(files.items()):
        where = place(path)
        if where is None:
            if wanted(path):
                problem = name_problem(path)
                findings.append(
                    _error(path, "name", f"It can't be in the repo: {problem}.")
                    if problem
                    else _error(
                        path, "layout", "This file has no place in the knowledge base's layout."
                    )
                )
            continue
        if reserved_skill(path) and wanted(path):
            findings.append(
                _error(path, "skill_reserved", "That skill name is DataLab's or Codex's own.")
            )
        text = as_text(content)
        if text is None:
            if wanted(path):
                findings.append(_error(path, "binary", "Only text files belong here."))
            continue
        if len(content) > size_limit(path):
            if wanted(path):
                limit = size_limit(path) // 1024
                findings.append(_error(path, "too_large", f"Over the {limit} KB limit."))
            continue
        texts[path] = text

    schema = _schema_index(texts, findings, wanted)
    pages: dict[str, dict[str, Any]] = {}
    for path, text in texts.items():
        where = place(path)
        if where == "page":
            meta = _check_page(path, text, findings if wanted(path) else [])
            if meta is not None:
                pages[path] = meta
        elif where == "skill" and wanted(path):
            _check_skill(path, text, findings)
    _check_ids(pages, findings, wanted)
    for path, meta in pages.items():
        if wanted(path):
            _check_links(path, meta, texts, pages, schema, findings)
    for path, text in texts.items():
        # index.md is written from the pages, which are scanned themselves.
        scanned = place(path) in ("top", "page", "skill", "skill_file", "generated")
        if wanted(path) and scanned and path != "index.md":
            findings.extend(data_findings(path, text))
    for path in files:
        if wanted(path):
            findings.extend(name_findings(path))

    index = render_index(pages, texts)
    if only is None and texts.get("index.md") != index:
        findings.append(
            Finding(
                "index.md",
                "index_stale",
                "warning",
                "index.md is out of date. DataLab rewrites it when a change is saved "
                "(or run `datalab kb-check --fix`).",
            )
        )
    return Report(findings, index)


def _error(path: str, rule: str, message: str, line: int | None = None) -> Finding:
    return Finding(path, rule, "error", message, line)


def _warning(path: str, rule: str, message: str, line: int | None = None) -> Finding:
    return Finding(path, rule, "warning", message, line)


def _check_page(path: str, text: str, findings: list[Finding]) -> dict[str, Any] | None:
    meta, problem = front_matter(text)
    if meta is None:
        findings.append(_error(path, "front_matter", f"The page can't be read: {problem}."))
        return None
    folder, stem = path.split("/")[0], path.split("/")[1][: -len(".md")]
    for key in _REQUIRED:
        if key not in meta:
            findings.append(_error(path, "missing_field", f"The front matter needs `{key}`."))
    for key in meta:
        if key not in _FIELDS:
            findings.append(_warning(path, "unknown_field", f"`{key}` isn't a known field."))
    page_id = meta.get("id")
    if "id" in meta and (not isinstance(page_id, str) or not _ID.fullmatch(page_id)):
        findings.append(_error(path, "id", "`id` must be letters, digits, dots, - or _."))
    elif isinstance(page_id, str) and page_id != stem:
        findings.append(_error(path, "id", f"`id` is {page_id!r}; the file name says {stem!r}."))
    if "kind" in meta and meta.get("kind") != FOLDERS[folder]:
        findings.append(_error(path, "kind", f"Pages in {folder}/ have `kind: {FOLDERS[folder]}`."))
    status = meta.get("status")
    if "status" in meta and status not in STATUSES:
        findings.append(_error(path, "status", f"`status` must be one of {', '.join(STATUSES)}."))
    summary = meta.get("summary")
    if "summary" in meta and (not isinstance(summary, str) or not summary.strip()):
        findings.append(_error(path, "summary", "`summary` must be one line of text."))
    elif isinstance(summary, str) and ("\n" in summary.strip() or len(summary) > 240):
        findings.append(_warning(path, "summary", "Keep `summary` to one short line."))
    strict = status == "reviewed"
    for key in ("evidence", "limitations", "cohorts"):
        value = meta.get(key)
        if key in meta and not isinstance(value, list):
            findings.append(_error(path, key, f"`{key}` must be a list."))
        elif key in meta and not value:
            message = f"`{key}` is empty."
            findings.append(
                _error(path, key, message + " A reviewed page needs it.")
                if strict
                else _warning(path, key, message)
            )
    for item in meta.get("limitations") or []:
        if not isinstance(item, str) or not item.strip():
            findings.append(_error(path, "limitations", "Each limitation is a line of text."))
            break
    for year in meta.get("cohorts") or []:
        if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100:
            findings.append(_error(path, "cohorts", f"{year!r} isn't a cohort year like 2025."))
    related = meta.get("related")
    if related is not None and not isinstance(related, list):
        findings.append(_error(path, "related", "`related` must be a list of pages."))
    if meta.get("reviewed_on") is not None and not _is_date(meta["reviewed_on"]):
        findings.append(_error(path, "reviewed_on", "`reviewed_on` must be a date (YYYY-MM-DD)."))
    if strict and not meta.get("reviewed_by"):
        findings.append(
            _warning(
                path,
                "reviewed_by",
                "A reviewed page names who reviewed it (DataLab fills this in on save).",
            )
        )
    return meta


def _is_date(value: object) -> bool:
    if isinstance(value, datetime.date):
        return True
    try:
        datetime.date.fromisoformat(str(value))
    except ValueError:
        return False
    return True


def _check_ids(pages: Mapping[str, dict[str, Any]], findings: list[Finding], wanted) -> None:
    by_id: dict[str, list[str]] = {}
    for path, meta in pages.items():
        page_id = meta.get("id")
        if isinstance(page_id, str):
            # Mac and Windows disks ignore case, so neither may ids.
            by_id.setdefault(page_id.casefold(), []).append(path)
    for paths in by_id.values():
        if len(paths) > 1:
            for path in paths:
                if wanted(path):
                    others = ", ".join(p for p in paths if p != path)
                    findings.append(_error(path, "duplicate_id", f"Same id as {others}."))


def _check_skill(path: str, text: str, findings: list[Finding]) -> None:
    meta, problem = front_matter(text)
    if meta is None:
        findings.append(_error(path, "front_matter", f"The skill can't be read: {problem}."))
        return
    folder = path.split("/")[1]
    name = meta.get("name")
    if not isinstance(name, str) or name != folder:
        findings.append(_error(path, "skill_name", f"`name` must be {folder!r}, its folder."))
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        findings.append(
            _error(path, "skill_description", "`description` says when the agent uses it.")
        )
    elif len(description) > 1024:
        findings.append(_error(path, "skill_description", "`description` is too long."))
    for key in meta:
        if key not in _SKILL_KEYS:
            findings.append(_warning(path, "unknown_field", f"`{key}` isn't a skill field."))


def _schema_index(
    texts: Mapping[str, str], findings: list[Finding], wanted
) -> dict[str, dict[str, set[str]]]:
    """schema -> table -> columns, from generated/schema; its files checked."""
    index: dict[str, dict[str, set[str]]] = {}
    for path, text in texts.items():
        if place(path) != "schema":
            continue
        _, _, folder, name = path.split("/")
        table = name[: -len(".yml")]
        problems = []
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError:
            raw = None
        if not isinstance(raw, dict):
            problems.append("It isn't a catalog table (a set of fields).")
            raw = {}
        extra = set(raw) - _SCHEMA_KEYS
        if extra:
            # Metadata only: nothing that could hold values.
            problems.append(f"Unexpected fields: {', '.join(sorted(map(str, extra)))}.")
        if raw and (raw.get("schema") != folder or raw.get("name") != table):
            problems.append("`schema` and `name` must match its folder and file name.")
        if raw and raw.get("type") not in ("TABLE", "VIEW"):
            problems.append("`type` must be TABLE or VIEW.")
        columns: set[str] = set()
        for column in raw.get("columns") or []:
            if not isinstance(column, dict) or set(column) - _COLUMN_KEYS:
                problems.append("Each column has only name, type, nullable, and comment.")
                break
            if isinstance(column.get("name"), str):
                columns.add(column["name"])
        for problem in problems:
            if wanted(path):
                findings.append(_error(path, "schema_file", problem))
        index.setdefault(folder, {})[table] = columns
    return index


def _check_links(
    path: str,
    meta: Mapping[str, Any],
    texts: Mapping[str, str],
    pages: Mapping[str, Any],
    schema: Mapping[str, Mapping[str, set[str]]],
    findings: list[Finding],
) -> None:
    for target in meta.get("related") or []:
        if not isinstance(target, str) or f"{target}.md" not in pages:
            findings.append(_error(path, "related", f"`related` names {target!r}: no such page."))
    for item in meta.get("evidence") or []:
        if not isinstance(item, dict) or len(item) != 1:
            findings.append(
                _error(path, "evidence", "Each evidence item is one `type: reference` pair.")
            )
            continue
        [(kind, value)] = item.items()
        value = str(value).strip()
        if kind not in EVIDENCE_TYPES:
            findings.append(
                _error(
                    path,
                    "evidence",
                    f"Evidence type {kind!r} isn't one of {', '.join(EVIDENCE_TYPES)}.",
                )
            )
        elif kind == "schema":
            parts = value.split(".")
            if not 2 <= len(parts) <= 3 or not all(_ORACLE_NAME.fullmatch(p) for p in parts):
                findings.append(
                    _error(path, "evidence", f"schema: {value!r} should be SCHEMA.TABLE[.COLUMN].")
                )
            else:
                findings.extend(_schema_problem(path, None, parts, schema))
        elif kind == "code" and not _CODE_REF.fullmatch(value):
            findings.append(
                _error(path, "evidence", f"code: {value!r} should be repo@commit path.")
            )
        elif kind == "legacy" and not _LEGACY_REF.fullmatch(value):
            findings.append(_error(path, "evidence", f"legacy: {value!r} should be a path."))
        elif kind == "paper" and not _DOI.fullmatch(value):
            findings.append(_error(path, "evidence", f"paper: {value!r} should be a DOI."))
        elif kind == "query" and f"{value}.md" not in pages:
            findings.append(_error(path, "evidence", f"query: {value!r}: no such page."))
    _, body, first = split_front_matter(texts[path])
    folder = posixpath.dirname(path)
    stem = path.split("/")[1][: -len(".md")]
    if path.startswith("tables/"):
        parts = stem.split(".")
        if len(parts) == 2 and all(_ORACLE_NAME.fullmatch(p) for p in parts):
            findings.extend(_schema_problem(path, None, parts, schema))
    for number, line in enumerate(body.splitlines(), start=first):
        for match in _MD_LINK.finditer(line):
            target = match.group(1)
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            resolved = posixpath.normpath(posixpath.join(folder, target.split("#")[0]))
            if resolved not in texts:
                findings.append(_error(path, "link", f"The link to {target} goes nowhere.", number))
        for match in _SCHEMA_REF.finditer(line):
            parts = [match.group(1), match.group(2)]
            column = match.group(3)
            if column and not (column.islower() and column in _FILE_EXTENSIONS):
                parts.append(column)
            findings.extend(_schema_problem(path, number, [p.upper() for p in parts], schema))


def _schema_problem(
    path: str,
    line: int | None,
    parts: list[str],
    schema: Mapping[str, Mapping[str, set[str]]],
) -> list[Finding]:
    if not schema:
        return []  # no generated/schema yet: nothing to compare with
    name = ".".join(parts)
    tables = schema.get(parts[0])
    if tables is None:
        return [_warning(path, "schema", f"There's no schema metadata for {parts[0]}.", line)]
    if parts[1] not in tables:
        return [_error(path, "schema", f"{parts[0]}.{parts[1]} isn't in generated/schema.", line)]
    if len(parts) == 3 and parts[2] not in tables[parts[1]]:
        return [_error(path, "schema", f"{name} isn't a column in generated/schema.", line)]
    return []


# Participant-data heuristics ---------------------------------------------

# Removed before looking: links, DOIs, pinned commits, code line references,
# and schema-qualified names, which look like IDs but aren't.
_NOT_DATA = re.compile(
    r"https?://\S+|10\.\d{4,9}/\S+|\S+@[0-9a-f]{7,40}\b|#L\d+(-L?\d+)?|\bIHS_\d{4}(\.[\w$#]+)*"
)
_ID_LIKE = re.compile(r"(?<![\w-])([A-Za-z]{1,6})[-_]?(\d{3,})[A-Za-z]?(?![\w-])")
_LONG_NUMBER = re.compile(r"(?<![\w.,/-])\d{6,}(?![\w,/-])")
_DATE = re.compile(
    r"\b(?:(?:19|20)\d\d[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)?\d\d)\b"
)
_EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
_NUMBER = re.compile(r"^[-+]?\d+(?:[.,]\d+)*%?$")
# Words that end in digits but aren't identifiers.
_TECHNICAL = {"sha", "md", "utf", "iso", "rfc", "int", "float", "x", "base", "crc"}


def data_findings(path: str, text: str) -> list[Finding]:
    """Lines that look like participant-level data (see the module docstring)."""
    found: list[Finding] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        cleaned = _NOT_DATA.sub(" ", line)
        ids = [m.group(0) for m in _ID_LIKE.finditer(cleaned) if _looks_like_id(m)]
        ids += _LONG_NUMBER.findall(cleaned)
        if ids and _DATE.search(cleaned):
            found.append(
                _data(
                    path, "date_near_id", "A date next to what looks like a study ID.", number, line
                )
            )
        elif ids:
            found.append(
                _data(path, "study_id", "This looks like a participant or study ID.", number, line)
            )
        emails = [m for m in _EMAIL.finditer(cleaned) if not _harmless_email(m.group(1))]
        if emails:
            found.append(_data(path, "email", "An email address.", number, line))
        tokens = re.split(r"[\s,;|]+", cleaned.strip())
        numbers = [t for t in tokens if _NUMBER.match(t)]
        if len(numbers) >= 12 and len(numbers) >= 0.6 * len(tokens):
            found.append(_data(path, "numeric_list", "A long list of numbers.", number, line))
    found += _pasted_tables(path, lines)
    return found


def name_findings(path: str) -> list[Finding]:
    """A file name that looks like it holds a study ID (or a date with one)."""
    cleaned = _NOT_DATA.sub(" ", path)
    tokens = [t for t in re.split(r"[/.\-\s]+", cleaned) if t]
    ids = [t for t in tokens if _LONG_NUMBER.fullmatch(t)]
    ids += [t for t in tokens if (m := _ID_LIKE.fullmatch(t)) and _looks_like_id(m)]
    if not ids:
        return []
    message = "The file's name looks like it has a study ID in it."
    return [Finding(path, "name_study_id", "data", message, None, subject=path)]


def _looks_like_id(match: re.Match[str]) -> bool:
    letters, digits = match.group(1), match.group(2)
    if len(digits) == 4 and digits[:2] in ("19", "20"):
        return False  # a year: IHS_2025, FY2024
    return letters.lower() not in _TECHNICAL


def _harmless_email(domain: str) -> bool:
    domain = domain.lower()
    return domain.endswith("users.noreply.github.com") or domain in ("example.com", "example.org")


def _data(path: str, rule: str, message: str, line: int, text: str) -> Finding:
    return Finding(path, rule, "data", message, line, subject=text.strip())


def _pasted_tables(path: str, lines: list[str]) -> list[Finding]:
    """Markdown tables, or comma- or tab-separated blocks, of mostly values."""
    found: list[Finding] = []
    block: list[tuple[int, list[str]]] = []

    def flush() -> None:
        rows = [cells for _, cells in block if not all(set(c) <= set("-: ") for c in cells)]
        cells = [c for row in rows[1:] for c in row if c]
        valued = [c for c in cells if _NUMBER.match(c) or _DATE.search(c) or _ID_LIKE.search(c)]
        if len(rows) - 1 >= 5 and cells and len(valued) >= 0.5 * len(cells):
            first, _ = block[0]
            subject = "\n".join(lines[n - 1] for n, _ in block[:3])
            found.append(
                _data(path, "pasted_table", "A table of values: row-level data?", first, subject)
            )
        block.clear()

    kind = None
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("|"):
            this, cells = "markdown", [c.strip() for c in stripped.strip("|").split("|")]
        elif stripped.count(",") >= 3 or stripped.count("\t") >= 3:
            this, cells = "delimited", [c.strip() for c in re.split(r"[,\t]", stripped)]
        else:
            this, cells = None, []
        if this != kind and block:
            flush()
        kind = this
        if this:
            block.append((number, cells))
    if block:
        flush()
    return found


# index.md -------------------------------------------------------------------


def render_index(pages: Mapping[str, Mapping[str, Any]], texts: Mapping[str, str]) -> str:
    out = [
        "# Knowledge base index",
        "",
        "One line per page, grouped by folder. DataLab's check writes this file from",
        "each page's front matter whenever a change is saved: don't edit it by hand.",
    ]
    for folder, title in _TITLES.items():
        entries = sorted(p for p in pages if p.startswith(folder + "/"))
        if not entries:
            continue
        out += ["", f"## {title} ({folder}/)", ""]
        for path in entries:
            meta = pages[path]
            summary = " ".join(str(meta.get("summary") or "").split())
            status = meta.get("status")
            note = f" *({status})*" if status in ("draft", "deprecated") else ""
            out.append(f"- [{meta.get('id', path)}]({path}): {summary}{note}")
    skills = sorted(p for p in texts if place(p) == "skill")
    if skills:
        out += ["", "## Lab skills (skills/)", ""]
        for path in skills:
            meta = front_matter(texts[path])[0] or {}
            description = " ".join(str(meta.get("description") or "").split())
            out.append(f"- [{path.split('/')[1]}]({path}): {description}")
    return "\n".join(out) + "\n"


# Reading a folder, and the command line -------------------------------------


def read_folder(root: Path) -> tuple[dict[str, bytes], list[str]]:
    """A knowledge base on disk: its regular files, and the paths that
    aren't (links are recorded, never followed). `.git` is skipped."""
    files: dict[str, bytes] = {}
    others: list[str] = []
    for folder, dirs, names in os.walk(root, followlinks=False):
        rel = Path(folder).relative_to(root).as_posix()
        for name in list(dirs):
            full = Path(folder) / name
            if name == ".git" and rel == ".":
                dirs.remove(name)
            elif full.is_symlink():
                others.append(_join(rel, name))
                dirs.remove(name)
        for name in names:
            full = Path(folder) / name
            path = _join(rel, name)
            if full.is_symlink() or not full.is_file():
                others.append(path)
            else:
                files[path] = full.read_bytes()
    return files, sorted(others)


def _join(folder: str, name: str) -> str:
    return name if folder == "." else f"{folder}/{name}"


def run(root: Path, *, fix: bool = False, output: str = "text", allow_data: bool = False) -> int:
    """`datalab kb-check`: 0 if the knowledge base passes, 1 if not."""
    files, others = read_folder(root)
    report = check(files, others=others)
    if fix and files.get("index.md", b"").decode("utf-8", "replace") != report.index:
        (root / "index.md").write_text(report.index, encoding="utf-8", newline="\n")
        report.findings = [f for f in report.findings if f.rule != "index_stale"]
        print("Wrote index.md.")
    failing = report.blocking() if not allow_data else report.errors
    if output == "json":
        print(json.dumps([f.to_dict() for f in report.findings], indent=1))
    else:
        for finding in report.findings:
            print(_line(finding, output))
        counts = (len(report.errors), len(report.data), len(report.warnings))
        print(
            f"{counts[0]} error(s), {counts[1]} possible participant-data hit(s), "
            f"{counts[2]} warning(s).",
            file=sys.stderr,
        )
    return 1 if failing else 0


def _line(finding: Finding, output: str) -> str:
    where = f"{finding.path}:{finding.line}" if finding.line else finding.path
    label = {"error": "error", "data": "possible participant data", "warning": "warning"}
    if output == "github":
        # GitHub Actions annotations. A data hit isn't an error there: the
        # person who saved it has already confirmed it in DataLab.
        level = "error" if finding.severity == "error" else "warning"
        line = f",line={finding.line}" if finding.line else ""
        return f"::{level} file={finding.path}{line}::{label[finding.severity]}: {finding.message}"
    return f"{where}: {label[finding.severity]}: {finding.message}"
