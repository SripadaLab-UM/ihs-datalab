"""Workflow files: the YAML model, and the checks a file must pass to run.

A workflow file (docs/WORKFLOWS.md) is parsed with Pydantic, then checked as
a whole: step references, parameters, output names, and `reads:`, the Oracle
objects the workflow may read. Every problem is reported with where it is in
the file (`steps[1].inputs.raw`) and a message written for the person
editing it, rather than stopping at the first.

The same checks run when a file is listed or validated, and again when a run
starts; at run time the declared `reads:` also go to the data service as the
only tables its SQL may read.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, ClassVar, Literal

import sqlglot
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
)
from sqlglot import exp
from sqlglot.errors import SqlglotError

from datalab.data.sqlcheck import SqlRejected, check_sql
from datalab.exports import effective_name, safe_name

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 256 * 1024

# Step ids and output files are lower case: NTFS ignores case, and they end
# up as folder and file names (see the runner spike, §5).
_ID = re.compile(r"[a-z][a-z0-9_]{0,47}")
_FILE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,99}")
_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_OBJECT = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}\.[A-Z][A-Z0-9_$#]{0,127}")
_COLUMN = re.compile(r"[A-Za-z][A-Za-z0-9_$#]{0,127}")
# The one output of a step that has `output:`, as R sees it: outputs$final.
SOLE_OUTPUT = "final"
# Where a pipeline's extracts appear in its container: /run/in/oracle/.
ORACLE_INPUT = "oracle"


@dataclass(frozen=True)
class Problem:
    path: str  # where in the file: "steps[1].inputs.raw", or "" for the whole file
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class WorkflowInvalid(ValueError):
    """The file doesn't pass the checks; `problems` says where and why."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems) or "The workflow isn't valid.")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ParamType = Literal["date", "string", "integer", "number", "boolean"]
Scalar = bool | int | float | str


class Parameter(_Strict):
    type: ParamType
    default: Scalar | None = None
    description: str = ""


class ReadDecl(_Strict):
    """One Oracle object a workflow or pipeline reads.

    A workflow lists objects (`IHS_2025.VFITBITDAILYDATA`); a pipeline also
    says which columns and rows DataLab extracts for it, since extraction is
    its only way to data.
    """

    object: str
    columns: tuple[str, ...] | None = None
    where: str | None = None
    whole_table: bool = False


def _reads(value: Any) -> Any:
    if isinstance(value, list):
        return [{"object": v} if isinstance(v, str) else v for v in value]
    return value


class _Step(_Strict):
    kind: ClassVar[str]
    id: str
    description: str = ""


class SqlStep(_Step):
    kind: ClassVar[str] = "sql"
    sql: str
    output: str


class RStep(_Step):
    kind: ClassVar[str] = "r"
    r: str
    inputs: dict[str, str] = Field(default_factory=dict)
    output: str | None = None
    outputs: dict[str, str] | None = None


class PipelineStep(_Step):
    kind: ClassVar[str] = "pipeline"
    pipeline: str
    inputs: dict[str, str] = Field(default_factory=dict)


class TotalRows(_Strict):
    """Rows that are totals of other rows: those with `value` in `column`.
    A total adds up the rows with the same values in `within`."""

    column: str
    value: str
    within: tuple[str, ...] = ()


