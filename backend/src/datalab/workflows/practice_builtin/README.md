# Practice DataLab's built-in workflows

These 8 files are copied byte for byte from the lab's `ihs-pipelines` repo at
commit `315ac98`, `workflows/<name>.yaml`. They are Yu's 8 routines, and
they're here for the **practice DataLab** only, which never signs in to
GitHub: it lists them in its Workflows tab (read-only, as `builtin/<name>.yaml`)
and runs them on the synthetic database. Practice delivers every destination
key to its own practice exports folder.

The real DataLab doesn't read these: it reads the lab's own copies from its
synced clone of `ihs-pipelines`.

Only these 8 are copied. The lab's other workflows call pipelines in the
private `ihsDataR` package, which isn't in this repo.

`tests/test_practice_builtin.py` checks each file's git blob id against the
one it has in `ihs-pipelines` at `315ac98`. To refresh them, see
docs/WORKFLOWS.md ("Practice's built-in workflows").
