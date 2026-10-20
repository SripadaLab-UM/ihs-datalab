"""The Dropbox API calls DataLab makes, as the signed-in person.

Only reads: listing a folder (and what changed since), one item's metadata,
and downloading one file. Every call goes to Dropbox's own API hosts with the
person's token in a header, and names the team space as its root, so the
lab's team folders are where Dropbox's web page shows them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from datalab.dropbox.auth import API, CONTENT, DropboxAuth, DropboxUnavailable

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30, read=120)
# How often a call is tried when Dropbox says to slow down (429) or is busy (503).
_ATTEMPTS = 4
_LONGEST_WAIT = 60.0
# Dropbox's content hash: SHA-256 of the SHA-256s of each 4 MB block.
_BLOCK = 4 * 1024 * 1024


class DropboxRefused(RuntimeError):
    """Dropbox answered a request with an error about it (409): a path that
    doesn't exist, a folder where a file was asked for, a cursor too old."""

    def __init__(self, summary: str) -> None:
        self.summary = summary
        super().__init__(summary)


class TooLarge(RuntimeError):
    pass


class TookTooLong(RuntimeError):
    pass


@dataclass(frozen=True)
class Item:
    """A file or folder as Dropbox lists it; `deleted` for one that's gone."""

    tag: str  # "file", "folder" or "deleted"
    name: str
    path_display: str
    path_lower: str
    id: str = ""
    size: int = 0
    modified: str | None = None
    content_hash: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Item:
        return cls(
            tag=str(raw.get(".tag", "")),
            name=str(raw.get("name", "")),
            path_display=str(raw.get("path_display") or raw.get("path_lower") or ""),
            path_lower=str(raw.get("path_lower") or ""),
            id=str(raw.get("id") or ""),
            size=int(raw.get("size") or 0),
            modified=raw.get("server_modified"),
            content_hash=raw.get("content_hash"),
        )


@dataclass(frozen=True)
class Page:
    items: list[Item]
    cursor: str
    has_more: bool


