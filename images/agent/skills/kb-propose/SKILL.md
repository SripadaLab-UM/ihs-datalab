---
name: kb-propose
description: Use when you've verified something durable about IHS data that the lab should keep (a device quirk, a cleaning or QC rule, a cohort difference, a verified query, a correction to a page), or the person asks you to add it to the knowledge base. Writes or edits a page in /work/kb for a person to review and share.
---

You propose edits by changing files in `/work/kb`. After your turn DataLab
shows the person the exact diff; they can edit it, discard it, or Save &
share it with the lab. Nothing leaves this conversation until they do.

**Never put participant-level data in the knowledge base**: no IDs, no
per-person dates, no rows or tables of values, no small counts. Schema
metadata and aggregate shapes ("about 5% of nights are missing") are fine.
DataLab scans every edit for things that look like data, and a person
reviews it; neither replaces your care.

What to propose:
- Only what is durable, general, and verified in this conversation. Not this
  analysis's results, and not guesses.
- Prefer editing an existing page over adding a near-duplicate. Keep edits
  small and focused; say in your answer what you changed and why.

Where it goes. The file name is the page's `id` plus `.md`, in its kind's
folder: `sources/`, `tables/` (named `SCHEMA.TABLE.md`), `features/`, `qc/`,
`cohorts/`, `queries/`, `decisions/`, `papers/`. Lab skills are
`skills/<name>/SKILL.md`, with `name` and `description` front matter.
Don't edit `index.md` (DataLab rewrites it), `generated/` (refreshed from
the database catalog), or anything outside these folders: those edits are
refused.

The page format:

```markdown
---
id: midnight-spanning-sleep
kind: qc                 # source | table | feature | qc | cohort | query | decision | paper
status: draft            # new pages are drafts
summary: One line saying what this page establishes.
evidence:                # each item one `type: reference`
  - schema: IHS_2025.VFITBITSLEEP.STARTTIME        # must exist in generated/schema
  - code: ihs-pipelines@a1b2c3d ihsDataR/R/sleep.R  # repo@commit path
  - query: queries/sleep-nights-per-person          # a page in queries/
  - paper: 10.1038/s41746-021-00400-z               # a DOI
  - legacy: reference/2024/Sleep_2024.R#L40-72
limitations:
  - What this doesn't cover, or where it could be wrong.
related: [sources/fitbit]  # other pages, as folder/id
cohorts: [2024, 2025]      # the years it applies to
---

# Midnight-spanning sleep

Plain-language explanation, caveats, and a worked example (no data).
```

Rules DataLab checks:
- `id` matches the file name and is unique; `kind` matches the folder.
- Every page has typed evidence and states its limitations; "the model
  said so" is never evidence.
- `related`, `query` evidence, and relative Markdown links must resolve;
  tables and columns you mention (`IHS_2025.TABLE.COLUMN`) must exist in
  `generated/schema`.
- Only people review. Don't change a page's `status`, and never write
  `reviewed_by` or `reviewed_on`: DataLab fills them in from the person
  who saves, and ignores yours.

For a `queries/` page, give the question, the SQL (bind variables, never
literal IDs or dates of people), what you checked and how, and the expected
result shape (columns and grain), never results.
