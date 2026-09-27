"""The knowledge-base check: layout, pages, links, schema, skills, data heuristics."""

from __future__ import annotations

import datetime

import pytest

from datalab.cli import main
from datalab.knowledge import check as kb
from tests.kb_fixtures import FITBIT, page, sample_kb


def rules(report: kb.Report, severity: str | None = None) -> set[tuple[str, str]]:
    return {(f.path, f.rule) for f in report.findings if severity in (None, f.severity)}


def with_files(**changes: str | bytes | None) -> dict[str, bytes]:
    files = sample_kb()
    for key, value in changes.items():
        path = key.replace("__", "/").replace("_DOT_", ".")
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value if isinstance(value, bytes) else value.encode()
    return files


def test_the_sample_knowledge_base_passes():
    report = kb.check(sample_kb())
    assert report.findings == []
    assert "- [fitbit](sources/fitbit.md): Fitbit trackers" in report.index
    assert "*(draft)*" in report.index


def test_front_matter_rules():
    bad = """---
id: steps
kind: qc
status: approved
summary: ""
evidence: []
limitations: []
cohorts: [25, "all"]
colour: blue
reviewed_on: yesterday
---
Body.
"""
    report = kb.check(with_files(features__steps_DOT_md=bad))
    found = {f.rule for f in report.findings if f.path == "features/steps.md"}
    assert {"kind", "status", "summary", "cohorts", "unknown_field", "reviewed_on"} <= found
    assert ("features/steps.md", "id") not in rules(report)  # id matches the file
    missing = kb.check(with_files(features__steps_DOT_md="no front matter\n"))
    assert ("features/steps.md", "front_matter") in rules(missing, "error")
    broken = kb.check(with_files(features__steps_DOT_md="---\nid: [\n---\n"))
    assert ("features/steps.md", "front_matter") in rules(broken, "error")


def test_a_reviewed_page_needs_evidence_limitations_and_years():
    text = page("steps", "feature", "features", status="reviewed")
    text = text.replace("  - legacy: reference/steps.R\n", "").replace(
        "evidence:\n", "evidence: []\n"
    )
    report = kb.check(with_files(features__steps_DOT_md=text))
    assert ("features/steps.md", "evidence") in rules(report, "error")
    assert ("features/steps.md", "reviewed_by") in rules(report, "warning")
    draft = kb.check(with_files(features__steps_DOT_md=text.replace("reviewed", "draft")))
    assert ("features/steps.md", "evidence") in rules(draft, "warning")


def test_ids_match_file_names_and_are_unique_ignoring_case():
    report = kb.check(with_files(qc__Other_DOT_md=page("fitbit", "qc", "qc")))
    assert ("qc/Other.md", "id") in rules(report, "error")
    twin = kb.check(with_files(qc__FITBIT_DOT_md=page("FITBIT", "qc", "qc")))
    assert ("qc/FITBIT.md", "duplicate_id") in rules(twin, "error")
    assert ("sources/fitbit.md", "duplicate_id") in rules(twin, "error")


def test_links_evidence_and_schema_references_must_resolve():
    text = FITBIT.replace("related: [qc/midnight-sleep]", "related: [qc/nowhere]")
    text = text.replace("TRACKERSTEPS\nlimitations", "TRACKERSTEP\nlimitations")
    text += "\n[gone](../qc/gone.md) and IHS_2025.NOSUCHVIEW and IHS_2025.VFITBITDAILYDATA.X.\n"
    text += "Fine: [web](https://example.org), IHS_2019.ANYTHING, `ihs_2025.vfitbitdailydata.md`.\n"
    report = kb.check(with_files(sources__fitbit_DOT_md=text))
    errors = [f.message for f in report.findings if f.severity == "error"]
    assert any("qc/nowhere" in m for m in errors)
    assert any("IHS_2025.VFITBITDAILYDATA.TRACKERSTEP isn't a column" in m for m in errors)
    assert any("../qc/gone.md" in m for m in errors)
    assert any("NOSUCHVIEW" in m for m in errors)
    assert any("VFITBITDAILYDATA.X isn't a column" in m for m in errors)
    assert not any("vfitbitdailydata.md" in m.lower() for m in errors)
    assert any("IHS_2019" in f.message for f in report.warnings)
    evidence = """  - code: ihs-pipelines ihsDataR/R/x.R
  - paper: not-a-doi
  - query: queries/missing
  - hunch: the model said so
"""
    report = kb.check(
        with_files(
            qc__midnight__sleep_DOT_md=None,
            qc__x_DOT_md=page("x", "qc", "qc").replace("  - legacy: reference/x.R\n", evidence),
        )
    )
    messages = [f.message for f in report.findings if f.path == "qc/x.md"]
    assert (
        sum("evidence" in m.lower() or "should be" in m or "no such page" in m for m in messages)
        >= 4
    )


