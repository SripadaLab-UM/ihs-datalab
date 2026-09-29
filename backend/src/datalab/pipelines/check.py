"""The check a pipeline change gets before Save & share: what can't be shared,
what may be participant data, and code that would run on colleagues' computers.

- **Errors** stop a save: a path outside `ihsDataR/` and `workflows/`, or in
  `.github/` (proposals.py), a workflow file that fails DataLab's
  workflow check (with the real profile's small-cell rule, and pipelines as
  in the change's own tree), and a pipeline's `pipeline.yaml` that fails
  the pipeline file's check (a workflow naming it wouldn't find it).
- **Data** findings wait for the person to confirm each one isn't
  participant data: the knowledge base's scan (knowledge/check.py:
  `data_findings`, `name_findings`), and, since fixtures are where real
  rows slip in, every changed data file (CSV and the like, in fixtures or
  anywhere), column names that look like identifiers, and short numbers
  next to dates (a 4-row `id_map.csv` of `1001,2019-03-02` gets past the
  knowledge base's scan, which wants long or prefixed IDs).
- **Code** findings wait for the person to confirm too: code that runs when
  the package is installed or loaded, or when R starts in the folder
  (`.Rprofile`, `configure`, `cleanup`, compiled code in `src/`, `.onLoad`
  and the like). Once pushed, it runs on everyone's computer, outside any
  sandbox, not only in DataLab's test container.

A finding's id is stable across edits elsewhere in the file (as in the
knowledge base), and its `text` is the line it's about, shown to the person
deciding.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from datalab.knowledge import check as kb
from datalab.pipelines.proposals import proposal_problem

Severity = Literal["error", "data", "code"]
MAX_TEXT = 200
MAX_LINE_FINDINGS = 20  # per file and rule; then one line saying how many more

_DATA_EXTENSIONS = {"csv", "tsv", "psv", "dat", "txt", "json", "ndjson", "jsonl", "tab"}
_DATA_FOLDERS = ("fixtures/", "testdata/", "extdata/", "/data/", "data-raw/")
_ALWAYS_DATA = {"csv", "tsv", "psv", "dat", "tab"}
# Column names (split at _ . - and spaces) that look like they identify someone.
_ID_WORDS = {
    "id", "ids", "mrn", "dob", "ssn", "subject", "participant", "patient", "person",
    "email", "phone", "zip", "zipcode", "birth", "birthdate", "name", "firstname", "lastname",
}  # fmt: skip
_ID_NAMES = {
    "participantid", "subjectid", "studyid", "patientid", "personid", "userid",
    "dateofbirth", "studyparticipantid", "recordid", "deviceid", "mrn",
}  # fmt: skip
_DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
_NUMBER = re.compile(r"(?<![\w.:/-])\d{3,9}(?![\w.:/-])")
_HOOK = re.compile(r"^[ \t]*\.(onLoad|onAttach|onUnload|onDetach)\s*(<-|=)", re.MULTILINE)
_PACKAGE = "ihsDataR/"
_INSTALL_SCRIPTS = {"configure", "configure.win", "configure.ucrt", "cleanup", "cleanup.win"}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    severity: Severity
    message: str
    line: int | None = None
    text: str = ""  # the line it's about, if any

    @property
    def id(self) -> str:
        key = f"{self.path}\0{self.rule}\0{self.text or self.message}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "text": self.text[:MAX_TEXT],
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def blocking(self, confirmed: Collection[str] = ()) -> list[Finding]:
        """What stops a save: every error, and each other finding not confirmed."""
        return self.errors + [
            f for f in self.findings if f.severity != "error" and f.id not in confirmed
        ]


# The problems DataLab's workflow check finds in a workflow file's text, with
# pipelines looked up in the tree being checked (service.py).
WorkflowCheck = Callable[[str], list[str]]
MAX_WORKFLOW_FINDINGS = 20  # per file; then one saying how many more


def check(files: Mapping[str, bytes | None], workflows: WorkflowCheck | None = None) -> Report:
    """The check on a change's files (None: deleted). With `workflows`, each
    changed workflow file must also pass the workflow check: a file that
    wouldn't run is an error, not something to find out later."""
    report = Report()
    for path in sorted(files):
        problem = proposal_problem(path)
        if problem:
            report.findings.append(
                Finding(path, "not_editable", "error", problem.capitalize() + ".")
            )
        content = files[path]
        if content is None:
            continue
        report.findings += [_from_kb(f) for f in kb.name_findings(path)]
        text = kb.as_text(content)
        report.findings += install_findings(path, text)
        if text is None:
            continue
        report.findings += [_from_kb(f) for f in kb.data_findings(path, text)]
        if workflows is not None and is_workflow_file(path):
            report.findings += _workflow_findings(path, workflows(text))
        if (name := pipeline_spec_name(path)) is not None:
            report.findings += pipeline_spec_findings(path, name, text)
        if is_data_file(path):
            report.findings += data_file_findings(path, text)
        elif "/tests/" in f"/{path}":
            report.findings += _capped(path, "date_near_number", _dates_near_numbers(text))
    return report


def is_workflow_file(path: str) -> bool:
    """A workflow file, as the Workflows tab lists them: workflows/<name>.yaml."""
    name = path.removeprefix("workflows/")
    return (
        path.startswith("workflows/")
        and "/" not in name
        and not name.startswith(".")
        and name.lower().endswith((".yaml", ".yml"))
    )


_PIPELINE_SPEC = re.compile(r"ihsDataR/inst/pipelines/([^/]+)/pipeline\.yaml")


