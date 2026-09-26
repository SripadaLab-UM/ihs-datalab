---
name: statistical-review
description: Use when reviewing, planning, or running statistical analyses, model checks, missingness checks, or quantitative summaries.
---

Apply statistical review discipline.

1. Identify the unit of analysis, outcome, predictors, grouping, and time variables.
2. Check missingness, duplicates, impossible values, and obvious coding issues before modeling.
3. Report sample sizes before and after exclusions.
4. Match methods to data shape and assumptions; do not overstate causal claims.
5. For models, inspect diagnostics relevant to the method.
6. Prefer effect sizes and uncertainty intervals over p-values alone.
7. Keep exploratory results clearly labeled as exploratory.
8. Save reusable code under `/work` and final tables and plots in `/work/outputs`.
9. Avoid exposing raw sensitive rows in the user-facing response unless explicitly requested and appropriate.