def test_table_pages_must_name_a_table_in_the_schema():
    ok = kb.check(
        with_files(
            tables__IHS_2025_DOT_VFITBITDAILYDATA_DOT_md=page(
                "IHS_2025.VFITBITDAILYDATA", "table", "tables"
            )
        )
    )
    assert ok.errors == []
    gone = kb.check(
        with_files(tables__IHS_2025_DOT_DROPPED_DOT_md=page("IHS_2025.DROPPED", "table", "tables"))
    )
    assert ("tables/IHS_2025.DROPPED.md", "schema") in rules(gone, "error")


def test_the_layout_and_file_rules():
    report = kb.check(
        with_files(
            notes_DOT_txt="x",
            sources__deep__page_DOT_md="x",
            qc__image_DOT_md=b"\x89PNG\x00\x00",
            qc__huge_DOT_md=b"a" * (kb.MAX_TEXT_BYTES + 1),
        ),
        others=["sources/link.md"],
    )
    assert {
        ("notes.txt", "layout"),
        ("sources/deep/page.md", "layout"),
        ("qc/image.md", "binary"),
        ("qc/huge.md", "too_large"),
        ("sources/link.md", "not_a_file"),
    } <= rules(report, "error")
    assert kb.place(".github/workflows/kb-check.yml") == "github"
    assert kb.place("skills/sleep-qc/scripts/check.R") == "skill_file"
    assert kb.proposal_problem("index.md") and kb.proposal_problem("generated/drift.md")
    assert kb.proposal_problem(".github/workflows/x.yml") and kb.proposal_problem("x.txt")
    assert kb.proposal_problem("qc/new.md") is None


def test_generated_schema_is_metadata_only():
    leaky = sample_kb()["generated/schema/IHS_2025/VFITBITDAILYDATA.yml"].decode()
    leaky += "sample_values: [SYN001, SYN002]\n"
    report = kb.check(with_files(generated__schema__IHS_2025__VFITBITDAILYDATA_DOT_yml=leaky))
    assert any("sample_values" in f.message for f in report.errors)
    renamed = kb.check(with_files(generated__schema__IHS_2025__OTHER_DOT_yml=leaky))
    assert any("match its folder" in f.message for f in renamed.errors)


def test_lab_skills_need_a_name_and_a_description():
    def with_skill(text: str) -> dict[str, bytes]:
        return {**sample_kb(), "skills/sleep-qc/SKILL.md": text.encode()}

    good = "---\nname: sleep-qc\ndescription: Use when cleaning sleep data.\n---\nSteps.\n"
    report = kb.check(with_skill(good))
    assert report.errors == []
    assert "- [sleep-qc](skills/sleep-qc/SKILL.md): Use when cleaning sleep data." in report.index
    bad = kb.check(with_skill("---\nname: other\n---\nSteps.\n"))
    assert {
        ("skills/sleep-qc/SKILL.md", "skill_name"),
        ("skills/sleep-qc/SKILL.md", "skill_description"),
    } <= rules(bad, "error")


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("Participant SYN001 wore it on 3 nights.", "study_id"),
        ("SYN001 2025-03-14 slept 7.2 hours", "date_near_id"),
        ("id 48213377 dropped out", "study_id"),
        ("Contact jane.doe@umich.edu for the file.", "email"),
        ("Values: 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987", "numeric_list"),
    ],
)
def test_participant_data_heuristics(line, rule):
    found = kb.data_findings("qc/x.md", f"Intro.\n{line}\n")
    assert [(f.rule, f.line, f.severity) for f in found] == [(rule, 2, "data")]


def test_things_that_look_like_ids_but_arent():
    text = """IHS_2025 and IHS_2024.VFITBITDAILYDATA.TRACKERSTEPS, FY2024, SHA256.
See https://doi.org/10.1038/s41746-021-00400-z and 10.1000/xyz123456.
code: ihs-pipelines@a1b2c3d4 ihsDataR/R/steps.R and reference/x.R#L120-188.
About 5% of 1,234 nights (2024-2025) were missing; PHQ9 and GAD7 scores.
Written by 42+yfang@users.noreply.github.com.
"""
    assert kb.data_findings("qc/x.md", text) == []


