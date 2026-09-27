"""Convert the prototype's Knowledge Spine into the lab knowledge base's layout.

A one-time conversion (docs/KNOWLEDGE_BASE.md, "Moving over from the
prototype spine"). It reads the Spine's registry (`spine/registry/*.yaml`)
and the Oracle metadata export, and writes a knowledge base for a person to
review before the first commit to `ihs-knowledge`, plus a review report:

    cd backend && uv run python ../scripts/convert-spine/convert_spine.py \\
        --spine <um-gpt-local-proxy>/spine \\
        --export <metadata export folder> \\
        --pipelines <ihs-pipelines checkout> \\
        --out <staging folder> --review <REVIEW.md>

It reads the registry as committed at SPINE_COMMIT unless told otherwise
(`--ref`, `--working-copy`), and applies the reviewer's decisions in
`review_decisions.yaml` next to this script (`--no-decisions` leaves them
out): text fixes to Spine entries, the DOIs behind manuscripts the Spine
cites by name, the cohorts each paper analysed, code evidence re-pinned to
`ihs-pipelines` (checked in the `--pipelines` checkout), cohorts the
reviewer confirmed, and pages the reviewer holds as drafts. Decisions add
evidence; they never mark a page reviewed, which is still `decide_status`'s
call. Their answers go into REVIEW.md.

After the reviewer has reviewed the output, and just before the first
commit, they name themselves on its reviewed pages:

    cd backend && uv run python ../scripts/convert-spine/convert_spine.py \\
        --stamp-reviewer <GitHub login> --out <the knowledge base>

Tests: `cd backend && uv run pytest -q ../scripts/convert-spine`.

What goes where:

- DataSource -> `sources/`; RawSchema and RawVariable -> `tables/`, one page
  per Oracle table or view, with the export as `generated/schema/`;
  CanonicalFeature -> `features/`, with its FeatureRecipe and its Construct
  folded in, and every Construct in the `features/constructs` glossary;
  QCRule -> `qc/`; the DOIs the Spine cites -> `papers/`, listing only what
  the Spine takes from each paper.
- Statuses: a Spine `validated` entry becomes `reviewed` only when typed
  evidence of the right kind supports it (see `decide_status`);
  everything else becomes `draft`, with the reason on the page and in the
  report. The conversion never writes `reviewed_by` or `reviewed_on`; the
  reviewer adds them with `--stamp-reviewer` once they have reviewed, the
  way DataLab's Save & share does.
- Nothing that could be participant data is carried over: counts and value
  distributions from queries, results' hashes, and profiling observations
  are left out (the report lists where, never what), and every page is run
  through the check's participant-data scan.

The sources are only read. The output depends only on them, so running the
conversion twice gives the same files.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import itertools
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from datalab.data.catalog import Catalog
from datalab.knowledge import check as kb

# The Spine commit the knowledge base is converted from (the user's decision:
# its last commit, without the working copy's uncommitted changes).
SPINE_COMMIT = "eea768cef048dacfc1b5e9925b9deafc624ea619"
# Who reviews the conversion and is stamped on its reviewed pages at install.
REVIEWER = "ataxali"
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")

LIST_KEYS = (
    "data_sources",
    "raw_schemas",
    "raw_variables",
    "constructs",
    "canonical_features",
    "feature_recipes",
    "qc_rules",
)
TYPE_NAMES = {
    "data_sources": "DataSource",
    "raw_schemas": "RawSchema",
    "raw_variables": "RawVariable",
    "constructs": "Construct",
    "canonical_features": "CanonicalFeature",
    "feature_recipes": "FeatureRecipe",
    "qc_rules": "QCRule",
}
# The cohort schemas an unqualified Oracle name is looked up in when the
# entry doesn't say: every Spine source is a 2024 or 2025 one, and the
# legacy scripts it cites are the 2024-25 ones.
DEFAULT_SCHEMAS = ("IHS_2024", "IHS_2025")
# Legacy scripts the Spine cites by name, and where the lab keeps them
# (ihs-pipelines `reference/`, docs/WORKFLOWS.md).
LEGACY_SCRIPTS = {
    "AggregateDailyMetrics_2024.R": "reference/2024/AggregateDailyMetrics_2024.R",
    "ODBC_connect_IHS2024-25.R": "reference/2024/ODBC_connect_IHS2024-25.R",
}
# Prototype code the Spine cites by a Python name rather than a path.
CODE_NAMES = {"oracle_broker._json_safe_oracle_value": "src/lab_ai/agent_server/oracle_broker.py"}

# Entries the Spine calls validated whose own text says they aren't settled.
HOLDS = {
    ("canonical_features", "resting_heart_rate_day"): (
        "its description says the recipe models only the Apple Watch branch "
        "(Fitbit and Garmin pending), but its recipe has Fitbit and Garmin "
        "cleaning rules, and the recipe links no code"
    ),
    ("raw_variables", "participant.study_participant_id"): (
        "its description says it is a candidate pending a reviewer's sign-off"
    ),
    ("data_sources", "garmin"): (
        "a limitation says the catalog export lists no views, but generated/schema "
        "(the 2026-09-26 export) lists them, including the view it names"
    ),
    ("data_sources", "participant"): (
        "a limitation says the catalog export lists no views, but generated/schema "
        "(the 2026-09-26 export) lists them, including the view it names"
    ),
    ("raw_variables", "healthkit.restingheartrate_sourceproducttype"): (
        "the Spine notes its participant-day deduplication rule hasn't been reviewed"
    ),
    ("raw_variables", "healthkit.restingheartrate_value"): (
        "the Spine notes its participant-day deduplication rule hasn't been reviewed"
    ),
    ("raw_variables", "healthkit.restingheartrate_samplekey"): (
        "the Spine notes its participant-day deduplication rule hasn't been reviewed"
    ),
}

# Privacy: what's left out. Keys in `extra` that hold counts, value
# distributions, or hashes of query results (derived from participant rows).
PRIVACY_KEYS = re.compile(
    r"(^|_)(rows|row_count|counts|coverage|duplicate_participant_days|observed_grain)$"
    r"|_rows_|result_sha256$"
)
# Keys that are the prototype's own bookkeeping: run, query, export and
# proposal ids, SQL hashes, and validation notes about the Spine itself.
MACHINERY_KEYS = re.compile(
    r"(^|_)(query_id|export_id|proposal_ids)$|^(query_id|export_id)$"
    r"|sql_sha256$|^validation_resolution$|^truncated$|^live_smoke_status$"
    r"|^live_smoke_yield_status$"
)
# Text: counts from live queries, and profiling observations (dates of
# observed coverage, "returns no rows"), removed from free text.
PRIVACY_TEXT = (
    re.compile(r"\s*\(\d[\d,]* rows?;[^)]*\)"),
    re.compile(r"\s*\(\d[\d,]*/\d[\d,]* populated\)"),
)
PROFILING_ITEM = re.compile(r"\bobserved\b.*\bcoverage\b|\breturns no rows\b", re.S)
MACHINERY_TEXT = re.compile(r"\s*\b(?:oracle_(?:query|export)|prop)_[0-9a-f]{32}\b")
# Fields whose values read like counts but aren't: renamed, values spelled out.
RELABEL = {
    "followup_suffix_observed_2026": (
        "followup_item_suffix_by_survey",
        "its result identifiers end in {0} (for example down{0})",
    ),
}
RELABEL_WHY = "the values are result-identifier suffixes, not counts"
# Oracle error codes look like study IDs to the check's scan; say them another way.
ORACLE_ERROR = re.compile(r"\bORA-(\d{5})\b")
# Participant-like values the check's scan might miss.
PARTICIPANT_LIKE = (
    ("a synthetic or study ID", re.compile(r"\bSYN[-_]?\d{3,}\b", re.I)),
    ("an email address", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("a phone number", re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")),
    ("a date of birth", re.compile(r"\b(?:dob|date of birth)\b\s*[:=]\s*\S", re.I)),
)

_DOI = re.compile(r"10\.\d{4,9}/[^\s;),]+")
_ORACLE_TOKEN = re.compile(
    r"(?<![\w.$#])(?:(IHS_\d{4})\.)?([A-Z][A-Z0-9_$#]{2,})(?:\.([A-Z][A-Z0-9_$#]*))?(?![\w$#])"
)
_IHSDATAR_PATH = re.compile(r"r/ihsDataR/[\w./-]+\.R\b")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


# Reading the sources --------------------------------------------------------


@dataclass
class Entity:
    key: str  # list key, e.g. "data_sources"
    data: dict[str, Any]
    file: str
    changed: bool = False  # differs from the Spine's last commit
    diff: tuple[str, ...] = ()  # which fields, if it does

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def status(self) -> str:
        return str(self.data.get("status") or "unmapped")

    @property
    def label(self) -> str:
        return f"{TYPE_NAMES[self.key]} `{self.id}`"


@dataclass
class Spine:
    entities: dict[str, dict[str, Entity]]  # list key -> id -> entity
    files: list[str]
    commit: str | None  # the Spine repo's HEAD, or the ref read
    ref: str | None  # the git ref read, or None for the working copy
    repo: str | None  # its name, for code evidence
    code_paths: set[str]  # files in the repo at that commit
    folder: Path | None = None  # the Spine's folder, in its git repo

    def get(self, key: str, entity_id: object) -> Entity | None:
        return self.entities[key].get(str(entity_id)) if entity_id is not None else None


def _git(folder: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(folder), *args], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def diff_paths(old: object, new: object, where: str = "") -> list[str]:
    """Which fields differ between two versions of an entry (names, never values)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new), key=str):
            path = f"{where}.{key}" if where else str(key)
            if key not in old:
                out.append(f"{path} (added)")
            elif key not in new:
                out.append(f"{path} (removed)")
            else:
                out += diff_paths(old[key], new[key], path)
        return out
    return [] if old == new else [f"{where or 'the entry'} (changed)"]


def read_spine(folder: Path, ref: str | None = None) -> Spine:
    """The Spine's registry: its working copy, or with `ref` as committed there."""
    registry = folder / "registry"
    commit = (_git(folder, "rev-parse", ref or "HEAD") or "").strip() or None
    if ref and not commit:
        raise SystemExit(f"{folder} has no git ref {ref}")
    prefix = (_git(folder, "rev-parse", "--show-prefix") or "").strip()
    remote = (_git(folder, "remote", "get-url", "origin") or "").strip()
    repo = re.sub(r"\.git$", "", remote.rstrip("/").rsplit("/", 1)[-1]) or None
    listing = (
        _git(folder, "ls-tree", "-r", "--full-tree", "--name-only", commit) if commit else None
    )
    code_paths = set((listing or "").splitlines())
    entities: dict[str, dict[str, Entity]] = {key: {} for key in LIST_KEYS}
    files = []
    if ref:
        folder_name = f"{prefix}registry/"
        names = sorted(
            n[len(folder_name) :]
            for n in code_paths
            if n.startswith(folder_name)
            and n.endswith(".yaml")
            and "/" not in n[len(folder_name) :]
        )
    else:
        names = [p.name for p in sorted(registry.glob("*.yaml"))]
    for name in names:
        files.append(name)
        if ref:
            text = _git(folder, "show", f"{commit}:{prefix}registry/{name}") or ""
        else:
            text = (registry / name).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        committed: dict[tuple[str, str], Any] = {}
        if commit:
            old = _git(folder, "show", f"{commit}:{prefix}registry/{name}")
            for key, items in (yaml.safe_load(old or "") or {}).items():
                for item in items or []:
                    committed[(key, str(item.get("id")))] = item
        for key in LIST_KEYS:
            for item in data.get(key) or []:
                entity = Entity(key, item, name)
                if entity.id in entities[key]:
                    raise SystemExit(f"Duplicate Spine id {key} {entity.id}")
                old_item = committed.get((key, entity.id))
                entity.changed = bool(commit) and old_item != item
                if entity.changed:
                    entity.diff = tuple(
                        diff_paths(old_item, item) if old_item else ["the whole entry (added)"]
                    )
                entities[key][entity.id] = entity
    return Spine(entities, files, commit, ref, repo, code_paths, folder)


def _tables(catalog: Catalog, schema: str) -> list[Any]:
    return [t for name in catalog.names(schema) if (t := catalog.get(f"{schema}.{name}"))]


class Schema:
    """The catalog, for looking names up."""

    def __init__(self, catalog: Catalog) -> None:
        self.index = catalog.column_index()
        self.types = {(t.schema, t.name): t.type for s in self.index for t in _tables(catalog, s)}

    def tables(self, schemas: Iterable[str], table: str) -> list[str]:
        return [s for s in sorted(set(schemas)) if table in self.index.get(s, {})]

    def columns(self, schemas: Iterable[str], table: str, column: str) -> list[str]:
        return [s for s in self.tables(schemas, table) if column in self.index[s][table]]

    def exists(self, ref: str) -> bool:
        parts = ref.split(".")
        tables = self.index.get(parts[0], {})
        if len(parts) < 2 or parts[1] not in tables:
            return False
        return len(parts) == 2 or parts[2] in tables[parts[1]]


# The reviewer's decisions ------------------------------------------------------

DECISIONS = Path(__file__).with_name("review_decisions.yaml")
_FIELD = re.compile(r"([A-Za-z_]\w*)(?:\[(\d+)\])?")


def stop(source: Path, message: str) -> SystemExit:
    return SystemExit(f"{source.name}: {message}")


@dataclass(frozen=True)
class PaperInfo:
    """A paper the reviewer identified: its DOI, what the Spine calls it, and
    the cohorts its methods say it analysed."""

    doi: str
    names: tuple[str, ...]  # what the Spine calls it where it gives no DOI
    label: str | None
    title: str
    venue: str
    year: int
    cohorts: frozenset[int]
    cohorts_from: str
    verified: tuple[str, ...]
    summary: str
    preprint: bool = False


