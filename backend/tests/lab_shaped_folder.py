"""A made-up file list shaped like the lab's processed-data folder.

Nothing here is real: names are random letters from a seeded generator.
What it copies from `datalab processed shape` on the real folder (counts
only) is its *shape*:
- year folders, plus archive, project, dictionary and temporary folders;
- most files are .xlsx, many with letters-only names, hundreds to a folder;
- per-participant folders that each hold the same files;
- IDs, dates and 8-digit numbers that look like dates in far-off years;
- code and caches (.py, .R, __pycache__, .Rhistory) among the data.

`expected` says what grouping v2 should make of it.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from datalab.dropbox.sources import FileEntry


@dataclass
class LabShaped:
    entries: list[FileEntry]
    # Made-up names that stand for people: no dataset may show one.
    person_names: set[str] = field(default_factory=set)
    # Datasets that must stay as they are, by pattern.
    kept: set[str] = field(default_factory=set)
    # Paths that belong to no dataset.
    clutter: set[str] = field(default_factory=set)


def lab_shaped_folder(seed: int = 7) -> LabShaped:
    rng = random.Random(seed)
    out = LabShaped([])
    count = 0

    def add(path: str, size: int = 50_000) -> None:
        nonlocal count
        count += 1
        out.entries.append(
            FileEntry(path, f"id:{count}", size, f"2025-01-{count % 28 + 1:02d}T00:00:00Z")
        )

    def person() -> str:
        # Letters only, some two words, some capitalised: like a name or a code.
        first = "".join(rng.choices(string.ascii_lowercase, k=rng.randint(4, 8)))
        if rng.random() < 0.3:
            first += "_" + "".join(rng.choices(string.ascii_lowercase, k=rng.randint(4, 8)))
        name = first.capitalize() if rng.random() < 0.5 else first
        out.person_names.add(name.lower())
        return name

    for year in range(2018, 2025):
        top = f"{year} data"
        # A folder's distinct measures, one file each: they stay apart.
        for measure in ("steps", "sleep", "mood", "heart_rate", "phq9"):
            add(f"{top}/Fitbit/{measure}_{year}.csv", 4_000_000)
        # Hundreds of letters-only .xlsx in one folder: one dataset.
        for _ in range(rng.randint(150, 400)):
            add(f"{top}/Surveys/Individual responses/{person()}.xlsx", 30_000)
        # IDs that already group.
        for n in range(30):
            add(f"{top}/Mood/SYN{n:04d}_daily_mood.csv", 20_000)
        # A folder per participant, each holding the same files.
        for _ in range(12):
            folder = f"{top}/Wearables/{person()}"
            add(f"{folder}/sleep.csv")
            add(f"{folder}/steps.csv")
        # 8-digit numbers that look like far-off dates: plain numbers.
        for batch in ("20851231", "20980101", "20370615"):
            add(f"{top}/Exports/batch_{batch}.csv")
        add(f"{top}/Exports/README.txt", 2_000)
    out.kept |= {
        "{year} data/fitbit/steps_{year}.csv",
        "{year} data/fitbit/phq9_{year}.csv",
        "{year} data/mood/{id}_daily_mood.csv",
        "{year} data/surveys/individual responses/{name}.xlsx",
        "{year} data/wearables/{name}/sleep.csv",
        "{year} data/wearables/{name}/steps.csv",
        "{year} data/exports/batch_{n}.csv",
    }

    # The dictionary: distinct codebooks, near the top, stay apart.
    for book in ("variables", "cohorts", "surveys", "wearables", "derived", "glossary",
                 "missing_codes", "sites", "waves", "devices", "consent", "timeline"):  # fmt: skip
        add(f"Dictionary/{book}.xlsx", 200_000)
    out.kept |= {"dictionary/variables.xlsx", "dictionary/timeline.xlsx"}

    # Projects with code, caches and outputs.
    for project in ("Alpha", "Bravo", "Charlie"):
        base = f"Projects/{project} project"
        for script in ("clean", "model", "plots"):
            add(f"{base}/code/{script}.py", 4_000)
            add(f"{base}/code/__pycache__/{script}.cpython-311.pyc", 4_000)
            add(f"{base}/code/{script}.R", 4_000)
            out.clutter |= {
                f"{base}/code/{script}.py",
                f"{base}/code/__pycache__/{script}.cpython-311.pyc",
                f"{base}/code/{script}.R",
            }
        add(f"{base}/.Rhistory", 1_000)
        add(f"{base}/outputs/~$results.xlsx", 165)
        out.clutter |= {f"{base}/.Rhistory", f"{base}/outputs/~$results.xlsx"}
        add(f"{base}/outputs/results.xlsx", 80_000)
        add(f"{base}/outputs/model_fit.RData", 900_000_000)

    # Temporary folders shared with collaborators: kept as named.
    for team in ("Team Red", "Team Blue", "Team Green", "Team Gold", "Team Grey", "Team Teal"):
        add(f"Temp folder for {team}/extract.csv", 10_000_000)
    out.kept.add("temp folder for team red/extract.csv")

    add("Archived/old_cohort_2014.sas7bdat", 2_000_000_000)
    add("README.docx", 30_000)
    return out