def test_a_pasted_table_of_values_is_flagged_but_a_column_table_isnt():
    values = "| id | date | steps |\n|---|---|---|\n" + "".join(
        f"| {n} | 2025-01-0{n} | {n * 1000} |\n" for n in range(1, 7)
    )
    [found] = [f for f in kb.data_findings("qc/x.md", values) if f.rule == "pasted_table"]
    assert found.line == 1
    columns = "| column | meaning |\n|---|---|\n" + "".join(
        f"| COL_{c} | What {c} means |\n" for c in "ABCDEFGH"
    )
    assert kb.data_findings("tables/x.md", columns) == []
    csv = "\n".join(f"{n},{n * 2},{n * 3},{n * 4}" for n in range(10, 17))
    assert any(f.rule == "pasted_table" for f in kb.data_findings("qc/x.md", csv))


def test_data_findings_block_until_confirmed_and_ids_survive_other_edits():
    text = page("x", "qc", "qc", body="Participant SYN001 is an example.")
    report = kb.check(with_files(qc__x_DOT_md=text), only={"qc/x.md"})
    [hit] = report.data
    assert report.blocking() == [hit]
    assert report.blocking({hit.id}) == []
    moved = text.replace("# x\n", "# x\n\nA new first paragraph.\n")
    [again] = kb.check(with_files(qc__x_DOT_md=moved), only={"qc/x.md"}).data
    assert again.id == hit.id and again.line != hit.line


def test_only_limits_findings_to_the_changed_files():
    files = with_files(qc__bad_DOT_md="no front matter")
    assert ("qc/bad.md", "front_matter") in rules(kb.check(files))
    assert kb.check(files, only={"sources/fitbit.md"}).findings == []


def test_review_fields_are_datalabs_to_set():
    old = FITBIT
    agent = FITBIT.replace("reviewed_by: yfang", "reviewed_by: codex").replace(
        "status: reviewed", "status: draft"
    )
    flags = kb.review_changes(old, agent)
    assert any("status: reviewed → draft" in f for f in flags)
    assert any("reviewed_by" in f for f in flags)
    kept = kb.keep_review_fields(agent, old)
    assert "reviewed_by: yfang" in kept and "codex" not in kept
    new_page = page("x", "qc", "qc").replace("status: draft\n", "status: draft\nreviewed_by: ai\n")
    assert "reviewed_by" not in kb.keep_review_fields(new_page, None)
    stamped = kb.stamp_review(FITBIT, "ataxali", datetime.date(2026, 9, 27))
    assert "reviewed_by: ataxali\n" in stamped and "reviewed_on: 2026-09-27\n" in stamped
    assert stamped.count("reviewed_by") == 1
    draft = page("x", "qc", "qc")
    assert kb.stamp_review(draft, "ataxali", datetime.date(2026, 9, 27)) == draft
    reviewed = draft.replace("status: draft", "status: reviewed")
    assert "reviewed_on: 2026-09-27" in kb.stamp_review(reviewed, "a", datetime.date(2026, 9, 27))


def test_the_command_line(tmp_path, capsys):
    for path, content in sample_kb().items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(content)
    assert main(["kb-check", str(tmp_path)]) == 0
    (tmp_path / "qc" / "x.md").write_text(page("x", "qc", "qc", body="See SYN001."))
    assert main(["kb-check", str(tmp_path)]) == 1  # a data hit, and index.md is stale
    assert main(["kb-check", str(tmp_path), "--allow-data-hits", "--format", "github"]) == 0
    out = capsys.readouterr().out
    assert "::warning file=qc/x.md,line=" in out and "possible participant data" in out
    assert main(["kb-check", str(tmp_path), "--fix", "--allow-data-hits"]) == 0
    assert "[x](qc/x.md)" in (tmp_path / "index.md").read_text()
    (tmp_path / "qc" / "link.md").symlink_to(tmp_path / "qc" / "x.md")
    assert main(["kb-check", str(tmp_path), "--allow-data-hits"]) == 1
    assert main(["kb-check", str(tmp_path / "nope")]) == 2


def test_the_github_workflow_template_runs_datalabs_published_check():
    from pathlib import Path

    import yaml

    template = Path(__file__).resolve().parents[2] / "kb" / "github-workflow.yml"
    workflow = yaml.safe_load(template.read_text())
    assert workflow["permissions"] == {"contents": "read"}
    [job] = workflow["jobs"].values()
    command = job["steps"][-1]["run"]
    assert "github.com/SripadaLab-UM/ihs_datalab@${DATALAB_REF}" in command
    assert "datalab kb-check . --format github" in command
    checkout = job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False