class SmallCells(_Strict):
    count_columns: tuple[str, ...]
    # A count from 1 to min - 1 is small; 0 may be shown. "$name" for a parameter.
    min: int | str = 11
    totals: TotalRows | None = None
    # A column that is the sum of `count_columns` in each row.
    total_column: str | None = None

    @field_validator("count_columns", mode="before")
    @classmethod
    def _one_or_more(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value


class BuiltinQc(_Strict):
    file: str
    min_rows: int | str | None = None
    max_rows: int | str | None = None
    required_columns: tuple[str, ...] = ()
    no_missing: tuple[str, ...] = ()
    max_missing: dict[str, float | str] = Field(default_factory=dict)
    unique_by: tuple[str, ...] | None = None
    small_cells: SmallCells | None = None

    @field_validator("small_cells", mode="before")
    @classmethod
    def _one_count_column(cls, rule: Any) -> Any:
        # The spike's form: `small_cells: {count_column: n, min: 11}`.
        if isinstance(rule, dict) and "count_column" in rule and "count_columns" not in rule:
            rule = dict(rule)
            rule["count_columns"] = rule.pop("count_column")
        return rule


class CustomQc(_Strict):
    r: str
    inputs: dict[str, str] = Field(default_factory=dict)


def _qc_kind(value: Any) -> str:
    if isinstance(value, dict):
        return "custom" if "r" in value else "builtin"
    return "custom" if isinstance(value, CustomQc) else "builtin"


class QcStep(_Step):
    kind: ClassVar[str] = "qc"
    qc: Annotated[
        Annotated[BuiltinQc, Tag("builtin")] | Annotated[CustomQc, Tag("custom")],
        Discriminator(_qc_kind),
    ]

    @property
    def custom(self) -> bool:
        return isinstance(self.qc, CustomQc)


_STEP_KEYS = ("sql", "r", "pipeline", "qc")


def _step_kind(value: Any) -> str | None:
    if isinstance(value, dict):
        kinds = [k for k in _STEP_KEYS if k in value]
        return kinds[0] if len(kinds) == 1 else None
    return getattr(value, "kind", None)


Step = Annotated[
    Annotated[SqlStep, Tag("sql")]
    | Annotated[RStep, Tag("r")]
    | Annotated[PipelineStep, Tag("pipeline")]
    | Annotated[QcStep, Tag("qc")],
    Discriminator(
        _step_kind,
        custom_error_type="step_kind",
        custom_error_message="A step needs exactly one of sql, r, pipeline, or qc.",
    ),
]


class Deliver(_Strict):
    destination: str
    folder: str
    files: tuple[str, ...]


class Workflow(_Strict):
    schema_version: int = SCHEMA_VERSION
    name: str
    description: str = ""
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    reads: tuple[ReadDecl, ...]
    steps: tuple[Step, ...]
    deliver: Deliver | None = None

    @field_validator("reads", mode="before")
    @classmethod
    def _short_reads(cls, value: Any) -> Any:
        return _reads(value)

    def step(self, step_id: str) -> Step:
        return next(s for s in self.steps if s.id == step_id)

    @property
    def read_objects(self) -> frozenset[str]:
        return frozenset(r.object for r in self.reads)


class PipelineFile(_Strict):
    """`inst/pipelines/<name>/pipeline.yaml` in the pipelines package."""

    name: str
    description: str = ""
    reads: tuple[ReadDecl, ...]
    # The workflow parameters the pipeline uses (in `where:` binds, and in R).
    parameters: tuple[str, ...] = ()
    outputs: dict[str, str]

    @field_validator("reads", mode="before")
    @classmethod
    def _short_reads(cls, value: Any) -> Any:
        return _reads(value)


@dataclass(frozen=True)
class Pipeline:
    """A pipeline from the pipelines package, as the workflow check needs it."""

    name: str
    spec: PipelineFile
    script: str  # its run.R


PipelineLookup = Callable[[str], Pipeline | None]


# ------------------------------------------------------------------ parsing


def parse_yaml(text: str) -> Any:
    if len(text.encode()) > MAX_FILE_BYTES:
        raise WorkflowInvalid(
            [Problem("", f"The file is larger than {MAX_FILE_BYTES // 1024} KB.")]
        )
    try:
        return _dates_as_text(yaml.safe_load(text))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else ""
        problem = getattr(error, "problem", None) or "it isn't valid YAML"
        raise WorkflowInvalid([Problem(where, f"The file isn't valid YAML: {problem}.")]) from None


def load_workflow(
    text: str,
    *,
    pipelines: PipelineLookup | None = None,
    allowed_schemas: frozenset[str] | None = None,
) -> Workflow:
    """Parse and check a workflow file. Raises WorkflowInvalid with every problem.

    `allowed_schemas` are the cohort schemas this DataLab may read (None:
    any). `pipelines` finds a pipeline by name in the pipelines package.
    """
    raw = parse_yaml(text)
    if not isinstance(raw, dict):
        raise WorkflowInvalid([Problem("", "A workflow file is a YAML mapping (name, steps, …).")])
    version = raw.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise WorkflowInvalid(
            [
                Problem(
                    "schema_version",
                    f"This DataLab reads workflow files of schema version {SCHEMA_VERSION}, "
                    f"not {version!r}.",
                )
            ]
        )
    try:
        workflow = Workflow.model_validate(raw)
    except ValidationError as error:
        raise WorkflowInvalid(_problems(error)) from None
    problems = check_workflow(workflow, pipelines=pipelines, allowed_schemas=allowed_schemas)
    if problems:
        raise WorkflowInvalid(problems)
    return workflow


def load_pipeline_file(text: str) -> PipelineFile:
    raw = parse_yaml(text)
    try:
        spec = PipelineFile.model_validate(raw)
    except ValidationError as error:
        raise WorkflowInvalid(_problems(error)) from None
    problems = _check_pipeline_file(spec)
    if problems:
        raise WorkflowInvalid(problems)
    return spec


def _dates_as_text(value: Any) -> Any:
    """YAML reads 2025-04-01 as a date; a workflow's dates are ISO text."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _dates_as_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dates_as_text(v) for v in value]
    return value


_STEP_TAGS = set(_STEP_KEYS)
_QC_TAGS = {"builtin", "custom"}


def _problems(error: ValidationError) -> list[Problem]:
    problems = []
    for item in error.errors(include_url=False):
        loc = list(item["loc"])
        # Drop the union tags Pydantic puts in the path (steps.1.r.inputs).
        cleaned: list[Any] = []
        for i, part in enumerate(loc):
            before = loc[i - 1] if i else None
            if (part in _STEP_TAGS and isinstance(before, int) and loc[:1] == ["steps"]) or (
                part in _QC_TAGS and before == "qc" and i >= 4
            ):
                continue
            cleaned.append(part)
        message = item["msg"]
        if item["type"] == "extra_forbidden":
            message = "DataLab doesn't know this key."
        elif item["type"] == "missing":
            message = "This is required."
        problems.append(Problem(_path(cleaned), message))
    return problems


def _path(parts: list[Any]) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


# ------------------------------------------------------------------ checks


def step_outputs(step: Step, pipelines: PipelineLookup | None = None) -> dict[str, str]:
    """A step's outputs, name to file name. A step with `output:` has one, `final`."""
    if isinstance(step, SqlStep):
        return {SOLE_OUTPUT: step.output}
    if isinstance(step, RStep):
        if step.outputs is not None:
            return dict(step.outputs)
        return {SOLE_OUTPUT: step.output} if step.output else {}
    if isinstance(step, PipelineStep):
        found = pipelines(step.pipeline) if pipelines else None
        return dict(found.spec.outputs) if found else {}
    return {}


def step_kind(step: Step) -> str:
    """The run record's kind: sql, r, pipeline, qc_builtin, or qc_custom."""
    if isinstance(step, QcStep):
        return "qc_custom" if step.custom else "qc_builtin"
    return step.kind


def step_inputs(step: Step) -> dict[str, str]:
    """A container step's declared inputs, name to reference."""
    if isinstance(step, RStep | PipelineStep):
        return dict(step.inputs)
    if isinstance(step, QcStep) and isinstance(step.qc, CustomQc):
        return dict(step.qc.inputs)
    return {}


def resolve_ref(ref: str, outputs: Mapping[str, Mapping[str, str]]) -> tuple[str, str] | str:
    """(step id, output name) for "step" or "step.output", or a message saying why not."""
    step_id, _, name = ref.partition(".")
    if step_id not in outputs:
        return f"{step_id!r} isn't an earlier step."
    files = outputs[step_id]
    if not files:
        return f"Step {step_id!r} has no outputs."
    if not name:
        if len(files) != 1:
            return f"Step {step_id!r} has several outputs: name one, as {step_id}.<output>."
        return step_id, next(iter(files))
    if name not in files:
        return f"Step {step_id!r} has no output {name!r}."
    return step_id, name


def check_workflow(
    workflow: Workflow,
    *,
    pipelines: PipelineLookup | None = None,
    allowed_schemas: frozenset[str] | None = None,
) -> list[Problem]:
    problems: list[Problem] = []

    def problem(path: str, message: str) -> None:
        problems.append(Problem(path, message))

    if not _NAME.fullmatch(workflow.name):
        problem("name", "Use lower case letters, digits, - and _ (at most 64).")

    # Parameters.
    for name, param in workflow.parameters.items():
        where = f"parameters.{name}"
        if not _ID.fullmatch(name):
            problem(where, "Parameter names are lower case letters, digits and _.")
        if param.default is not None:
            try:
                coerce_param(param, param.default)
            except ValueError as error:
                problem(f"{where}.default", str(error))

    def param_ref(path: str, value: Any, *, numeric: bool = True) -> None:
        if isinstance(value, str):
            if not value.startswith("$"):
                problem(path, "Give a number, or $name for a parameter.")
                return
            param = workflow.parameters.get(value[1:])
            if param is None:
                problem(path, f"There's no parameter {value[1:]!r}.")
            elif numeric and param.type not in ("integer", "number"):
                problem(path, f"Parameter {value[1:]!r} must be an integer or number.")

    # Steps, in order: references may point only at earlier steps.
    outputs: dict[str, dict[str, str]] = {}
    csv_outputs: dict[str, set[str]] = {}
    seen: set[str] = set()
    pipeline_reads: dict[str, set[str]] = {}
    for index, step in enumerate(workflow.steps):
        where = f"steps[{index}]"
        if not _ID.fullmatch(step.id):
            problem(f"{where}.id", "Step ids are lower case letters, digits and _ (at most 48).")
        if step.id in seen:
            problem(f"{where}.id", f"Another step is already called {step.id!r}.")
        seen.add(step.id)

        if isinstance(step, SqlStep):
            if not step.output.endswith(".csv"):
                problem(f"{where}.output", "A SQL step's output is a .csv file.")
            problems += _check_sql_step(step, where, workflow, allowed_schemas)
        if isinstance(step, RStep) and step.output is not None and step.outputs is not None:
            problem(where, "Give output or outputs, not both.")
        if isinstance(step, PipelineStep):
            found = pipelines(step.pipeline) if pipelines else None
            if found is None:
                problem(f"{where}.pipeline", f"There's no pipeline {step.pipeline!r}.")
            else:
                pipeline_reads[step.id] = {r.object for r in found.spec.reads}
                for name in found.spec.parameters:
                    if name not in workflow.parameters:
                        problem(
                            f"{where}.pipeline",
                            f"Pipeline {step.pipeline!r} uses parameter {name!r}, "
                            "which the workflow doesn't declare.",
                        )
                if ORACLE_INPUT in step.inputs:
                    problem(f"{where}.inputs.{ORACLE_INPUT}", "This name is the extracts' own.")

        for name, ref in step_inputs(step).items():
            path = f"{where}{'.qc' if isinstance(step, QcStep) else ''}.inputs.{name}"
            if not _ID.fullmatch(name):
                problem(path, "Input names are lower case letters, digits and _.")
            resolved = resolve_ref(ref, outputs)
            if isinstance(resolved, str):
                problem(path, resolved)

        if isinstance(step, QcStep) and isinstance(step.qc, BuiltinQc):
            qc = step.qc
            resolved = resolve_ref(qc.file, outputs)
            if isinstance(resolved, str):
                problem(f"{where}.qc.file", resolved)
            elif resolved[1] not in csv_outputs.get(resolved[0], set()):
                problem(f"{where}.qc.file", "Built-in checks read a .csv output.")
            for key in ("min_rows", "max_rows"):
                param_ref(f"{where}.qc.{key}", getattr(qc, key))
            for column, share in qc.max_missing.items():
                param_ref(f"{where}.qc.max_missing.{column}", share)
                if isinstance(share, float | int) and not 0 <= share <= 1:
                    problem(f"{where}.qc.max_missing.{column}", "A share from 0 to 1.")
            if qc.small_cells is not None:
                rule = qc.small_cells
                param_ref(f"{where}.qc.small_cells.min", rule.min)
                if isinstance(rule.min, int) and rule.min < 1:
                    problem(f"{where}.qc.small_cells.min", "At least 1.")
                if not rule.count_columns:
                    problem(f"{where}.qc.small_cells.count_columns", "Name the count columns.")
            columns = [
                *qc.required_columns,
                *qc.no_missing,
                *qc.max_missing,
                *(qc.unique_by or ()),
            ]
            for column in columns:
                if not column or len(column) > 128:
                    problem(f"{where}.qc", f"{column!r} isn't a column name.")

        files = step_outputs(step, pipelines)
        folded: set[str] = set()
        for name, file in files.items():
            path = f"{where}.output" if name == SOLE_OUTPUT else f"{where}.outputs.{name}"
            if not _ID.fullmatch(name):
                problem(path, "Output names are lower case letters, digits and _.")
            if not _FILE.fullmatch(file) or ".." in file or effective_name(file) != file:
                problem(
                    path,
                    "Output files are lower case letters, digits, '.', '-' and '_', "
                    "and can't end in a dot.",
                )
            if file in folded:
                problem(path, f"Another output of this step is already called {file!r}.")
            folded.add(file)
        outputs[step.id] = files
        csv_outputs[step.id] = {n for n, f in files.items() if f.endswith(".csv")}

    if not workflow.steps:
        problem("steps", "A workflow needs at least one step.")

    problems += check_reads(workflow, pipeline_reads)

    if workflow.deliver is not None:
        deliver = workflow.deliver
        if not _KEY.fullmatch(deliver.destination):
            problem(
                "deliver.destination",
                "A destination key is lower case letters, digits, - and _ (set per computer "
                "in Settings).",
            )
        if not deliver.folder or safe_name(deliver.folder) != deliver.folder:
            problem("deliver.folder", "Use a plain folder name that works on Mac and Windows.")
        if not deliver.files:
            problem("deliver.files", "Name the outputs to deliver.")
        chosen: set[tuple[str, str]] = set()
        for index, ref in enumerate(deliver.files):
            resolved = resolve_ref(ref, outputs)
            if isinstance(resolved, str):
                problem(f"deliver.files[{index}]", resolved)
            elif resolved in chosen:
                problem(f"deliver.files[{index}]", "This output is already listed.")
            else:
                chosen.add(resolved)
        names = [outputs[s][n] for s, n in chosen]
        if len(names) != len(set(names)):
            problem("deliver.files", "Two delivered outputs have the same file name.")
    return problems


def _check_sql_step(
    step: SqlStep, where: str, workflow: Workflow, allowed: frozenset[str] | None
) -> list[Problem]:
    try:
        checked = check_sql(
            step.sql, allowed_schemas=allowed or _schemas_named(step.sql), columns=None
        )
    except SqlRejected as error:
        return [Problem(f"{where}.sql", str(error))]
    problems = []
    declared = {name.lower() for name in workflow.parameters}
    for bind in checked.binds:
        if bind.lower() not in declared:
            problems.append(
                Problem(f"{where}.sql", f":{bind} isn't one of the workflow's parameters.")
            )
    return problems


def _schemas_named(sql: str) -> frozenset[str]:
    try:
        tree = sqlglot.parse_one(sql, read="oracle")
    except SqlglotError:
        return frozenset()
    return frozenset(t.db.upper() for t in tree.find_all(exp.Table) if t.db)


def sql_tables(sql: str) -> frozenset[str]:
    """The objects a SQL step reads, as SCHEMA.OBJECT (it must pass the SQL check)."""
    checked = check_sql(sql, allowed_schemas=_schemas_named(sql), columns=None)
    return frozenset(str(t) for t in checked.tables)


def sql_binds(sql: str) -> tuple[str, ...]:
    return check_sql(sql, allowed_schemas=_schemas_named(sql), columns=None).binds


def check_reads(workflow: Workflow, pipeline_reads: Mapping[str, set[str]]) -> list[Problem]:
    """`reads:` must list exactly what the workflow reads.

    Every object a SQL step names is in it (joins and subqueries included;
    CTE names aren't objects), a pipeline's own `reads:` are in it, and every
    entry is used by some step, so the list can't drift into "whatever we
    might read".
    """
    problems: list[Problem] = []
    declared: set[str] = set()
    if not workflow.reads:
        problems.append(Problem("reads", "List the Oracle objects the workflow reads."))
    for index, entry in enumerate(workflow.reads):
        path = f"reads[{index}]"
        if not _OBJECT.fullmatch(entry.object):
            problems.append(Problem(path, "Write SCHEMA.OBJECT, in upper case."))
        if entry.columns is not None or entry.where is not None or entry.whole_table:
            problems.append(
                Problem(path, "columns, where and whole_table belong in a pipeline's reads.")
            )
        if entry.object in declared:
            problems.append(Problem(path, f"{entry.object} is listed twice."))
        declared.add(entry.object)
    used: set[str] = set()
    for index, step in enumerate(workflow.steps):
        if isinstance(step, SqlStep):
            try:
                tables = sql_tables(step.sql)
            except SqlRejected:
                continue  # reported by the SQL check
            for table in sorted(tables - declared):
                problems.append(
                    Problem(f"steps[{index}].sql", f"This reads {table}, which isn't in reads.")
                )
            used |= tables
        if isinstance(step, PipelineStep) and step.id in pipeline_reads:
            wanted = pipeline_reads[step.id]
            for table in sorted(wanted - declared):
                problems.append(
                    Problem(
                        f"steps[{index}].pipeline",
                        f"Pipeline {step.pipeline!r} reads {table}, which isn't in reads.",
                    )
                )
            used |= wanted
    for index, entry in enumerate(workflow.reads):
        if entry.object in declared - used:
            problems.append(Problem(f"reads[{index}]", f"No step reads {entry.object}."))
    return problems


def _check_pipeline_file(spec: PipelineFile) -> list[Problem]:
    problems: list[Problem] = []
    if not _ID.fullmatch(spec.name):
        problems.append(Problem("name", "Pipeline names are lower case letters, digits and _."))
    if not spec.reads:
        problems.append(Problem("reads", "List the Oracle objects the pipeline reads."))
    seen: set[str] = set()
    for index, entry in enumerate(spec.reads):
        path = f"reads[{index}]"
        if not _OBJECT.fullmatch(entry.object):
            problems.append(Problem(path, "Write SCHEMA.OBJECT, in upper case."))
        if entry.object in seen:
            problems.append(Problem(path, f"{entry.object} is listed twice."))
        seen.add(entry.object)
        for column in entry.columns or ():
            if not _COLUMN.fullmatch(column):
                problems.append(Problem(f"{path}.columns", f"{column!r} isn't a column name."))
        if not entry.columns:
            problems.append(Problem(f"{path}.columns", "List the columns to extract."))
        # A pipeline mustn't pull a whole cohort table by accident.
        if entry.where is None and not entry.whole_table:
            problems.append(
                Problem(path, "Give where: (or whole_table: true) so extraction is bounded.")
            )
        if entry.where is not None and entry.whole_table:
            problems.append(Problem(path, "Give where or whole_table, not both."))
    for name in spec.parameters:
        if not _ID.fullmatch(name):
            problems.append(Problem("parameters", f"{name!r} isn't a parameter name."))
    if not spec.outputs:
        problems.append(Problem("outputs", "A pipeline has at least one output."))
    for name, file in spec.outputs.items():
        if not _ID.fullmatch(name) or not _FILE.fullmatch(file) or effective_name(file) != file:
            problems.append(Problem(f"outputs.{name}", "Use lower case names and file names."))
    return problems


def extract_sql(entry: ReadDecl) -> str:
    """The query DataLab runs to extract one of a pipeline's declared objects."""
    columns = ", ".join(entry.columns or ())
    sql = f"SELECT {columns} FROM {entry.object}"
    if entry.where:
        sql += f" WHERE {entry.where}"
    return sql


# ------------------------------------------------------------ parameters


def coerce_param(param: Parameter, value: Any) -> Scalar:
    """A parameter value as its declared type, or ValueError saying why not."""
    kind = param.type
    if kind == "date":
        text = value.isoformat() if isinstance(value, date) else value
        if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError("A date is written YYYY-MM-DD.")
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{text} isn't a date.") from None
        return text
    if kind == "integer":
        if isinstance(value, bool):
            raise ValueError("Give a whole number.")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value.strip()):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise ValueError("Give a whole number.")
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError("Give a number.")
        if isinstance(value, int | float):
            return float(value)
        try:
            return float(str(value))
        except ValueError:
            raise ValueError("Give a number.") from None
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("Give true or false.")
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise ValueError("Give some text.")
    text = str(value)
    if len(text) > 1000:
        raise ValueError("At most 1,000 characters.")
    return text


def resolve_params(workflow: Workflow, given: Mapping[str, Any]) -> dict[str, Scalar]:
    """Every parameter's value: what was given, else its default."""
    problems: list[Problem] = []
    values: dict[str, Scalar] = {}
    for name in sorted(set(given) - set(workflow.parameters)):
        problems.append(Problem(f"params.{name}", "The workflow has no such parameter."))
    for name, param in workflow.parameters.items():
        value = given.get(name, param.default)
        if value is None:
            problems.append(Problem(f"params.{name}", "This parameter needs a value."))
            continue
        try:
            values[name] = coerce_param(param, value)
        except ValueError as error:
            problems.append(Problem(f"params.{name}", str(error)))
    if problems:
        raise WorkflowInvalid(problems)
    return values


def param_value(value: int | float | str | None, params: Mapping[str, Scalar]) -> Any:
    """A QC setting: a number, or "$name" for a parameter's value."""
    if isinstance(value, str) and value.startswith("$"):
        return params[value[1:]]
    return value


def bind_value(value: Scalar) -> Any:
    """How a parameter goes to Oracle as a bind: dates as text (the SQL uses
    TO_DATE), booleans as 1 or 0."""
    if isinstance(value, bool):
        return int(value)
    return value
