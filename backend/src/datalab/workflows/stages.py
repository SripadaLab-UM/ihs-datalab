"""A workflow as three stages, and edits to them: Extract → Process & QC → Deliver.

The Workflows tab's New workflow review shows a draft as three cards
(docs/WORKFLOWS.md, As built: New workflow):

- **Extract**: the SQL steps, the tables they read, and the parameters;
- **Process & QC**: the R and pipeline steps and the checks, in file order;
- **Deliver**: where the files go.

`stages_of` gives that view of a file. `apply_edits` changes it: the file is
read into the workflow model (model.py), the edits are made to the model's
data, the result is read into the model again, and the YAML is written from
it (`workflow_yaml`). Nothing edits the YAML's text. The file check
(`load_workflow`) then runs on the new text, as it does on every draft; this
module only builds and rewrites, it never decides what's valid.

A rewritten file keeps its keys and values; comments and layout go.

**Dropping columns** is common enough to have its own fields: an R step
written from `drop_columns_script` (its first line says so) shows as the
list of columns it removes, and editing the list writes the script again
from the template. Any other R step shows its script as it is.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from datalab.data.sqlcheck import SqlRejected
from datalab.workflows.model import (
    SOLE_OUTPUT,
    BuiltinQc,
    CustomQc,
    PipelineStep,
    Problem,
    QcStep,
    RStep,
    SqlStep,
    Workflow,
    WorkflowInvalid,
    _problems,
    parse_yaml,
    sql_tables,
    step_outputs,
)

# The first line of a script DataLab wrote to drop columns: how it's recognised.
DROP_MARK = "# DataLab: drop columns"
# A column name as Oracle allows it unquoted ($ and # too), or as R's read.csv
# may give it (dots). None needs escaping inside an R "..." string: only a
# backslash or a quote would, and neither is allowed.
_COLUMN = re.compile(r"[A-Za-z][A-Za-z0-9_.$#]{0,127}")
_ID = re.compile(r"[a-z][a-z0-9_]{0,47}")
MAX_COLUMNS = 500


class StagesRefused(ValueError):
    """The edits can't be made (the message says why): the file is unchanged."""


# ------------------------------------------------------------------ the view


class StageParameter(BaseModel):
    name: str
    type: Literal["date", "string", "integer", "number", "boolean"]
    default: bool | int | float | str | None
    description: str


class ExtractStep(BaseModel):
    id: str
    description: str
    sql: str
    output: str
    # The objects the SQL reads, when DataLab can tell (SQL it can't read: none).
    tables: list[str]


class SmallCellsView(BaseModel):
    count_columns: list[str]
    min: int | str


class ProcessItem(BaseModel):
    """One step of Process & QC: an R step, a pipeline, or a check."""

    id: str
    kind: Literal["r", "pipeline", "check", "custom_check"]
    description: str
    inputs: dict[str, str] = {}
    outputs: dict[str, str] = {}
    # R steps: the script, and for a drop-columns step the columns it drops.
    script: str | None = None
    drop_columns: list[str] | None = None
    pipeline: str | None = None
    # Built-in checks.
    file: str | None = None
    min_rows: int | str | None = None
    max_rows: int | str | None = None
    required_columns: list[str] = []
    no_missing: list[str] = []
    unique_by: list[str] | None = None
    small_cells: SmallCellsView | None = None
    # Checks this view doesn't show as fields (max_missing, totals…), kept as they are.
    other_rules: list[str] = []


class DeliverView(BaseModel):
    destination: str
    folder: str
    files: list[str]
    without_small_cells: dict[str, str]


class OutputChoice(BaseModel):
    ref: str  # what `deliver.files` and a check's `file` name: "clean" or "clean.stats"
    step: str
    file: str


class Stages(BaseModel):
    name: str
    description: str
    parameters: list[StageParameter]
    reads: list[str]
    extract: list[ExtractStep]
    process: list[ProcessItem]
    deliver: DeliverView | None
    # Every step output, for the Deliver card's files and a new check's file.
    outputs: list[OutputChoice]


