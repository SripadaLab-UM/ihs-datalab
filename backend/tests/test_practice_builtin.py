"""Practice DataLab's built-in workflows are the lab's 8 routines, byte for byte.

They're copied from `ihs-pipelines` at 315ac98 (practice_builtin/README.md).
Each file's git blob id here is the one `git ls-tree 315ac98 workflows/`
gives there, so a change to either side shows here. To refresh them, see
docs/WORKFLOWS.md ("Practice's built-in workflows").
"""

from __future__ import annotations

from datalab.workflows.source import BUILTIN_DIR, git_blob_id

UPSTREAM_COMMIT = "315ac98"
UPSTREAM_BLOBS = {
    "fitbit_daily_2025.yaml": "952e9ce93fb7ee814f43bd7bc768a3bdab6db94e",
    "fitbit_sleep_logs_2025.yaml": "9a109206a89497e153822a52fda44a0bbb4134ee",
    "garmin_daily_summary_2025.yaml": "387547537a889c1874c2acd97fd8087f3ad4899e",
    "garmin_sleep_summary_2025.yaml": "876082597e3b9b7cd05ec1e874a591121789e85b",
    "healthkit_daily_steps_2025.yaml": "005a95c96730308e0a4d151dc75d99f6db13edee",
    "healthkit_sleep_intervals_2025.yaml": "f19c1c3416722a44e604b57f47d020196398911d",
    "mood_daily_2025.yaml": "215f60b978de6cab0ff78a691c4135adc272f7f0",
    "smoking_survey_responses_dropbox_testing.yaml": "5e0ebd9addcb0a5f20d673f5a372b4fcc007f91b",
}


def test_the_bundled_files_are_the_upstream_ones_byte_for_byte():
    bundled = {p.name: git_blob_id(p.read_bytes()) for p in BUILTIN_DIR.glob("*.yaml")}
    assert bundled == UPSTREAM_BLOBS


def test_only_the_eight_and_their_readme_are_bundled():
    names = {p.name for p in BUILTIN_DIR.iterdir() if not p.name.startswith(".")}
    assert names == {*UPSTREAM_BLOBS, "README.md"}
    readme = (BUILTIN_DIR / "README.md").read_text(encoding="utf-8")
    assert UPSTREAM_COMMIT in readme and "ihs-pipelines" in readme
