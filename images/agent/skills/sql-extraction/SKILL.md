---
name: sql-extraction
description: Use when writing SQL against the IHS database, finding tables or columns, or extracting a dataset.
---

Query the IHS database carefully and show your work.

1. Catalog first. Use `search_catalog` to find candidate tables, then
   `describe_table` for each one you'll use. Tables and columns differ between
   cohort years: check each year you query.
2. Qualify every table with its schema (`IHS_2025.VFITBITDAILYDATA`).
3. Profile before you deliver: a `COUNT(*)`, the date range, and the number
   of distinct participants, in small queries. Keep these separate from the
   final extraction.
4. Use bind variables (`:start_date`) for values, and check date column types
   before filtering on them.
5. Select the columns you need; avoid `SELECT *`.
6. The `query` tool returns a preview; the full result is a CSV in
   `/data/oracle`. Work from the file.
7. Show every SQL statement you ran, with its purpose and row count, in your
   answer. The person can also see them in the Data accessed panel.
8. If a query is rejected (it isn't a single SELECT, or it's too large), read
   the message, narrow the query, and try again. Don't try to get around a
   limit; ask the person instead.
