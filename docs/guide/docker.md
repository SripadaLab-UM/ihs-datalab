---
title: When Docker can't run
summary: What the Docker banner means, and how to fix a Windows computer that won't let Docker start.
order: 33
keywords: docker, docker desktop, windows, administrator, admin, permission, virtual machine, wsl, banner, fix, not running, starting
---

# When Docker can't run

Every conversation, workflow run and pipeline test runs in Docker Desktop, in
a sealed-off space on your computer. When Docker can't run, a banner under
DataLab's header says so, on every page, until it can. The SQL Playground,
exports and Settings keep working meanwhile, and nothing you've done in
DataLab is lost.

## "Docker Desktop isn't running"

Click **Open Docker Desktop**, or open it from the Start menu yourself.
DataLab also opens it when DataLab starts. The banner then says **Docker
Desktop is starting**: that takes a minute or two, and the banner goes away
once it's ready.

## "Docker can't start: Windows is blocking its virtual machine"

On Windows, Docker runs a small virtual machine, which needs a Windows
permission to start. On Michigan Medicine computers, a Windows policy takes
that permission away from time to time (you don't need to do anything to
cause it). DataLab notices, and opens a dialog, **Docker needs a Windows
fix**, the first time. After that, **How to fix it…** in the banner opens it
again.

1. **Turn on your temporary administrator access** first. On a Michigan
   Medicine computer that's on your profile page; the dialog links to it
   when your lab has set it up. Wait until it says it's on: it can take a
   minute.
2. Click **Fix it**. Windows asks whether to allow changes: click **Yes**.
   The box may be behind other windows; DataLab says **Waiting for
   Windows…** until you answer it.
3. DataLab gives the permission back and restarts Docker Desktop. The dialog
   says **Fixed**, and the banner says Docker Desktop is starting. Click
   **Done**.

If the dialog says **Windows didn't give permission**, your administrator
access most likely wasn't on yet: turn it on, then click **Fix it** again.
**Check again** looks once more without asking Windows for anything, and
**Later** closes the dialog (the banner stays).

No administrator access? **Restart Windows**: that gives the permission back
too, until the policy runs again. You don't need to run DataLab itself as an
administrator: only the fix needs it, for a moment, and only when you click
**Fix it**.