@dataclass
class Decisions:
    path: Path
    reviewer: str
    decided_on: str
    papers: dict[str, PaperInfo]  # DOI -> paper
    unpublished: list[dict[str, Any]]
    repin: dict[str, Any] | None
    legacy: dict[str, dict[str, Any]]  # script name -> what's known of it
    removed: dict[str, Any] | None
    entries: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    answers: list[dict[str, Any]]
    still_open: list[str]

    def alias(self, part: str) -> str | None:
        """The DOI of the paper a citation names without one."""
        for paper in self.papers.values():
            for name in paper.names:
                if re.match(rf"{re.escape(name)}(?![\w-])", part.strip()):
                    return paper.doi
        return None

    def alias_target(self, doi: str) -> bool:
        """Whether the Spine cites this paper by name somewhere."""
        paper = self.papers.get(doi)
        return bool(paper and paper.names)

    def legacy_seen(self, ref: str) -> bool:
        name = ref.split("#", 1)[0].rsplit("/", 1)[-1]
        return bool(self.legacy.get(name, {}).get("seen", True))


def _need(data: Mapping[str, Any], key: str, kind: type, where: str, source: Path) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise stop(source, f"{where}: `{key}` must be a {kind.__name__}")
    return value


def load_decisions(path: Path) -> Decisions:
    """Read and check the overlay. Anything malformed stops the run."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise stop(path, "not a mapping")
    known = {
        "reviewer", "decided_on", "papers", "unpublished", "code_repin", "legacy_scripts",
        "removed_code_names", "entries", "pages", "answers", "still_open",
    }  # fmt: skip
    if extra := sorted(set(data) - known):
        raise stop(path, f"unknown keys: {', '.join(extra)}")
    papers: dict[str, PaperInfo] = {}
    for n, item in enumerate(data.get("papers") or []):
        where = f"papers[{n}]"
        doi = str(_need(item, "doi", str, where, path))
        if not kb._DOI.fullmatch(doi) or doi in papers:
            raise stop(path, f"{where}: {doi!r} isn't a new DOI")
        cohorts = _need(item, "cohorts", list, where, path)
        if not cohorts or not all(isinstance(y, int) and 2000 <= y <= 2100 for y in cohorts):
            raise stop(path, f"{where}: cohorts must be years")
        verified = _need(item, "verified", list, where, path)
        papers[doi] = PaperInfo(
            doi=doi,
            names=tuple(str(x) for x in item.get("names") or []),
            label=item.get("label"),
            title=str(_need(item, "title", str, where, path)),
            venue=str(_need(item, "venue", str, where, path)),
            year=int(_need(item, "year", int, where, path)),
            cohorts=frozenset(cohorts),
            cohorts_from=str(_need(item, "cohorts_from", str, where, path)),
            verified=tuple(str(v) for v in verified),
            summary=one_line(_need(item, "summary", str, where, path)),
            preprint=bool(item.get("preprint")),
        )
    for n, item in enumerate(data.get("entries") or []):
        where = f"entries[{n}]"
        _need(item, "entry", str, where, path)
        _need(item, "question", int, where, path)
        _need(item, "source", str, where, path)
        if item["entry"].split("/", 1)[0] not in LIST_KEYS:
            raise stop(path, f"{where}: {item['entry']!r} isn't <list key>/<id>")
        ops = {"replace", "set", "release_hold", "exclude"}
        if not set(item) & ops or set(item) - ops - {"entry", "question", "source"}:
            raise stop(path, f"{where}: needs one of {', '.join(sorted(ops))}, and nothing else")
    for n, item in enumerate(data.get("pages") or []):
        where = f"pages[{n}]"
        _need(item, "page", str, where, path)
        _need(item, "question", int, where, path)
        allowed = {"page", "question", "source", "cohorts", "evidence", "limitations", "hold"}
        if extra := sorted(set(item) - allowed):
            raise stop(
                path, f"{where}: unknown keys {', '.join(extra)} (a page can't be marked reviewed)"
            )
        if ("cohorts" in item or "evidence" in item or "hold" in item) and not item.get("source"):
            raise stop(path, f"{where}: cohorts, evidence and holds need a `source`")
    for n, item in enumerate(data.get("answers") or []):
        _need(item, "question", int, f"answers[{n}]", path)
        _need(item, "answer", str, f"answers[{n}]", path)
    for finding in kb.data_findings(path.name, path.read_text(encoding="utf-8")):
        raise stop(path, f"line {finding.line}: {finding.message} No participant data here.")
    return Decisions(
        path=path,
        reviewer=str(data.get("reviewer") or REVIEWER),
        decided_on=str(data.get("decided_on") or ""),
        papers=papers,
        unpublished=list(data.get("unpublished") or []),
        repin=data.get("code_repin"),
        legacy=dict(data.get("legacy_scripts") or {}),
        removed=data.get("removed_code_names"),
        entries=list(data.get("entries") or []),
        pages=list(data.get("pages") or []),
        answers=list(data.get("answers") or []),
        still_open=[one_line(x) for x in data.get("still_open") or []],
    )


@dataclass
class Applied:
    """What the per-entry decisions did to the Spine."""

    released: set[tuple[str, str]] = field(default_factory=set)  # HOLDS lifted
    excluded: dict[tuple[str, str], tuple[Entity, str, int]] = field(default_factory=dict)
    touched: dict[int, set[tuple[str, str]]] = field(default_factory=lambda: defaultdict(set))


def _replace(entity: Entity, op: Mapping[str, Any], source: Path) -> None:
    """Replace `old` with `new` in one text field; `old` must be there once."""
    match = _FIELD.fullmatch(str(op.get("field")))
    old, new = op.get("old"), op.get("new")
    if not match or not isinstance(old, str) or not old or not isinstance(new, str):
        raise stop(source, f"{entity.label}: a replace needs `field`, `old` and `new`")
    name, index = match.group(1), match.group(2)
    container: Any = entity.data
    key: Any = name
    if index is None and name not in container:
        raise stop(source, f"{entity.label}: no {op['field']}")
    if index is not None:
        container, key = entity.data.get(name), int(index)
        if not isinstance(container, list) or key >= len(container):
            raise stop(source, f"{entity.label}: no {op['field']}")
    value = container[key]
    if not isinstance(value, str) or value.count(old) != 1:
        raise stop(source, f"{entity.label}: {op['field']} doesn't have the text to replace once")
    value = " ".join(value.replace(old, new).split())
    if value:
        container[key] = value
    else:  # nothing left: the field, or the list item, goes
        del container[key]


def apply_entries(spine: Spine, decisions: Decisions) -> tuple[Spine, Applied]:
    """The Spine with the reviewer's per-entry decisions applied (a copy)."""
    spine = copy.deepcopy(spine)
    applied = Applied()
    for item in decisions.entries:
        key, _, entity_id = item["entry"].partition("/")
        entity = spine.get(key, entity_id)
        if entity is None:
            raise stop(decisions.path, f"{item['entry']} isn't in the Spine")
        question = item["question"]
        applied.touched[question].add((key, entity_id))
        if item.get("exclude"):
            del spine.entities[key][entity_id]
            applied.excluded[(key, entity_id)] = (entity, one_line(item["exclude"]), question)
            continue
        for op in item.get("replace") or []:
            _replace(entity, op, decisions.path)
        for name, value in (item.get("set") or {}).items():
            if name in entity.data:
                raise stop(decisions.path, f"{entity.label} already has `{name}`")
            entity.data[name] = value
        if item.get("release_hold"):
            if (key, entity_id) not in HOLDS:
                raise stop(decisions.path, f"{entity.label} has no hold to release")
            applied.released.add((key, entity_id))
    return spine, applied


@dataclass
class Pipelines:
    """The ihs-pipelines checkout that `code` evidence is re-pinned to."""

    repo: str
    commit: str
    prefix: str  # where ihsDataR is in it
    from_prefix: str  # where it was in the Spine's repo
    paths: set[str]


def open_pipelines(folder: Path | None, decisions: Decisions, spine: Spine) -> Pipelines | None:
    """Check the re-pin: the commit is in the checkout, its tree is the Spine
    repo's, and the code names said to be removed aren't in it."""
    repin = decisions.repin
    if not repin:
        return None
    if folder is None:
        raise SystemExit(
            f"{decisions.path.name} re-pins code to {repin['repo']}: pass its checkout with "
            "--pipelines"
        )
    commit = str(repin["commit"])
    prefix, from_prefix = str(repin["prefix"]), str(repin["from_prefix"])
    found = (_git(folder, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}") or "").strip()
    if found != commit:
        raise SystemExit(f"{folder} has no commit {commit}")
    theirs = (_git(folder, "rev-parse", f"{commit}:{prefix.rstrip('/')}") or "").strip()
    ours = ""
    if spine.folder is not None and spine.commit:
        ours = _git(spine.folder, "rev-parse", f"{spine.commit}:{from_prefix.rstrip('/')}") or ""
    if not theirs or theirs != ours.strip():
        raise SystemExit(
            f"{prefix} at {repin['repo']}@{commit[:7]} isn't the same tree as {from_prefix} at "
            f"the Spine's commit, so code evidence can't be re-pinned there"
        )
    removed = decisions.removed or {}
    for name in removed.get("names") or []:
        found_name = _git(folder, "grep", "-q", "-w", "-F", "-e", str(name), commit, "--", prefix)
        if found_name is not None:
            raise stop(decisions.path, f"{name} is said to be removed but is in {repin['repo']}")
    listing = _git(folder, "ls-tree", "-r", "--full-tree", "--name-only", commit) or ""
    return Pipelines(str(repin["repo"]), commit, prefix, from_prefix, set(listing.splitlines()))


def years_text(years: Iterable[int]) -> str:
    """Years as runs ("2012 to 2015; 2018"), never a long list of numbers."""
    runs: list[list[int]] = []
    for y in sorted(set(years)):
        if runs and y == runs[-1][-1] + 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    return "; ".join(str(r[0]) if len(r) == 1 else f"{r[0]} to {r[-1]}" for r in runs)


# Evidence -------------------------------------------------------------------


def year(schema: str) -> int:
    return int(schema.split(".")[0].split("_")[1])


@dataclass
class Evidence:
    typed: set[tuple[str, str]] = field(default_factory=set)
    cited: list[str] = field(default_factory=list)  # untyped Spine citations
    unresolved: set[str] = field(default_factory=set)  # names not in the catalog
    # Schema refs found only by looking an unqualified name up in DEFAULT_SCHEMAS.
    assumed: set[str] = field(default_factory=set)

    def add(self, other: Evidence) -> None:
        # A ref stays assumed only if no side found it any other way.
        mine = {r for k, r in self.typed if k == "schema" and r not in self.assumed}
        theirs = {r for k, r in other.typed if k == "schema" and r not in other.assumed}
        self.typed |= other.typed
        self.cited += [c for c in other.cited if c not in self.cited]
        self.unresolved |= other.unresolved
        self.assumed = (self.assumed | other.assumed) - mine - theirs

    def solid_years(self) -> set[int]:
        """Years of the schema evidence that doesn't rest on the default lookup."""
        return {year(r) for k, r in self.typed if k == "schema" and r not in self.assumed}

    def kinds(self) -> set[str]:
        return {kind for kind, _ in self.typed}

    def years(self) -> set[int]:
        return {year(ref) for kind, ref in self.typed if kind == "schema"}

    def items(self) -> list[dict[str, str]]:
        order = {kind: n for n, kind in enumerate(kb.EVIDENCE_TYPES)}
        return [{k: v} for k, v in sorted(self.typed, key=lambda e: (order[e[0]], e[1]))]


def split_anchor(anchor: str) -> list[str]:
    """A source_anchor's parts: split on `;` outside parentheses."""
    parts, depth, current = [], 0, ""
    for ch in anchor:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        depth = max(depth, 0)
        if ch == ";" and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    return [p for p in parts if p]


class Resolver:
    def __init__(
        self,
        spine: Spine,
        schema: Schema,
        decisions: Decisions | None = None,
        pipelines: Pipelines | None = None,
    ) -> None:
        self.spine = spine
        self.schema = schema
        self.decisions = decisions
        self.pipelines = pipelines

    def code(self, path: str) -> tuple[str, str] | None:
        """Code evidence for a path in the Spine's repo: pinned to that repo's
        commit, or to ihs-pipelines where the reviewer re-pinned it."""
        spine, pipes = self.spine, self.pipelines
        if not (spine.commit and spine.repo and path in spine.code_paths):
            return None
        if pipes and path.startswith(pipes.from_prefix):
            target = pipes.prefix + path[len(pipes.from_prefix) :]
            if target not in pipes.paths:
                raise SystemExit(f"{target} isn't in {pipes.repo}@{pipes.commit[:7]}")
            return ("code", f"{pipes.repo}@{pipes.commit} {target}")
        return ("code", f"{spine.repo}@{spine.commit} {path}")

    def pipeline_code(self, ref: str) -> tuple[str, str]:
        """Code evidence the reviewer named: a path (with an optional #L line
        anchor) in ihs-pipelines, which must exist at the pinned commit."""
        pipes = self.pipelines
        if pipes is None:
            raise SystemExit(f"code evidence {ref} needs the decisions' code_repin")
        if ref.split("#", 1)[0] not in pipes.paths:
            raise SystemExit(f"{ref} isn't in {pipes.repo}@{pipes.commit[:7]}")
        return ("code", f"{pipes.repo}@{pipes.commit} {ref}")

    def objects(
        self,
        text: str,
        schemas: Iterable[str],
        *,
        strict: bool = False,
        columns: bool = False,
        assumed: bool = False,
    ) -> Evidence:
        """Oracle names in `text` that are in the catalog. Qualified names
        (IHS_2025.X) that aren't are recorded; unqualified words are only
        taken as names when they are one (unless `strict`)."""
        found = Evidence()
        for match in _ORACLE_TOKEN.finditer(text):
            owner, table, column = match.groups()
            where = [owner] if owner else list(schemas)
            hits = self.schema.tables(where, table)
            if not hits:
                if owner or strict:
                    found.unresolved.add(f"{owner}.{table}" if owner else table)
                continue
            for s in hits:
                if column and columns and column in self.schema.index[s][table]:
                    ref = f"{s}.{table}.{column}"
                else:
                    ref = f"{s}.{table}"
                found.typed.add(("schema", ref))
                if assumed and not owner:
                    found.assumed.add(ref)
        return found

    def anchor(self, anchor: object, schemas: Iterable[str], *, assumed: bool = False) -> Evidence:
        found = Evidence()
        if not anchor:
            return found
        for part in split_anchor(clean_text(str(anchor))):
            typed = False
            for doi in _DOI.findall(part):
                found.typed.add(("paper", doi))
                typed = True
            if self.decisions and (doi := self.decisions.alias(part)):
                found.typed.add(("paper", doi))
                typed = True
            for script, path in LEGACY_SCRIPTS.items():
                if script in part:
                    found.typed.add(("legacy", legacy_ref(path, script, part)))
                    typed = True
            for path in _IHSDATAR_PATH.findall(part):
                code = self.code(path)
                if code:
                    found.typed.add(code)
                    typed = True
            for name, path in CODE_NAMES.items():
                code = self.code(path) if name in part else None
                if code:
                    found.typed.add(code)
                    typed = True
            objects = self.objects(part, schemas, assumed=assumed)
            found.add(objects)
            if not typed and not objects.typed:
                found.cited.append(part)
        return found


def split_basis(evidence: Evidence) -> dict[str, set[int]]:
    """The cohort years schema evidence gives: those found where the entry
    (or its sources) said to look, and those only from the default lookup."""
    solid = evidence.solid_years()
    basis = {"schema": solid, "assumed": evidence.years() - solid}
    return {k: v for k, v in basis.items() if v}


def legacy_ref(path: str, script: str, text: str) -> str:
    lines = re.search(rf"{re.escape(script)}\s+lines?\s+(\d+)\s*-\s*(\d+)", text)
    return f"{path}#L{lines.group(1)}-{lines.group(2)}" if lines else path


# Text: privacy and safety ---------------------------------------------------


@dataclass(frozen=True)
class Removal:
    entry: str  # e.g. "DataSource `oura`"
    where: str  # e.g. "extra.observed_grain"
    why: str
    cut: bool = False  # only part of the text went; the rest is kept


class Cleaner:
    """Removes what mustn't be carried over, and records where (never what)."""

    def __init__(self) -> None:
        self.privacy: list[Removal] = []
        self.machinery: list[Removal] = []
        self.reworded: list[Removal] = []

    def text(self, entity: Entity, where: str, value: str) -> str | None:
        """`value` cleaned, or None if it has to go altogether."""
        for pattern in PRIVACY_TEXT:
            if pattern.search(value):
                value = pattern.sub("", value)
                self.privacy.append(
                    Removal(entity.label, where, "a count from a live query", cut=True)
                )
        if MACHINERY_TEXT.search(value):
            value = MACHINERY_TEXT.sub("", value)
            self.machinery.append(Removal(entity.label, where, "a prototype query or export id"))
        if ORACLE_ERROR.search(value):
            value = ORACLE_ERROR.sub(r"Oracle error \1", value)
            self.reworded.append(
                Removal(entity.label, where, "ORA-nnnnn written as 'Oracle error nnnnn'")
            )
        for why, pattern in PARTICIPANT_LIKE:
            if pattern.search(value):
                self.privacy.append(Removal(entity.label, where, f"looks like {why}"))
                return None
        if kb.data_findings("x", value):
            self.privacy.append(
                Removal(entity.label, where, "the check's participant-data scan flagged it")
            )
            return None
        return value

    def items(self, entity: Entity, where: str, values: object) -> list[str]:
        out = []
        for n, value in enumerate(values or []):  # type: ignore[union-attr]
            if isinstance(value, str) and PROFILING_ITEM.search(value):
                self.privacy.append(
                    Removal(entity.label, f"{where}[{n}]", "an observation from profiling data")
                )
                continue
            cleaned = self.text(entity, f"{where}[{n}]", str(value))
            if cleaned is not None:
                out.append(cleaned)
        return out

    def extra(self, entity: Entity, value: object, where: str = "extra") -> object:
        if isinstance(value, dict):
            out = {}
            for key in sorted(value, key=str):
                path = f"{where}.{key}"
                if key in RELABEL and isinstance(value[key], dict):
                    name, template = RELABEL[key]
                    out[name] = {str(k): template.format(v) for k, v in sorted(value[key].items())}
                    self.reworded.append(
                        Removal(entity.label, path, f"relabelled `{name}`: {RELABEL_WHY}")
                    )
                elif PRIVACY_KEYS.search(str(key)):
                    why = "counts or a value distribution from live data"
                    if "sha256" in str(key):
                        why = "a hash of a query result"
                    self.privacy.append(Removal(entity.label, path, why))
                elif MACHINERY_KEYS.search(str(key)):
                    self.machinery.append(Removal(entity.label, path, "prototype bookkeeping"))
                else:
                    cleaned = self.extra(entity, value[key], path)
                    if cleaned not in (None, {}, []):
                        out[str(key)] = cleaned
            return out
        if isinstance(value, list):
            items = [self.extra(entity, v, f"{where}[{n}]") for n, v in enumerate(value)]
            return [v for v in items if v not in (None, {}, [])]
        if isinstance(value, str):
            return self.text(entity, where, value)
        return value


def clean_text(value: str) -> str:
    """Text for evidence lookups: without the prototype's ids."""
    return MACHINERY_TEXT.sub("", value)


def one_line(text: object) -> str:
    return " ".join(str(text or "").split())


def summary_of(text: str, fallback: str) -> str:
    text = one_line(text) or fallback
    if len(text) <= 200:
        return text
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    if len(first) <= 200:
        return first
    return text[:197].rsplit(" ", 1)[0] + "..."


# Pages ----------------------------------------------------------------------


@dataclass
class Page:
    folder: str
    id: str
    summary: str
    entities: list[Entity]
    evidence: Evidence = field(default_factory=Evidence)
    limitations: list[str] = field(default_factory=list)
    related: set[str] = field(default_factory=set)
    cohorts: set[int] = field(default_factory=set)
    body: list[str] = field(default_factory=list)
    holds: list[str] = field(default_factory=list)  # why it can't be reviewed
    status: str = "draft"
    assembled: bool = False  # made by the conversion, not one Spine entry
    # Where the page's cohorts came from ("spine", "schema", "assumed",
    # "recipe", "inherited"), and the years each gave.
    cohort_basis: dict[str, set[int]] = field(default_factory=dict)
    cohort_source: str = ""  # where the reviewer's confirmed cohorts come from

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.id}.md"

    @property
    def ref(self) -> str:
        return f"{self.folder}/{self.id}"


