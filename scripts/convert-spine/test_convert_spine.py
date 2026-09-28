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
                "implemented_in": "r/ihsDataR/R/feature_steps_day.R",
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
            {
                "id": "watch_filter",
                "name": "Watch filter",
                "applies_to": ["steps_day"],
                "condition": "Keep samples from the watch.",
                "source_anchor": "ODBC_connect_IHS2024-25.R (Watch)",
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
        (spine / "registry" / name).write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
    code = repo / "r/ihsDataR/R"
    code.mkdir(parents=True)
    (code / "feature_steps_day.R").write_text(
        "build_steps_day <- function() NULL\n", encoding="utf-8"
    )
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://github.com/lab/proto.git")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    # A change after the commit: the diary source's text isn't on record as reviewed.
    changed = dict(REGISTRY["sources.seed.yaml"])
    diary = dict(changed["data_sources"][1], known_limitations=["Edited later."])
    changed["data_sources"] = [changed["data_sources"][0], diary]
    (spine / "registry" / "sources.seed.yaml").write_text(
        yaml.safe_dump(changed, sort_keys=False), encoding="utf-8"
    )
    export = tmp_path / "ihs_oracle_metadata_20260926T060331Z"
    export.mkdir()
    for name, rows in EXPORT.items():
        with (export / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, HEADERS[name])
            writer.writeheader()
            writer.writerows(rows)
    return spine, export


def run(sources: tuple[Path, Path], out: Path) -> cs.Result:
    spine, export = sources
    return cs.convert(spine, export, out)


def meta(out: Path, path: str) -> dict:
    fields, _ = kb.front_matter((out / path).read_text(encoding="utf-8"))
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
    assert (out / "index.md").read_text(encoding="utf-8") == result.report.index


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
    assert "MoodDriver" in (out / "features/mood_day.md").read_text(encoding="utf-8")
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
        assert "reviewed_by" not in path.read_text(encoding="utf-8")


def test_leaves_out_participant_data(sources, tmp_path):
    out = tmp_path / "kb"
    result = run(sources, out)
    text = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*.md"))
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
    rule = (out / "qc/steps_positive.md").read_text(encoding="utf-8")
    assert "ORA-01843" not in rule and "Oracle error 01843" in rule
    threshold = (out / "qc/phq9_threshold.md").read_text(encoding="utf-8")
    assert "NOSUCHTABLE in IHS_2025 (not in generated/schema)" in threshold


def test_never_deletes_other_files(sources, tmp_path):
    out = tmp_path / "kb"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(sources, out)
    assert (out / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_review_outside_the_knowledge_base(sources, tmp_path):
    spine, export = sources
    out = tmp_path / "kb"
    args = ["--spine", str(spine), "--export", str(export), "--out", str(out),
            "--ref", "HEAD", "--no-decisions"]  # fmt: skip
    with pytest.raises(SystemExit):
        cs.main([*args, "--review", str(out / "REVIEW.md")])
    code = cs.main([*args, "--review", str(tmp_path / "REVIEW.md")])
    assert code == 0
    assert "## Status changes" in (tmp_path / "REVIEW.md").read_text(encoding="utf-8")


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
    page = (tmp_path / "kb/sources/fitbit.md").read_text(encoding="utf-8")
    assert "Daily summaries only." in page


def test_relabels_suffixes(sources, tmp_path):
    out = tmp_path / "kb"
    run(sources, out)
    page = (out / "sources/fitbit.md").read_text(encoding="utf-8")
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
    assert "Edited later." not in (tmp_path / "kb-head/sources/diary.md").read_text(
        encoding="utf-8"
    )
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
    text = (out / reviewed[0]).read_text(encoding="utf-8")
    assert kb.stamp_review(text, "ataxali", cs.datetime.date(2026, 10, 1)) == text
    with pytest.raises(SystemExit):
        cs.main(["--stamp-reviewer", "not a login", "--out", str(out)])


# The reviewer's decisions ---------------------------------------------------


@pytest.fixture
def pipelines(tmp_path: Path) -> tuple[Path, str]:
    """An ihs-pipelines checkout whose ihsDataR/ is the prototype's r/ihsDataR."""
    repo = tmp_path / "pipes"
    (repo / "ihsDataR/R").mkdir(parents=True)
    (repo / "ihsDataR/R/feature_steps_day.R").write_text(
        "build_steps_day <- function() NULL\n", encoding="utf-8"
    )
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ihsDataR")
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    return repo, commit


def decisions_yaml(commit: str, **changes: object) -> dict:
    data: dict = {
        "reviewer": "ataxali",
        "decided_on": "2026-09-27",
        "papers": [
            {
                "doi": "10.1234/mood.2026",
                "names": ["MoodDriver"],
                "label": "MoodDriver",
                "title": "A study of mood and wearables",
                "venue": "medRxiv (preprint)",
                "year": 2026,
                "preprint": True,
                "cohorts": [2018, 2019],
                "cohorts_from": "Methods",
                "verified": ["https://doi.org/10.1234/mood.2026"],
                "summary": "Mood and wearable features.",
            },
            {
                "doi": "10.1038/s41746-021-00400-z",
                "title": "Sleep variability and depression",
                "venue": "npj Digital Medicine",
                "year": 2021,
                "cohorts": [2017, 2018],
                "cohorts_from": "Methods",
                "verified": ["https://doi.org/10.1038/s41746-021-00400-z"],
                "summary": "Sleep variability.",
            },
        ],
        "code_repin": {
            "from_prefix": "r/ihsDataR/",
            "repo": "pipes",
            "commit": commit,
            "prefix": "ihsDataR/",
            "source": "same tree",
        },
        "legacy_scripts": {"ODBC_connect_IHS2024-25.R": {"seen": False, "where": "nowhere"}},
        "removed_code_names": {"question": 4, "source": "test", "names": ["read_fitbit_daily"]},
        "entries": [
            {
                "entry": "raw_variables/fitbit.MODIFIEDDATE",
                "question": 9,
                "exclude": "a duplicate",
                "source": "the registry",
            },
            {
                "entry": "data_sources/fitbit",
                "question": 7,
                "replace": [
                    {"field": "known_limitations[0]", "old": "Daily summaries only", "new": "Days"}
                ],
                "release_hold": True,
                "source": "the catalog",
            },
        ],
        "pages": [
            {
                "page": "qc/steps_positive",
                "question": 1,
                "cohorts": [2024, 2025],
                "evidence": [{"code": "ihsDataR/R/feature_steps_day.R#L1"}],
                "source": "legacy and code",
            },
            {
                "page": "qc/phq9_threshold",
                "question": 2,
                "hold": "part of it comes from a draft",
                "source": "the paper",
            },
        ],
        "answers": [{"question": 1, "answer": "Confirmed.", "evidence": "the scripts"}],
        "still_open": ["Push the pipelines repo first."],
    }
    data.update(changes)
    return data


def load(tmp_path: Path, data: dict) -> cs.Decisions:
    path = tmp_path / "review_decisions.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return cs.load_decisions(path)


@pytest.fixture
def held(monkeypatch):
    monkeypatch.setitem(cs.HOLDS, ("data_sources", "fitbit"), "its limitation is stale")


def test_applies_the_decisions(sources, pipelines, tmp_path, held):
    spine, export = sources
    repo, commit = pipelines
    decisions = load(tmp_path, decisions_yaml(commit))
    out = tmp_path / "kb"
    result = cs.convert(spine, export, out, "HEAD", decisions, repo)
    assert result.report.errors == [] and result.report.data == []
    # A manuscript named in the Spine, given its DOI, is paper evidence, with its cohorts.
    mood = meta(out, "features/mood_day.md")
    assert {"paper": "10.1234/mood.2026"} in mood["evidence"]
    assert mood["cohorts"] == [2018, 2019] and mood["status"] == "reviewed"
    assert any("preprint" in x for x in mood["limitations"])
    assert "A study of mood and wearables" in (out / "papers/mooddriver.md").read_text(
        encoding="utf-8"
    )
    assert meta(out, "papers/mooddriver.md")["status"] == "draft"
    # Code is re-pinned to ihs-pipelines.
    steps = meta(out, "features/steps_day.md")
    assert {"code": f"pipes@{commit} ihsDataR/R/feature_steps_day.R"} in steps["evidence"]
    rule = meta(out, "qc/steps_positive.md")
    assert {"code": f"pipes@{commit} ihsDataR/R/feature_steps_day.R#L1"} in rule["evidence"]
    assert rule["cohorts"] == [2024, 2025] and rule["status"] == "reviewed"
    # A legacy script nobody has seen isn't enough.
    watch = result.converter.pages["qc/watch_filter"]
    assert watch.status == "draft" and any("question 5" in h for h in watch.holds)
    # The reviewer's hold, and the paper's cohorts on a page it holds.
    threshold = result.converter.pages["qc/phq9_threshold"]
    assert any("holds it as a draft" in h for h in threshold.holds)
    assert threshold.cohorts == {2017, 2018}
    # A text fix and a released hold; a removed runner is said to be gone.
    fitbit = meta(out, "sources/fitbit.md")
    assert fitbit["status"] == "reviewed"
    assert "Days." in fitbit["limitations"]
    assert any("no longer in ihsDataR (read_fitbit_daily)" in x for x in fitbit["limitations"])
    # The candidate duplicate is left out, so its table page can be reviewed.
    assert meta(out, "tables/IHS_2025.FITBITDAILYDATA.md")["status"] == "reviewed"
    review = cs.review_md(result)
    answered = review.split("## Reviewer questions: answered")[1].split("## Inputs")[0]
    assert "`features/mood_day`" in answered.split("Draft to reviewed:")[1].split("\n")[0]
    assert "`qc/watch_filter`" in answered.split("Reviewed to draft:")[1].split("\n")[0]
    assert "- **Answer.** Confirmed." in answered
    assert "left out by the reviewer (question 9)" in review
    assert "## Still open" in review and "## Questions for the reviewer" not in review
    # Deterministic, and it survives the install stamp.
    again = cs.convert(spine, export, tmp_path / "kb2", "HEAD", decisions, repo)
    assert kb.read_folder(out)[0] == kb.read_folder(tmp_path / "kb2")[0]
    assert cs.review_md(again) == review
    stamped, report = cs.stamp(out, "ataxali", cs.datetime.date(2026, 10, 1))
    assert stamped and report.blocking() == []


def test_without_the_hold_released(sources, pipelines, tmp_path, held):
    spine, export = sources
    repo, commit = pipelines
    data = decisions_yaml(commit)
    del data["entries"][1]["release_hold"]
    out = tmp_path / "kb"
    cs.convert(spine, export, out, "HEAD", load(tmp_path, data), repo)
    assert meta(out, "sources/fitbit.md")["status"] == "draft"


@pytest.mark.parametrize(
    "change",
    [
        # The text to replace isn't there.
        lambda d: d["entries"][1]["replace"][0].update(old="not in the Spine"),
        # There's no hold to release.
        lambda d: d["entries"][1].update(entry="data_sources/diary"),
        # No such entry, or page.
        lambda d: d["entries"][0].update(entry="raw_variables/nope"),
        lambda d: d["pages"][0].update(page="qc/nope"),
        # Code evidence that isn't at the pinned commit.
        lambda d: d["pages"][0].update(evidence=[{"code": "ihsDataR/R/nope.R"}]),
        # A name said to be removed that is still there.
        lambda d: d["removed_code_names"].update(names=["build_steps_day"]),
        # A pinned commit that isn't in the checkout.
        lambda d: d["code_repin"].update(commit="0" * 40),
    ],
)
def test_stale_decisions_stop_the_run(sources, pipelines, tmp_path, held, change):
    spine, export = sources
    repo, commit = pipelines
    data = decisions_yaml(commit)
    change(data)
    with pytest.raises(SystemExit):
        cs.convert(spine, export, tmp_path / "kb", "HEAD", load(tmp_path, data), repo)


def test_decisions_need_the_pipelines_checkout(sources, pipelines, tmp_path, held):
    spine, export = sources
    _, commit = pipelines
    with pytest.raises(SystemExit):
        cs.convert(spine, export, tmp_path / "kb", "HEAD", load(tmp_path, decisions_yaml(commit)))


def test_decisions_cant_mark_reviewed_or_hold_data(pipelines, tmp_path):
    _, commit = pipelines
    data = decisions_yaml(commit)
    data["pages"][0]["status"] = "reviewed"
    with pytest.raises(SystemExit):
        load(tmp_path, data)
    data = decisions_yaml(commit, still_open=["Participant P12345 enrolled on 2025-07-01."])
    with pytest.raises(SystemExit):
        load(tmp_path, data)


def test_the_real_decisions_load():
    decisions = cs.load_decisions(cs.DECISIONS)
    assert decisions.alias("MoodDriver (RHR > 100 bpm excluded)") == "10.64898/2026.03.03.26347299"
    assert decisions.alias("Social Smartphone Manuscript") is None
    assert not decisions.legacy_seen("reference/2024/ODBC_connect_IHS2024-25.R")
    assert decisions.legacy_seen("reference/2024/AggregateDailyMetrics_2024.R#L1-2")
    assert {a["question"] for a in decisions.answers} == set(range(1, len(cs.QUESTIONS) + 1))