class DropboxClient:
    def __init__(
        self,
        auth: DropboxAuth,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._auth = auth
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._sleep = sleep

    def root_namespace(self) -> str:
        account = self._auth.account()
        if account is None:
            raise DropboxUnavailable("Connect Dropbox in Settings first.")
        return account.root_namespace

    # Listing ----------------------------------------------------------------

    def list_folder(self, path: str, *, recursive: bool = False) -> Iterator[Page]:
        """Every page of a folder's listing ("" is the team space's top level)."""
        page = self._page(
            self._rpc(
                "files/list_folder",
                {
                    "path": path,
                    "recursive": recursive,
                    "include_deleted": False,
                    "include_non_downloadable_files": False,
                    "limit": 2000,
                },
            )
        )
        yield page
        while page.has_more:
            page = self.changes(page.cursor)
            yield page

    def changes(self, cursor: str) -> Page:
        """What changed since `cursor` (deleted items included). DropboxRefused
        with `reset` in its summary when the cursor is too old: list again."""
        return self._page(self._rpc("files/list_folder/continue", {"cursor": cursor}))

    def metadata(self, path: str) -> Item:
        return Item.from_raw(self._rpc("files/get_metadata", {"path": path}))

    # Downloading ------------------------------------------------------------

    def download(
        self,
        path: str,
        dest: Path,
        *,
        max_bytes: int,
        deadline: float,
        byte_range: tuple[int, int] | None = None,
    ) -> Item:
        """Stream one file to `dest` (written as dest.partial, then renamed).

        Refused (TooLarge) as soon as it passes `max_bytes`; TookTooLong past
        `deadline` (a time.monotonic() value). The whole file's content hash
        is checked against what Dropbox says it is. `byte_range` (first, last)
        fetches only part of it, unchecked."""
        partial = dest.with_name(dest.name + ".partial")
        headers = {"Dropbox-API-Arg": json.dumps({"path": path}, ensure_ascii=True)}
        if byte_range is not None:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        try:
            with self._send("POST", f"{CONTENT}/2/files/download", headers) as answer:
                result = Item.from_raw(json.loads(answer.headers.get("Dropbox-API-Result", "{}")))
                if byte_range is None and result.size > max_bytes:
                    raise TooLarge(f"{result.size} bytes")
                written = 0
                hasher = ContentHasher()
                with partial.open("wb") as out:
                    for chunk in answer.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise TooLarge(f"over {max_bytes} bytes")
                        if time.monotonic() > deadline:
                            raise TookTooLong("the download ran past its deadline")
                        hasher.update(chunk)
                        out.write(chunk)
            if (
                byte_range is None
                and result.content_hash
                and hasher.hexdigest() != result.content_hash
            ):
                raise DropboxUnavailable(
                    "The downloaded file didn't match Dropbox's copy. Try again."
                )
            partial.replace(dest)
            return result
        finally:
            partial.unlink(missing_ok=True)

    # ------------------------------------------------------------------------

    def _rpc(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        with self._send("POST", f"{API}/2/{endpoint}", headers, content=json.dumps(body)) as answer:
            answer.read()
            raw = answer.json()
        return raw if isinstance(raw, dict) else {}

    def _send(self, method: str, url: str, headers: dict[str, str], **kwargs: Any):
        """A response that succeeded, as a context manager (closed on exit).
        Retries when Dropbox asks to slow down; raises for anything else."""
        for attempt in range(1, _ATTEMPTS + 1):
            request = self._http.build_request(
                method,
                url,
                headers={
                    **headers,
                    **self._auth.headers(),
                    "Dropbox-API-Path-Root": json.dumps(
                        {".tag": "root", "root": self.root_namespace()}
                    ),
                },
                **kwargs,
            )
            try:
                answer = self._http.send(request, stream=True)
            except httpx.HTTPError as error:
                raise DropboxUnavailable(
                    f"Dropbox couldn't be reached ({type(error).__name__})."
                ) from None
            status = answer.status_code
            if status in (200, 206):
                return _Closing(answer)
            text = _read_small(answer)
            if status in (429, 503) and attempt < _ATTEMPTS:
                wait = _retry_after(answer, text, attempt)
                log.info("Dropbox asked to wait %.0f s (%s)", wait, status)
                self._sleep(wait)
                continue
            if status == 401:
                self._auth.refused()
            if status == 409:
                raise DropboxRefused(_summary(text) or "Dropbox refused the request.")
            raise DropboxUnavailable(f"Dropbox answered {status}. Try again later.")
        raise DropboxUnavailable("Dropbox is busy. Try again later.")

    def _page(self, raw: dict[str, Any]) -> Page:
        return Page(
            items=[Item.from_raw(e) for e in raw.get("entries") or [] if isinstance(e, dict)],
            cursor=str(raw.get("cursor") or ""),
            has_more=bool(raw.get("has_more")),
        )


class _Closing:
    """A streamed response, closed when the `with` block ends."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def __enter__(self) -> httpx.Response:
        return self._response

    def __exit__(self, *_: object) -> None:
        self._response.close()


class ContentHasher:
    """Dropbox's content hash, computed as the bytes arrive."""

    def __init__(self) -> None:
        self._overall = hashlib.sha256()
        self._block = hashlib.sha256()
        self._in_block = 0

    def update(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            take = min(len(view), _BLOCK - self._in_block)
            self._block.update(view[:take])
            self._in_block += take
            view = view[take:]
            if self._in_block == _BLOCK:
                self._overall.update(self._block.digest())
                self._block = hashlib.sha256()
                self._in_block = 0

    def hexdigest(self) -> str:
        overall = self._overall.copy()
        if self._in_block:
            overall.update(self._block.digest())
        return overall.hexdigest()


def content_hash(path: Path) -> str:
    hasher = ContentHasher()
    with path.open("rb") as handle:
        while chunk := handle.read(_BLOCK):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_small(answer: httpx.Response) -> str:
    try:
        return answer.read()[:4000].decode("utf-8", "replace")
    except httpx.HTTPError:
        return ""
    finally:
        answer.close()


def _summary(text: str) -> str:
    try:
        raw = json.loads(text)
    except ValueError:
        return ""
    return str(raw.get("error_summary") or "")[:200] if isinstance(raw, dict) else ""


def _retry_after(answer: httpx.Response, text: str, attempt: int) -> float:
    try:
        wait = float(answer.headers.get("Retry-After", ""))
    except ValueError:
        try:
            wait = float(json.loads(text)["error"]["retry_after"])
        except (ValueError, KeyError, TypeError):
            wait = 2.0**attempt
    return max(1.0, min(wait, _LONGEST_WAIT))
