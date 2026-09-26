---
name: reproducible-report
description: Use when producing a report, summary, methods note, figure, table, or any file the user should keep.
---

Make every deliverable easy to find, understand, and regenerate.

1. Put deliverables in `/work/outputs` with clear, descriptive file names.
   Keep scratch files in `/work`.
2. Keep the source that produced each deliverable (`analysis.R`,
   `report.md`, `figure.py`) in `/work`, with enough method detail for
   another analyst to rerun it.
3. In reports, state the question, the data used (cohort schemas, tables,
   and attached file names), row and participant counts, filters,
   exclusions, and parameter choices.
4. Separate findings from assumptions and limitations. Don't overstate
   observational associations as causal.
5. Prefer self-contained HTML (offline: inline CSS/JS, embedded data) or
   Markdown. For Markdown, `render-report /work/<source>.md` renders it into
   `/work/outputs`.
6. Never put credentials, tokens, or participant identifiers in deliverables
   unless the user explicitly asks for identifiers.
7. When you create or update deliverables, list their paths in your answer.