def front_matter(meta: Mapping[str, Any]) -> str:
    """The front matter, one field a line: lists of plain values in brackets,
    evidence one `type: reference` a line, as docs/KNOWLEDGE_BASE.md shows."""
    lines = []
    for key, value in meta.items():
        if key == "evidence" and value:
            lines.append("evidence:")
            for item in value:
                [(kind, ref)] = item.items()
                lines.append(f"  - {kind}: {_scalar(ref)}")
        elif key == "limitations" and value:
            lines.append("limitations:")
            lines += [f"  - {_scalar(v)}" for v in value]
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(_scalar(v) for v in value)}]")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    text = "\n".join(lines) + "\n"
    if yaml.safe_load(text) != dict(meta):
        raise AssertionError(f"front matter doesn't round-trip for {meta.get('id')}")
    return text


def _scalar(value: object) -> str:
    """One YAML scalar on one line, quoted only when it must be."""
    text = yaml.safe_dump(value, width=1_000_000, allow_unicode=False, default_flow_style=True)
    text = text.removesuffix("\n...\n").removesuffix("\n")
    if "\n" in text:
        raise AssertionError(f"not one line: {value!r}")
    return text


def link(page: Page, target: str, text: str | None = None) -> str:
    """A Markdown link from `page` to the page `target` (folder/id)."""
    folder, name = target.split("/", 1)
    href = f"{name}.md" if folder == page.folder else f"../{folder}/{name}.md"
    return f"[{text or target}]({href})"