def stages_of(workflow: Workflow) -> Stages:
    extract: list[ExtractStep] = []
    process: list[ProcessItem] = []
    outputs: list[OutputChoice] = []
    for step in workflow.steps:
        files = step_outputs(step)
        for name, file in files.items():
            ref = step.id if name == SOLE_OUTPUT and len(files) == 1 else f"{step.id}.{name}"
            outputs.append(OutputChoice(ref=ref, step=step.id, file=file))
        if isinstance(step, SqlStep):
            extract.append(
                ExtractStep(
                    id=step.id,
                    description=step.description,
                    sql=step.sql,
                    output=step.output,
                    tables=sorted(_tables(step.sql)),
                )
            )
        elif isinstance(step, RStep):
            process.append(
                ProcessItem(
                    id=step.id,
                    kind="r",
                    description=step.description,
                    inputs=dict(step.inputs),
                    outputs=files,
                    script=step.r,
                    drop_columns=dropped_columns(step.r, step.inputs),
                )
            )
        elif isinstance(step, PipelineStep):
            process.append(
                ProcessItem(
                    id=step.id,
                    kind="pipeline",
                    description=step.description,
                    inputs=dict(step.inputs),
                    pipeline=step.pipeline,
                )
            )
        elif isinstance(step, QcStep) and isinstance(step.qc, CustomQc):
            process.append(
                ProcessItem(
                    id=step.id,
                    kind="custom_check",
                    description=step.description,
                    inputs=dict(step.qc.inputs),
                    script=step.qc.r,
                )
            )
        elif isinstance(step, QcStep) and isinstance(step.qc, BuiltinQc):
            qc = step.qc
            other = []
            if qc.max_missing:
                other.append("max_missing")
            rule = qc.small_cells
            if rule is not None and (rule.totals or rule.total_column or rule.percent_columns):
                other.append("small_cells totals and percentages")
            process.append(
                ProcessItem(
                    id=step.id,
                    kind="check",
                    description=step.description,
                    file=qc.file,
                    min_rows=qc.min_rows,
                    max_rows=qc.max_rows,
                    required_columns=list(qc.required_columns),
                    no_missing=list(qc.no_missing),
                    unique_by=list(qc.unique_by) if qc.unique_by is not None else None,
                    small_cells=SmallCellsView(count_columns=list(rule.count_columns), min=rule.min)
                    if rule
                    else None,
                    other_rules=other,
                )
            )
    deliver = workflow.deliver
    return Stages(
        name=workflow.name,
        description=workflow.description,
        parameters=[
            StageParameter(name=n, type=p.type, default=p.default, description=p.description)
            for n, p in workflow.parameters.items()
        ],
        reads=sorted(workflow.read_objects),
        extract=extract,
        process=process,
        deliver=DeliverView(
            destination=deliver.destination,
            folder=deliver.folder,
            files=list(deliver.files),
            without_small_cells=dict(deliver.without_small_cells),
        )
        if deliver
        else None,
        outputs=outputs,
    )


def _tables(sql: str) -> frozenset[str]:
    try:
        return sql_tables(sql)
    except (SqlRejected, ValueError):
        return frozenset()


# ------------------------------------------------------------------ edits


class ParameterEdit(BaseModel):
    default: bool | int | float | str | None = None
    description: str | None = Field(default=None, max_length=500)
    # Remove the default (the person gives a value each run).
    clear_default: bool = False


