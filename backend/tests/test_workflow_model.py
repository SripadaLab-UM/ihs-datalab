"""Workflow files: parsing, and the checks that say where a problem is."""

from __future__ import annotations

from pathlib import Path

import pytest

from datalab.workflows.model import (
    Pipeline,
    WorkflowInvalid,
    load_pipeline_file,
    load_workflow,
    problem_position,
    resolve_params,
)
from datalab.workflows.source import SourceError, WorkflowFolder, git_blob_id
from tests.test_workflow_runner import WEEKLY

COHORTS = frozenset({"IHS_2024", "IHS_2025"})


def problems(text: str, **kwargs) -> list[str]:
    with pytest.raises(WorkflowInvalid) as refused:
        load_workflow(text, allowed_schemas=COHORTS, **kwargs)
    return [str(p) for p in refused.value.problems]


def test_the_docs_example_shape_loads():
    workflow = load_workflow(WEEKLY, allowed_schemas=COHORTS)
    assert [s.id for s in workflow.steps] == ["extract", "check_raw", "summary", "check_summary"]
    # Unquoted YAML dates are the ISO text the SQL binds expect.
    assert workflow.parameters["start_date"].default == "2025-04-01"
    assert workflow.read_objects == {"IHS_2025.WEARABLE_DAILY"}
    assert workflow.deliver is not None and workflow.deliver.files == ("summary",)


def test_schema_problems_carry_their_path():
    found = problems(
        WEEKLY.replace("      min_rows: 1\n", "      min_rows: 1\n      min_row: 2\n").replace(
            "  - id: summary\n    r: |", "  - id: summary\n    sql: x\n    r: |"
        )
    )
    assert "steps[1].qc.min_row: DataLab doesn't know this key." in found
    assert "steps[2]: A step needs exactly one of sql, r, pipeline, or qc." in found


def test_references_must_point_at_earlier_steps():
    text = WEEKLY.replace("inputs: { raw: extract }", "inputs: { raw: check_summary }")
    text = text.replace("files: [summary]", "files: [summary, nowhere]")
    found = problems(text)
    assert "steps[2].inputs.raw: 'check_summary' isn't an earlier step." in found
    assert "deliver.files[1]: 'nowhere' isn't an earlier step." in found


def test_built_in_checks_read_a_csv_and_name_real_parameters():
    text = WEEKLY.replace("output: weekly.csv", "output: weekly.parquet").replace(
        "min: $min_cell", "min: $smallest"
    )
    found = problems(text)
    assert "steps[3].qc.file: Built-in checks read a .csv output." in found
    assert "steps[3].qc.small_cells.min: There's no parameter 'smallest'." in found


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            (
                "      ORDER BY",
                "        AND STEPS > (SELECT 1 FROM IHS_2025.PARTICIPANTS)\n      ORDER BY",
            ),
            "steps[0].sql: This reads IHS_2025.PARTICIPANTS, which isn't in reads.",
        ),
        (
            (
                "  - IHS_2025.WEARABLE_DAILY\n",
                "  - IHS_2025.WEARABLE_DAILY\n  - IHS_2025.PARTICIPANTS\n",
            ),
            "reads[1]: No step reads IHS_2025.PARTICIPANTS.",
        ),
        (("FROM IHS_2025.WEARABLE_DAILY", "FROM WEARABLE_DAILY"), "Qualify every table"),
        (("FROM IHS_2025.WEARABLE_DAILY", "FROM IHS_2025.WEARABLE_DAILY@ELSEWHERE"), "links"),
        (("  - IHS_2025.WEARABLE_DAILY\n", "  - ihs_2025.wearable_daily\n"), "upper case"),
        (("TO_DATE(:end_date", "TO_DATE(:until"), ":until isn't one of the workflow's parameters"),
        (("FROM IHS_2025.WEARABLE_DAILY", "FROM IHS_2019.WEARABLE_DAILY"), "IHS_2019"),
    ],
)
def test_reads_must_match_the_sql(change, message):
    found = problems(WEEKLY.replace(*change))
    assert any(message in p for p in found), found


