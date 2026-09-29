---
name: sql-extraction
description: Use when writing SQL against the IHS database, finding tables or columns, or extracting a dataset.
---

Query the IHS database carefully and show your work.

1. Catalog first. Use `search_catalog` (or `find_concept` for a research
   concept) to find candidate tables, then `describe_table` for each one
   you'll use. Tables and columns differ between cohort years: check each
   year you query.
2. Before joining two tables, check `join_paths`: it lists shared columns and
   warns about cross-cohort joins, different participant identifiers, and
   joins without a date that would multiply rows.
3. Qualify every table with its schema (`IHS_2025.VFITBITDAILYDATA`).
4. Profile before you deliver: a `COUNT(*)`, the date range, and the number
   of distinct participants, in small queries. Keep these separate from the
   final extraction.
5. Use bind variables (`:start_date`) for values, and check date column types
   before filtering on them.
6. Select the columns you need; avoid `SELECT *`.
7. The `query` tool returns a preview; the full result is a CSV in
   `/data/oracle`. Work from the file.
8. Show every SQL statement you ran, with its purpose and row count, in your
   answer. The person can also see them under Queries.
9. Oracle's built-in SQL functions all work: dates and time zones
   (`TO_TIMESTAMP_TZ`, `FROM_TZ`, `NEW_TIME`), text and regular expressions,
   `LISTAGG`, statistics (`MEDIAN`, `PERCENTILE_CONT`, `CORR`, `REGR_*`,
   `STATS_*`), and window functions. Not allowed: package calls such as
   `DBMS_LOB.SUBSTR`, XML functions, `SYS_CONTEXT`, and functions defined in
   the database.
   A `TIMESTAMP WITH TIME ZONE` column comes back in the CSV as its local
   time without the offset, so values in different offsets can't be compared
   or subtracted there. Select `SYS_EXTRACT_UTC(col)` for the instant in UTC,
   or `TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS TZH:TZM')` to keep the offset.
10. Column names are exact. A column spelled with lower-case letters (the
    survey views' `"Bdate"`, `"interest0"`, `"Black tea"`) must be written in
    double quotes, exactly as `describe_table` shows it: unquoted, `Bdate` means
    `BDATE`, which doesn't exist. Upper-case names need no quotes.
11. CLOB columns (long text, such as `QUESTIONTEXT` and `ANSWERCHOICES` in the
    survey dictionaries) can be selected and searched with `LIKE`, `IS NULL`,
    `LENGTH` and `INSTR`, but Oracle can't use them in `SELECT DISTINCT`,
    `GROUP BY`, `ORDER BY`, `PARTITION BY`, `UNION` (without `ALL`),
    `MIN`/`MAX`/`COUNT`, `=`/`IN`/`BETWEEN` or a join. `SUBSTR`, `UPPER`,
    `TRIM` and `||` still return a CLOB. Convert first:
    `TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000))` is ordinary text of up to 1,000
    characters. To count them, use `COUNT(LENGTH(col))`.
12. Correctness checks (AGENTS.md), in Oracle SQL. Timestamps against days,
    and repeated days, before any person-day count:

    ```sql
    SELECT COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || RECORD_DATE) AS timestamps,
           COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || SUBSTR(RECORD_DATE, 1, 10)) AS local_days
    FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
    ```

    The UTC date of a text timestamp is `TO_CHAR(SYS_EXTRACT_UTC(
    TO_TIMESTAMP_TZ(RECORD_DATE, 'YYYY-MM-DD HH24:MI:SS TZH:TZM')), 'YYYY-MM-DD')`.
    The cohort once, checked, then used for numerator and denominator alike:

    ```sql
    WITH cohort AS (
      SELECT DISTINCT PARTICIPANTIDENTIFIER FROM IHS_2025.VW_IHS_PARTICIPANT_SUMMARY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL)
    SELECT (SELECT COUNT(*) FROM cohort) AS cohort_n,
           (SELECT COUNT(DISTINCT h.PARTICIPANTIDENTIFIER)
              FROM IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE h
              JOIN cohort c ON c.PARTICIPANTIDENTIFIER = h.PARTICIPANTIDENTIFIER) AS with_data,
           (SELECT COUNT(DISTINCT h.PARTICIPANTIDENTIFIER)
              FROM IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE h
              WHERE h.PARTICIPANTIDENTIFIER NOT IN (SELECT PARTICIPANTIDENTIFIER FROM cohort))
             AS outside_cohort
    FROM DUAL
    ```

    Duplicate cohort IDs: `GROUP BY PARTICIPANTIDENTIFIER HAVING COUNT(*) > 1`
    on the cohort's source, before `DISTINCT` hides them.
13. If a query is rejected (it isn't a single SELECT, or it's too large), read
    the message, narrow the query, and try again. If the database refuses it,
    the message gives the Oracle code and what to change. Don't try to get
    around a limit; ask the person instead.
