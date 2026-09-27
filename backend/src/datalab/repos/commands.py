"""`datalab github sign-in` and `datalab repos sync`: the installer's GitHub steps.

After installing, the installers (installer/) offer the GitHub sign-in for
the lab's two repos, then clone them, with the same code the app uses
(github.py, sync.py). Neither applies to the practice profile, or where the
lab's settings don't name the GitHub App and repos: they say so and return
`SKIPPED`, and the installer carries on.

Exit codes: 0 done, 1 didn't work (the message says why and what to do),
2 skipped (not needed here).
"""

from __future__ import annotations

import os
import time
import webbrowser
from collections.abc import Callable

from datalab.config import Settings
from datalab.repos.github import GitHubAuth, GitHubUnavailable, SignInNeeded, access_message

DONE, FAILED, SKIPPED = 0, 1, 2

Say = Callable[[str], None]


def why_not(settings: Settings, auth: GitHubAuth | None) -> str | None:
    """Why the GitHub steps don't apply here (the same as Settings says)."""
    from datalab.api.github import unavailable

    return unavailable(settings, auth)


def lab_repos(settings: Settings) -> list[tuple[str, str]]:
    """The configured lab repos, as (key in DataLab, owner/name)."""
    repos = settings.repos
    pairs = (("knowledge", repos.knowledge), ("pipelines", repos.pipelines))
    return [(key, name) for key, name in pairs if name is not None]


def sign_in(
    settings: Settings,
    auth: GitHubAuth | None,
    *,
    say: Say = print,
    open_browser: bool = True,
    again: bool = False,
    sleep: Callable[[float], None] | None = None,
    browser: Callable[[str], object] | None = None,
) -> int:
    """Sign in with GitHub's device flow, in the terminal."""
    sleep = sleep or _sleep
    browser = browser or _open_page
    if _as_root():
        # The tokens would go in root's keychain, not the person's.
        say("Sign in to GitHub as yourself, not with sudo (or as root).")
        return FAILED
    why = why_not(settings, auth)
    if why is not None or auth is None:
        say(f"Skipping the GitHub sign-in: {why}")
        return SKIPPED
    if auth.signed_in() and not again:
        account = auth.status().account
        who = f" as @{account.login}" if account else ""
        say(f"Already signed in to GitHub{who}.")
        return _report_access(settings, auth, say)
    try:
        state = auth.start()
        say(
            f"To let DataLab use the lab's repositories, open {state.verification_uri} "
            f"and enter the code {state.user_code}"
        )
        uri = state.verification_uri or ""
        if open_browser and uri.startswith("https://github.com/"):
            browser(uri)
        say("Waiting for GitHub… (press Ctrl-C to skip this for now)")
        while state.state == "waiting":
            sleep(float(state.interval or 5))
            state = auth.poll()
    except KeyboardInterrupt:
        auth.cancel()
        say("Skipped. Sign in later in DataLab, under Settings → GitHub.")
        return FAILED
    except (GitHubUnavailable, SignInNeeded) as error:
        say(f"The GitHub sign-in didn't work: {error} Try again later in Settings → GitHub.")
        return FAILED
    if state.state != "signed in":
        say(f"The GitHub sign-in didn't finish: {state.message or state.state}")
        return FAILED
    who = f" as @{state.account.login}" if state.account else ""
    say(f"Signed in to GitHub{who}.")
    if state.message:
        say(state.message)
    return _report_access(settings, auth, say)


def _as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _open_page(url: str) -> object:
    return webbrowser.open(url)


def sync(settings: Settings, auth: GitHubAuth | None, *, say: Say = print) -> int:
    """Clone (or bring up to date) each configured lab repo. Hold the data
    folder's lock around this (the CLI does)."""
    from datalab import db
    from datalab.repos.sync import RepoSync, SyncState

    why = why_not(settings, auth)
    if why is not None or auth is None:
        say(f"Skipping the lab repos: {why}")
        return SKIPPED
    if not auth.signed_in():
        say("Sign in to GitHub first (datalab github sign-in), then sync the lab's repos.")
        return FAILED
    connection = db.connect(settings.database_file)
    try:
        state = SyncState(connection)
        result = DONE
        for key, repo in lab_repos(settings):
            say(f"Syncing {repo}…")
            repo_sync = RepoSync(
                key, repo, settings.data_dir, auth, state, contact=settings.repos.access_contact
            )
            head = repo_sync.sync()
            if head is not None:
                say(f"  {repo} is up to date ({head[:12]}).")
                continue
            result = FAILED
            status = repo_sync.status()
            say(f"  {status.get('message') or 'The sync failed.'}")
        return result
    finally:
        connection.close()


def _report_access(settings: Settings, auth: GitHubAuth, say: Say) -> int:
    """Say which repos the account can't open, and whom to ask."""
    result = DONE
    account = auth.status().account
    for _, repo in lab_repos(settings):
        try:
            access = auth.repo_access(repo)
        except (GitHubUnavailable, SignInNeeded) as error:
            say(f"Couldn't check your access to {repo}: {error}")
            result = FAILED
            continue
        if access == "none":
            say(
                access_message(
                    repo, account.login if account else None, settings.repos.access_contact
                ).replace("then press Sync", "then run: datalab repos sync")
            )
            result = FAILED
    return result
