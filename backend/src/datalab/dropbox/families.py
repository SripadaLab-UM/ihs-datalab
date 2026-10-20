"""Files grouped into datasets ("families") by name pattern.

A processed-data folder holds many files that are one dataset in parts: a
file per year, per wave, per participant. Replacing the parts that vary
(dates, years, other numbers and ID-like codes) with placeholders turns
`fitbit/2025/daily_SYN0042.csv` into `fitbit/{year}/daily_{id}.csv`, so
thousands of files read as a few hundred datasets. It also keeps
participant IDs out of every summary: a pattern never names one person.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from datalab.dropbox.sources import FileEntry

# In this order: a date first (its year is part of it), then a year, then a
# code with letters and 3+ digits (an ID), then any other run of digits.
_DATE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[-_.]?(?:0[1-9]|1[0-2])[-_.]?(?:0[1-9]|[12]\d|3[01])(?!\d)"
)
_YEAR = re.compile(r"(?<!\d)(?:19[89]\d|20[0-4]\d)(?!\d)")
_ID = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,6}[-_]?\d{3,}(?![0-9])")
_NUMBER = re.compile(r"\d+")


def pattern(path: str) -> str:
    """The dataset a file belongs to: its path with dates, years, ID-like
    codes and other numbers replaced, lower-cased."""
    text = path.lower()
    text = _DATE.sub("{date}", text)
    text = _YEAR.sub("{year}", text)
    text = _ID.sub("{id}", text)
    return _NUMBER.sub("{n}", text)


def years_in(path: str) -> list[int]:
    return sorted(
        {int(m.group(0)[:4]) for m in _DATE.finditer(path)}
        | {int(m.group(0)) for m in _YEAR.finditer(path)}
    )


@dataclass
class Family:
    pattern: str
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
    """Datasets in pattern order."""
    found: dict[str, Family] = {}
    for entry in entries:
        key = pattern(entry.path)
        family = found.get(key)
        if family is None:
            family = found[key] = Family(key)
        family.files += 1
        family.bytes += entry.size
        family.years.update(years_in(entry.path))
        if family.newest is None or (entry.modified or "") > family.newest:
            family.newest = entry.modified
            family.example = entry.path
    return [found[k] for k in sorted(found)]
