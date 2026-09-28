---
name: data-analysis
description: Use when analyzing study data or attached files (CSV, TSV, Excel, JSON, Parquet, text, PDF) in DataLab.
---

Analyze data reproducibly inside the DataLab workspace.

Where things are:

1. Query results from the `ihs-data` tools are CSV files in `/data/oracle` (read-only).
2. Attached files are in `/inputs` (read-only). Never try to modify them.
3. Write scripts and intermediate files in `/work`, and deliverables in `/work/outputs`.
4. There is no internet in a data session; don't try to install packages.

Workflow:

1. Inventory the inputs: names, sizes, types. For tabular files, start with
   `profile-data <file>` to see columns, types, and missingness.
2. Check the data before analysing it: unit of analysis, duplicates, impossible
   values, date ranges, and missingness. Report row and participant counts
   before and after each filter or exclusion.
3. Use R for tidyverse/data.table work, statistical models, and the lab's R
   code. Use Python where it fits better (tabular wrangling, plotting, PDF
   text extraction).
4. Save the script that produced each result as a named file, such as
   `/work/scripts/<short_descriptive_name>.R`, and run that file, so it can be
   rerun. Keep inline `Rscript -e`/`python3 -c` runs for quick checks.
5. Summarize assumptions, filters, missing-data handling, and the paths of
   the files you created.

Useful commands: `profile-data`, `render-report`, `rg`, `jq`, `sqlite3`,
`pandoc`, `pdftotext`, `tesseract`.
