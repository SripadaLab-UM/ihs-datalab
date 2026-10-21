"""Files grouped into datasets ("families") by name pattern.

A processed-data folder holds many files that are one dataset in parts: a
file per year, per wave, per participant. Replacing the parts that vary
(dates, years, other numbers and ID-like codes) with placeholders turns
`fitbit/2025/daily_SYN0042.csv` into `fitbit/{year}/daily_{id}.csv`, so
thousands of files read as a few hundred datasets. It also keeps
participant IDs out of every summary: a pattern never names one person.

A plain number is part of the name when it's the same in every file of
the dataset (the 9 of `phq9`), so a dataset's name keeps it and only the
numbers that vary become `{n}`: `phq9_wave{n}_{year}.csv`. Dates, years
and ID-like codes are always placeholders, even in a dataset of one file.
A year is one from 2000 to next year; other four-digit numbers, and dates
outside those years, are plain numbers.

Names with no digits don't group by pattern (`jdoe.xlsx`, `asmith.xlsx`),
and may themselves name someone. So, at least three levels down, where a
folder holds `NAMES_ALIKE` or more files that group with nothing else and
share an extension and a shape (letters as `a`, digits as `9`), they are
one dataset, `{name}.xlsx`; fewer are more likely a folder's distinct
measures (`steps.csv`, `sleep.csv`). Likewise `FOLDERS_ALIKE` or more
sibling folders whose files are named alike (a folder per participant,
each with the same files) are one, `{name}/`. Higher up, names are the
lab's own (`2018 data`, `Fitbit`) and stay.

Code (`.py`, `.R`, `.sas`…) and caches (`__pycache__`, `.pyc`,
`.Rhistory`, Office lock files) belong to no dataset; they stay in the
file list, so the agent can still find and load them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from datalab.dropbox.sources import FileEntry

# One pass, earliest first; at one place a date wins over a year (its year is
# part of it), then a year, then a code with letters and 3+ digits (an ID),
# then any other run of digits. An ID's digits are never a date or a year:
# `steps_2024` is a year in a name, not a code.
_DATE = r"(?:19|20)\d{2}[-_.]?(?:0[1-9]|1[0-2])[-_.]?(?:0[1-9]|[12]\d|3[01])(?!\d)"
_YEAR = r"(?:19[89]\d|20[0-4]\d)(?!\d)"
_PARTS = re.compile(
    rf"(?P<date>(?<!\d){_DATE})"
    rf"|(?P<year>(?<!\d){_YEAR})"
    rf"|(?P<id>(?<![a-z0-9])[a-z]{{1,6}}[-_]?(?!{_DATE})(?!{_YEAR})\d{{3,}}(?![0-9]))"
    r"|(?P<n>\d+)"
)
FIRST_YEAR = 2000

# How many like siblings make one dataset of names that don't group.
NAMES_ALIKE = 10
FOLDERS_ALIKE = 5
# The segment (0-based) from which sibling names may be merged: a file in a
# folder two levels down (`2018 data/Surveys/x.xlsx` is segment 2), and a
# folder three levels down.
_FILES_FROM = 2
_FOLDERS_FROM = 2

_CLUTTER_FOLDERS = {"__pycache__", ".git", ".ipynb_checkpoints", ".rproj.user", ".svn"}
_CLUTTER_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".rhistory"}
_CLUTTER_EXTENSIONS = {
    # Caches and histories.
    ".pyc", ".pyo", ".history", ".rhistory", ".rproj",
    # Code: how data was made, not data.
    ".py", ".r", ".ipynb", ".sh", ".pbs", ".slurm", ".sas", ".do", ".m", ".jl",
}  # fmt: skip


def _years() -> range:
    return range(FIRST_YEAR, datetime.now(UTC).year + 2)


def _split(path: str) -> tuple[str, list[tuple[str, str]]]:
    """A path's pattern, and what each placeholder replaced, in order."""
    text = path.lower()
    parts: list[tuple[str, str]] = []
    years = _years()

    def placeholder(match: re.Match[str]) -> str:
        kind = match.lastgroup or "n"
        if kind in ("date", "year") and int(match.group(0)[:4]) not in years:
            kind = "n"
        parts.append((kind, match.group(0)))
        return "{" + kind + "}"

    return _PARTS.sub(placeholder, text), parts


def pattern(path: str) -> str:
    """The pattern a file's own name gives: its path with dates, years,
    ID-like codes and every number replaced, lower-cased. (Its dataset may
    be wider: see `families`.)"""
    return _split(path)[0]


def years_in(path: str) -> list[int]:
    _, parts = _split(path)
    return sorted({int(text[:4]) for kind, text in parts if kind in ("date", "year")})


def shape_of(name: str) -> str:
    """A name with its letters as `a` and digits as `9`, each run once; its
    extension as it is."""
    stem, extension = _stem(name)
    shaped = re.sub(r"\d+", "9", re.sub(r"[^\W\d_]+", "a", stem))
    return shaped + extension.lower()


def is_clutter(path: str) -> bool:
    """Code and caches that belong to no dataset."""
    segments = path.lower().split("/")
    name = segments[-1]
    return (
        any(s in _CLUTTER_FOLDERS for s in segments[:-1])
        or name in _CLUTTER_NAMES
        or name.startswith(("~$", ".~lock"))
        or _stem(name)[1] in _CLUTTER_EXTENSIONS
    )


def _stem(name: str) -> tuple[str, str]:
    dot = name.rfind(".")
    return (name[:dot], name[dot:]) if dot > 0 else (name, "")


