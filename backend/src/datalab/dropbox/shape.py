"""`datalab processed shape`: the chosen folder's shape, from DataLab's file
list, for sizing the schema catalog and the wiki.

It prints counts only: by top-level folder, file type, size band and year,
and how well files group into datasets. No file names and no dataset
patterns (one that didn't group could still hold an ID); where grouping
fails, file names appear only as their shape, letters as `a` and digits
as `9` (`Ab_12.csv` is `a_9.csv`).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from datalab.config import Settings
from datalab.dropbox.families import Family, families, years_in
from datalab.dropbox.sources import FileEntry

_MB = 1024 * 1024
_SIZE_BANDS = [
    ("under 1 MB", _MB),
    ("1-10 MB", 10 * _MB),
    ("10-100 MB", 100 * _MB),
    ("100 MB-1 GB", 1024 * _MB),
    ("1-10 GB", 10 * 1024 * _MB),
    ("10 GB and over", None),
]
# Upper limits are exclusive.
_DATASET_BANDS = [
    ("1 file", 2),
    ("2-10", 11),
    ("11-100", 101),
    ("101-1000", 1001),
    ("over 1000", None),
]


def shape_of(name: str) -> str:
    """A file name with its letters as `a` and digits as `9`, each run once;
    its extension as it is."""
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        stem, extension = name, ""
    shaped = re.sub(r"\d+", "9", re.sub(r"[^\W\d_]+", "a", stem))
    return f"{shaped}.{extension.lower()}" if extension else shaped


def report(entries: Iterable[FileEntry], say: Callable[[str], None] = print) -> None:
    files = list(entries)
    sets = families(files)
    total = sum(f.size for f in files)
    say(f"{len(files):,} files, {_size(total)}, in {len(sets):,} datasets "
        f"({len(files) / max(len(sets), 1):.1f} files per dataset)")  # fmt: skip

    say("\nBy top-level folder: files, size, datasets, datasets of one file")
    by_top: dict[str, list[FileEntry]] = defaultdict(list)
    for entry in files:
        by_top[_top(entry.path)].append(entry)
    sets_by_top: dict[str, list[Family]] = defaultdict(list)
    for family in sets:
        sets_by_top[_top(family.key)].append(family)
    for top in sorted(by_top):
        group, its_sets = by_top[top], sets_by_top[top]
        single = sum(1 for f in its_sets if f.files == 1)
        say(f"  {top}: {len(group):,}, {_size(sum(e.size for e in group))}, "
            f"{len(its_sets):,}, {single:,}")  # fmt: skip

    say("\nBy file type: files, size")
    types: Counter[str] = Counter()
    type_bytes: Counter[str] = Counter()
    for entry in files:
        kind = _extension(entry.name)
        types[kind] += 1
        type_bytes[kind] += entry.size
    shown = types.most_common(25)
    for kind, count in shown:
        say(f"  {kind}: {count:,}, {_size(type_bytes[kind])}")
    rest = types.keys() - {kind for kind, _ in shown}
    if rest:
        say(f"  {len(rest)} other types: {sum(types[k] for k in rest):,}, "
            f"{_size(sum(type_bytes[k] for k in rest))}")  # fmt: skip

    say("\nBy size: files, size")
    for label, entries_in in _banded(files, _SIZE_BANDS, lambda e: e.size).items():
        say(f"  {label}: {len(entries_in):,}, {_size(sum(e.size for e in entries_in))}")
    if files:
        say(f"  largest file: {_size(max(e.size for e in files))}")

    say("\nBy year in the path: files")
    years: Counter[str] = Counter()
    for entry in files:
        found = years_in(entry.path)
        years[str(max(found)) if found else "no year"] += 1
    for year in sorted(years):
        say(f"  {year}: {years[year]:,}")

    say("\nDatasets by how many files they hold: datasets, files")
    for label, sets_in in _banded(sets, _DATASET_BANDS, lambda f: f.files).items():
        say(f"  {label}: {len(sets_in):,}, {sum(f.files for f in sets_in):,}")

    singles = [f for f in sets if f.files == 1]
    if not singles:
        return
    per_folder = Counter(f.folder for f in singles)
    crowded = sum(1 for f in singles if per_folder[f.folder] > 10)
    say(f"\nOf the {len(singles):,} one-file datasets, {crowded:,} are in folders holding over 10 "
        "of them (there, file names don't group); the rest are in folders holding 10 or fewer.")  # fmt: skip
    say("Their file names' commonest shapes (letters as a, digits as 9):")
    for name_shape, count in Counter(
        shape_of(f.example.rsplit("/", 1)[-1]) for f in singles if f.example
    ).most_common(15):
        say(f"  {name_shape}: {count:,}")
    depths = Counter(f.key.count("/") + 1 for f in singles)
    say("Their depth (folders + file): " + ", ".join(f"{d}: {depths[d]:,}" for d in sorted(depths)))


def _top(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(files at the top)"


def _extension(name: str) -> str:
    return "." + name.rsplit(".", 1)[1].lower() if "." in name.strip(".") else "(none)"


def _banded[T](
    items: list[T], bands: list[tuple[str, int | None]], measure: Callable[[T], int]
) -> dict[str, list[T]]:
    banded: dict[str, list[T]] = {label: [] for label, _ in bands}
    for item in items:
        for label, limit in bands:
            if limit is None or measure(item) < limit:
                banded[label].append(item)
                break
    return banded


def _size(n: int) -> str:
    for unit, scale in (("TB", 1024**4), ("GB", 1024**3), ("MB", _MB), ("KB", 1024)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} bytes"


def run(settings: Settings) -> int:
    """The CLI command (cli.py)."""
    from datalab import db
    from datalab.dropbox.index import ProcessedIndex

    connection = db.connect(settings.database_file)
    try:
        index = ProcessedIndex(connection)
        chosen = index.chosen()
        if chosen is None or chosen.indexed_at is None:
            print("Choose a folder in Settings → Connections, and let DataLab list it, first.")
            return 2
        print(f"{chosen.folder_path}, listed {chosen.indexed_at}")
        report(index.files())
        return 0
    finally:
        connection.close()
