"""What to say when Oracle refuses a query that passed the SQL check.

For the errors a query commonly runs into, the message is the Oracle code
and DataLab's own explanation of what to change, never Oracle's text: that
text can quote values (23ai says which string wasn't a number), or name the
database's objects and setup. The one thing taken from it is the identifier
ORA-00904 names, and only when the query itself contains it, so it's a name
the query already wrote. Any other error keeps the first line of Oracle's
message, as before: it names the problem without echoing data values.
"""

from __future__ import annotations

import re

import oracledb

_CLOB_FIX = "use TO_CHAR(SUBSTR(col, 1, 1000)) there"
_DATES = (
    "a value couldn't be read as a date in the format given. Check the column's type "
    "with describe_table (a DATE needs no conversion), and write dates as "
    "TO_DATE(:start_date, 'YYYY-MM-DD')."
)
_TOO_LONG = (
    "a text result is longer than Oracle allows (4,000 bytes). Take a shorter piece "
    "with SUBSTR, or add ON OVERFLOW TRUNCATE to LISTAGG."
)
_UNREADABLE = "Oracle couldn't read the SQL. Check the brackets, commas and keywords near the end."

EXPLANATIONS: dict[str, str] = {
    "ORA-00904": (
        "a name in the query isn't a column Oracle can find{name}. Check its spelling "
        "with describe_table; a column spelled with lower-case letters must be written "
        'in double quotes, exactly as describe_table shows it ("Bdate").'
    ),
    "ORA-00932": (
        "inconsistent data types. Often a CLOB (long text) column in SELECT DISTINCT, "
        f"GROUP BY, ORDER BY, MIN/MAX or a comparison: {_CLOB_FIX}. Otherwise two values "
        "of different types are compared or combined: convert one with TO_CHAR, "
        "TO_NUMBER or TO_DATE."
    ),
    "ORA-22848": (
        "a CLOB (long text) column can't be sorted, grouped or compared (DISTINCT, "
        f"GROUP BY, ORDER BY, UNION, =, IN): {_CLOB_FIX}."
    ),
    "ORA-22849": (
        "a CLOB (long text) column can't be used in this function (MIN, MAX, COUNT, "
        f"LAG and the like): {_CLOB_FIX}, or COUNT(LENGTH(col)) to count it."
    ),
    "ORA-00942": (
        "a table or view in the query doesn't exist in that cohort, or DataLab's account "
        "can't read it. Check the name and its schema with search_catalog."
    ),
    "ORA-01722": (
        "a text value couldn't be converted to a number. Compare a text column with text "
        "('1', not 1), and use TO_NUMBER only where every value is a number."
    ),
    "ORA-01858": _DATES,
    "ORA-01861": _DATES,
    "ORA-01841": _DATES,
    "ORA-01843": _DATES,
    "ORA-01847": _DATES,
    "ORA-01839": _DATES,
    "ORA-01830": _DATES,
    "ORA-01840": _DATES,
    "ORA-01476": "division by zero. Guard the divisor: x / NULLIF(y, 0).",
    "ORA-00979": (
        "not a GROUP BY expression: every selected column that isn't inside an aggregate "
        "(COUNT, SUM, MIN…) must be in GROUP BY, written the same way."
    ),
    "ORA-00937": (
        "a column is selected beside an aggregate without a GROUP BY: add GROUP BY for "
        "that column, or aggregate it too."
    ),
    "ORA-00934": (
        "an aggregate (COUNT, SUM, MIN…) isn't allowed there: filter on it with HAVING, not WHERE."
    ),
    "ORA-01427": (
        "a subquery used as a single value returned more than one row. Make it return "
        "one row (an aggregate, or a tighter condition), or use IN."
    ),
    "ORA-00918": (
        "a column name is in more than one of the tables: qualify it with its table's "
        "alias (t.PARTICIPANTIDENTIFIER)."
    ),
    "ORA-12899": _TOO_LONG,
    "ORA-01489": _TOO_LONG,
    "ORA-00933": _UNREADABLE,
    "ORA-00936": _UNREADABLE,
    "ORA-00907": _UNREADABLE,
    "ORA-01652": (
        "the query needed more temporary space than the database allows. Narrow it: "
        "fewer rows or columns, or aggregate in the database."
    ),
}

_INVALID_IDENTIFIER = re.compile(r'ORA-00904: ("[^"\r\n]{1,128}"(?:\."[^"\r\n]{1,128}"){0,2}):')


def oracle_code(error: oracledb.Error) -> str | None:
    """ORA-00932, DPY-4010 and the like, when the error has one."""
    first = error.args[0] if error.args else None
    code = getattr(first, "full_code", None)
    if isinstance(code, str) and code:
        return code
    found = re.match(r"((?:ORA|DPY|DPI)-\d{4,5}):", str(error))
    return found.group(1) if found else None


def explain(error: oracledb.Error, sql: str | None = None) -> str:
    """The message a failed query shows: to the agent, in Queries, in the chat."""
    code = oracle_code(error)
    explanation = EXPLANATIONS.get(code or "")
    if code is None or explanation is None:
        # The first line of an Oracle error names the problem (e.g. ORA-00942:
        # table or view does not exist) without echoing data values.
        return str(error).splitlines()[0][:300] if str(error) else type(error).__name__
    name = ""
    if code == "ORA-00904" and sql:
        found = _INVALID_IDENTIFIER.search(str(error))
        if found and _written_in(found.group(1), sql):
            name = f": {found.group(1)}"
    return f"{code}: {explanation.format(name=name)}"


def _written_in(identifier: str, sql: str) -> bool:
    """Whether the query itself names this identifier (every part of it)."""
    parts = [p.strip('"') for p in identifier.split(".")]
    upper = sql.upper()
    return all(p and (p in sql or p.upper() in upper) for p in parts)