def test_cte_names_arent_objects():
    text = WEEKLY.replace(
        "      SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS\n"
        "      FROM IHS_2025.WEARABLE_DAILY\n",
        "      WITH d AS (SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS"
        " FROM IHS_2025.WEARABLE_DAILY)\n"
        "      SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS FROM d\n",
    )
    assert load_workflow(text, allowed_schemas=COHORTS).read_objects == {"IHS_2025.WEARABLE_DAILY"}


def test_names_and_versions():
    assert any("schema version 1" in p for p in problems("schema_version: 2\n" + WEEKLY))
    found = problems(
        WEEKLY.replace("name: weekly_steps", "name: Weekly Steps")
        .replace("- id: summary", "- id: Summary")
        .replace("folder: weekly_steps", "folder: 'a/b'")
    )
    assert any(p.startswith("name:") for p in found)
    assert any(p.startswith("steps[2].id:") for p in found)
    assert any(p.startswith("deliver.folder:") for p in found)
    assert any("isn't valid YAML" in p for p in problems("name: [unclosed"))


def test_parameters_are_typed():
    workflow = load_workflow(WEEKLY, allowed_schemas=COHORTS)
    values = resolve_params(workflow, {"min_cell": "5", "end_date": "2025-04-15"})
    assert values == {"start_date": "2025-04-01", "end_date": "2025-04-15", "min_cell": 5}
    with pytest.raises(WorkflowInvalid) as refused:
        resolve_params(workflow, {"min_cell": "few", "end_date": "2025-02-30", "extra": 1})
    paths = {p.path for p in refused.value.problems}
    assert paths == {"params.min_cell", "params.end_date", "params.extra"}
    bad_default = WEEKLY.replace("default: 2025-05-01", "default: soon")
    assert any(p.startswith("parameters.end_date.default:") for p in problems(bad_default))


PIPELINE = """\
name: daily_metrics
reads:
  - object: IHS_2025.WEARABLE_DAILY
    columns: [STUDY_PARTICIPANT_ID, RECORD_DATE, STEPS]
    where: RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
parameters: [start_date]
outputs: { daily: daily_metrics.csv }
"""

USES_PIPELINE = """\
name: metrics
reads: [IHS_2025.WEARABLE_DAILY]
parameters:
  start_date: { type: date, default: 2025-04-01 }
steps:
  - id: metrics
    pipeline: daily_metrics
  - id: check
    qc: { file: metrics, min_rows: 1 }
"""


def test_a_pipeline_brings_its_reads_and_outputs():
    spec = load_pipeline_file(PIPELINE)
    lookup = {"daily_metrics": Pipeline("daily_metrics", spec, "run <- 1")}.get
    workflow = load_workflow(USES_PIPELINE, pipelines=lookup, allowed_schemas=COHORTS)
    assert workflow.read_objects == {"IHS_2025.WEARABLE_DAILY"}
    found = problems(
        USES_PIPELINE.replace("IHS_2025.WEARABLE_DAILY", "IHS_2025.OTHER"), pipelines=lookup
    )
    assert any("reads IHS_2025.WEARABLE_DAILY, which isn't in reads" in p for p in found)
    assert any("There's no pipeline" in p for p in problems(USES_PIPELINE))


def test_a_pipelines_extraction_must_be_bounded():
    with pytest.raises(WorkflowInvalid) as refused:
        load_pipeline_file(
            PIPELINE.replace("    where: RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')\n", "")
        )
    assert any("whole_table" in str(p) for p in refused.value.problems)


