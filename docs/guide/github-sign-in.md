---
title: Sign in to GitHub
summary: Connect DataLab to the lab's private knowledge-base and pipelines repositories with a one-time code.
order: 30
keywords: github, sign in, sign-in, login, code, device, token, access, repository, team, sync, sign out
---

# Sign in to GitHub

DataLab itself is public, but the lab's knowledge base (`ihs-knowledge`) and
pipelines (`ihs-pipelines`) are private repositories on GitHub. Signing in
lets DataLab download them and share your reviewed changes. One sign-in
covers both.

## Sign in

1. Open **Settings & Safety → Connections → GitHub** (or press **Sign in**
   beside the GitHub mark at the top right) and press **Sign in with GitHub**.
2. DataLab shows a short code. Open GitHub's device page from the link, enter
   the code, and approve **IHS DataLab**.
3. DataLab notices within a few seconds and downloads the repositories.

## What DataLab can do with it

- The sign-in goes through the lab's GitHub App, which can reach only those
  two repositories, and only their files.
- DataLab keeps the sign-in in your computer's keychain. It's used only by
  DataLab itself, never inside an agent's container.
- Changes are saved only when you press
  [Save & share](glossary.md#save--share), under your GitHub name.

The sign-in renews itself while you use DataLab. You're signed out only if
GitHub refuses to renew it; a network problem doesn't sign you out.

## If you don't have access

If GitHub says the repository isn't found, your account isn't in the lab's
DataLab team yet. DataLab says whom to ask.

## "Git isn't installed on this computer"

On Windows, DataLab installs Git for you the first time **Sync** needs it
(for your account only; no administrator is needed), then carries on. If that
can't happen, for example with no internet, install Git from git-scm.com,
restart DataLab and press **Sync** again.

## Keeping up to date

**Sync** downloads what others have saved. DataLab also syncs by itself
before it gives a new conversation its copy of the knowledge base, if the
last sync was a while ago.

## Sign out

**Sign out** removes the sign-in from this computer.

The practice DataLab never signs in to GitHub, so nothing from practice can
reach the lab's repositories.
