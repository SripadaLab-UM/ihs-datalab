---
name: kb-maintain
description: Use when the person asks you to review, tidy, or audit the lab knowledge base, for example after a schema refresh. Finds contradictions, stale or weakly supported pages, broken links, and tables or columns that no longer exist, and proposes small fixes.
---

Work from your copy at `/work/kb`, and report before you change much.

1. Read `AGENTS.md` and `index.md`, then go folder by folder.
2. Look for:
   - pages that contradict each other (the same rule stated two ways, a
     feature defined differently in two places);
   - tables and columns a page mentions that aren't in
     `generated/schema/<SCHEMA>/` any more, or that `generated/drift.md`
     says changed between cohorts; in a data session, confirm with
     `describe_table`;
   - `related` entries and links that go nowhere;
   - claims without typed evidence, `code` evidence pinned to a very old
     commit, and pages with no limitations;
   - `cohorts` lists that miss years the page's rule clearly covers, or
     include years it can't;
   - `reviewed` pages whose evidence no longer holds; don't change their
     status yourself, flag them for a person.
   - anything that looks like participant-level data: flag it at once.
3. Report what you found, grouped by how much it matters, with the page ids.
4. Fix only what the person agrees to, with the `kb-propose` skill: small,
   separate edits a reviewer can follow, not a mass rewrite.
