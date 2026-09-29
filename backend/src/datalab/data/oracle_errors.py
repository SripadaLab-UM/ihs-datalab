"""What to say when Oracle refuses a query that passed the SQL check.

For the errors a query commonly runs into, the message is the Oracle code
and DataLab's own explanation of what to change, never Oracle's text: that
text can quote values (23ai says which string wasn't a number), or name the
database's objects and setup. The one thing taken from it is the identifier
ORA-00904 names, and only when the query itself contains it, so it's a name
the query already wrote. Any other error shows only its code and a general
sentence: Oracle's text for it isn't passed on, since newer versions quote
values in it and some name the server (ORA-12801 names a parallel query
server's host and SID; it's explained by the error it wraps). Not reaching
the database (DPY-6005, ORA-12514, …) says only the code, and a refused
sign-in what to do about it.

Each error also has a category, which travels with it to the agent and the
chat: validation (DataLab's SQL check), sql, permission, timeout,
connection, cancelled or result_limit.
"""

from __future__ import annotations

import re

import oracledb

_CLOB_FIX = (
    "convert it with TO_CHAR(SUBSTR(col, 1, 1000)) there, and check MAX(LENGTH(col)) so "
    "no value is cut unseen"
)
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
_CONVERSION = (
    "long text converted to ordinary text is longer than Oracle's 4,000 bytes. Convert "
    "at most 1,000 characters (TO_CHAR(SUBSTR(col, 1, 1000))), and check MAX(LENGTH(col)) "
    "to see whether any value is cut."
)

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
    "ORA-22835": _CONVERSION,
    "ORA-64203": _CONVERSION,
    "ORA-01031": (
        "DataLab's account isn't allowed to do that: it can only read the cohort tables."
    ),
    "ORA-01652": (
        "the query needed more temporary space than the database allows. Narrow it: "
        "fewer rows or columns, or aggregate in the database."
    ),
}

_INVALID_IDENTIFIER = re.compile(r'ORA-00904: ("[^"\r\n]{1,128}"(?:\."[^"\r\n]{1,128}"){0,2}):')
_ANY_CODE = re.compile(r"\b((?:ORA|DPY|DPI)-\d{4,5})\b")
# Wraps the real error, and names the parallel server (host and SID).
_PARALLEL = "ORA-12801"
_PERMISSION = frozenset({"ORA-00942", "ORA-01031"})
# Signing in refused: the password, or the account.
SIGN_IN = {
    "ORA-01017": "the saved password was refused. Update it in Settings → Connections",
    "ORA-01005": "the saved password was refused. Update it in Settings → Connections",
    "ORA-28000": "the account is locked. Ask the database's administrator to unlock it",
    "ORA-28001": "the account's password has expired. Change it, then update it in Settings",
}
# The database ended DataLab's session while the query ran.
_SESSION_ENDED = frozenset({"ORA-00028", "ORA-01012", "ORA-02396", "ORA-01089"})
# Not reaching the database, or losing it: the first line can name the host.
_CONNECTION_PREFIXES = ("DPY-6", "ORA-12", "ORA-03113", "ORA-03114", "ORA-03135", "DPY-4011")
_SQL_ERRORS_IN_12 = frozenset({"ORA-12899", _PARALLEL})
# A single round trip ran longer than the call timeout.
CALL_TIMEOUT_CODES = frozenset({"DPY-4024", "ORA-03156"})


def oracle_code(error: oracledb.Error) -> str | None:
    """ORA-00932, DPY-4010 and the like, when the error has one. For ORA-12801
    (a parallel query server failed), the error it wraps."""
    first = error.args[0] if error.args else None
    code = getattr(first, "full_code", None)
    if not (isinstance(code, str) and code):
        found = re.match(r"((?:ORA|DPY|DPI)-\d{4,5}):", str(error))
        code = found.group(1) if found else None
    if code == _PARALLEL:
        inner = [c for c in _ANY_CODE.findall(str(error)) if c != _PARALLEL]
        return inner[0] if inner else _PARALLEL
    return code


def category(code: str | None) -> str:
    """Which kind of failure an Oracle or driver error is."""
    if code is None:
        return "sql"
    if code in CALL_TIMEOUT_CODES:
        return "timeout"
    if code in _PERMISSION:
        return "permission"
    if code in SIGN_IN or code in _SESSION_ENDED:
        return "connection"
    if code.startswith(_CONNECTION_PREFIXES) and code not in _SQL_ERRORS_IN_12:
        return "connection"
    return "sql"


def connection_message(code: str | None) -> str:
    """DataLab's own words for not reaching the database: never the driver's,
    which name the host and port."""
    shown = f" ({code})" if code else ""
    return (
        f"DataLab couldn't reach the database{shown}. Check the network or VPN "
        "connection, then try again."
    )


def sign_in_message(code: str) -> str:
    return f"DataLab couldn't sign in to the database ({code}): {SIGN_IN[code]}."


def explain(error: oracledb.Error, sql: str | None = None) -> str:
    """The message a failed query shows: to the agent, in Queries, in the chat."""
    code = oracle_code(error)
    if code in SIGN_IN:
        return sign_in_message(code)
    if code in _SESSION_ENDED:
        return (
            f"The database ended DataLab's session ({code}) while the query ran. "
            "Try again in a moment."
        )
    if category(code) == "connection":
        return connection_message(code)
    if code == _PARALLEL:
        return f"{_PARALLEL}: a parallel query server failed. Try again, or narrow the query."
    explanation = EXPLANATIONS.get(code or "")
    if code is None:
        return "Oracle refused the query, without an error code DataLab recognises."
    if explanation is None:
        # Oracle's own text isn't shown (it can quote values or name the
        # server): the code alone, to look up.
        wrapped = " (inside ORA-12801, a parallel query server's error)" if _wrapped(error) else ""
        return (
            f"{code}: Oracle refused the query{wrapped}. DataLab has no explanation of "
            "this code, and doesn't show Oracle's own text for it (it can quote data)."
        )
    name = ""
    if code == "ORA-00904" and sql:
        found = _INVALID_IDENTIFIER.search(str(error))
        if found and _written_in(found.group(1), sql):
            name = f": {found.group(1)}"
    return f"{code}: {explanation.format(name=name)}"


def _wrapped(error: oracledb.Error) -> bool:
    return str(error).startswith(_PARALLEL)


def _written_in(identifier: str, sql: str) -> bool:
    """Whether the query itself names this identifier: every part of it as a
    whole name, in any letter case (Oracle upper-cases unquoted names)."""
    parts = [p.strip('"') for p in identifier.split(".")]
    return all(
        p
        and re.search(
            rf"(?<![A-Za-z0-9_$#]){re.escape(p)}(?![A-Za-z0-9_$#])", sql, flags=re.IGNORECASE
        )
        for p in parts
    )