def pipeline_spec_name(path: str) -> str | None:
    """The pipeline a `pipeline.yaml` is for (its folder's name), or None."""
    found = _PIPELINE_SPEC.fullmatch(path)
    return found.group(1) if found else None


def pipeline_spec_findings(path: str, name: str, text: str) -> list[Finding]:
    """A pipeline file as workflows will read it (workflows/source.py,
    `pipelines_in`): it must pass the pipeline file's check, and be named
    as its folder."""
    from datalab.workflows.model import WorkflowInvalid, load_pipeline_file

    try:
        spec = load_pipeline_file(text)
    except WorkflowInvalid as error:
        problems = [str(p) for p in error.problems] or [str(error)]
    else:
        problems = (
            []
            if spec.name == name
            else [f"name: it's {spec.name!r}, but its folder is {name!r}: they must match."]
        )
    return [
        Finding(path, "pipeline", "error", f"The pipeline check: {why}")
        for why in problems[:MAX_WORKFLOW_FINDINGS]
    ]


def _workflow_findings(path: str, problems: list[str]) -> list[Finding]:
    found = [
        Finding(path, "workflow", "error", f"The workflow check: {why}")
        for why in problems[:MAX_WORKFLOW_FINDINGS]
    ]
    if len(problems) > MAX_WORKFLOW_FINDINGS:
        more = len(problems) - MAX_WORKFLOW_FINDINGS
        found.append(
            Finding(path, "workflow", "error", f"The workflow check: {more} more problems.")
        )
    return found


def is_data_file(path: str) -> bool:
    extension = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if extension in _ALWAYS_DATA:
        return True
    return extension in _DATA_EXTENSIONS and any(f in f"/{path}" for f in _DATA_FOLDERS)


def data_file_findings(path: str, text: str) -> list[Finding]:
    lines = text.splitlines()
    found = [
        Finding(
            path,
            "data_file",
            "data",
            "A data file. Confirm every row in it is made up for the tests, not taken from "
            "the study (even a few rows, or rows that look harmless).",
        )
    ]
    if lines:
        columns = [c.strip().strip("\"'") for c in re.split(r"[,;\t|]", lines[0])]
        named = [c for c in columns if looks_identifying(c)]
        if named:
            found.append(
                Finding(
                    path,
                    "id_columns",
                    "data",
                    f"Columns that may identify someone: {', '.join(named[:8])}.",
                    1,
                    lines[0],
                )
            )
    found += _capped(path, "date_near_number", _dates_near_numbers(text))
    return found


def install_findings(path: str, text: str | None) -> list[Finding]:
    """Code that runs outside DataLab's test container once the change is shared."""
    name = path.rsplit("/", 1)[-1]
    if name in (".Rprofile", ".Renviron"):
        return [
            Finding(
                path,
                "startup_code",
                "code",
                f"{name} runs whenever R starts in this folder, on the computer of anyone "
                "who uses the repo. Confirm you've read it and it should.",
            )
        ]
    if not path.startswith(_PACKAGE):
        return []
    inside = path[len(_PACKAGE) :]
    if inside in _INSTALL_SCRIPTS:
        return [
            Finding(
                path,
                "install_script",
                "code",
                f"{name} runs when the package is installed, on everyone's computer. "
                "Confirm you've read it and it should.",
            )
        ]
    if inside.startswith("src/"):
        return [
            Finding(
                path,
                "compiled_code",
                "code",
                "Compiled code (or its build settings): it's built and run when the package "
                "is installed, on everyone's computer. Confirm you've read it.",
            )
        ]
    if inside.startswith("R/") and text is not None:
        found = []
        for match in _HOOK.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(
                Finding(
                    path,
                    "load_hook",
                    "code",
                    f".{match.group(1)} runs whenever the package is loaded, on everyone's "
                    "computer. Confirm you've read what it does.",
                    line,
                    text.splitlines()[line - 1].strip(),
                )
            )
        return found
    return []


def looks_identifying(column: str) -> bool:
    """Whether a column name looks like it identifies someone (`id`, `dob`,
    `STUDY_PARTICIPANT_ID`…). Also used by workflow drafts (workflows/drafts.py)."""
    lowered = column.lower()
    words = [w for w in re.split(r"[_.\-\s]+", lowered) if w]
    squashed = "".join(words)
    return squashed in _ID_NAMES or any(w in _ID_WORDS for w in words)


def _dates_near_numbers(text: str) -> list[tuple[int, str]]:
    """Lines with a date and a 3 to 9 digit number that isn't a year: maybe an ID and its day."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not _DATE.search(line):
            continue
        rest = _DATE.sub(" ", line)
        numbers = [n for n in _NUMBER.findall(rest) if not (len(n) == 4 and n[:2] in ("19", "20"))]
        if numbers:
            found.append((number, line.strip()))
    return found


def _capped(path: str, rule: str, lines: list[tuple[int, str]]) -> list[Finding]:
    message = "A date next to a number that could be a participant ID."
    found = [Finding(path, rule, "data", message, n, t) for n, t in lines[:MAX_LINE_FINDINGS]]
    if len(lines) > MAX_LINE_FINDINGS:
        more = len(lines) - MAX_LINE_FINDINGS
        found.append(
            Finding(path, rule, "data", f"And {more} more lines like these in this file.", None, "")
        )
    return found


def _from_kb(finding: kb.Finding) -> Finding:
    severity: Severity = "error" if finding.severity == "error" else "data"
    return Finding(
        finding.path, finding.rule, severity, finding.message, finding.line, finding.subject
    )