class StepEdit(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    sql: str | None = Field(default=None, max_length=64 * 1024)
    r: str | None = Field(default=None, max_length=64 * 1024)
    drop_columns: list[str] | None = Field(default=None, max_length=MAX_COLUMNS)


class SmallCellsEdit(BaseModel):
    count_columns: list[str] = Field(max_length=MAX_COLUMNS)
    min: int | str = 11


class CheckEdit(BaseModel):
    """A built-in check's rules as the card shows them: each one given replaces the file's."""

    description: str | None = Field(default=None, max_length=500)
    file: str | None = None
    min_rows: int | str | None = None
    max_rows: int | str | None = None
    required_columns: list[str] | None = Field(default=None, max_length=MAX_COLUMNS)
    no_missing: list[str] | None = Field(default=None, max_length=MAX_COLUMNS)
    unique_by: list[str] | None = Field(default=None, max_length=MAX_COLUMNS)
    small_cells: SmallCellsEdit | None = None
    # Take a rule out: "min_rows", "max_rows", "unique_by", "small_cells"…
    remove: list[Literal["min_rows", "max_rows", "unique_by", "small_cells"]] = []


class NewCheck(CheckEdit):
    id: str | None = None
    file: str  # type: ignore[assignment]  # required for a new check


class NewDropColumns(BaseModel):
    """A new drop-columns step on an earlier step's file, placed right after it."""

    id: str | None = None
    input: str  # the step (or step.output) whose file it reads
    columns: list[str] = Field(max_length=MAX_COLUMNS)
    output: str | None = None
    description: str = Field(default="", max_length=500)


class DeliverEdit(BaseModel):
    destination: str | None = None
    folder: str | None = None
    files: list[str] | None = Field(default=None, max_length=100)
    without_small_cells: dict[str, str] | None = None


class StageEdits(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    parameters: dict[str, ParameterEdit] = Field(default_factory=dict, max_length=100)
    steps: dict[str, StepEdit] = Field(default_factory=dict, max_length=100)
    checks: dict[str, CheckEdit] = Field(default_factory=dict, max_length=100)
    add_checks: list[NewCheck] = Field(default_factory=list, max_length=20)
    add_drop_columns: list[NewDropColumns] = Field(default_factory=list, max_length=20)
    remove_steps: list[str] = Field(default_factory=list, max_length=100)
    deliver: DeliverEdit | None = None
    # No delivery: the outputs stay in the run folder.
    no_deliver: bool = False


def read_model(text: str) -> Workflow:
    """The file as the workflow model, before the whole-file checks (which
    `load_workflow` makes). Raises WorkflowInvalid if the model can't read it."""
    raw = parse_yaml(text)
    if not isinstance(raw, dict):
        raise WorkflowInvalid([Problem("", "A workflow file is a YAML mapping (name, steps, …).")])
    try:
        return Workflow.model_validate(raw)
    except ValidationError as error:
        raise WorkflowInvalid(_problems(error)) from None


def apply_edits(text: str, edits: StageEdits) -> str:
    """The file with the edits made, written again from the model.

    Raises WorkflowInvalid when the file can't be read as a workflow (fix it
    in the YAML first), and StagesRefused when an edit can't be made."""
    data = model_data(read_model(text))
    steps: list[dict[str, Any]] = data["steps"]
    by_id = {s["id"]: s for s in steps}

    if edits.name is not None:
        data["name"] = edits.name.strip()
    if edits.description is not None:
        data["description"] = edits.description.strip()

    params = data.setdefault("parameters", {})
    for name, change in edits.parameters.items():
        if name not in params:
            raise StagesRefused(f"There's no parameter {name!r}.")
        if change.description is not None:
            params[name]["description"] = change.description.strip()
        if change.clear_default:
            params[name].pop("default", None)
        elif change.default is not None:
            params[name]["default"] = change.default

    sql_changed = False
    for step_id, change in edits.steps.items():
        step = _step(by_id, step_id)
        if change.description is not None:
            step["description"] = change.description.strip()
        if change.sql is not None:
            if "sql" not in step:
                raise StagesRefused(f"Step {step_id!r} isn't a SQL step.")
            step["sql"] = _block(change.sql)
            sql_changed = True
        if change.r is not None and change.drop_columns is not None:
            raise StagesRefused("Change the script or the columns it drops, not both.")
        if change.r is not None:
            if "r" not in step:
                raise StagesRefused(f"Step {step_id!r} isn't an R step.")
            step["r"] = _block(change.r)
        if change.drop_columns is not None:
            inputs = step.get("inputs") or {}
            if "r" not in step or dropped_columns(step["r"], inputs) is None:
                raise StagesRefused(
                    f"Step {step_id!r} isn't a drop-columns step: edit its R script instead."
                )
            step["r"] = drop_columns_script(next(iter(inputs)), change.drop_columns)

    for step_id, change in edits.checks.items():
        step = _step(by_id, step_id)
        qc = step.get("qc")
        if not isinstance(qc, dict) or "r" in qc:
            raise StagesRefused(f"Step {step_id!r} isn't a built-in check.")
        if change.description is not None:
            step["description"] = change.description.strip()
        _check_rules(qc, change)

    for step_id in edits.remove_steps:
        _step(by_id, step_id)
        steps[:] = [s for s in steps if s["id"] != step_id]
        del by_id[step_id]

    for new in edits.add_drop_columns:
        source = new.input.partition(".")[0]
        _step(by_id, source)
        step_id = _new_id(new.id or f"drop_{source}", by_id)
        added = {
            "id": step_id,
            "description": new.description.strip() or "Remove columns that aren't needed.",
            "r": drop_columns_script("raw", new.columns),
            "inputs": {"raw": new.input},
            "output": new.output or f"{step_id}.csv",
        }
        _insert_after(steps, source, added)
        by_id[step_id] = added
        # A check or delivery of the file it cleans now means the cleaned file.
        _move_refs(steps, data, new.input, step_id, after=step_id)

    for new in edits.add_checks:
        target = new.file.partition(".")[0]
        _step(by_id, target)
        step_id = _new_id(new.id or f"check_{target}", by_id)
        rules: dict[str, Any] = {"file": new.file}
        _check_rules(rules, new)
        added = {"id": step_id, "qc": rules}
        if new.description:
            added["description"] = new.description.strip()
        # After the step it checks and the checks already on it.
        last = target
        for s in steps[[x["id"] for x in steps].index(target) + 1 :]:
            if (
                isinstance(s.get("qc"), dict)
                and str(s["qc"].get("file", "")).partition(".")[0] == target
            ):
                last = s["id"]
            else:
                break
        _insert_after(steps, last, added)
        by_id[step_id] = added

    if edits.no_deliver:
        data.pop("deliver", None)
    elif edits.deliver is not None:
        deliver = data.get("deliver") or {
            "destination": "",
            "folder": data.get("name", ""),
            "files": [],
        }
        change = edits.deliver
        if change.destination is not None:
            deliver["destination"] = change.destination.strip()
        if change.folder is not None:
            deliver["folder"] = change.folder.strip()
        if change.files is not None:
            deliver["files"] = list(change.files)
        if change.without_small_cells is not None:
            reasons = {k: v.strip() for k, v in change.without_small_cells.items() if v.strip()}
            if reasons:
                deliver["without_small_cells"] = reasons
            else:
                deliver.pop("without_small_cells", None)
        data["deliver"] = deliver

    if sql_changed or edits.remove_steps:
        data["reads"] = _reads_after(data)

    try:
        workflow = Workflow.model_validate(data)
    except ValidationError as error:
        raise WorkflowInvalid(_problems(error)) from None
    return workflow_yaml(workflow)


def _step(by_id: dict[str, dict[str, Any]], step_id: str) -> dict[str, Any]:
    step = by_id.get(step_id)
    if step is None:
        raise StagesRefused(f"There's no step {step_id!r}.")
    return step


def _new_id(wanted: str, by_id: dict[str, Any]) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", wanted.lower()).strip("_")[:44] or "step"
    if not base[0].isalpha():
        base = f"s_{base}"[:44]
    candidate, n = base, 2
    while candidate in by_id:
        candidate, n = f"{base}_{n}", n + 1
    if not _ID.fullmatch(candidate):
        raise StagesRefused(f"{wanted!r} can't be a step id.")
    return candidate


def _insert_after(steps: list[dict[str, Any]], after: str, step: dict[str, Any]) -> None:
    index = [s["id"] for s in steps].index(after)
    steps.insert(index + 1, step)


def _move_refs(
    steps: list[dict[str, Any]], data: dict[str, Any], old: str, new: str, *, after: str
) -> None:
    """Checks after `after`, and the delivery, that name `old` now name `new`."""
    seen = False
    for s in steps:
        if s["id"] == after:
            seen = True
            continue
        if seen and isinstance(s.get("qc"), dict) and s["qc"].get("file") == old:
            s["qc"]["file"] = new
    deliver = data.get("deliver")
    if deliver and old in deliver.get("files", []):
        deliver["files"] = [new if f == old else f for f in deliver["files"]]


def _check_rules(qc: dict[str, Any], change: CheckEdit) -> None:
    if change.file is not None:
        qc["file"] = change.file
    for key in ("min_rows", "max_rows"):
        value = getattr(change, key)
        if value is not None:
            qc[key] = value
    for key in ("required_columns", "no_missing", "unique_by"):
        value = getattr(change, key)
        if value is not None:
            columns = _columns(value)
            if columns or key == "unique_by":
                qc[key] = columns
            else:
                qc.pop(key, None)
    if change.small_cells is not None:
        rule = qc.get("small_cells") or {}
        rule = {k: v for k, v in rule.items() if k not in ("count_column",)}
        rule["count_columns"] = _columns(change.small_cells.count_columns)
        rule["min"] = change.small_cells.min
        qc["small_cells"] = rule
    for key in change.remove:
        qc.pop(key, None)
    if qc.get("unique_by") == []:
        qc.pop("unique_by")


def _columns(values: list[str]) -> list[str]:
    out = []
    for value in values:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def _block(text: str) -> str:
    """Code as a block: one newline at the end, none at the start."""
    return text.strip("\n") + "\n"


def _reads_after(data: dict[str, Any]) -> list[Any]:
    """`reads:` after the SQL changed: the objects every SQL step reads, and,
    when there are pipeline steps, what was declared too (their reads are in
    the package; the file check says if one is left over)."""
    found: set[str] = set()
    pipelines = False
    for step in data["steps"]:
        if "sql" in step:
            found |= _tables(step["sql"])
        pipelines = pipelines or "pipeline" in step
    declared = data.get("reads") or []
    if pipelines or not found:
        for entry in declared:
            found.add(entry if isinstance(entry, str) else entry["object"])
    kept = {(e if isinstance(e, str) else e["object"]): e for e in declared}
    return [kept.get(name, name) for name in sorted(found)]


# ------------------------------------------------------------------ drop columns


def drop_columns_script(input_name: str, columns: list[str]) -> str:
    """An R step that writes its input without `columns`, values as they were read.

    Every value is read as text, so numbers and dates are written back exactly
    as they came, and an empty cell stays empty."""
    names = _columns(columns)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", input_name):
        raise StagesRefused(f"{input_name!r} can't name an input.")
    bad = [c for c in names if not _COLUMN.fullmatch(c)]
    if bad:
        raise StagesRefused(f"These aren't column names: {', '.join(bad)}.")
    listed = ", ".join(f'"{c}"' for c in names)
    return (
        f"{DROP_MARK}\n"
        f'x <- read.csv(inputs${input_name}, check.names = FALSE, colClasses = "character",\n'
        "              na.strings = character(0))\n"
        f"drop <- c({listed})\n"
        "keep <- setdiff(names(x), drop)\n"
        'write.csv(x[, keep, drop = FALSE], outputs$final, row.names = FALSE, na = "")\n'
    )


_DROP_LINE = re.compile(r'drop <- c\(((?:"[^"\\]*"(?:, )?)*)\)')


def dropped_columns(script: str, inputs: dict[str, str] | Any) -> list[str] | None:
    """The columns a drop-columns script removes, or None if it isn't one
    (exactly as `drop_columns_script` writes it, for its one input)."""
    if not script.startswith(DROP_MARK + "\n") or not inputs or len(inputs) != 1:
        return None
    found = _DROP_LINE.search(script)
    if found is None:
        return None
    columns = re.findall(r'"([^"\\]*)"', found.group(1))
    try:
        again = drop_columns_script(next(iter(inputs)), columns)
    except StagesRefused:
        return None
    return columns if again == _block(script) else None


# ------------------------------------------------------------------ writing


def model_data(workflow: Workflow) -> dict[str, Any]:
    """The workflow as plain data, as a file would give it: only what's set,
    and `reads:` entries that name just an object written as that name."""
    data = workflow.model_dump(mode="json", exclude_defaults=True)
    data["reads"] = [
        entry["object"] if set(entry) == {"object"} else entry for entry in data.get("reads", [])
    ]
    data.setdefault("steps", [])
    for step, original in zip(data["steps"], workflow.steps, strict=True):
        qc = getattr(original, "qc", None)
        if isinstance(qc, BuiltinQc) and qc.small_cells is not None:
            # The floor is written out, so a reader sees it.
            step["qc"]["small_cells"]["min"] = qc.small_cells.min
    return data


_ORDER = ("schema_version", "name", "description", "parameters", "reads", "steps", "deliver")
_STEP_ORDER = ("id", "description", "sql", "r", "pipeline", "qc", "inputs", "output", "outputs")


def workflow_yaml(workflow: Workflow) -> str:
    """A workflow file's text, written from the model."""
    data = model_data(workflow)
    ordered = {k: data[k] for k in _ORDER if k in data}
    ordered["steps"] = [
        {k: step[k] for k in (*_STEP_ORDER, *step) if k in step} for step in data["steps"]
    ]
    text = yaml.dump(ordered, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    # A blank line before each section and between steps, as people write them.
    return re.sub(r"(?<!steps:)\n(?=(?:  - id:|(?:parameters|reads|steps|deliver):))", "\n\n", text)


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        # Never anchors and aliases: the file check refuses them (safeyaml.py).
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # Lists indented under their key (`steps:` then `  - id:`).
        super().increase_indent(flow, False)


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


def _flow_list(dumper: yaml.SafeDumper, value: list) -> yaml.Node:
    flow = all(isinstance(v, str | int | float | bool) and "\n" not in str(v) for v in value)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=flow or None)


def _mapping(dumper: yaml.SafeDumper, value: dict) -> yaml.Node:
    # Short mappings of plain values on one line: a parameter, a step's inputs.
    flow = (
        0 < len(value) <= 4
        and all(
            isinstance(v, str | int | float | bool) and "\n" not in str(v) for v in value.values()
        )
        and len(repr(value)) < 90
    )
    return dumper.represent_mapping("tag:yaml.org,2002:map", value, flow_style=flow or None)


_Dumper.add_representer(str, _str)
_Dumper.add_representer(list, _flow_list)
_Dumper.add_representer(dict, _mapping)
