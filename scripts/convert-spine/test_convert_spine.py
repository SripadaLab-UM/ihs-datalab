"""Tests for the Spine conversion, on a small synthetic Spine and export.

cd backend && uv run pytest -q ../scripts/convert-spine
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from datalab.knowledge import check as kb

sys.path.insert(0, str(Path(__file__).parent))

import convert_spine as cs  # pyright: ignore[reportMissingImports]

REGISTRY = {
    "sources.seed.yaml": {
        "data_sources": [
            {
                "id": "fitbit",
                "name": "Fitbit",
                "modality": "wearable",
                "platform": "Fitbit",
                "available_cohorts": ["IHS_2024", "IHS_2025"],
                "known_limitations": [
                    "Daily summaries only (41 rows; 40 non-null PARTICIPANTID).",
                    "Observed coverage from the profile starts in April; January returns no rows.",
                ],
                "extra": {
                    "nap_validation_counts": {"AUTO": 17, "DEVICE": 4},
                    "zero_row_query_id": "oracle_query_" + "ab" * 16,
                    "package_reader": "read_fitbit_daily",
                    "followup_suffix_observed_2026": {"JuneSurvey": "4"},
                },
                "status": "validated",
            },
            {
                "id": "diary",
                "name": "Diary app",
                "modality": "smartphone_app",
                "platform": "Diary",
                "available_cohorts": ["IHS_2025"],
                "source_anchor": "Diary manuscript",
                "status": "validated",
            },
        ]
    },
    "steps.seed.yaml": {
        "raw_variables": [
            {
                "id": "fitbit.TRACKERSTEPS",
                "data_source": "fitbit",
                "oracle_object": "FITBITDAILYDATA",
                "oracle_column": "TRACKERSTEPS",
                "description": "Tracker steps (12/40 populated).",
                "maps_to_feature": "steps_day",
                "status": "validated",
            },
            {
                "id": "fitbit.MODIFIEDDATE",
                "data_source": "fitbit",
                "oracle_object": "IHS_2025.FITBITDAILYDATA",
                "oracle_column": "MODIFIEDDATE",
                "status": "candidate",
            },
        ],
        "canonical_features": [
            {
                "id": "steps_day",
                "description": "Daily steps.",
                "construct": "step_count",
                "recipe": "steps_day_v1",
                "source_variables": ["fitbit.TRACKERSTEPS"],
                "status": "validated",
            },
            {
                "id": "mood_day",
                "description": "Daily mood.",
                "source_anchor": "MoodDriver",
                "status": "validated",
            },
        ],
        "feature_recipes": [
            {
                "id": "steps_day_v1",
                "canonical_feature": "steps_day",
                "sources": ["fitbit"],
                "cleaning_rules": ["Keep steps > 0."],
                "qc_rules": ["steps_positive"],
                "source_anchor": "AggregateDailyMetrics_2024.R (steps section)",
                "status": "validated",
            }
        ],
        "constructs": [
            {"id": "step_count", "name": "Step count", "status": "validated"},
            {"id": "freshness", "name": "Freshness", "status": "candidate"},
        ],
        "qc_rules": [
            {
                "id": "steps_positive",
                "name": "Steps positive",
                "applies_to": ["steps_day"],
                "condition": "value > 0; a bad bind raises ORA-01843.",
                "source_anchor": "AggregateDailyMetrics_2024.R",
                "status": "validated",
            },
            {
                "id": "phq9_threshold",
                "name": "PHQ-9 threshold",
                "applies_to": ["mood_day"],
                "condition": "Score >= 10; see IHS_2025.NOSUCHTABLE.",
                "source_anchor": "Sleep paper 10.1038/s41746-021-00400-z",
                "status": "validated",
            },
        ],
    },
}

EXPORT = {
    "objects": [
        {"owner": s, "object_name": "FITBITDAILYDATA", "object_type": "TABLE"}
        for s in ("IHS_2024", "IHS_2025")
    ],
    "columns": [
        {
            "owner": s,
            "object_name": "FITBITDAILYDATA",
            "column_id": str(n),
            "column_name": c,
            "data_type": t,
            "data_length": "",
            "data_precision": "",
            "data_scale": "",
            "char_length": "",
            "nullable": "Y",
        }
        for s in ("IHS_2024", "IHS_2025")
        for n, (c, t) in enumerate(
            [("PARTICIPANTIDENTIFIER", "VARCHAR2"), ("TRACKERSTEPS", "NUMBER")]
            + ([("MODIFIEDDATE", "DATE")] if s == "IHS_2025" else []),
            start=1,
        )
    ],
    "table_comments": [],
    "column_comments": [],
    "constraints": [],
    "constraint_columns": [],
}
HEADERS = {
    "objects": ["owner", "object_name", "object_type"],
    "columns": [
        "owner", "object_name", "column_id", "column_name", "data_type", "data_length",
        "data_precision", "data_scale", "char_length", "nullable",
    ],
    "table_comments": ["owner", "object_name", "comments"],
    "column_comments": ["owner", "object_name", "column_name", "comments"],
    "constraints": ["owner", "constraint_name", "constraint_type", "table_name"],
    "constraint_columns": ["owner", "constraint_name", "table_name", "column_name", "position"],
}  # fmt: skip


def git(folder: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "proto"
    spine = repo / "spine"
    (spine / "registry").mkdir(parents=True)
    for name, data in REGISTRY.items():
        (spine / "registry" / name).write_text(yaml.safe_dump(data, sort_keys=False))
    code = repo / "r/ihsDataR/R"
    code.mkdir(parents=True)
    (code / "feature_steps_day.R").write_text("build_steps_day <- function() NULL\n")
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://github.com/lab/proto.git")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    # A change after the commit: the diary source's text isn't on record as reviewed.
    changed = dict(REGISTRY["sources.seed.yaml"])
    diary = dict(changed["data_sources"][1], known_limitations=["Edited later."])
    changed["data_sources"] = [changed["data_sources"][0], diary]
    (spine / "registry" / "sources.seed.yaml").write_text(yaml.safe_dump(changed, sort_keys=False))
    export = tmp_path / "ihs_oracle_metadata_20260926T060331Z"
    export.mkdir()
    for name, rows in EXPORT.items():
        with (export / f"{name}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, HEADERS[name])
            writer.writeheader()
            writer.writerows(rows)
    return spine, export


def run(sources: tuple[Path, Path], out: Path) -> cs.Result:
    spine, export = sources
    return cs.convert(spine, export, out)


def meta(out: Path, path: str) -> dict:
    fields, _ = kb.front_matter((out / path).read_text())
    assert fields is not None
    return fields


def test_passes_the_check(sources, tmp_path):
    out = tmp_path / "kb"
    result = run(sources, out)
    assert result.report.errors == []
    assert result.report.data == []
    files, others = kb.read_folder(out)
    assert kb.check(files, others=others).blocking() == []
    assert (out / "generated/schema/IHS_2025/FITBITDAILYDATA.yml").exists()
    assert (out / "index.md").read_text() == result.report.index


def test_is_deterministic(sources, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    run(sources, first)
    result = run(sources, second)
    files_a, _ = kb.read_folder(first)
    files_b, _ = kb.read_folder(second)
    assert files_a == files_b
    # And the report.
    assert cs.review_md(result) == cs.review_md(run(sources, first))


def test_statuses(sources, tmp_path):
    out = tmp_path / "kb"
    result = run(sources, out)
    steps = meta(out, "features/steps_day.md")
    assert steps["status"] == "reviewed"
    assert steps["cohorts"] == [2024, 2025]
    assert {"legacy": "reference/2024/AggregateDailyMetrics_2024.R"} in steps["evidence"]
    assert {"schema": "IHS_2024.FITBITDAILYDATA.TRACKERSTEPS"} in steps["evidence"]
    assert "reviewed_by" not in steps and "reviewed_on" not in steps
    # A manuscript without a DOI or path isn't evidence the check can verify.
    mood = meta(out, "features/mood_day.md")
    assert mood["status"] == "draft"
    assert "MoodDriver" in (out / "features/mood_day.md").read_text()
    # A candidate on the page makes it a draft.
    assert meta(out, "tables/IHS_2025.FITBITDAILYDATA.md")["status"] == "draft"
    # Changed since the Spine's last commit.
    diary = result.converter.pages["sources/diary"]
    assert diary.status == "draft"
    assert any("after the Spine's last commit" in h for h in diary.holds)
    # A paper's method gets no cohorts from the features it's applied to.
    assert meta(out, "qc/phq9_threshold.md")["cohorts"] == []
    assert meta(out, "qc/steps_positive.md")["cohorts"] == [2024, 2025]
    # Papers and the glossary are assembled, so drafts.
    assert meta(out, "papers/sleep-paper.md")["status"] == "draft"
    assert meta(out, "features/constructs.md")["status"] == "draft"
    for path in (out / "features").glob("*.md"):
        assert "reviewed_by" not in path.read_text()


def test_leaves_out_participant_data(sources, tmp_path):
    out = tmp_path / "kb"
    result = run(sources, out)
    text = "\n".join(p.read_text() for p in out.rglob("*.md"))
    for value in ("41 rows", "12/40", "AUTO", "January returns no rows", "oracle_query_"):
        assert value not in text
    assert "read_fitbit_daily" in text  # the rest of `extra` is kept
    where = {(r.entry, r.where) for r in result.converter.clean.privacy}
    assert ("DataSource `fitbit`", "extra.nap_validation_counts") in where
    assert ("DataSource `fitbit`", "known_limitations[1]") in where
    review = cs.review_md(result)
    assert "extra.nap_validation_counts" in review
    assert "17" not in review.split("## Left out for privacy")[1].split("##")[0]


def test_rewords_what_the_check_would_misread(sources, tmp_path):
    out = tmp_path / "kb"
    run(sources, out)
    rule = (out / "qc/steps_positive.md").read_text()
    assert "ORA-01843" not in rule and "Oracle error 01843" in rule
    threshold = (out / "qc/phq9_threshold.md").read_text()
    assert "NOSUCHTABLE in IHS_2025 (not in generated/schema)" in threshold


def test_never_deletes_other_files(sources, tmp_path):
    out = tmp_path / "kb"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    with pytest.raises(SystemExit):
        run(sources, out)
    assert (out / "notes.txt").read_text() == "mine"


def test_review_outside_the_knowledge_base(sources, tmp_path):
    spine, export = sources
    out = tmp_path / "kb"
    with pytest.raises(SystemExit):
        cs.main(["--spine", str(spine), "--export", str(export), "--out", str(out),
                 "--review", str(out / "REVIEW.md"), "--ref", "HEAD"])  # fmt: skip
    code = cs.main(["--spine", str(spine), "--export", str(export), "--out", str(out),
                    "--review", str(tmp_path / "REVIEW.md"), "--ref", "HEAD"])  # fmt: skip
    assert code == 0
    assert "## Status changes" in (tmp_path / "REVIEW.md").read_text()


def test_counts_each_entry_once(sources, tmp_path):
    result = run(sources, tmp_path / "kb")
    review = cs.review_md(result)
    entries = sum(len(es) for es in result.converter.spine.entities.values())
    per_entry = review.split("Per entry")[1].split("Per page")[0]
    assert f"- Total: {entries}" in per_entry
    # Entries are counted by the page they went to, not the paper pages that cite them.
    assert "papers/" not in per_entry


def test_says_what_was_cut_and_what_left(sources, tmp_path):
    result = run(sources, tmp_path / "kb")
    privacy = cs.review_md(result).split("## Left out for privacy")[1].split("## Prototype")[0]
    altogether, cut = privacy.split("Cut from a sentence")
    assert "known_limitations[0]" in cut and "known_limitations[0]" not in altogether
    assert "known_limitations[1]" in altogether
    # The limitation itself is still on the page, without its count.
    page = (tmp_path / "kb/sources/fitbit.md").read_text()
    assert "Daily summaries only." in page


def test_relabels_suffixes(sources, tmp_path):
    out = tmp_path / "kb"
    run(sources, out)
    page = (out / "sources/fitbit.md").read_text()
    assert "followup_suffix_observed_2026" not in page
    assert "`followup_item_suffix_by_survey`" in page
    assert "its result identifiers end in 4" in page


def test_decisions_and_the_committed_version(sources, tmp_path):
    spine, export = sources
    result = run(sources, tmp_path / "kb")
    review = cs.review_md(result)
    assert "`--working-copy`" in review.split("## Inputs")[0]
    assert "DataSource `diary`: known_limitations (added)" in review
    assert [p for p, _, _ in result.from_commit] == ["sources/diary.md"]
    # Built from the commit: nothing counts as changed, and the later edit isn't there.
    committed = cs.convert(spine, export, tmp_path / "kb-head", ref="HEAD")
    assert not any(
        e.changed for es in committed.converter.spine.entities.values() for e in es.values()
    )
    assert "Edited later." not in (tmp_path / "kb-head/sources/diary.md").read_text()
    assert committed.from_commit == []
    head = cs.review_md(committed)
    assert head.index("## Decided: converted from") < head.index("## Inputs")
    assert "Read as committed at `HEAD`" in head
    # What the working copy has that the commit doesn't is said, and left out.
    assert "DataSource `diary`: known_limitations (added)" in head


def test_marks_thin_cohorts(sources, tmp_path):
    result = run(sources, tmp_path / "kb")
    review = cs.review_md(result)
    section = review.split("### Where cohorts came from")[1].split("### Draft pages")[0]
    assert "`qc/steps_positive` [2024, 2025]: inherited [2024, 2025]. **Thin:**" in section
    assert "`features/steps_day` [2024, 2025]:" in section
    legacy = review.split("### How far the check verifies evidence")[1]
    assert "- `qc/steps_positive`" in legacy.split("Reviewed feature and QC")[0]


def test_stamps_the_reviewer_at_install(sources, tmp_path):
    spine, export = sources
    out = tmp_path / "kb"
    result = cs.convert(spine, export, out, ref="HEAD")
    assert not [f for f in result.report.errors + result.report.data]
    before = [f for f in result.report.warnings if f.rule == "reviewed_by"]
    reviewed = sorted(p.path for p in result.converter.pages.values() if p.status == "reviewed")
    assert reviewed and len(before) == len(reviewed)
    for page in result.converter.pages.values():  # nobody named before reviewing
        assert "reviewed_by" not in meta(out, page.path)
        assert "reviewed_on" not in meta(out, page.path)
    code = cs.main(["--stamp-reviewer", "ataxali", "--out", str(out), "--on", "2026-10-01"])
    assert code == 0
    for path in reviewed:
        fields = meta(out, path)
        assert fields["reviewed_by"] == "ataxali"
        assert str(fields["reviewed_on"]) == "2026-10-01"
    drafts = [p.path for p in result.converter.pages.values() if p.status == "draft"]
    assert all("reviewed_by" not in meta(out, p) for p in drafts)
    files, others = kb.read_folder(out)
    report = kb.check(files, others=others)
    assert report.blocking() == []
    assert not [f for f in report.findings if f.rule == "reviewed_by"]
    # Stamped exactly as DataLab's Save & share would stamp them.
    text = (out / reviewed[0]).read_text()
    assert kb.stamp_review(text, "ataxali", cs.datetime.date(2026, 10, 1)) == text
    with pytest.raises(SystemExit):
        cs.main(["--stamp-reviewer", "not a login", "--out", str(out)])