def test_the_folder_reads_workflows_and_pipelines(tmp_path: Path):
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "metrics.yaml").write_text(USES_PIPELINE)
    (tmp_path / "workflows" / "notes.txt").write_text("not a workflow")
    pipeline = tmp_path / "ihsDataR" / "inst" / "pipelines" / "daily_metrics"
    pipeline.mkdir(parents=True)
    (pipeline / "pipeline.yaml").write_text(PIPELINE)
    (pipeline / "run.R").write_text("write.csv(x, outputs$daily)\n")
    folder = WorkflowFolder(tmp_path)
    assert folder.paths() == ["workflows/metrics.yaml"]
    file = folder.read("workflows/metrics.yaml")
    assert file.source == "file" and file.blob.startswith("sha256:")
    found = folder.pipeline("daily_metrics")
    assert found is not None and found.spec.outputs == {"daily": "daily_metrics.csv"}
    assert folder.pipeline("../../etc") is None
    for outside in ("../x.yaml", "/etc/passwd", "workflows/../../x.yaml"):
        with pytest.raises(Exception):  # noqa: B017 (SourceError)
            folder.read(outside)
    (tmp_path / "workflows" / "linked.yaml").symlink_to(tmp_path / "workflows" / "metrics.yaml")
    with pytest.raises(Exception):  # noqa: B017
        folder.read("workflows/linked.yaml")


def test_git_blob_ids_match_git():
    assert git_blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_the_small_cell_minimum_cant_go_below_11():
    found = problems(WEEKLY.replace("min: $min_cell", "min: 5"))
    assert any(p.startswith("steps[3].qc.small_cells.min: At least 11") for p in found)


def test_real_profile_deliveries_need_a_small_cell_check():
    unchecked = WEEKLY.replace(
        "      small_cells: { count_column: n_participants, min: $min_cell }\n",
        "      min_rows: 1\n",
    )
    assert load_workflow(unchecked, allowed_schemas=COHORTS)  # practice: not required
    found = problems(unchecked, require_small_cells=True)
    assert any(p.startswith("deliver.files[0]: A delivered CSV needs a small_cells") for p in found)
    opted_out = unchecked + "  without_small_cells:\n    summary: weekly means only, no counts\n"
    assert load_workflow(opted_out, allowed_schemas=COHORTS, require_small_cells=True)
    too_short = unchecked + "  without_small_cells: { summary: ok }\n"
    assert any("Say why" in p for p in problems(too_short, require_small_cells=True))
    # The check covers exactly the delivered output, not an earlier one.
    assert load_workflow(WEEKLY, allowed_schemas=COHORTS, require_small_cells=True)


def test_a_pipelines_where_is_checked_as_sql():
    def lookup_for(where: str):
        spec = load_pipeline_file(
            PIPELINE.replace("RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')", where)
        )
        return {"daily_metrics": Pipeline("daily_metrics", spec, "")}.get

    for where, message in (
        ("RECORD_DATE >= UTL_HTTP.REQUEST('x')", "reads[0]"),
        ("STEPS IN (SELECT STEPS FROM IHS_2025.PARTICIPANTS)", "may read only"),
        ("RECORD_DATE >= :since", ":since isn't one of the pipeline's parameters"),
    ):
        found = problems(USES_PIPELINE, pipelines=lookup_for(where))
        assert any(message in p for p in found), (where, found)


def test_every_real_delivery_is_checked_or_says_why_not():
    tsv = (
        WEEKLY.replace("output: weekly.csv", "output: weekly.tsv")
        .replace("      small_cells: { count_column: n_participants, min: $min_cell }\n", "")
        .replace("  - id: check_summary\n    qc:\n      file: summary\n", "")
    )
    found = problems(tsv, require_small_cells=True)
    assert any("Built-in checks read only CSVs" in p for p in found), found
    reason = "weekly means by device only; no counts in it"
    assert load_workflow(
        tsv + f"  without_small_cells: {{ summary: {reason} }}\n",
        allowed_schemas=COHORTS,
        require_small_cells=True,
    )
    for bad, message in (
        ("x" * 301, "300 characters"),
        ("checked by hand with participant SYN-0001", "identifier"),
        ("approved by jane@example.org for the share", "identifier"),
    ):
        found = problems(tsv + f"  without_small_cells: {{ summary: '{bad}' }}\n")
        assert any(message in p for p in found), (bad, found)


