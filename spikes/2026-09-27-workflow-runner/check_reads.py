"""Question 3: check a workflow's SQL steps against its declared `reads:`.

Rules proposed for the workflow-file check (DataLab's own code):

1. `reads:` lists every Oracle object the workflow reads, as SCHEMA.OBJECT,
   in upper case. A pipeline declares its own `reads:` in its pipeline.yaml;
   a workflow that runs the pipeline inherits them.
2. Every table or view a SQL step names must be schema-qualified and in
   `reads:`. CTE names don't count as objects.
3. Every entry in `reads:` must be used by a SQL step or by a pipeline the
   workflow runs; an unused entry is an error, so the list can't drift into
   "whatever we might read".

    python check_reads.py            runs the examples below
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def tables_in(sql: str) -> tuple[set[str], list[str]]:
    """Objects a query reads, as SCHEMA.OBJECT, and problems found."""
    problems: list[str] = []
    tree = sqlglot.parse_one(sql, read="oracle")
    ctes = {cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE)}
    found: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.upper()
        schema = (table.db or "").upper()
        if not schema:
            if name not in ctes:
                problems.append(f"{name} isn't schema-qualified: write SCHEMA.{name}.")
            continue
        if table.catalog or "@" in name:
            problems.append(f"{schema}.{name}: database links aren't allowed.")
            continue
        found.add(f"{schema}.{name}")
    return found, problems


def check_reads(workflow: dict, pipeline_reads: dict[str, list[str]] | None = None) -> list[str]:
    """Problems with a workflow's `reads:`; an empty list means it's consistent."""
    declared_raw = workflow.get("reads")
    if not isinstance(declared_raw, list) or not declared_raw:
        return ["The workflow must list the Oracle objects it reads under `reads:`."]
    problems: list[str] = []
    declared: set[str] = set()
    for entry in declared_raw:
        name = entry if isinstance(entry, str) else (entry or {}).get("object", "")
        if name.count(".") != 1 or name != name.upper():
            problems.append(f"`reads:` entry {name!r} must be SCHEMA.OBJECT in upper case.")
        declared.add(name.upper())
    used: set[str] = set()
    for step in workflow.get("steps", []):
        if "sql" in step:
            try:
                tables, found_problems = tables_in(step["sql"])
            except sqlglot.errors.ParseError as error:
                problems.append(f"Step {step['id']}: the SQL doesn't parse ({error}).")
                continue
            problems += [f"Step {step['id']}: {p}" for p in found_problems]
            for table in sorted(tables - declared):
                problems.append(f"Step {step['id']} reads {table}, which isn't in `reads:`.")
            used |= tables
        if "pipeline" in step:
            wanted = set((pipeline_reads or {}).get(step["pipeline"], []))
            for table in sorted(wanted - declared):
                problems.append(
                    f"Pipeline {step['pipeline']} reads {table}, which isn't in `reads:`."
                )
            used |= wanted
    for table in sorted(declared - used):
        problems.append(f"{table} is in `reads:` but no step reads it.")
    return problems


EXAMPLES = [
    ("consistent", ["IHS_2025.VFITBITDAILYDATA"],
     "SELECT * FROM IHS_2025.VFITBITDAILYDATA WHERE RECORD_DATE >= :d"),
    ("undeclared join", ["IHS_2025.VFITBITDAILYDATA"],
     "SELECT f.* FROM IHS_2025.VFITBITDAILYDATA f "
     "JOIN IHS_2025.PARTICIPANTS p ON p.ID = f.STUDY_PARTICIPANT_ID"),
    ("undeclared in a subquery", ["IHS_2025.VFITBITDAILYDATA"],
     "SELECT * FROM IHS_2025.VFITBITDAILYDATA WHERE STUDY_PARTICIPANT_ID IN "
     "(SELECT ID FROM IHS_2024.PARTICIPANTS)"),
    ("CTE names are not objects", ["IHS_2025.VFITBITDAILYDATA"],
     "WITH d AS (SELECT * FROM IHS_2025.VFITBITDAILYDATA) SELECT COUNT(*) FROM d"),
    ("unqualified table", ["IHS_2025.VFITBITDAILYDATA"],
     "SELECT * FROM VFITBITDAILYDATA"),
    ("declared but unused", ["IHS_2025.VFITBITDAILYDATA", "IHS_2025.GARMIN_DAILY"],
     "SELECT * FROM IHS_2025.VFITBITDAILYDATA"),
    ("database link", ["IHS_2025.VFITBITDAILYDATA"],
     "SELECT * FROM IHS_2025.VFITBITDAILYDATA@OTHERDB"),
]


def main() -> None:
    for label, reads, sql in EXAMPLES:
        workflow = {"reads": reads, "steps": [{"id": "extract", "sql": sql}]}
        problems = check_reads(workflow)
        print(f"{label}: {'OK' if not problems else '; '.join(problems)}")
    print("pipeline:", check_reads(
        {"reads": ["IHS_2025.VFITBITDAILYDATA"],
         "steps": [{"id": "metrics", "pipeline": "daily_metrics_2025"}]},
        {"daily_metrics_2025": ["IHS_2025.VFITBITDAILYDATA", "IHS_2025.VGARMINDAILY"]},
    ))


if __name__ == "__main__":
    main()
