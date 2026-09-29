# DataLab user guide

The user guide lives in [`docs/guide/`](guide/), one short page per task.
The same pages are DataLab's in-app Help (bundled into the app, since it can
only contact DataLab itself), and the glossary's first paragraphs are its
tooltips. This page only lists them, so there's one copy of every word.

Start with [Start here](guide/README.md).

<!-- The list below is checked against docs/guide by frontend/src/lib/guide.test.ts:
     one line per page, in each page's `order`. -->

1. [Start here](guide/README.md)
2. [What DataLab will and won't do](guide/safety.md)
3. [Ask your first question](guide/first-question.md)
4. [Read an answer, its trace, and "How was this made?"](guide/reading-an-answer.md)
5. [Analysis plans and pilots](guide/plans.md)
6. [Export results](guide/exporting.md)
7. [The SQL Playground](guide/sql-playground.md)
8. [Knowledge proposals](guide/knowledge-proposals.md)
9. [Run a workflow](guide/running-a-workflow.md)
10. [Pipelines](guide/pipelines.md)
11. [Sign in to GitHub](guide/github-sign-in.md)
12. [Settings & Safety](guide/settings.md)
13. [Practice and real data](guide/practice-and-real.md)
14. [When Docker can't run](guide/docker.md)
15. [The tour](guide/tour.md)
16. [Glossary](guide/glossary.md)

## Writing for the guide

- One task per page, in plain words, in the second person, for a researcher
  who is new to DataLab ([DESIGN.md](DESIGN.md), "Writing").
- Say only what DataLab enforces, matching [SAFETY.md](SAFETY.md). Where
  something is the agent's instructions rather than a rule, say so.
- Each page starts with front matter: `title`, `summary`, `order` (its place
  in Help's contents), `keywords` for search, and `screens`, the app routes
  the page is Help's topic for (`/sql` exactly, `/sql/*` below it).
- Link to other pages by file (`plans.md`, `glossary.md#plan`). Help turns
  these into its own links, and they work on GitHub too. Don't link outside
  `docs/guide/`: Help can't show those pages.
- A glossary term's first paragraph is its tooltip: keep it to a sentence or
  two. Tooltips name terms by their heading's anchor (`rigor-review`), and a
  test fails if one is missing.
- The tour's steps are the `##` sections of `tour.md`, in order.