def render_value(value: object, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and not _simple_list(item):
                lines.append(f"{pad}- `{key}`:")
                lines += render_value(item, indent + 1)
            else:
                lines.append(f"{pad}- `{key}`: {_inline(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines += render_value(item, indent + 1)
            else:
                lines.append(f"{pad}- {_inline(item)}")
        return lines
    return [f"{pad}- {_inline(value)}"]


def _simple_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(v, str) and re.fullmatch(r"[\w$#.-]+", v) for v in value
    )


def _inline(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(f"`{v}`" for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return one_line(value)


def provenance(entity: Entity, cleaner: Cleaner) -> list[str]:
    anchor = entity.data.get("source_anchor")
    lines = [
        f"- {entity.label} in `registry/{entity.file}`, Spine status `{entity.status}`"
        + (
            f", access tier `{entity.data['access_tier']}`"
            if entity.data.get("access_tier")
            else ""
        )
        + "."
    ]
    if anchor:
        text = cleaner.text(entity, "source_anchor", one_line(anchor))
        if text:
            lines.append(f"  - Spine source: {text}")
    return lines


class Converter:
    def __init__(
        self,
        spine: Spine,
        schema: Schema,
        decisions: Decisions | None = None,
        pipelines: Pipelines | None = None,
        applied: Applied | None = None,
    ) -> None:
        self.spine = spine
        self.schema = schema
        self.decisions = decisions
        self.applied = applied or Applied()
        self.resolve = Resolver(spine, schema, decisions, pipelines)
        # Which pages each question's decisions touched (for REVIEW.md).
        self.touched: dict[int, set[str]] = defaultdict(set)
        self.clean = Cleaner()
        self.pages: dict[str, Page] = {}  # ref -> page
        self.home: dict[tuple[str, str], str] = {}  # (key, id) -> page ref
        self.unmapped: list[tuple[str, str]] = []  # (entry, why)
        self.var_refs: dict[str, Evidence] = {}  # raw variable id -> its columns
        self.renamed: list[tuple[str, str]] = []

    # Shared -----------------------------------------------------------------

    def source_schemas(self, source_ids: Iterable[object]) -> list[str]:
        out: set[str] = set()
        for source_id in source_ids:
            source = self.spine.get("data_sources", source_id)
            if source:
                out |= {str(c) for c in source.data.get("available_cohorts") or []}
        return sorted(out)

    def add(self, page: Page, key: str | None = None, entity_id: str | None = None) -> Page:
        if page.ref in self.pages:
            raise SystemExit(f"Two pages at {page.ref}")
        self.pages[page.ref] = page
        if key and entity_id:
            self.home[(key, entity_id)] = page.ref
        return page

    def page_of(self, key: str, entity_id: object) -> str | None:
        return self.home.get((key, str(entity_id)))

    def text(self, entity: Entity, where: str, value: object) -> str:
        return self.clean.text(entity, where, one_line(value)) or ""

    # Raw variables: where each one's column is -----------------------------

    def locate_variables(self) -> None:
        for var in self.spine.entities["raw_variables"].values():
            obj = str(var.data.get("oracle_object") or "")
            column = str(var.data.get("oracle_column") or "").upper()
            owner, _, table = obj.upper().rpartition(".")
            schemas = [owner] if owner else self.source_schemas([var.data.get("data_source")])
            found = Evidence()
            years = self.schema.columns(schemas, table, column) if column else []
            for s in years:
                found.typed.add(("schema", f"{s}.{table}.{column}"))
            if not years:
                found.unresolved.add(f"{obj}.{column}")
            self.var_refs[var.id] = found

    # Tables -------------------------------------------------------------------

    def tables(self) -> None:
        groups: dict[str, list[Entity]] = defaultdict(list)
        where: dict[str, list[str]] = {}
        for entity in self.spine.entities["raw_schemas"].values():
            cohort = str(entity.data.get("cohort") or "")
            table = str(entity.data.get("oracle_object") or "").upper()
            if self.schema.tables([cohort], table):
                groups[f"{cohort}.{table}"].append(entity)
                where[entity.id] = [cohort]
            else:
                self.unmapped.append((entity.label, f"{cohort}.{table} isn't in the catalog"))
        for var in self.spine.entities["raw_variables"].values():
            refs = sorted(r for _, r in self.var_refs[var.id].typed)
            if not refs:
                missing = sorted(self.var_refs[var.id].unresolved)[0]
                why = f"its column {missing} isn't in the catalog for its source's cohorts"
                self.unmapped.append((var.label, why))
                continue
            schemas = sorted({r.split(".")[0] for r in refs})
            table = refs[0].split(".")[1]
            groups[f"{schemas[-1]}.{table}"].append(var)
            where[var.id] = schemas
        for name in sorted(groups):
            self.table_page(name, groups[name], where)

    def table_page(self, name: str, entities: list[Entity], where: Mapping[str, list[str]]) -> None:
        schema, table = name.split(".")
        sources = sorted(
            {str(e.data.get("data_source")) for e in entities if e.data.get("data_source")}
        )
        source_names = [
            str(s.data.get("name"))
            for s in (self.spine.get("data_sources", i) for i in sources)
            if s
        ]
        described = sorted(
            {str(c).upper() for e in entities for c in e.data.get("expected_columns") or []}
            | {
                str(e.data["oracle_column"]).upper()
                for e in entities
                if e.data.get("oracle_column")
            }
        )
        page = Page(
            "tables",
            name,
            summary_of(
                f"{name}: {' and '.join(source_names) or 'IHS'} "
                f"{self.schema.types.get((schema, table), 'TABLE').lower()}; the Spine "
                f"describes {len(described)} of its columns.",
                name,
            ),
            entities,
        )
        page.evidence.typed.add(("schema", name))
        page.limitations.append(
            "Only the columns listed here are described; generated/schema has all of them."
        )
        kind = self.schema.types.get((schema, table), "TABLE").lower()
        page.body += [f"# {name}", "", f"A {kind} in the {schema} cohort schema.", ""]
        years: set[int] = set()
        for entity in entities:
            self.home[(entity.key, entity.id)] = page.ref
            years |= {year(s) for s in where[entity.id]}
            page.evidence.add(
                self.resolve.anchor(entity.data.get("source_anchor"), where[entity.id])
            )
            for source in [entity.data.get("data_source")]:
                if source and self.spine.get("data_sources", source):
                    page.related.add(f"sources/{source}")
        for s in sorted(years):
            if f"IHS_{s}" != schema:
                page.body += [
                    f"The Spine's notes below also hold for IHS_{s}.{table}, which has the "
                    "columns they name.",
                    "",
                ]
        other = sorted(
            set(self.schema.tables(self.schema.index, table)) - {f"IHS_{y}" for y in years}
        )
        if other:
            page.body.append(
                f"Tables of the same name are also in {', '.join(other)}; the Spine doesn't say "
                "whether they mean the same, so this page doesn't cover them."
            )
        for entity in entities:
            if entity.key == "raw_schemas":
                self.raw_schema_section(page, entity)
        variables = [e for e in entities if e.key == "raw_variables"]
        if variables:
            page.body += ["", "## Columns the Spine describes"]
        for var in sorted(variables, key=lambda e: (str(e.data.get("oracle_column")), e.id)):
            self.variable_section(page, var)
        page.cohorts = years
        page.cohort_basis["schema"] = set(years)
        page.body += ["", "## Where this came from", ""]
        for entity in entities:
            page.body += provenance(entity, self.clean)
        self.add(page)

    def raw_schema_section(self, page: Page, entity: Entity) -> None:
        schema, table = page.id.split(".")
        page.body += ["", f"## As the Spine describes it (`{entity.id}`)", ""]
        cols = [str(c).upper() for c in entity.data.get("expected_columns") or []]
        for col in cols:
            if self.schema.columns([schema], table, col):
                page.evidence.typed.add(("schema", f"{page.id}.{col}"))
            else:
                page.evidence.unresolved.add(f"{page.id}.{col}")
        if cols:
            page.body.append(f"Expected columns: {', '.join(f'`{c}`' for c in cols)}.")
        caveats = self.clean.items(entity, "known_caveats", entity.data.get("known_caveats"))
        page.limitations += caveats
        extra = self.clean.extra(entity, entity.data.get("extra"))
        if extra:
            page.body += ["", "Details from the Spine:", "", *render_value(extra)]

    def variable_section(self, page: Page, var: Entity) -> None:
        data = var.data
        column = str(data.get("oracle_column") or "").upper()
        page.evidence.add(self.var_refs[var.id])
        page.body += ["", f"### {column} (`{var.id}`)", ""]
        if data.get("description"):
            page.body.append(self.text(var, "description", data["description"]))
            page.body.append("")
        facts = [f"Spine status: `{var.status}`"]
        if data.get("name") and str(data["name"]) != column:
            facts.append(f"name in the Spine: {one_line(data['name'])}")
        if data.get("data_type"):
            facts.append(f"type in the Spine: `{one_line(data['data_type'])}`")
        _, _, table = str(data.get("oracle_object")).upper().rpartition(".")
        types = self.schema.index.get(page.id.split(".")[0], {}).get(table, {})
        if column in types:
            facts.append(f"type in the catalog: `{types[column]}`")
        page.body += [f"- {fact}" for fact in facts]
        construct = data.get("maps_to_construct")
        if construct and self.spine.get("constructs", construct):
            page.body.append(f"- construct: `{construct}` (see the constructs glossary)")
            page.related.add("features/constructs")
        feature = data.get("maps_to_feature")
        if feature and self.spine.get("canonical_features", feature):
            page.body.append(f"- used by: {link(page, f'features/{feature}')}")
            page.related.add(f"features/{feature}")
        extra = self.clean.extra(var, data.get("extra"))
        if isinstance(extra, dict) and extra.get("legacy_source"):
            page.evidence.add(
                self.resolve.anchor(extra["legacy_source"], DEFAULT_SCHEMAS, assumed=True)
            )
        if extra:
            page.body += ["- details from the Spine:", *render_value(extra, 1)]

    # Sources ------------------------------------------------------------------

    def sources(self) -> None:
        for source in self.spine.entities["data_sources"].values():
            self.source_page(source)

    def source_page(self, source: Entity) -> None:
        data = source.data
        schemas = [str(c) for c in data.get("available_cohorts") or []]
        name = one_line(data.get("name")) or source.id
        page = Page(
            "sources",
            source.id,
            summary_of(
                f"{name}: {one_line(data.get('modality')).replace('_', ' ') or 'data'} "
                f"source ({one_line(data.get('platform')) or 'platform not recorded'}).",
                name,
            ),
            [source],
        )
        self.home[("data_sources", source.id)] = page.ref
        page.cohorts = {year(s) for s in schemas}
        page.cohort_basis["spine"] = set(page.cohorts)
        page.evidence.add(self.resolve.anchor(data.get("source_anchor"), schemas))
        page.limitations += self.clean.items(
            source, "known_limitations", data.get("known_limitations")
        )
        page.body += [f"# {name}", ""]
        facts = [
            f"Modality: {one_line(data.get('modality')) or 'not recorded'}.",
            f"Platform: {one_line(data.get('platform')) or 'not recorded'}.",
            f"Cohorts in the Spine: {', '.join(schemas) or 'none recorded'}.",
        ]
        if data.get("contains_pii") is not None:
            facts.append(
                "The Spine marks it as holding personal information."
                if data["contains_pii"]
                else "The Spine marks it as holding no personal information."
            )
        page.body += [f"- {fact}" for fact in facts]
        tables = sorted(
            {
                self.home[(e.key, e.id)]
                for key in ("raw_schemas", "raw_variables")
                for e in self.spine.entities[key].values()
                if str(e.data.get("data_source")) == source.id and (e.key, e.id) in self.home
            }
        )
        for ref in tables:
            page.evidence.typed.add(("schema", ref.split("/", 1)[1]))
            page.related.add(ref)
        if tables:
            page.body += ["", "## Tables the Spine describes", ""]
            page.body += [f"- {link(page, ref, ref.split('/', 1)[1])}" for ref in tables]
        extra = self.clean.extra(source, data.get("extra"))
        if extra:
            page.evidence.add(self.resolve.objects(yaml.safe_dump(extra), schemas))
            page.evidence.unresolved |= self.missing_columns(extra, schemas)
            page.body += ["", "## Details from the Spine", "", *render_value(extra)]
        page.body += ["", "## Where this came from", "", *provenance(source, self.clean)]
        self.add(page)

    def missing_columns(self, extra: object, schemas: list[str]) -> set[str]:
        """Columns the Spine lists for an Oracle object in `extra` that the
        catalog doesn't have."""
        missing: set[str] = set()
        if isinstance(extra, list):
            for item in extra:
                missing |= self.missing_columns(item, schemas)
        if not isinstance(extra, dict):
            return missing
        for value in extra.values():
            missing |= self.missing_columns(value, schemas)
        obj = str(extra.get("oracle_object") or "").upper()
        owner, _, table = obj.rpartition(".")
        where = [owner] if owner else schemas
        for key in ("required_columns", "verified_columns", "companion_columns"):
            for column in extra.get(key) or []:
                if obj and not self.schema.columns(where, table, str(column).upper()):
                    missing.add(f"{obj}.{column}")
        return missing

    # Features -----------------------------------------------------------------

    def features(self) -> None:
        recipes = {
            str(r.data.get("canonical_feature")): r
            for r in self.spine.entities["feature_recipes"].values()
        }
        for recipe in self.spine.entities["feature_recipes"].values():
            if not self.spine.get("canonical_features", recipe.data.get("canonical_feature")):
                self.unmapped.append((recipe.label, "its canonical_feature isn't in the Spine"))
        for feature in self.spine.entities["canonical_features"].values():
            recipe = recipes.get(feature.id) or self.spine.get(
                "feature_recipes", feature.data.get("recipe")
            )
            self.feature_page(feature, recipe)

    def feature_page(self, feature: Entity, recipe: Entity | None) -> None:
        data = feature.data
        entities = [feature] + ([recipe] if recipe else [])
        page = Page(
            "features",
            feature.id,
            summary_of(self.text(feature, "description", data.get("description")), feature.id),
            entities,
        )
        self.home[("canonical_features", feature.id)] = page.ref
        variables = [str(v) for v in data.get("source_variables") or []]
        sources = [str(s) for s in (recipe.data.get("sources") or [])] if recipe else []
        if recipe:
            self.home[("feature_recipes", recipe.id)] = page.ref
            variables += [
                str(v) for v in recipe.data.get("inputs") or [] if str(v) not in variables
            ]
        for var_id in variables:
            var = self.spine.get("raw_variables", var_id)
            if var:
                page.evidence.add(self.var_refs[var_id])
                sources.append(str(var.data.get("data_source")))
                if (ref := self.page_of("raw_variables", var_id)) is not None:
                    page.related.add(ref)
            else:
                page.holds.append(f"it names a raw variable `{var_id}` the Spine doesn't have")
        sources = sorted(set(sources))
        schemas = self.source_schemas(sources) or list(DEFAULT_SCHEMAS)
        assumed = not self.source_schemas(sources)
        for entity in entities:
            anchor = entity.data.get("source_anchor")
            page.evidence.add(self.resolve.anchor(anchor, schemas, assumed=assumed))
        if recipe and recipe.data.get("implemented_in"):
            code = self.resolve.code(str(recipe.data["implemented_in"]))
            if code:
                page.evidence.typed.add(code)
            else:
                page.holds.append(
                    f"its recipe's code `{recipe.data['implemented_in']}` isn't in the "
                    "Spine repo's last commit"
                )
        page.cohorts = page.evidence.years()
        page.cohort_basis = split_basis(page.evidence)
        if recipe and page.evidence.kinds() & {"code", "legacy"}:
            # The recipe's sources, in the cohorts the Spine says they cover:
            # what the lab's code and legacy scripts it cites were written for.
            recipe_sources = [str(s) for s in recipe.data.get("sources") or []]
            from_recipe = {year(s) for s in self.source_schemas(recipe_sources)}
            if from_recipe:
                page.cohort_basis["recipe"] = from_recipe
            page.cohorts |= from_recipe
        for source in sources:
            if self.spine.get("data_sources", source):
                page.related.add(f"sources/{source}")
        page.body += [
            f"# {feature.id}",
            "",
            self.text(feature, "description", data.get("description")),
        ]
        facts = [
            ("Unit", data.get("unit")),
            ("Granularity", data.get("granularity")),
        ]
        page.body += [""] + [f"- {k}: {one_line(v)}" for k, v in facts if v]
        construct = self.spine.get("constructs", data.get("construct"))
        if construct:
            page.related.add("features/constructs")
            page.body += ["", f"## Construct: {one_line(construct.data.get('name'))}", ""]
            page.body.append(self.text(construct, "description", construct.data.get("description")))
            parents = [
                c for c in self.spine.entities["constructs"].values()
                if construct.id in (c.data.get("children") or [])
            ]  # fmt: skip
            if parents:
                page.body += [
                    "",
                    "Part of: " + ", ".join(one_line(c.data.get("name")) for c in parents) + ".",
                ]
            page.body += [
                "",
                f"See {link(page, 'features/constructs', 'the constructs glossary')} "
                f"(`{construct.id}`).",
            ]
        if recipe:
            self.recipe_section(page, recipe)
        else:
            page.limitations.append(
                "The Spine has no recipe for this feature, so how it's computed isn't "
                "recorded here."
            )
        if variables:
            page.body += ["", "## Raw variables", ""]
            for var_id in variables:
                ref = self.page_of("raw_variables", var_id)
                page.body.append(
                    f"- `{var_id}`" + (f": {link(page, ref, ref.split('/', 1)[1])}" if ref else "")
                )
        page.body += ["", "## Where this came from", ""]
        for entity in entities:
            page.body += provenance(entity, self.clean)
        self.add(page)

    def recipe_section(self, page: Page, recipe: Entity) -> None:
        data = recipe.data
        path = data.get("implemented_in")
        code = self.resolve.code(str(path)) if path else None
        page.body += ["", f"## How it's computed (recipe `{recipe.id}`)", ""]
        if data.get("description"):
            page.body += [self.text(recipe, "description", data["description"]), ""]
        facts = [
            ("Aggregation window", data.get("aggregation_window")),
            ("Unit", data.get("unit")),
            ("Sources", ", ".join(f"`{s}`" for s in data.get("sources") or [])),
            ("Inputs", ", ".join(f"`{s}`" for s in data.get("inputs") or [])),
            ("Quality flags", ", ".join(f"`{s}`" for s in data.get("quality_flags") or [])),
            ("Code", f"`{code[1]}`" if code else ""),
        ]
        if "review_required" in data:
            facts.append(("Review required (Spine)", "yes" if data["review_required"] else "no"))
        page.body += [f"- {k}: {v}" for k, v in facts if v]
        rules = self.clean.items(recipe, "cleaning_rules", data.get("cleaning_rules"))
        if rules:
            page.body += ["", "Cleaning rules:", ""] + [f"- {one_line(r)}" for r in rules]
        qc = [str(q) for q in data.get("qc_rules") or []]
        if qc:
            page.body += ["", "QC rules:", ""]
            for rule in qc:
                if self.spine.get("qc_rules", rule):
                    page.body.append(f"- {link(page, f'qc/{rule}', rule)}")
                    page.related.add(f"qc/{rule}")
                else:
                    page.body.append(f"- `{rule}` (not in the Spine)")
                    page.holds.append(f"its recipe names a QC rule `{rule}` the Spine doesn't have")
        if data.get("implemented_in"):
            page.limitations.append(
                "The cleaning rules are as the Spine recorded them; the linked code is "
                "authoritative."
            )

    def glossary(self) -> None:
        constructs = self.spine.entities["constructs"]
        page = Page(
            "features",
            "constructs",
            "Glossary of the scientific constructs the lab's features measure, from the Spine.",
            list(constructs.values()),
            assembled=True,
        )
        page.limitations += [
            "Constructs are concepts, not tied to cohort years; the Spine gives none.",
            "Assembled by the conversion from the Spine's Construct entries; each entry "
            "keeps its own Spine status.",
        ]
        used: dict[str, list[str]] = defaultdict(list)
        for feature in self.spine.entities["canonical_features"].values():
            used[str(feature.data.get("construct"))].append(feature.id)
        page.body += [
            "# Constructs",
            "",
            "The scientific concepts the lab's derived features measure, independent of "
            "any device's fields. Carried over from the prototype Spine.",
        ]
        for construct in sorted(constructs.values(), key=lambda c: c.id):
            self.home[("constructs", construct.id)] = page.ref
            data = construct.data
            page.evidence.add(
                self.resolve.anchor(data.get("source_anchor"), DEFAULT_SCHEMAS, assumed=True)
            )
            page.body += [
                "",
                f"## {one_line(data.get('name')) or construct.id} (`{construct.id}`)",
                "",
            ]
            if data.get("description"):
                page.body += [self.text(construct, "description", data["description"]), ""]
            for label, key in (("Narrower", "children"), ("Related", "related_constructs")):
                names = [str(c) for c in data.get(key) or []]
                if names:
                    page.body.append(f"- {label}: " + ", ".join(f"`{c}`" for c in names))
            features = sorted(used.get(construct.id, []))
            if features:
                page.body.append(
                    "- Features: " + ", ".join(link(page, f"features/{f}", f) for f in features)
                )
            extra = self.clean.extra(construct, data.get("extra"))
            if extra:
                page.body += ["- Details from the Spine:", *render_value(extra, 1)]
            page.body += provenance(construct, self.clean)
        self.add(page)

    # QC rules -----------------------------------------------------------------

    def qc(self) -> None:
        for rule in self.spine.entities["qc_rules"].values():
            self.qc_page(rule)

    def qc_page(self, rule: Entity) -> None:
        data = rule.data
        page = Page(
            "qc",
            rule.id,
            summary_of(self.text(rule, "name", data.get("name")), rule.id),
            [rule],
        )
        self.home[("qc_rules", rule.id)] = page.ref
        targets = [str(t) for t in data.get("applies_to") or []]
        extra = self.clean.extra(rule, data.get("extra"))
        page.evidence.add(
            self.resolve.anchor(data.get("source_anchor"), DEFAULT_SCHEMAS, assumed=True)
        )
        if isinstance(extra, dict):
            if extra.get("implemented_in"):
                code = self.resolve.code(str(extra["implemented_in"]))
                if code:
                    page.evidence.typed.add(code)
            page.evidence.add(
                self.resolve.objects(yaml.safe_dump(extra), DEFAULT_SCHEMAS, assumed=True)
            )
        page.body += [f"# {one_line(data.get('name')) or rule.id}", ""]
        condition = self.text(rule, "condition", data.get("condition"))
        page.body += [condition, ""]
        facts = [("When it fails", data.get("on_fail")), ("Severity", data.get("severity"))]
        page.body += [f"- {k}: `{one_line(v)}`" for k, v in facts if v]
        if targets:
            page.body += ["", "## Applies to", ""]
        for target in targets:
            feature = self.spine.get("canonical_features", target)
            var = self.spine.get("raw_variables", target)
            if feature:
                page.body.append(f"- {link(page, f'features/{target}', target)}")
                page.related.add(f"features/{target}")
            elif var and (ref := self.page_of("raw_variables", target)):
                page.body.append(f"- `{target}`: {link(page, ref, ref.split('/', 1)[1])}")
                page.related.add(ref)
                tables = {r.rsplit(".", 1)[0] for _, r in self.var_refs[target].typed}
                page.evidence.typed |= {("schema", t) for t in tables}
            else:
                page.body.append(f"- `{target}` (not in the Spine)")
                page.holds.append(f"it applies to `{target}`, which the Spine doesn't have")
        if extra:
            page.body += ["", "## Details from the Spine", "", *render_value(extra)]
        kinds = page.evidence.kinds()
        if kinds & {"code", "legacy"}:
            page.limitations.append(
                "Stated as the Spine recorded it; the code that applies it is authoritative."
            )
        elif "paper" in kinds:
            page.limitations.append(
                "Taken from a published paper's methods; check it matches how the lab's "
                "pipelines apply it before relying on it for another analysis."
            )
        page.body += ["", "## Where this came from", "", *provenance(rule, self.clean)]
        self.add(page)

    def qc_cohorts(self) -> None:
        for page in self.pages.values():
            if page.folder != "qc":
                continue
            page.cohorts |= page.evidence.years()
            page.cohort_basis = split_basis(page.evidence)
            if not page.evidence.kinds() & {"code", "legacy"}:
                continue  # a paper's method holds where the paper says, which isn't recorded
            # Pipeline logic holds wherever the features it's applied to do.
            inherited: set[int] = set()
            for ref in page.related:
                if ref.startswith("features/") and ref in self.pages:
                    inherited |= self.pages[ref].cohorts
            if inherited:
                page.cohort_basis["inherited"] = inherited
            page.cohorts |= inherited

    # Papers -------------------------------------------------------------------

    def papers(self) -> None:
        cited: dict[str, list[tuple[str, Entity]]] = defaultdict(list)
        for key in LIST_KEYS:
            for entity in self.spine.entities[key].values():
                for part in split_anchor(str(entity.data.get("source_anchor") or "")):
                    for doi in _DOI.findall(part):
                        label = one_line(part.split(doi)[0]) or doi
                        cited[doi].append((label, entity))
                    if self.decisions and (doi := self.decisions.alias(part)):
                        paper = self.decisions.papers[doi]
                        cited[doi].append((paper.label or paper.names[0], entity))
        slugs: set[str] = set()
        for doi in sorted(cited):
            labels = sorted({label for label, _ in cited[doi]})
            label = labels[0]
            known = self.decisions.papers.get(doi) if self.decisions else None
            if known and known.label:
                label = known.label
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "paper"
            while slug in slugs or any(p.id.casefold() == slug for p in self.pages.values()):
                slug += "-paper"
            slugs.add(slug)
            entities = sorted(
                {(e.key, e.id): e for _, e in cited[doi]}.values(), key=lambda e: (e.key, e.id)
            )
            page = Page(
                "papers",
                slug,
                f'Lab paper the Spine cites as "{label}" ({doi}), and what the Spine '
                "takes from it.",
                entities,
                assembled=True,
            )
            page.evidence.typed.add(("paper", doi))
            if known:
                self.paper_page(page, known, label)
            else:
                page.limitations += [
                    "Not a summary of the paper: this lists only what the Spine takes from it. "
                    "Its title, authors, cohorts, and findings aren't recorded here yet.",
                ]
                page.body += [
                    f"# {label}",
                    "",
                    f"DOI: [{doi}](https://doi.org/{doi})",
                    "",
                    "The prototype Spine cites this paper for the entries below. A person should "
                    "add the title, authors, the cohorts it used, and a short summary.",
                ]
            page.body += ["", "## What the Spine takes from it", ""]
            for entity in entities:
                ref = self.page_of(entity.key, entity.id)
                what = one_line(
                    entity.data.get("description") or entity.data.get("condition")
                    or entity.data.get("name") or ""
                )  # fmt: skip
                what = self.clean.text(entity, "description", what) or ""
                target = link(page, ref, entity.id) if ref else f"`{entity.id}`"
                page.body.append(f"- {TYPE_NAMES[entity.key]} {target}: {what}")
                if ref and ref != page.ref:
                    page.related.add(ref)
                    self.pages[ref].related.add(page.ref)
            self.add(page)

    def paper_page(self, page: Page, paper: PaperInfo, label: str) -> None:
        """A paper page for a paper the reviewer identified: its title, the
        cohorts it analysed, and a short summary in our own words."""
        venue = paper.venue.split(" (", 1)[0]
        page.summary = summary_of(f"{paper.title} ({venue}, {paper.year}).", page.summary)
        page.cohorts = set(paper.cohorts)
        page.cohort_basis = {"paper": set(paper.cohorts)}
        page.limitations += [
            "The summary is a short description in our own words, not the paper's; the paper "
            "is authoritative.",
            f"Cohorts are the ones its methods describe ({paper.cohorts_from}).",
        ]
        if paper.preprint:
            page.limitations.append("A preprint, not peer reviewed.")
        page.body += [
            f"# {paper.title}",
            "",
            f"- {paper.venue}, {paper.year}. DOI: [{paper.doi}](https://doi.org/{paper.doi})",
            f"- Cited in the Spine as: {label}.",
            f"- Cohorts analysed: {years_text(paper.cohorts)} ({paper.cohorts_from}).",
            "- Checked at: " + ", ".join(f"<{url}>" for url in paper.verified) + ".",
            "",
            paper.summary,
        ]
        self.touched[3].add(page.ref)

    # The reviewer's decisions, on the pages ---------------------------------------

    def paper_cohorts(self) -> None:
        """A feature or QC page whose only evidence of method is a paper
        applies to the cohorts that paper analysed (question 3)."""
        if not self.decisions:
            return
        for page in self.pages.values():
            if page.folder not in ("features", "qc") or page.assembled:
                continue
            if page.evidence.kinds() & {"code", "legacy"}:
                continue  # the pipeline's cohorts, inferred above
            dois = sorted(r for k, r in page.evidence.typed if k == "paper")
            known = [self.decisions.papers[d] for d in dois if d in self.decisions.papers]
            if not known:
                continue
            years = set().union(*(p.cohorts for p in known))
            # The paper's cohorts replace years guessed only from the default lookup.
            firm = set().union(*(v for k, v in page.cohort_basis.items() if k != "assumed"), set())
            guessed = page.cohort_basis.pop("assumed", set()) - firm
            page.cohorts = (page.cohorts - guessed) | years
            page.cohort_basis["paper"] = years
            page.limitations.append(
                f"Its cohorts are the ones the cited paper{'s' if len(known) > 1 else ''} "
                f"analysed ({years_text(years)}); whether it holds for other years isn't recorded."
            )
            for paper in known:
                if paper.preprint:
                    page.limitations.append(
                        f"{paper.label or paper.doi} ({paper.doi}) is a preprint, "
                        "not peer reviewed."
                    )
            self.touched[3].add(page.ref)
        for page in self.pages.values():
            if any(k == "paper" and self.decisions.alias_target(r) for k, r in page.evidence.typed):
                self.touched[2].add(page.ref)

    def apply_pages(self) -> None:
        """The reviewer's per-page decisions: evidence, confirmed cohorts,
        limitations, and holds. None of them can mark a page reviewed."""
        if not self.decisions:
            return
        source = self.decisions.path
        for item in self.decisions.pages:
            page = self.pages.get(item["page"])
            if page is None:
                raise stop(source, f"there's no page {item['page']}")
            for evidence in item.get("evidence") or []:
                [(kind, ref)] = evidence.items()
                if kind == "code":
                    page.evidence.typed.add(self.resolve.pipeline_code(str(ref)))
                elif kind == "paper" and ref in self.decisions.papers:
                    page.evidence.typed.add(("paper", str(ref)))
                elif kind == "legacy" and kb._LEGACY_REF.fullmatch(str(ref)):
                    page.evidence.typed.add(("legacy", str(ref)))
                else:
                    raise stop(source, f"{item['page']}: evidence {kind}: {ref} isn't usable")
            if "cohorts" in item:
                years = {int(y) for y in item["cohorts"]}
                page.cohorts = years
                page.cohort_basis = {"reviewer": years}
                page.cohort_source = one_line(item["source"])
            page.limitations += [one_line(x) for x in item.get("limitations") or []]
            if item.get("hold"):
                page.holds.append(
                    f"the reviewer holds it as a draft (question {item['question']}): "
                    + one_line(item["hold"])
                )
            self.touched[item["question"]].add(page.ref)

    def removed_names(self) -> None:
        """Say so on pages that name prototype code no longer in ihsDataR."""
        removed = (self.decisions.removed if self.decisions else None) or {}
        names = [str(n) for n in removed.get("names") or []]
        for page in self.pages.values():
            text = "\n".join(page.body)
            found = [n for n in names if re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", text)]
            if found:
                page.limitations.append(
                    "Its details name prototype code that is no longer in ihsDataR ("
                    + ", ".join(found)
                    + "); the Oracle objects and columns are what generated/schema checks."
                )
                self.touched[int(removed.get("question") or 4)].add(page.ref)

    def mark_touched(self) -> None:
        """Map the per-entry decisions, and re-pinned code, to pages."""
        for question, keys in self.applied.touched.items():
            for key in keys:
                if key in self.home:
                    self.touched[question].add(self.home[key])
        pipes = self.resolve.pipelines
        for page in self.pages.values():
            if pipes and any(
                k == "code" and r.startswith(f"{pipes.repo}@") for k, r in page.evidence.typed
            ):
                self.touched[4].add(page.ref)

    # Statuses -----------------------------------------------------------------

    def decide_status(self, page: Page) -> None:
        """`reviewed` only if every Spine entry on the page is `validated` and
        unchanged since the Spine's last commit, nothing on it is held, it has
        typed evidence of the kind the page needs, every Oracle name it gives
        is in the catalog, and it has cohorts (given or inferred)."""
        holds = list(page.holds)
        if page.assembled:
            holds.append("the conversion assembled it; there's no single Spine entry behind it")
        for entity in page.entities:
            if entity.status != "validated":
                holds.append(f"{entity.label} is `{entity.status}` in the Spine")
            if entity.changed:
                holds.append(
                    f"{entity.label} was changed after the Spine's last commit, so no review "
                    "of its current text is on record"
                )
            key = (entity.key, entity.id)
            if key in HOLDS and key not in self.applied.released:
                holds.append(f"{entity.label}: {HOLDS[key]}")
        kinds = page.evidence.kinds()
        needed = {"code", "legacy", "paper"}
        if page.folder in ("sources", "tables"):
            needed.add("schema")
        # A legacy script counts only if someone has seen it (question 5).
        unseen = sorted(
            r for k, r in page.evidence.typed
            if k == "legacy" and self.decisions and not self.decisions.legacy_seen(r)
        )  # fmt: skip
        supporting = {k for k, r in page.evidence.typed if not (k == "legacy" and r in unseen)}
        if not supporting & needed and not page.assembled:
            if kinds & needed:
                holds.append(
                    "its only evidence of how it's computed is "
                    + ", ".join(f"`{r}`" for r in unseen)
                    + ", a script nobody has seen in a repo yet (question 5)"
                )
                self.touched[5].add(page.ref)
            else:
                cited = "; ".join(page.evidence.cited) or "nothing"
                holds.append(
                    "it has no typed evidence of the kind it needs (the Spine cites only: "
                    + cited
                    + ")"
                )
        if page.evidence.unresolved:
            holds.append(
                "it names Oracle objects that aren't in generated/schema: "
                + ", ".join(sorted(page.evidence.unresolved))
            )
        if not page.cohorts and not page.assembled:
            holds.append("the Spine doesn't say which cohorts it applies to")
        page.holds = list(dict.fromkeys(holds))
        page.status = "draft" if page.holds else "reviewed"
        if not page.cohorts and not any("cohort" in x for x in page.limitations):
            page.limitations.append("The Spine doesn't say which cohort years this applies to.")
        if not page.limitations:
            page.limitations.append(
                "The Spine recorded no limitations for this; that doesn't mean there are none."
            )

    # Writing ------------------------------------------------------------------

    def render(self, page: Page) -> str:
        related = sorted(r for r in page.related if r != page.ref and r in self.pages)
        meta: dict[str, Any] = {
            "id": page.id,
            "kind": kb.FOLDERS[page.folder],
            "status": page.status,
            "summary": page.summary,
            "evidence": page.evidence.items(),
            "limitations": [one_line(x) for x in dict.fromkeys(page.limitations)],
        }
        if related:
            meta["related"] = related
        meta["cohorts"] = sorted(page.cohorts)
        front = front_matter(meta)
        body = list(page.body)
        if page.holds:
            body += ["", "## Why this page is a draft", ""]
            body += [f"- {h[0].upper()}{h[1:]}." for h in page.holds]
        text = "\n".join(body).strip() + "\n"
        text = re.sub(r"\n{3,}", "\n\n", text)
        return f"---\n{front}---\n\n{self.guard(page, text)}"

    def guard(self, page: Page, text: str) -> str:
        """Qualified Oracle names the catalog doesn't have, said so plainly
        (the check requires every IHS_nnnn.NAME a page mentions to exist)."""

        def replace(match: re.Match[str]) -> str:
            owner, table, column = match.group(1), match.group(2), match.group(3)
            if column and column.islower() and column in kb._FILE_EXTENSIONS:
                column = None  # a file name, as the check reads it
            parts = [owner, table.upper()] + ([column.upper()] if column else [])
            if self.schema.exists(".".join(parts)):
                return match.group(0)
            if column and self.schema.exists(f"{owner}.{table.upper()}"):
                self.renamed.append((page.path, match.group(0)))
                return f"{owner}.{table} (its column {column} isn't in generated/schema)"
            self.renamed.append((page.path, match.group(0)))
            return f"{'.'.join(parts[1:])} in {owner} (not in generated/schema)"

        return kb._SCHEMA_REF.sub(replace, text)


# The rest of the knowledge base -------------------------------------------------

AGENTS_MD = """\
# IHS knowledge base

The lab's shared, reviewed knowledge about Intern Health Study data: what
tables and columns mean, device quirks, cleaning and QC rules, how derived
features are computed, and the papers behind them. DataLab gives every
session a copy at `/work/kb`; GitHub keeps the history.

## The one rule

**No participant-level data, ever.** No study or participant IDs, no
per-person dates, no rows or tables of values, no small counts. Schema
metadata and aggregate shapes are fine. DataLab scans every edit, and a
person reviews it; neither replaces your care.

## How to read it

1. Start from `index.md`: one line per page, grouped by folder.
2. Search with grep, for example `rg -il 'sleep|FITBITSLEEPLOGS' .`
3. Weigh each page by its front matter:
   - `status`: prefer `reviewed` pages; say when you rely on a `draft`.
     A draft says why it is one at the bottom of the page.
   - `cohorts`: a page applies only to the years it lists.
     `generated/drift.md` lists differences between cohort schemas.
   - `evidence`: `schema` (the column exists), `code` (how the lab's code
     computes it, at a pinned commit), `legacy` (older lab code),
     `paper` (a DOI), `query` (a verified query page).
   - `limitations`: carry the relevant ones into your answer.
4. Cite pages by folder and id with their status, for example
   "(knowledge base: features/steps_day, reviewed)".

## Layout

- `sources/`: one page per data source (a device, app, or survey).
- `tables/`: one page per Oracle table or view worth explaining,
  named `SCHEMA.TABLE.md`.
- `features/`: derived measures, how they're computed, and the
  `constructs` glossary.
- `qc/`: cleaning and QC rules.
- `cohorts/`, `queries/`, `decisions/`: cohort years, verified queries,
  and lab decisions (empty at first).
- `papers/`: lab papers, with their DOI.
- `generated/`: the Oracle catalog (metadata only, never values),
  refreshed by DataLab. Don't edit it.
- `skills/<name>/SKILL.md`: the lab's own procedures.

## How to propose an edit

Edit or add a page in your copy; DataLab shows the person the diff, and
they decide. Follow the page format (front matter: `id`, `kind`, `status`,
`summary`, `evidence`, `limitations`, `related`, `cohorts`). New pages are
drafts. Never change a page's `status`, and never write `reviewed_by` or
`reviewed_on`: DataLab fills them in from the person who saves. Don't edit
`index.md` or `generated/`.

## Where this came from

The first pages were converted from the prototype "Knowledge Spine" and
reviewed by a person before the first commit. Pages keep a "Where this
came from" section naming the Spine entry behind them.
"""

README_MD = """\
# ihs-knowledge

The IHS lab knowledge base: Markdown pages with YAML front matter, read by
DataLab's agents and edited through DataLab. Start with `AGENTS.md` (the
rules) and `index.md` (the pages). DataLab's knowledge-base check runs on
every push (`.github/workflows/kb-check.yml`).
"""


def drift_md(schema: Schema, generated_at: str) -> str:
    index = schema.index
    cohorts = sorted(index)
    names = sorted({t for s in cohorts for t in index[s]})
    lines = [
        "# Cohort drift",
        "",
        f"Differences between the cohort schemas in `generated/schema` (catalog export "
        f"{generated_at}). Metadata only. Renames aren't detected: a renamed table or "
        "column shows as one removed and one added.",
        "",
        "## Tables by cohort",
        "",
        f"{len(names)} table and view names across {', '.join(cohorts)}.",
        "",
    ]
    for name in names:
        present = [s for s in cohorts if name in index[s]]
        if present != cohorts:
            lines.append(f"- `{name}`: {year_ranges(present, cohorts)}")
    lines += ["", "## Columns added or removed between consecutive cohorts", ""]
    for name in names:
        present = [s for s in cohorts if name in index[s]]
        changes = []
        for old, new in itertools.pairwise(present):
            added = sorted(set(index[new][name]) - set(index[old][name]))
            removed = sorted(set(index[old][name]) - set(index[new][name]))
            if added or removed:
                what = []
                if added:
                    what.append("added " + ", ".join(f"`{c}`" for c in added))
                if removed:
                    what.append("removed " + ", ".join(f"`{c}`" for c in removed))
                changes.append(f"  - {old} to {new}: {'; '.join(what)}")
        if changes:
            lines += [f"- `{name}`", *changes]
    return "\n".join(lines) + "\n"


def year_ranges(present: list[str], cohorts: list[str]) -> str:
    """The cohorts a table is in, as runs of consecutive cohort schemas
    ("2021 to 2023; 2025"). Never a comma-separated list of numbers, which
    the check's scan would take for pasted values."""
    runs: list[list[str]] = []
    for schema in present:
        if runs and cohorts.index(schema) == cohorts.index(runs[-1][-1]) + 1:
            runs[-1].append(schema)
        else:
            runs.append([schema])
    return "; ".join(r[0][4:] if len(r) == 1 else f"{r[0][4:]} to {r[-1][4:]}" for r in runs)


def generated_readme(export: Path, generated_at: str, tables: int) -> str:
    return (
        "# Generated\n\n"
        "Refreshed by DataLab from the Oracle catalog; don't edit by hand.\n\n"
        f"- `schema/`: {tables} tables and views, one YAML file each, from the catalog "
        f"metadata export of {generated_at} (`{export.name}`): names, types, nullability, "
        "comments, and primary keys. Metadata only, never values.\n"
        "- `drift.md`: tables and columns added or removed between cohort schemas.\n"
    )


MANAGED = ("AGENTS.md", "README.md", "index.md", "generated", *kb.FOLDERS)


def prepare(out: Path) -> None:
    """Empty the output folder of what an earlier run wrote. Anything else
    in it stops the run, so the conversion never deletes someone's files."""
    if out.exists():
        others = sorted(p.name for p in out.iterdir() if p.name not in MANAGED)
        if others:
            raise SystemExit(f"{out} has files the conversion didn't write: {', '.join(others)}")
        for name in MANAGED:
            path = out / name
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    out.mkdir(parents=True, exist_ok=True)


def export_time(export: Path) -> str:
    match = re.search(r"(\d{8})T(\d{6})Z", export.name)
    if not match:
        return export.name
    day = datetime.datetime.strptime(match.group(1), "%Y%m%d").date()
    return f"{day.isoformat()} {match.group(2)[:2]}:{match.group(2)[2:4]} UTC"


@dataclass
class Result:
    converter: Converter
    report: kb.Report
    catalog_tables: int
    export: Path
    spine_dir: Path
    # With the working copy read: how the pages would differ if built from the
    # Spine's last commit instead. (path, status now, status from the commit;
    # None where a page wouldn't exist.)
    from_commit: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    # With a commit read: working-copy entries that differ from it, left out.
    excluded: list[tuple[str, list[str]]] = field(default_factory=list)
    reviewer: str = REVIEWER
    # With the reviewer's decisions: each page's status without them (None
    # where the page wouldn't exist), and the Spine as read, before them.
    before: dict[str, str | None] = field(default_factory=dict)
    original: Spine | None = None


def build(
    spine: Spine,
    schema: Schema,
    decisions: Decisions | None = None,
    pipelines: Path | None = None,
) -> Converter:
    applied, pipes = None, None
    if decisions:
        pipes = open_pipelines(pipelines, decisions, spine)
        spine, applied = apply_entries(spine, decisions)
    converter = Converter(spine, schema, decisions, pipes, applied)
    converter.locate_variables()
    converter.tables()
    converter.sources()
    converter.features()
    converter.glossary()
    converter.qc()
    converter.qc_cohorts()
    converter.papers()
    converter.paper_cohorts()
    converter.apply_pages()
    converter.removed_names()
    converter.mark_touched()
    for page in converter.pages.values():
        converter.decide_status(page)
    return converter


def compare(now: Converter, then: Converter) -> list[tuple[str, str | None, str | None]]:
    texts_now = {p.path: (p.status, now.render(p)) for p in now.pages.values()}
    texts_then = {p.path: (p.status, then.render(p)) for p in then.pages.values()}
    out = []
    for path in sorted(set(texts_now) | set(texts_then)):
        a, b = texts_now.get(path), texts_then.get(path)
        if a != b:
            out.append((path, a[0] if a else None, b[0] if b else None))
    return out


def convert(
    spine_dir: Path,
    export: Path,
    out: Path,
    ref: str | None = None,
    decisions: Decisions | None = None,
    pipelines: Path | None = None,
) -> Result:
    spine = read_spine(spine_dir, ref)
    catalog = Catalog.from_metadata_export(export)
    schema = Schema(catalog)
    converter = build(spine, schema, decisions, pipelines)
    before: dict[str, str | None] = {}
    if decisions:
        baseline = build(spine, schema)
        before = {p.ref: p.status for p in baseline.pages.values()}
        for key in converter.applied.excluded:  # where a left-out entry would have been
            question = converter.applied.excluded[key][2]
            if key in baseline.home:
                converter.touched[question].add(baseline.home[key])
    from_commit = []
    changed = any(e.changed for es in spine.entities.values() for e in es.values())
    if ref is None and spine.commit and changed:
        then = build(read_spine(spine_dir, spine.commit), schema, decisions, pipelines)
        from_commit = compare(converter, then)
    excluded = left_out(spine, read_spine(spine_dir)) if ref else []
    prepare(out)
    catalog.save(out / "generated" / "schema")
    when = export_time(export)
    files: dict[str, str] = {
        "AGENTS.md": AGENTS_MD,
        "README.md": README_MD,
        "generated/drift.md": drift_md(schema, when),
        "generated/README.md": generated_readme(export, when, len(catalog)),
    }
    for page in converter.pages.values():
        files[page.path] = converter.render(page)
    for path, text in files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    disk, others = kb.read_folder(out)
    report = kb.check(disk, others=others)
    (out / "index.md").write_text(report.index, encoding="utf-8", newline="\n")
    disk, others = kb.read_folder(out)
    report = kb.check(disk, others=others)
    return Result(
        converter, report, len(catalog), export, spine_dir, from_commit, excluded,
        before=before, original=spine,
    )  # fmt: skip


def left_out(read: Spine, working: Spine) -> list[tuple[str, list[str]]]:
    """Working-copy entries that differ from the version read, by field."""
    out = []
    for key in LIST_KEYS:
        for entity_id in sorted(set(read.entities[key]) | set(working.entities[key])):
            a, b = read.entities[key].get(entity_id), working.entities[key].get(entity_id)
            if a and b and a.data != b.data:
                out.append((a.label, diff_paths(a.data, b.data)))
            elif a is None and b is not None:
                out.append((b.label, ["the whole entry (only in the working copy)"]))
            elif b is None and a is not None:
                out.append((a.label, ["the whole entry (removed in the working copy)"]))
    return out


def stamp(folder: Path, reviewer: str, day: datetime.date) -> tuple[list[str], kb.Report]:
    """Name `reviewer` on every reviewed page, as DataLab's Save & share does
    (`stamp_review`), rewrite index.md, and check the result. Run by the
    reviewer, after their review, just before the first commit."""
    if not _LOGIN.fullmatch(reviewer):
        raise SystemExit(f"{reviewer!r} isn't a GitHub login")
    files, others = kb.read_folder(folder)
    stamped = []
    for path, content in sorted(files.items()):
        text = kb.as_text(content)
        if kb.place(path) != "page" or text is None:
            continue
        fields, _ = kb.front_matter(text)
        if not fields or fields.get("status") != "reviewed":
            continue
        new = kb.stamp_review(text, reviewer, day)
        if new != text:
            (folder / path).write_text(new, encoding="utf-8", newline="\n")
        stamped.append(path)
    files, others = kb.read_folder(folder)
    report = kb.check(files, others=others)
    (folder / "index.md").write_text(report.index, encoding="utf-8", newline="\n")
    files, others = kb.read_folder(folder)
    return stamped, kb.check(files, others=others)


# The review report ---------------------------------------------------------------


def review_md(result: Result) -> str:
    conv = result.converter
    spine = conv.spine
    original = result.original or spine  # before the reviewer left any entry out
    left = conv.applied.excluded
    pages = sorted(conv.pages.values(), key=lambda p: p.path)
    out = [
        "# Review: the Knowledge Spine converted to the knowledge base",
        "",
        "Generated by `scripts/convert-spine/convert_spine.py` in the DataLab repo. "
        "Nothing here has been committed to `ihs-knowledge`. Read this, then the pages, "
        "then install (at the end).",
        "",
        *decided(result),
        *answered(result),
        "## Inputs",
        "",
        f"- Spine: `{result.spine_dir}` (repo `{spine.repo}`, commit `{spine.commit}`), "
        f"registry files: {', '.join(spine.files)}. "
        + (
            f"Read as committed at `{spine.ref}`."
            if spine.ref
            else "The registry's working copy was read, including changes made after that commit."
        ),
        f"- Oracle metadata export: `{result.export}` ({result.catalog_tables} tables and views).",
        "",
        "## Counts",
        "",
        "Entries in:",
        "",
    ]
    total = 0
    for key in LIST_KEYS:
        entities = original.entities[key].values()
        total += len(entities)
        statuses = defaultdict(int)
        for e in entities:
            statuses[e.status] += 1
        detail = ", ".join(f"{n} {s}" for s, n in sorted(statuses.items()))
        out.append(f"- {TYPE_NAMES[key]}: {len(entities)} ({detail or 'none'})")
    out += [f"- Total: {total}", "", "Pages out:", ""]
    by_folder: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for page in pages:
        by_folder[page.folder][page.status] += 1
    for folder in kb.FOLDERS:
        if folder in by_folder:
            counts = by_folder[folder]
            detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
            out.append(f"- `{folder}/`: {sum(counts.values())} ({detail})")
    out += [
        f"- Total: {len(pages)} pages, plus `generated/schema/` ({result.catalog_tables} files), "
        "`generated/drift.md`, `generated/README.md`, `AGENTS.md`, `README.md`, `index.md`.",
        "",
        "## Where each entry went",
        "",
        "| Spine type | Page |",
        "|---|---|",
    ]
    for key in LIST_KEYS:
        for entity in original.entities[key].values():
            ref = conv.home.get((key, entity.id))
            where = f"`{ref}`" if ref else "not mapped"
            if (key, entity.id) in left:
                where = f"left out by the reviewer (question {left[(key, entity.id)][2]})"
            out.append(f"| {TYPE_NAMES[key]} `{entity.id}` | {where} |")
    out += ["", "## Status changes", ""]
    out += [
        "The rule: a Spine `validated` entry becomes `reviewed` only if every entry on its "
        "page is `validated` and unchanged since the Spine's last commit, nothing in its own "
        "text says it's unsettled, it has typed evidence of the kind the page needs "
        "(`schema` for sources and tables; `code`, `legacy`, or `paper` for any page), every "
        "Oracle name it gives is in the catalog, and it has cohorts. `candidate` becomes "
        "`draft`. Pages the conversion assembled (the constructs glossary, papers) are "
        "drafts. No page has `reviewed_by` or `reviewed_on` yet (see Decided). How far the "
        "check verifies that evidence, and where cohorts were inferred, are in the next two "
        "sections.",
        *(
            [
                "",
                'The reviewer\'s decisions (see "Reviewer questions: answered") add evidence, '
                "cohorts, text fixes and holds before this rule is applied; they never mark a "
                "page reviewed themselves.",
            ]
            if conv.decisions
            else []
        ),
        "",
        "Per entry (each Spine entry counted once, by the page it went to):",
        "",
    ]
    moves: dict[str, int] = defaultdict(int)
    for key in LIST_KEYS:
        for entity in original.entities[key].values():
            ref = conv.home.get((key, entity.id))
            status = conv.pages[ref].status if ref else "not mapped"
            if (key, entity.id) in left:
                status = "left out by the reviewer"
            moves[f"{entity.status} → {status}"] += 1
    for move in sorted(moves):
        out.append(f"- **{move}**: {moves[move]}")
    out.append(f"- Total: {sum(moves.values())}")
    shared = [p for p in pages if not p.assembled and len(p.entities) > 1]
    out += [
        "",
        "Per page: a page that holds several entries takes one status for all of them, so "
        "one unsettled entry makes the whole page a draft. These pages hold more than one:",
        "",
    ]
    for page in shared:
        statuses = ", ".join(f"`{e.id}` {e.status}" for e in page.entities)
        out.append(f"- `{page.ref}` ({page.status}): {statuses}")
    out += [
        "",
        "The constructs glossary holds all 40 constructs, and the paper pages list the "
        "entries that cite them; both are drafts, and the paper pages don't change any "
        "entry's status above.",
        "",
        "### Reviewed pages",
        "",
    ]
    for page in pages:
        if page.status == "reviewed":
            kinds = ", ".join(sorted(page.evidence.kinds()))
            out.append(f"- `{page.ref}` (evidence: {kinds}; cohorts {sorted(page.cohorts)})")
    out += evidence_section(pages, conv)
    out += cohort_section(pages, conv)
    out += ["", "### Draft pages, and why", ""]
    for page in pages:
        if page.status == "draft":
            spine_statuses = ", ".join(sorted({e.status for e in page.entities}))
            out.append(f"- `{page.ref}` (Spine: {spine_statuses})")
            out += [f"  - {h}" for h in page.holds]
    out += ["", "## Entries that couldn't be mapped", ""]
    out += [f"- {entry}: {why}" for entry, why in conv.unmapped] or ["- None."]
    out += [
        "",
        "## Left out for privacy",
        "",
        "Where, never what. Counts and value distributions from live queries, hashes of "
        "query results, and observations from profiling the data were left out, following "
        "the Spine handoff's rule (no row counts, distinct counts, date ranges, or "
        "profiling) and the knowledge base's (no small counts). The metadata export's "
        "table and column comments were scanned too.",
        "",
    ]
    privacy = list(dict.fromkeys(conv.clean.privacy))
    out += ["Left out altogether:", ""]
    out += [f"- {r.entry}, `{r.where}`: {r.why}" for r in privacy if not r.cut] or ["- None."]
    out += ["", "Cut from a sentence; the rest of the text is kept:", ""]
    out += [f"- {r.entry}, `{r.where}`: {r.why}" for r in privacy if r.cut] or ["- None."]
    out += [
        "",
        "Not carried over:",
        "",
        "- The metadata export's `privileges.csv`, `synonyms.csv` and `referenced_objects.csv`, "
        "and the objects' creation and DDL times, aren't catalog metadata fields "
        "`generated/schema` may hold, so they weren't carried over.",
        "",
        "## Prototype bookkeeping left out",
        "",
        "Query, export, runner and proposal ids, SQL hashes, and notes about the Spine's own "
        "validation, which mean nothing outside the prototype:",
        "",
    ]
    counts: dict[str, int] = defaultdict(int)
    for r in dict.fromkeys(conv.clean.machinery):
        counts[r.entry] += 1
    out += [f"- {entry}: {n} field(s)" for entry, n in sorted(counts.items())] or ["- None."]
    out += ["", "## Reworded", ""]
    out += [f"- {r.entry}, `{r.where}`: {r.why}" for r in dict.fromkeys(conv.clean.reworded)]
    out += [
        f"- `{path}`: `{name}` isn't in the catalog, so it's written unqualified"
        for path, name in dict.fromkeys(conv.renamed)
    ]
    if not conv.clean.reworded and not conv.renamed:
        out.append("- None.")
    out += [
        "",
        "## Mapping decisions",
        "",
        "- One page per Oracle table or view in `tables/`, in the newest cohort schema where "
        "the Spine's columns exist; the page's `cohorts` are every year where they do.",
        "- Each feature page folds in its recipe and a section on its construct; all 40 "
        "constructs are in the `features/constructs` glossary.",
        "- Evidence is typed from the Spine's fields: Oracle names that are in the catalog "
        "become `schema`; `implemented_in` and `r/ihsDataR/...` paths become `code` pinned to "
        "the Spine repo's last commit; the 2024 legacy scripts become `legacy` paths under "
        "`reference/2024/` (the `ihs-pipelines` layout); DOIs become `paper`. Other citations "
        "(unpublished manuscripts, prototype contracts) are kept in each page's \"Where this "
        "came from\" section but aren't typed evidence.",
        "- Unqualified Oracle names are looked up in the entry's source's cohorts, or in "
        f"{' and '.join(DEFAULT_SCHEMAS)} when the entry doesn't say.",
        "- `papers/` pages list only what the Spine takes from each paper; nothing was read "
        "from the papers themselves.",
        "- `cohorts/`, `queries/`, and `decisions/` are empty: the Spine has no such entries.",
        "",
        "## Check",
        "",
        "`cd backend && uv run datalab kb-check <staging folder>`:",
        "",
        f"- {len(result.report.errors)} error(s), {len(result.report.data)} possible "
        f"participant-data hit(s), {len(result.report.warnings)} warning(s).",
    ]
    rules: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for f in result.report.findings:
        rules[(f.severity, f.rule, f.message)].append(f.path)
    for (severity, rule, message), paths in sorted(rules.items()):
        shown = ", ".join(f"`{p}`" for p in sorted(paths)[:8])
        more = f" and {len(paths) - 8} more" if len(paths) > 8 else ""
        out.append(f"- {severity} `{rule}` ({len(paths)}): {message} {shown}{more}")
    out += [
        "",
        "Reviewed pages are warned about because they don't name a reviewer yet; the "
        "stamp at install (step 4) clears those warnings. Empty `cohorts` and `evidence` "
        "warnings are drafts whose years or evidence the Spine doesn't give.",
        "",
        *(
            still_open(conv.decisions)
            if conv.decisions
            else [
                "## Questions for the reviewer",
                "",
                *[f"{n}. {q}" for n, q in enumerate(QUESTIONS, start=1)],
            ]
        ),
        "",
        "## Installing it (a maintainer, with their own credentials)",
        "",
        "1. Clone the repo: `git clone https://github.com/SripadaLab-UM/ihs-knowledge.git`.",
        "2. Copy the staging folder's contents into the clone: "
        "`cp -R <staging folder>/. ihs-knowledge/` (this report is outside the staging "
        "folder, so it isn't copied).",
        "3. Add the check workflow: copy `kb/github-workflow.yml` from the DataLab repo to "
        "`ihs-knowledge/.github/workflows/kb-check.yml`, and set `DATALAB_REF` in it to the "
        "DataLab release the lab runs. DataLab's GitHub App can't push workflows, so this "
        "has to be a person's own push.",
        f"4. Once {result.reviewer} has finished reviewing, stamp the reviewed pages in the "
        "clone: `cd <DataLab>/backend && uv run python "
        f"../scripts/convert-spine/convert_spine.py --stamp-reviewer {result.reviewer} "
        "--out <path to ihs-knowledge>`. It sets `reviewed_by` and `reviewed_on` (today) on "
        "every `status: reviewed` page and runs the check.",
        "5. Run the check in the clone: `cd <DataLab>/backend && uv run datalab kb-check "
        "<path to ihs-knowledge>`. It should report no errors, no possible participant data, "
        "and no `reviewed_by` warnings.",
        '6. Make the first commit and push: `git add -A && git commit -m "Knowledge base '
        'converted from the prototype Spine" && git push origin main`.',
        "",
    ]
    return "\n".join(out)


def decided(result: Result) -> list[str]:
    """What the user decided before anything goes into the real repo."""
    conv = result.converter
    spine = conv.spine
    reviewed = [p for p in conv.pages.values() if p.status == "reviewed"]
    commit = (spine.commit or "")[:7]
    out = [
        f"## Decided: converted from {commit}; the reviewer, {result.reviewer}, is stamped "
        "at install after review",
        "",
    ]
    if spine.ref:
        out.append(
            f"- **Source.** The Spine's registry as committed at `{spine.commit}`, so this "
            "output can be rebuilt exactly from that commit."
        )
        if result.excluded:
            out.append(
                "  The Spine's working copy has changes that aren't committed; they are left "
                "out. By field (never values):"
            )
            out += [f"  - {label}: " + "; ".join(diff) for label, diff in result.excluded]
    else:
        out.append(
            "- **Source.** The Spine's working copy (`--working-copy`), not a commit: this "
            "output can't be rebuilt from a commit."
        )
        changed = [e for es in spine.entities.values() for e in es.values() if e.changed]
        out += [f"  - {e.label}: " + "; ".join(e.diff) for e in changed]
    out += [
        f"- **Reviewer.** {result.reviewer} reviews this conversion and is named on the "
        f"{len(reviewed)} reviewed pages. So that nobody is named before reviewing, the "
        "staging output has no `reviewed_by` or `reviewed_on`: step 4 of the install stamps "
        f"`reviewed_by: {result.reviewer}` and `reviewed_on:` the day it's run on every "
        "`status: reviewed` page, just before the first commit. It uses DataLab's own "
        "stamping (`stamp_review`, as Save & share does), so the fields are exactly what "
        "DataLab would write; the check has no history to compare in a first commit, and "
        "afterwards DataLab keeps them until a person saves the page again. Drafts are left "
        "unstamped.",
        "",
    ]
    return out


def status_changes(result: Result) -> list[tuple[str, str | None, str | None]]:
    """Pages whose status the reviewer's decisions changed (ref, before, after;
    None where the page doesn't exist)."""
    pages = result.converter.pages
    out = []
    for ref in sorted(set(pages) | set(result.before)):
        before = result.before.get(ref)
        after = pages[ref].status if ref in pages else None
        if before != after:
            out.append((ref, before, after))
    return out


def answered(result: Result) -> list[str]:
    """Each question, its answer and evidence, and what it changed."""
    conv = result.converter
    decisions = conv.decisions
    if not decisions:
        return []
    pages, before = conv.pages, result.before
    changes = status_changes(result)
    up = [ref for ref, a, b in changes if a == "draft" and b == "reviewed"]
    down = [ref for ref, a, b in changes if a == "reviewed" and b == "draft"]
    new = [(ref, b) for ref, a, b in changes if a is None]
    out = [
        "## Reviewer questions: answered",
        "",
        f"{decisions.reviewer}'s decisions of {decisions.decided_on}, in "
        f"`scripts/convert-spine/{decisions.path.name}`. The conversion applies them on top of "
        "the Spine's commit; each one there cites its source (a file and line, a commit, a URL "
        "or a DOI), and the conversion stops if one no longer fits the sources. They add "
        "evidence, cohorts, text fixes and holds; statuses still follow the rule under "
        '"Status changes", so no decision marks a page reviewed by itself.',
        "",
        f"All together: {len(up)} pages went from draft to reviewed, {len(down)} from reviewed "
        f"to draft, and {len(new)} are new.",
        "",
        "- Draft to reviewed: " + (", ".join(f"`{r}`" for r in up) or "none") + ".",
        "- Reviewed to draft: " + (", ".join(f"`{r}`" for r in down) or "none") + ".",
        "- New: " + (", ".join(f"`{r}` ({s})" for r, s in new) or "none") + ".",
        "",
    ]
    answers = {a["question"]: a for a in decisions.answers}
    for n, question in enumerate(QUESTIONS, start=1):
        answer = answers.get(n)
        out.append(f"{n}. *{question}*")
        if answer:
            out.append(f"   - **Answer.** {one_line(answer['answer'])}")
            evidence = one_line(answer.get("evidence")) or "see the decisions"
            out.append(f"   - **Evidence.** {evidence}")
        else:
            out.append("   - **Not answered yet.**")
        touched = sorted(r for r in conv.touched.get(n, set()) if r in pages)
        moved = [
            f"`{r}` ({before[r]} to {pages[r].status})"
            if before.get(r)
            else f"`{r}` (new, {pages[r].status})"
            for r in touched
            if before.get(r) != pages[r].status
        ]
        same = [r for r in touched if before.get(r) == pages[r].status]
        if moved:
            out.append("   - **Status changes on the pages it touched.** " + ", ".join(moved) + ".")
        if same:
            shown = ", ".join(f"`{r}`" for r in same[:10])
            more = f" and {len(same) - 10} more" if len(same) > 10 else ""
            out.append(f"   - **Edited, status unchanged.** {shown}{more}.")
        if not moved and not same:
            out.append("   - **Pages changed.** None.")
    out += ["", "### Papers checked", ""]
    for paper in sorted(decisions.papers.values(), key=lambda p: (p.year, p.doi)):
        names = f"; the Spine calls it {', '.join(paper.names)}" if paper.names else ""
        out.append(
            f"- `{paper.doi}`: {paper.title} ({paper.venue}, {paper.year}){names}. Cohorts: "
            f"{years_text(paper.cohorts)}. Checked at " + ", ".join(paper.verified) + "."
        )
    if decisions.unpublished:
        out += ["", "Named in the Spine, with no DOI to be found:", ""]
        for item in decisions.unpublished:
            out.append(
                f"- {item.get('name')}: {one_line(item.get('what'))}. "
                f"Searched: {one_line(item.get('searched'))}."
            )
    out.append("")
    return out


def still_open(decisions: Decisions) -> list[str]:
    return [
        "## Still open",
        "",
        'The questions are answered above ("Reviewer questions: answered"). What still needs '
        "a person:",
        "",
        *[f"- {item}" for item in decisions.still_open],
    ]


def only_legacy(page: Page) -> bool:
    return page.evidence.kinds() - {"schema"} == {"legacy"}


def evidence_section(pages: list[Page], conv: Converter | None = None) -> list[str]:
    decisions = conv.decisions if conv else None
    reviewed = [p for p in pages if p.status == "reviewed" and p.folder in ("features", "qc")]
    bare = [p for p in reviewed if p.evidence.kinds() == {"legacy"}]
    with_schema = [p for p in reviewed if only_legacy(p) and p not in bare]
    out = [
        "",
        "### How far the check verifies evidence",
        "",
        "The check resolves `schema` evidence against `generated/schema`. For `code`, "
        "`legacy`, and `paper` it checks only the format (repo@commit path, a path, a DOI).",
        "",
    ]
    pipes = conv.resolve.pipelines if conv else None
    if decisions:
        out += [
            "What the conversion checked beyond that, with the reviewer's decisions:",
            "",
            "- `code`: every path exists at its pinned commit. "
            + (
                f"Paths in `ihsDataR` are pinned to `{pipes.repo}@{pipes.commit[:7]}`, whose "
                f"`{pipes.prefix}` is the same tree as `{pipes.from_prefix}` at the Spine's "
                "commit (question 4); other prototype code stays pinned to the Spine's repo."
                if pipes
                else "They are pinned to the Spine's repo."
            ),
            "- `legacy`: the paths are where docs/WORKFLOWS.md says the 2024 scripts go "
            "(`reference/2024/` in `ihs-pipelines`), which doesn't exist yet (question 5). "
            + " ".join(
                f"`{name}`: "
                + ("seen and the cited parts checked" if info.get("seen") else "not seen")
                + f" ({one_line(info.get('where'))})."
                for name, info in sorted(decisions.legacy.items())
            )
            + " A script nobody has seen doesn't count as a page's evidence of how something "
            "is computed.",
            f"- `paper`: all {len(decisions.papers)} DOIs were resolved at the DOI registry "
            '(question 2); see the list under "Reviewer questions: answered".',
            "",
            "Reviewed pages whose only evidence is a legacy script (a seen one):",
            "",
        ]
    else:
        out += [
            "The conversion confirmed that each `code` path exists at the pinned commit of the "
            "prototype repo. It didn't check the `legacy` paths: `reference/2024/` in "
            "`ihs-pipelines` is where docs/WORKFLOWS.md says the 2024 scripts go, not somewhere "
            "they were seen. DOIs weren't looked up.",
            "",
            "Reviewed pages whose only evidence is an assumed `reference/2024/` path:",
            "",
        ]
    out += [f"- `{p.ref}`" for p in bare] or ["- None."]
    out += [
        "",
        "Reviewed feature and QC pages whose only evidence besides `schema` (which shows the "
        "tables exist, not how they're used) is "
        + ("a legacy script:" if decisions else "an assumed `reference/2024/` path:"),
        "",
    ]
    out += [f"- `{p.ref}`" for p in with_schema] or ["- None."]
    return out


BASIS = {
    "spine": "the Spine's `available_cohorts` or raw-schema `cohort`",
    "schema": "the cohort schemas where the tables and columns it names are in generated/schema",
    "assumed": "tables it names without a schema, looked up in IHS_2024 and IHS_2025 because "
    "nothing says which cohorts",
    "recipe": "its recipe's sources' cohorts in the Spine (because it cites code or legacy)",
    "inherited": "the cohorts of the features it's applied to (because it cites code or legacy)",
    "paper": "the cohorts the paper it cites analysed, from the paper's methods (question 3)",
    "reviewer": "confirmed by the reviewer (question 1); the source is on each line",
}


def thin(page: Page) -> list[str]:
    """Why a page's inferred cohorts are weakly supported."""
    basis = page.cohort_basis
    if "reviewer" in basis:
        return []
    firm = set().union(*(v for k, v in basis.items() if k != "assumed"))
    out = []
    if basis.get("assumed", set()) - firm:
        years = sorted(basis["assumed"] - firm)
        out.append(f"{years} only from the default IHS_2024/IHS_2025 lookup")
    legacy = [r for k, r in page.evidence.typed if k == "legacy" and "/2024/" in r]
    if legacy and 2024 not in page.cohorts:
        out.append(f"cites a 2024 legacy script but lists only {sorted(page.cohorts)}")
    if len(page.cohorts) == 1 and set(basis) != {"paper"}:
        out.append("a single cohort")
    if set(basis) == {"inherited"}:
        out.append("only from the features it's applied to")
    if set(basis) == {"recipe"}:
        out.append("only from its recipe's sources; it names no tables")
    return out


def cohort_section(pages: list[Page], conv: Converter | None = None) -> list[str]:
    decisions = conv.decisions if conv else None
    out = [
        "",
        "### Where cohorts came from",
        "",
        "The Spine gives cohorts only for data sources (`available_cohorts`) and raw schemas "
        "(`cohort`). Every other page's `cohorts` were inferred by the conversion, from one or "
        "more of:",
        "",
    ]
    out += [f"- **{k}**: {v}" for k, v in BASIS.items() if k != "spine"]
    out += [
        "",
        (
            "A page whose only evidence of method is a paper lists the cohorts that paper "
            "analysed. The reviewer confirmed the cohorts of the reviewed pages that have code "
            "or a legacy script (question 1). The reviewed feature and QC pages, with where "
            "their years came from:"
            if decisions
            else "A paper's method gets no cohorts this way, so paper-only pages have none. "
            "Confirm the cohorts on every reviewed page (question 1). The reviewed feature and "
            "QC pages, with where their years came from:"
        ),
        "",
    ]
    for page in pages:
        if page.status != "reviewed" or page.folder not in ("features", "qc"):
            continue
        parts = "; ".join(f"{k} {sorted(v)}" for k, v in sorted(page.cohort_basis.items()))
        weak = thin(page)
        note = f" **Thin:** {'; '.join(weak)}." if weak else ""
        if page.cohort_source:
            note += f" Source: {page.cohort_source}."
        out.append(f"- `{page.ref}` {sorted(page.cohorts)}: {parts}.{note}")
    out += [
        "",
        "Reviewed table pages list the years where the Spine's columns are in the catalog; "
        "for raw variables without a schema, that lookup used their source's "
        "`available_cohorts`. Reviewed source pages use the Spine's `available_cohorts` as "
        "given.",
    ]
    return out


QUESTIONS = (
    'Cohorts are inferred on every feature, QC, and table page (see "Where cohorts came '
    'from"). Please confirm the cohorts on each reviewed page, especially the ones marked '
    "thin.",
    "Many validated entries cite only unpublished manuscripts or analyses by name "
    "(MoodDriver, Social Smartphone Manuscript, Sleep & Step ETT), which can't be typed "
    "evidence, so their pages are drafts. Can you give a DOI, or a path to the code, for each?",
    "Pages whose only evidence is a paper have no cohorts: the Spine doesn't record which "
    "cohorts each paper used. Which years should they list?",
    "`code` evidence is pinned to the prototype repo (`um-gpt-local-proxy`) at the Spine's "
    "last commit. Should it be re-pinned to `ihs-pipelines` once `ihsDataR` has moved there?",
    "`legacy` evidence assumes the 2024 scripts will be in `ihs-pipelines` at "
    "`reference/2024/` (docs/WORKFLOWS.md). Is that where they'll be, under these names?",
    "The Spine's uncommitted changes are left out (see Decided). Should they be proposed "
    "through DataLab once the knowledge base is live?",
    "The Garmin and participant sources say the catalog export has no views; the current "
    "export has them. Should those limitations be dropped?",
    "`resting_heart_rate_day`: its description and its recipe disagree about the Fitbit "
    "and Garmin branches, and the recipe links no code although the prototype has "
    "`r/ihsDataR/R/feature_resting_heart_rate_day.R`. Which is right?",
    "Two raw variables describe the same column, IHS_2025.VGARMINACTIVITYSUMMARY."
    "STARTTIMEINSECONDS (one candidate, one validated). Keep one?",
    "The Spine's METRICS_BACKLOG.md still lists `resting_heart_rate_day` and "
    "`active_minutes_day` as candidates; the registry (after later port commits) says "
    "validated, which the conversion followed. Agreed?",
    "Qualitative notes from looking at data are kept where they have no numbers (for "
    "example, that zero-step Oura rows exist); counts, distributions, and observed date "
    "ranges were removed. Is that the right line?",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--spine", type=Path, help="the Spine's folder")
    parser.add_argument("--export", type=Path, help="the metadata export")
    parser.add_argument("--out", type=Path, required=True, help="the knowledge base's folder")
    parser.add_argument("--review", type=Path, help="where to write REVIEW.md")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--ref",
        default=SPINE_COMMIT,
        help=f"read the registry as committed at this git ref (default {SPINE_COMMIT[:7]})",
    )
    source.add_argument(
        "--working-copy", action="store_true", help="read the registry's working copy instead"
    )
    parser.add_argument(
        "--reviewer", default=REVIEWER, help="who the report says will review and be stamped"
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=DECISIONS,
        help=f"the reviewer's decisions to apply (default {DECISIONS.name} next to this script)",
    )
    parser.add_argument(
        "--no-decisions", action="store_true", help="convert without the reviewer's decisions"
    )
    parser.add_argument(
        "--pipelines",
        type=Path,
        help="an ihs-pipelines checkout with the commit the decisions re-pin code to",
    )
    parser.add_argument(
        "--stamp-reviewer",
        metavar="LOGIN",
        help="install step, after review: name LOGIN on every reviewed page in --out",
    )
    parser.add_argument(
        "--on", type=datetime.date.fromisoformat, help="the stamp's date (default today)"
    )
    args = parser.parse_args(argv)
    if args.stamp_reviewer:
        day = args.on or datetime.date.today()
        stamped, report = stamp(args.out, args.stamp_reviewer, day)
        print(f"Stamped {len(stamped)} reviewed pages: reviewed_by {args.stamp_reviewer}, {day}.")
        print(
            f"Check: {len(report.errors)} error(s), {len(report.data)} possible participant-data "
            f"hit(s), {len(report.warnings)} warning(s)."
        )
        return 1 if report.blocking() else 0
    if not (args.spine and args.export and args.review):
        parser.error("--spine, --export and --review are needed to convert")
    review = args.review.resolve()
    if review.is_relative_to(args.out.resolve()):
        raise SystemExit("--review must be outside --out: the check allows no extra files there")
    ref = None if args.working_copy else args.ref
    decisions = None if args.no_decisions else load_decisions(args.decisions)
    result = convert(args.spine, args.export, args.out, ref, decisions, args.pipelines)
    result.reviewer = args.reviewer
    review.write_text(review_md(result), encoding="utf-8", newline="\n")
    report = result.report
    pages = result.converter.pages.values()
    reviewed = sum(p.status == "reviewed" for p in pages)
    print(
        f"Wrote {len(pages)} pages ({reviewed} reviewed) and {result.catalog_tables} schema "
        f"files to {args.out}, and {review}."
    )
    if decisions:
        changes = status_changes(result)
        print(
            f"Decisions from {decisions.path.name}: {len(changes)} page(s) changed status "
            '(see REVIEW.md, "Reviewer questions: answered").'
        )
    print(
        f"Check: {len(report.errors)} error(s), {len(report.data)} possible participant-data "
        f"hit(s), {len(report.warnings)} warning(s)."
    )
    return 1 if report.blocking() else 0


if __name__ == "__main__":
    sys.exit(main())
