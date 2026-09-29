---
name: kb-use
description: Use before relying on what an IHS table, column, device, derived feature, QC rule, or cohort year means, and when a lab convention or verified query might apply. Looks it up in the lab knowledge base at /work/kb and cites it.
---

The lab knowledge base is the lab's shared, reviewed knowledge about IHS
data. Your copy is at `/work/kb`. Use it to stay grounded; don't treat it as
more than it claims to be.

1. First check that it's there (`ls /work/kb`). If `/work/kb` doesn't exist
   (the practice DataLab has none), or holds only a README saying the
   knowledge base isn't here, carry on without it: don't try to read its
   files, and say so when it matters.
2. Read `/work/kb/AGENTS.md` (the rules) and `/work/kb/index.md` (one line
   per page). Then search: `rg -il 'sleep|VFITBITSLEEP' /work/kb`.
3. Each page's front matter tells you how far to trust it:
   - `status`: prefer `reviewed` pages. Say so when you rely on a `draft`.
     `deprecated` pages explain what not to do any more.
   - `cohorts`: a page applies only to the years it lists. Tables, columns,
     and rules change between cohorts; `generated/drift.md` lists the
     differences.
   - `evidence`: weigh each claim by its kind. `schema` means the column
     exists and means this; `code` is how the lab's code computes it (at a
     pinned commit); `query` means the SQL was checked; `paper` means it
     was used this way in published work; `legacy` is older code, for
     reference.
   - `limitations`: read them, and carry the relevant ones into your answer.
4. `queries/` holds verified example queries. Start from one when it fits,
   and still check its tables with `describe_table` and profile the result.
5. `generated/schema/` is catalog metadata (never values). In a data
   session, the `search_catalog` and `describe_table` tools are the live
   source.
6. Cite the pages you relied on by folder and id, with their status, for
   example "(knowledge base: features/steps_day, reviewed)".
7. If a page looks wrong or out of date, or you verified something it's
   missing, say so and use the `kb-propose` skill.
8. Lab skills in `/work/kb/skills/` are the lab's own procedures; use them
   like any other skill.