def test_percent_columns_name_a_count_column():
    text = WEEKLY.replace(
        "small_cells: { count_column: n_participants, min: $min_cell }",
        "small_cells: { count_column: n_participants, percent_columns: { pct: n_days } }",
    )
    assert any("isn't one of the count columns" in p for p in problems(text))


def test_problem_positions_point_into_the_file():
    text = WEEKLY + "  without_small_cells:\n    summary.final: Only aggregate counts.\n"
    lines = text.splitlines()

    def line_of(prefix: str) -> int:
        return next(i for i, line in enumerate(lines, 1) if line.startswith(prefix))

    assert problem_position(text, "steps[1].qc.min_rows") == (line_of("      min_rows"), 7)
    assert problem_position(text, "steps[2]") == (line_of("  - id: summary"), 5)
    assert problem_position(text, "deliver.files[0]") == (line_of("  files:"), 11)
    # A key with a dot in it.
    assert problem_position(text, "deliver.without_small_cells.summary.final") == (
        line_of("    summary.final"),
        5,
    )
    # Something the file lacks: the nearest key that's there.
    assert problem_position(text, "steps[0].inputs.raw") == (line_of("  - id: extract"), 5)
    assert problem_position(text, "steps[9]") == (line_of("steps:"), 1)
    assert problem_position(text, "line 4") == (4, 1)
    assert problem_position(text, "") is None
    assert problem_position(text, "nothing") is None
    assert problem_position("a: [", "a") is None


def test_the_folder_serves_only_its_workflow_files(tmp_path: Path):
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "a.yaml").write_text(WEEKLY)
    (tmp_path / "workflows" / ".b.yaml").write_text(WEEKLY)
    (tmp_path / "root.yaml").write_text(WEEKLY)
    folder = WorkflowFolder(tmp_path)
    assert folder.paths() == ["workflows/a.yaml"]
    assert folder.read("workflows/a.yaml").text == WEEKLY
    for path in ("root.yaml", "workflows/.b.yaml", ".b.yaml"):
        with pytest.raises(SourceError):
            folder.read(path)


def test_pipeline_problems_check_the_name_and_name_no_host_path(tmp_path):
    from datalab.workflows.model import Problem

    root = tmp_path / "folder"
    (root / "ihsDataR" / "inst" / "pipelines" / "bad").mkdir(parents=True)
    (root / "ihsDataR" / "inst" / "pipelines" / "bad" / "pipeline.yaml").write_text("name: [\n")
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "pipeline.yaml").write_text("secret: 1\n")
    folder = WorkflowFolder(root)
    for name in (str(tmp_path / "evil"), "../../../evil", str(tmp_path / "nope"), "A"):
        assert folder.pipeline_problems(name) == [
            "Pipeline names are lower case letters, digits and _ (at most 48)."
        ]
    assert folder.pipeline_problems("missing") == [
        "ihsDataR/inst/pipelines/missing/pipeline.yaml isn't there."
    ]
    assert folder.pipeline_problems("bad")  # its own problems, whatever they are
    assert all(str(tmp_path) not in why for why in folder.pipeline_problems("bad"))
    # A link out of the package isn't followed.
    (root / "ihsDataR" / "inst" / "pipelines" / "linked").symlink_to(tmp_path / "evil")
    assert folder.pipeline_problems("linked") == [
        "ihsDataR/inst/pipelines/linked/pipeline.yaml isn't there."
    ]
    # The check hands over the name as the file gave it, not in its message.
    with pytest.raises(WorkflowInvalid) as refused:
        load_workflow('name: t\nreads: []\nsteps:\n  - id: p\n    pipeline: "x\'."\n')
    [problem] = [p for p in refused.value.problems if p.pipeline is not None]
    assert problem.pipeline == "x'."
    assert problem == Problem(problem.path, problem.message)  # not part of equality
