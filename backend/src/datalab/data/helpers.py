"""Metadata helpers for the agent: how tables join, and where a concept lives.

Metadata only: these read the catalog (tables, columns, types, comments),
never the data. They save the agent guesswork that would otherwise cost
exploratory queries against the database.
"""

from __future__ import annotations

from dataclasses import dataclass

from datalab.data.catalog import Catalog, Column, TableInfo

# How IHS identifies participants. The app/device identifier and the study
# identifier are different columns; linking them needs a table with both.
PARTICIPANT_KEYS = (
    "PARTICIPANTIDENTIFIER",
    "PARTICIPANTID",
    "STUDY_PARTICIPANT_ID",
    "USERID",
    "USER_ID",
)
# Whole words of a column name (split at "_") that mark a date, or endings.
_DATE_WORDS = {"DATE", "DAY", "TIME", "TIMESTAMP", "START", "END", "DT"}
_DATE_ENDINGS = ("DATE", "DATETIME", "TIMESTAMP")
# Record-keeping timestamps: when a row was written, not when anything happened.
_AUDIT_WORDS = ("MODIFIED", "CREATED", "INSERTED", "UPDATED", "LOADED", "ETL")

# Plain words researchers use, and the words the catalog uses for them.
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "sleep": ("sleep", "asleep", "bed", "minutesasleep", "timeinbed", "efficiency"),
    "steps": ("steps", "trackersteps", "step"),
    "activity": ("steps", "activity", "active", "sedentary", "calories", "exercise"),
    "heart rate": ("heart", "heartrate", "restingheartrate", "hr"),
    "hrv": ("hrv", "rmssd", "variability"),
    "mood": ("mood", "emotion", "valence", "daily"),
    "depression": ("phq", "phq9", "interest", "down", "depress"),
    "anxiety": ("gad", "gad7", "anxious", "nervous", "worry"),
    "work hours": ("work", "hours", "workhours", "shift", "duty"),
    "demographics": ("age", "gender", "sex", "race", "ethnicity", "specialty", "baseline"),
    "survey": ("survey", "question", "response", "dictionary"),
    "wellbeing": ("wellbeing", "stress", "burnout", "satisfaction"),
}


@dataclass(frozen=True)
class JoinKey:
    column: str
    role: str  # "participant", "date", "other", "audit"
    types: tuple[str, str]
    note: str = ""


def join_keys(catalog: Catalog, first: str, second: str) -> dict:
    """The columns two tables share, most useful for joining first, with caveats."""
    a, b = catalog.get(first), catalog.get(second)
    missing = [name for name, table in ((first, a), (second, b)) if table is None]
    if missing or a is None or b is None:
        return {"error": f"Not in the catalog: {', '.join(missing)}. Use search_catalog."}
    b_columns = {c.name: c for c in b.columns}
    keys: list[JoinKey] = []
    for column in a.columns:
        other = b_columns.get(column.name)
        if other is None:
            continue
        role = _role(column)
        note = ""
        if role == "audit":
            note = "when the row was written, not when it was measured: don't join on it"
        elif _family(column.type) != _family(other.type):
            note = f"types differ ({column.type} vs {other.type}): convert before joining"
        keys.append(JoinKey(column.name, role, (column.type, other.type), note))
    keys.sort(key=lambda k: ("participant", "date", "other", "audit").index(k.role))
    notes: list[str] = []
    if a.schema != b.schema:
        notes.append(
            f"The tables are in different cohorts ({a.schema}, {b.schema}). Participant "
            "identifiers are per cohort: joining across cohorts usually isn't meaningful."
        )
    if not any(k.role == "participant" for k in keys):
        notes.append("No shared participant identifier. " + _mapping_hint(catalog, a, b))
    if any(k.role == "participant" for k in keys) and not any(k.role == "date" for k in keys):
        notes.append(
            "Shared participant identifier but no shared date column: a join on the identifier "
            "alone pairs every row of one with every row of the other for that person. "
            f"Dates in {a.name}: {_dates(a)}; in {b.name}: {_dates(b)}. Align them to the "
            "same grain (for example, the calendar day) first."
        )
    return {
        "tables": [a.qualified_name, b.qualified_name],
        "shared_columns": [
            {"column": k.column, "role": k.role, "types": list(k.types), "note": k.note}
            for k in keys
        ],
        "notes": notes,
    }


def find_concept(catalog: Catalog, concept: str, cohorts: list[str] | None = None) -> dict:
    """Candidate tables and columns for a plain-language concept."""
    words = _CONCEPTS.get(concept.strip().lower())
    queries = [concept] + [w for w in (words or ()) if w != concept.lower()]
    seen: dict[str, dict] = {}
    for query in queries:
        for hit in catalog.search(query, limit=15, schemas=cohorts):
            entry = seen.setdefault(
                hit.table.qualified_name,
                {
                    "table": hit.table.qualified_name,
                    "comment": hit.table.comment,
                    "matching_columns": [],
                    "score": 0.0,
                },
            )
            entry["score"] += hit.score
            for column in hit.matching_columns:
                if column not in entry["matching_columns"]:
                    entry["matching_columns"].append(column)
    ranked = sorted(seen.values(), key=lambda e: -e["score"])[:20]
    for entry in ranked:
        del entry["score"]
    return {
        "concept": concept,
        "searched_for": queries,
        "candidates": ranked,
        "note": "Candidates from names and comments only. Check each with describe_table, "
        "and confirm it's populated with a small count, before relying on it.",
    }


def _role(column: Column) -> str:
    name = column.name.upper()
    if name in PARTICIPANT_KEYS:
        return "participant"
    if any(word in name for word in _AUDIT_WORDS):
        return "audit"
    words = set(name.split("_"))
    if _family(column.type) == "date" or words & _DATE_WORDS or name.endswith(_DATE_ENDINGS):
        return "date"
    return "other"


def _dates(table: TableInfo) -> str:
    dates = [c.name for c in table.columns if _role(c) == "date"]
    return ", ".join(dates[:6]) or "none"


def _family(sql_type: str) -> str:
    upper = sql_type.upper()
    if "CHAR" in upper or "CLOB" in upper:
        return "text"
    if "NUMBER" in upper or "INT" in upper or "FLOAT" in upper:
        return "number"
    if "DATE" in upper or "TIMESTAMP" in upper:
        return "date"
    return upper


def _mapping_hint(catalog: Catalog, a: TableInfo, b: TableInfo) -> str:
    a_keys = {c.name for c in a.columns} & set(PARTICIPANT_KEYS)
    b_keys = {c.name for c in b.columns} & set(PARTICIPANT_KEYS)
    if not a_keys or not b_keys:
        return "One of the tables has no participant identifier at all."
    bridges = [
        t.qualified_name
        for t in _tables_in(catalog, a.schema)
        if a_keys & {c.name for c in t.columns} and b_keys & {c.name for c in t.columns}
    ]
    if bridges:
        return (
            f"{a.name} uses {', '.join(sorted(a_keys))} and {b.name} uses "
            f"{', '.join(sorted(b_keys))}; these tables have both and can link them: "
            + ", ".join(bridges[:5])
        )
    return "No table in the catalog has both identifiers; ask how they're linked."


def _tables_in(catalog: Catalog, schema: str) -> list[TableInfo]:
    return [t for t in (catalog.get(f"{schema}.{name}") for name in catalog.names(schema)) if t]
