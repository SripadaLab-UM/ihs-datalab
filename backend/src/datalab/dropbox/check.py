"""`datalab processed check-read-only`: show that DataLab's Dropbox sign-in
can't change anything in Dropbox.

With the person's own sign-in (the one DataLab uses), it asks Dropbox to
upload a new file, make a folder, delete, move and copy, all inside the
chosen folder, and expects every request to be refused for lack of
permission. It only ever names new, made-up paths, so even if Dropbox
allowed one, no existing file would be touched (and what it made is
removed, and said). Then it checks a real file in the folder is unchanged.
Nothing is printed but outcomes: never a token.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from datalab.config import Settings
from datalab.dropbox.auth import API, CONTENT, DropboxAuth, DropboxUnavailable, SignInNeeded
from datalab.dropbox.index import ProcessedIndex


@dataclass(frozen=True)
class Attempt:
    what: str
    refused: bool
    said: str


def check_read_only(
    auth: DropboxAuth,
    index: ProcessedIndex,
    *,
    http: httpx.Client | None = None,
    say: Callable[[str], None] = print,
) -> int:
    """0 if every write was refused and the folder's files are unchanged, else 1."""
    chosen = index.chosen()
    account = auth.account()
    if chosen is None or account is None:
        say("Connect Dropbox and choose a folder in Settings → Connections first.")
        return 2
    client = http or httpx.Client(timeout=httpx.Timeout(30))
    root = json.dumps({".tag": "root", "root": account.root_namespace})
    marker = f"datalab-write-check-{secrets.token_hex(4)}"
    new = f"{chosen.folder_path}/{marker}"

    def headers(**extra: str) -> dict[str, str]:
        return {**auth.headers(), "Dropbox-API-Path-Root": root, **extra}

    def rpc(endpoint: str, body: dict) -> httpx.Response:
        return client.post(
            f"{API}/2/{endpoint}",
            headers=headers(**{"Content-Type": "application/json"}),
            content=json.dumps(body),
        )

    say(f"Checking that DataLab's Dropbox sign-in can't change {chosen.folder_path}")
    say("Every request below names a new, made-up path; none names an existing file.")
    before = _fingerprint(rpc, chosen.folder_id)
    attempts = [
        _attempt(
            "upload a new file",
            lambda: client.post(
                f"{CONTENT}/2/files/upload",
                headers=headers(
                    **{
                        "Dropbox-API-Arg": json.dumps({"path": f"{new}.txt", "mode": "add"}),
                        "Content-Type": "application/octet-stream",
                    }
                ),
                content=b"DataLab write check: this file should never exist.\n",
            ),
        ),
        _attempt("make a folder", lambda: rpc("files/create_folder_v2", {"path": f"{new}-folder"})),
        _attempt("delete", lambda: rpc("files/delete_v2", {"path": f"{new}-missing.txt"})),
        _attempt(
            "move",
            lambda: rpc(
                "files/move_v2",
                {"from_path": f"{new}-missing.txt", "to_path": f"{new}-moved.txt"},
            ),
        ),
        _attempt(
            "copy",
            lambda: rpc(
                "files/copy_v2",
                {"from_path": f"{new}-missing.txt", "to_path": f"{new}-copied.txt"},
            ),
        ),
    ]
    for attempt in attempts:
        mark = "refused" if attempt.refused else "ALLOWED"
        say(f"  {mark:8} {attempt.what}: {attempt.said}")
    allowed = [a for a in attempts if not a.refused]
    if allowed:
        # Only what it made itself, by its made-up names.
        for path in (f"{new}.txt", f"{new}-folder"):
            rpc("files/delete_v2", {"path": path})
        say("Removed what the check made. DataLab's Dropbox app must have read-only scopes:")
        say("account_info.read, files.metadata.read and files.content.read, nothing else.")
    after = _fingerprint(rpc, chosen.folder_id)
    unchanged = before == after
    say(
        "  The folder's files are unchanged."
        if unchanged
        else "  The folder's files changed while the check ran (by someone else, or this check)."
    )
    ok = not allowed and unchanged
    say("PASS: Dropbox refused every change." if ok else "FAIL: see above.")
    return 0 if ok else 1


def _attempt(what: str, send: Callable[[], httpx.Response]) -> Attempt:
    try:
        answer = send()
    except httpx.HTTPError as error:
        return Attempt(what, False, f"couldn't ask Dropbox ({type(error).__name__})")
    text = answer.text[:300]
    try:
        raw = answer.json()
        summary = str(raw.get("error_summary") or raw.get("error") or "")
    except ValueError:
        summary = text
    # Only a refusal for lack of permission counts (401 missing_scope, or 403):
    # "not found" (409) would mean Dropbox looked for the path, so the app
    # could have written.
    scope = "missing_scope" in text
    refused = answer.status_code == 403 or (answer.status_code == 401 and scope)
    said = f"HTTP {answer.status_code}{', no permission to write' if scope else ''}"
    if summary and not scope:
        said += f" ({summary[:120]})"
    return Attempt(what, refused, said)


def _fingerprint(rpc: Callable[[str, dict], httpx.Response], folder_id: str) -> list[tuple]:
    """Every file under the folder: path, revision and content hash."""
    found: list[tuple] = []
    answer = rpc("files/list_folder", {"path": folder_id, "recursive": True, "limit": 2000})
    while True:
        if answer.status_code != 200:
            raise DropboxUnavailable(f"Dropbox answered {answer.status_code} listing the folder.")
        raw = answer.json()
        found += [
            (e.get("path_lower"), e.get("rev"), e.get("content_hash"))
            for e in raw.get("entries") or []
            if e.get(".tag") == "file"
        ]
        if not raw.get("has_more"):
            return sorted(found)
        answer = rpc("files/list_folder/continue", {"cursor": raw["cursor"]})


def run(settings: Settings) -> int:
    """The CLI command (cli.py)."""
    from datalab import db

    if settings.profile == "practice" or not settings.dropbox.app_key:
        print("This needs the real DataLab with [dropbox] app_key set, and Dropbox connected.")
        return 2
    connection = db.connect(settings.database_file)
    try:
        return check_read_only(DropboxAuth(settings.dropbox.app_key), ProcessedIndex(connection))
    except SignInNeeded as error:
        print(error)
        return 2
    finally:
        connection.close()
