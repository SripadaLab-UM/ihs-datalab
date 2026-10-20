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
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

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


def _split(path: str) -> tuple[str, list[tuple[str, str]]]:
    """A path's pattern, and what each placeholder replaced, in order."""
    text = path.lower()
    parts: list[tuple[str, str]] = []

    def placeholder(match: re.Match[str]) -> str:
        kind = match.lastgroup or "n"
        parts.append((kind, match.group(0)))
        return "{" + kind + "}"

    return _PARTS.sub(placeholder, text), parts


def pattern(path: str) -> str:
    """The pattern a file is grouped by: its path with dates, years, ID-like
    codes and every number replaced, lower-cased."""
    return _split(path)[0]


def years_in(path: str) -> list[int]:
    _, parts = _split(path)
    return sorted({int(text[:4]) for kind, text in parts if kind in ("date", "year")})


@dataclass
class Family:
    # Its name: the pattern, with the numbers that never vary put back.
    pattern: str
    # The pattern every one of its files has (pattern() of each path).
    key: str = ""
    files: int = 0
    bytes: int = 0
    newest: str | None = None
    years: set[int] = field(default_factory=set)
    # The newest file, to load when the dataset's columns are wanted.
    example: str | None = None

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


def families(entries: Iterable[FileEntry]) -> list[Family]:
    """Datasets in name order."""
    found: dict[str, Family] = {}
    # For each dataset, the value each plain number had, while it's the same
    # in every file so far (None once it has varied).
    numbers: dict[str, list[str | None]] = {}
    for entry in entries:
        key, parts = _split(entry.path)
        values = [text for kind, text in parts if kind == "n"]
        family = found.get(key)
        if family is None:
            family = found[key] = Family(key, key)
            numbers[key] = list(values)
        else:
            numbers[key] = [
                seen if seen == value else None
                for seen, value in zip(numbers[key], values, strict=True)
            ]
        family.files += 1
        family.bytes += entry.size
        family.years.update(int(t[:4]) for kind, t in parts if kind in ("date", "year"))
        if family.newest is None or (entry.modified or "") > family.newest:
            family.newest = entry.modified
            family.example = entry.path
    for key, family in found.items():
        family.pattern = _named(key, numbers[key])
    return sorted(found.values(), key=lambda f: f.pattern)


def _named(key: str, constants: list[str | None]) -> str:
    """The pattern with each `{n}` that never varied replaced by its number."""
    kept = iter(constants)
    return re.sub(r"\{n\}", lambda _: next(kept) or "{n}", key)


def dataset_of(path: str, entries: Iterable[FileEntry]) -> str:
    """The name of the dataset `path` belongs to, among `entries`."""
    key = pattern(path)
    for family in families(e for e in entries if pattern(e.path) == key):
        return family.pattern
    return key