@dataclass
class Family:
    # Its name: the pattern, with the numbers that never vary put back.
    pattern: str
    # The pattern every one of its files has, `{name}`s included.
    key: str = ""
    files: int = 0
    bytes: int = 0
    newest: str | None = None
    years: set[int] = field(default_factory=set)
    # The newest file, to load when the dataset's columns are wanted.
    example: str | None = None
    # Its files' paths.
    members: list[str] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return self.pattern.rsplit("/", 1)[0] if "/" in self.pattern else ""

    def summary(self) -> dict[str, object]:
        return {
            "dataset": self.pattern,
            "files": self.files,
            "size": self.bytes,
            "years": f"{min(self.years)}-{max(self.years)}"
            if len(self.years) > 1
            else (str(next(iter(self.years))) if self.years else None),
            "newest_modified": self.newest,
        }


@dataclass
class _File:
    entry: FileEntry
    # Each path segment's pattern, and the values of its `{n}`s.
    segments: list[str]
    numbers: list[list[str]]
    years: set[int]

    @classmethod
    def of(cls, entry: FileEntry) -> _File:
        segments, numbers, years = [], [], set()
        for segment in entry.path.split("/"):
            key, parts = _split(segment)
            segments.append(key)
            numbers.append([text for kind, text in parts if kind == "n"])
            years.update(int(text[:4]) for kind, text in parts if kind in ("date", "year"))
        return cls(entry, segments, numbers, years)

    @property
    def key(self) -> str:
        return "/".join(self.segments)


def families(entries: Iterable[FileEntry]) -> list[Family]:
    """Datasets in name order. Code and caches are left out."""
    files = [_File.of(e) for e in entries if not is_clutter(e.path)]
    _merge_names(files)
    _merge_folders(files)
    _merge_names(files)  # files that now share a merged folder
    found: dict[str, Family] = {}
    # For each dataset, the value each plain number had, while it's the same
    # in every file so far (None once it has varied).
    numbers: dict[str, list[str | None]] = {}
    for file in files:
        key = file.key
        values = [value for segment in file.numbers for value in segment]
        family = found.get(key)
        if family is None:
            family = found[key] = Family(key, key)
            numbers[key] = list(values)
        else:
            numbers[key] = [
                seen if seen == value else None
                for seen, value in zip(numbers[key], values, strict=True)
            ]
        entry = file.entry
        family.files += 1
        family.bytes += entry.size
        family.years |= file.years
        family.members.append(entry.path)
        if family.newest is None or (entry.modified or "") > family.newest:
            family.newest = entry.modified
            family.example = entry.path
        if family.example is None:
            family.example = entry.path
    for key, family in found.items():
        family.pattern = _named(key, numbers[key])
    return sorted(found.values(), key=lambda f: f.pattern)


def _merge_names(files: list[_File]) -> None:
    """In each folder deep enough, where `NAMES_ALIKE` or more files of one
    extension and shape are each in no dataset with another, every file of
    that shape there becomes `{name}.ext`: a name that recurs (the same
    person's file in two years) is still a name."""
    alike: dict[tuple[str, str, str], dict[str, list[_File]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for file in files:
        if len(file.segments) - 1 < _FILES_FROM:
            continue
        stem, extension = _stem(file.segments[-1])
        folder = "/".join(file.segments[:-1])
        alike[(folder, extension, shape_of(stem))][stem].append(file)
    for by_name in alike.values():
        if sum(1 for same in by_name.values() if len(same) == 1) < NAMES_ALIKE:
            continue
        for file in (file for same in by_name.values() for file in same):
            extension = _stem(file.segments[-1])[1]
            file.segments[-1] = "{name}" + extension
            # Only the extension's numbers are left in this segment.
            kept = extension.count("{n}")
            file.numbers[-1] = file.numbers[-1][len(file.numbers[-1]) - kept :]


def _merge_folders(files: list[_File]) -> None:
    """Deepest first: `FOLDERS_ALIKE` or more sibling folders, deep enough, whose
    files are named alike below them become `{name}`."""
    deepest = max((len(f.segments) for f in files), default=0) - 2
    for level in range(deepest, _FOLDERS_FROM - 1, -1):
        siblings: dict[str, dict[str, list[_File]]] = defaultdict(lambda: defaultdict(list))
        for file in files:
            if len(file.segments) - 1 > level:
                parent = "/".join(file.segments[:level])
                siblings[parent][file.segments[level]].append(file)
        for by_folder in siblings.values():
            if len(by_folder) < FOLDERS_ALIKE:
                continue
            contents = {
                name: frozenset("/".join(f.segments[level + 1 :]) for f in inside)
                for name, inside in by_folder.items()
            }
            alike = Counter(contents.values())
            for name, inside in by_folder.items():
                if alike[contents[name]] >= FOLDERS_ALIKE:
                    for file in inside:
                        file.segments[level] = "{name}"
                        file.numbers[level] = []


def _named(key: str, constants: list[str | None]) -> str:
    """The pattern with each `{n}` that never varied replaced by its number."""
    kept = iter(constants)
    return re.sub(r"\{n\}", lambda _: next(kept) or "{n}", key)


def dataset_of(path: str, entries: Iterable[FileEntry]) -> str:
    """The name of the dataset `path` belongs to, among `entries` (its own
    pattern when it's in none: code and caches)."""
    for family in families(entries):
        if path in family.members:
            return family.pattern
    return pattern(path)
