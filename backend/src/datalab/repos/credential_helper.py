"""The git credential helper that hands git the signed-in person's GitHub token.

DataLab names it on each git command that talks to GitHub, and nowhere else
(see `git.py`):

    git -c credential.helper= \
        -c credential.helper='!"<python>" -m datalab.repos.credential_helper' fetch

The empty value first switches off every helper in the person's own git
config for that one command, so the token is never offered to one of them to
store (osxkeychain, `store`, a credential manager).

Git writes `key=value` lines on stdin. For `get` over https to github.com
this answers `username=x-access-token` and `password=<token>` on stdout,
which is a pipe to git. `store` and `erase` do nothing: the token belongs to
DataLab's keychain entry. This never refreshes the token (only DataLab's
process does, so a refresh can't race another); DataLab makes sure it's
fresh before it runs git.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from datalab.repos.github import TokenStore

HOST = "github.com"
USERNAME = "x-access-token"


def parse(text: str) -> dict[str, str]:
    """Git's request: `key=value` lines, up to a blank line."""
    request: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            break
        key, sep, value = line.partition("=")
        if sep:
            request[key] = value
    return request


def answer(operation: str, request: dict[str, str], token: Callable[[], str | None]) -> str:
    """What to write back to git: nothing unless it's a `get` for GitHub over https."""
    if operation != "get":
        return ""
    if request.get("protocol") != "https" or request.get("host", "").lower() != HOST:
        return ""
    value = token()
    if not value or any(c in value for c in "\r\n\0"):
        return ""
    return f"username={USERNAME}\npassword={value}\n"


def _saved_token() -> str | None:
    tokens = TokenStore().load()
    return tokens.access_token if tokens else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    operation = args[0] if args else ""
    request = parse(sys.stdin.read())
    sys.stdout.write(answer(operation, request, _saved_token))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
