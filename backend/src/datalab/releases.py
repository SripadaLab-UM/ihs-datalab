"""Checking GitHub for a newer DataLab release (milestone 7).

DataLab's host process asks the GitHub Releases API of the app repo
(`[updates] repository`) for its releases, without signing in: the app repo
is public (docs/DISTRIBUTION.md, "Decided"). Nothing else ever makes this
call: not the browser, and never a container. No token is sent, so the
GitHub sign-in for the lab repos plays no part.

Which release is offered (docs/DISTRIBUTION.md, "Which releases are offered"):

- never a draft, and only a release whose tag is a PEP 440 version newer
  than the installed one (`v0.1.0-alpha.3` is 0.1.0a3);
- pre-releases only on the "pre-release" channel, or on "auto" while the
  installed DataLab is itself a pre-release;
- only a release that carries everything an update needs: the DataLab
  package for exactly that version, `constraints.txt`, `images.json` and
  `SHA256SUMS`, with `SHA256SUMS` listing the other three (and agreeing with
  the checksums GitHub keeps for each file, where it has them). Anything
  else is skipped, and the newest of the rest is offered.

Being offline, rate-limited, or unable to see the releases (while the repo
was private, GitHub answered 404) never shows as an error: the check says it
couldn't check, and DataLab carries on.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from packaging.version import InvalidVersion, Version

from datalab import __version__
from datalab.config import Settings

log = logging.getLogger(__name__)

API = "https://api.github.com"
SUMS = "SHA256SUMS"
CONSTRAINTS = "constraints.txt"
IMAGES = "images.json"
# The most release notes shown, and the most of SHA256SUMS read.
MAX_NOTES = 20_000
MAX_SUMS_BYTES = 64 * 1024
# Asked for by the person ("Check now"): at most this often.
MIN_CHECK_SECONDS = 60
_TIMEOUT = httpx.Timeout(connect=10, read=30, write=10, pool=10)
_HEADERS = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"}
_WHEEL = re.compile(r"datalab-([A-Za-z0-9.+!]+)-py3-none-any\.whl")
_SHA256 = re.compile(r"[0-9a-f]{64}")

CheckState = Literal[
    "not-checked", "up-to-date", "available", "offline", "rate-limited", "not-visible", "failed"
]


class CheckProblem(RuntimeError):
    """The check couldn't finish. `state` says how, for the Updates section."""

    def __init__(self, state: CheckState, message: str, retry_at: float | None = None) -> None:
        super().__init__(message)
        self.state = state
        self.retry_at = retry_at


class ChecksumMismatch(RuntimeError):
    """A release's files don't match its SHA256SUMS (or GitHub's own checksums)."""


def parse_version(text: str) -> Version | None:
    """A tag or package version as PEP 440 sees it ("v0.1.0-alpha.2" is 0.1.0a2)."""
    try:
        return Version(text.strip())
    except InvalidVersion:
        return None


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


@dataclass(frozen=True)
class Asset:
    name: str
    # The API's address for the file (`.../releases/assets/<id>`).
    url: str
    size: int
    # GitHub's own SHA-256 of the file, where it keeps one.
    sha256: str | None = None


@dataclass(frozen=True)
class Release:
    tag: str
    version: str  # normalised PEP 440
    prerelease: bool
    title: str
    notes: str
    published_at: str | None
    page: str | None
    wheel: Asset
    constraints: Asset
    images: Asset
    sums: Asset

    @property
    def files(self) -> tuple[Asset, ...]:
        """What an update downloads, besides SHA256SUMS."""
        return (self.wheel, self.constraints, self.images)


def releases_from(raw: Any, repository: str) -> tuple[list[Release], list[str]]:
    """The releases GitHub listed that an update could install, and why each other was skipped."""
    found: list[Release] = []
    skipped: list[str] = []
    if not isinstance(raw, list):
        raise CheckProblem("failed", "GitHub's list of releases couldn't be read.")
    for item in raw:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag_name") or "")
        if item.get("draft"):
            continue
        release, why = _release(item, tag, repository)
        if release is None:
            skipped.append(f"{tag or '(no tag)'}: {why}")
        else:
            found.append(release)
    return found, skipped


def include_prereleases(channel: str, current: str) -> bool:
    if channel == "pre-release":
        return True
    if channel == "stable":
        return False
    version = parse_version(current)
    return version is not None and version.is_prerelease


def choose(releases: list[Release], current: str, channel: str) -> Release | None:
    """The newest release to offer on `channel`, newer than `current`, if any."""
    pre = include_prereleases(channel, current)
    offered = [r for r in releases if is_newer(r.version, current) and (pre or not r.prerelease)]
    return max(offered, key=lambda r: Version(r.version), default=None)


def parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS as `sha256sum` writes it: `<hex>  <name>` (or `<hex> *<name>`).

    Only plain file names are accepted, and a name listed twice with two
    checksums makes the whole file unusable."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if match is None:
            raise ChecksumMismatch(f"SHA256SUMS has a line that isn't a checksum: {line[:80]!r}")
        digest, name = match.group(1).lower(), match.group(2)
        if not _plain_name(name):
            raise ChecksumMismatch(f"SHA256SUMS names something that isn't a plain file: {name!r}")
        if sums.get(name, digest) != digest:
            raise ChecksumMismatch(f"SHA256SUMS lists {name} twice, with different checksums.")
        sums[name] = digest
    return sums


def expected_sha256(release: Release, sums: dict[str, str], asset: Asset) -> str:
    """The checksum `asset` must have: SHA256SUMS's, which GitHub's own must match."""
    listed = sums.get(asset.name)
    if listed is None:
        raise ChecksumMismatch(f"SHA256SUMS for {release.tag} doesn't list {asset.name}.")
    if asset.sha256 is not None and asset.sha256 != listed:
        raise ChecksumMismatch(
            f"{asset.name} in {release.tag}: SHA256SUMS and GitHub have different checksums."
        )
    return listed


def check_listing(release: Release, sums: dict[str, str]) -> dict[str, str]:
    """Every file an update downloads, with the checksum it must have."""
    return {a.name: expected_sha256(release, sums, a) for a in release.files}


# ------------------------------------------------------------------ GitHub


class ReleaseSource:
    """The app repo's releases on GitHub, read without signing in."""

    def __init__(self, repository: str, *, http: httpx.Client | None = None) -> None:
        self.repository = repository
        self._http = http or httpx.Client(timeout=_TIMEOUT)

    def list(self) -> list[Any]:
        response = self._get(f"{API}/repos/{self.repository}/releases", {"per_page": "30"})
        try:
            return response.json()
        except ValueError:
            raise CheckProblem("failed", "GitHub's list of releases couldn't be read.") from None

    def read_small(self, asset: Asset, limit: int) -> bytes:
        """A small release file (SHA256SUMS), whole, checked against GitHub's checksum."""
        if asset.size > limit:
            raise ChecksumMismatch(f"{asset.name} is larger than a checksum file should be.")
        chunks = bytearray()
        with self._download(asset) as response:
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > limit:
                    raise ChecksumMismatch(
                        f"{asset.name} is larger than a checksum file should be."
                    )
        data = bytes(chunks)
        if asset.sha256 is not None and hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ChecksumMismatch(f"{asset.name} doesn't match GitHub's checksum for it.")
        return data

    def download(self, asset: Asset, target: Path, *, sha256: str, cancel=None) -> None:
        """Save a release file to `target`, refusing it unless it has `sha256`.

        It's written beside `target` first and moved into place only once
        whole and checked, so `target` is never a partial or wrong file."""
        partial = target.with_name(f".{target.name}.partial")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with self._download(asset) as response, partial.open("wb") as out:
                for chunk in response.iter_bytes():
                    if cancel is not None and cancel():
                        raise CheckProblem("failed", "The download was cancelled.")
                    size += len(chunk)
                    if size > max(asset.size, 1) * 2 + 1024:
                        raise ChecksumMismatch(f"{asset.name} is larger than GitHub said.")
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != sha256:
                raise ChecksumMismatch(f"{asset.name} doesn't match its checksum in SHA256SUMS.")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _download(self, asset: Asset):
        prefix = f"{API}/repos/{self.repository}/releases/assets/"
        if not asset.url.startswith(prefix):
            raise ChecksumMismatch(f"{asset.name} isn't a file of {self.repository}'s releases.")
        url = asset.url
        # Redirects are followed by hand, and only to https: GitHub sends
        # release files from its own storage host.
        for _ in range(5):
            try:
                request = self._http.build_request(
                    "GET", url, headers={"accept": "application/octet-stream"}
                )
                response = self._http.send(request, stream=True, follow_redirects=False)
            except httpx.HTTPError as error:
                raise _offline(error) from None
            if response.is_redirect:
                location = response.headers.get("location", "")
                response.close()
                target = urlsplit(str(httpx.URL(url).join(location)))
                if target.scheme != "https" or not target.hostname:
                    raise CheckProblem("failed", "GitHub sent the download somewhere unexpected.")
                url = target.geturl()
                continue
            try:
                _raise_for(response)
                yield response
            finally:
                response.close()
            return
        raise CheckProblem("failed", "GitHub redirected the download too many times.")

    def _get(self, url: str, params: dict[str, str]) -> httpx.Response:
        try:
            response = self._http.get(url, params=params, headers=_HEADERS)
        except httpx.HTTPError as error:
            raise _offline(error) from None
        _raise_for(response)
        return response


def _offline(error: httpx.HTTPError) -> CheckProblem:
    log.info("update check: GitHub not reachable (%s)", type(error).__name__)
    return CheckProblem("offline", "Couldn't reach GitHub.")


def _raise_for(response: httpx.Response) -> None:
    status = response.status_code
    if status == 200:
        return
    remaining = response.headers.get("x-ratelimit-remaining")
    if status == 429 or (status == 403 and remaining == "0"):
        retry_at = _retry_at(response)
        raise CheckProblem("rate-limited", "GitHub asked DataLab to wait.", retry_at)
    if status == 404:
        raise CheckProblem("not-visible", "GitHub didn't show the releases.")
    raise CheckProblem("failed", f"GitHub answered {status}.")


def _retry_at(response: httpx.Response) -> float:
    now = time.time()
    with contextlib.suppress(ValueError, TypeError):
        after = response.headers.get("retry-after")
        if after is not None:
            return now + max(float(after), 1)
    with contextlib.suppress(ValueError, TypeError):
        reset = response.headers.get("x-ratelimit-reset")
        if reset is not None:
            return max(float(reset), now + 1)
    return now + 15 * 60


# ------------------------------------------------------------------ checker


@dataclass(frozen=True)
class CheckResult:
    state: CheckState
    message: str
    current: str
    channel: str
    checked_at: str | None = None
    release: Release | None = None
    # The checksum each of the release's files must have, from its SHA256SUMS.
    expected: dict[str, str] = field(default_factory=dict)
    # Don't ask GitHub again before this (seconds since the epoch), if set.
    retry_at: float | None = None


class UpdateChecker:
    """The last update check, and checking again. Thread-safe; the network
    call runs on the caller's thread (never the event loop)."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: ReleaseSource | None = None,
        current: str = __version__,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.current = current
        self.source = source or ReleaseSource(settings.updates.repository)
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        channel = settings.updates.channel
        off = not settings.updates.check_on_start
        self._last = CheckResult(
            "not-checked",
            "Checking for updates when DataLab starts is off (updates.check_on_start). "
            "Check now to look."
            if off
            else "Not checked yet.",
            current,
            channel,
        )

    @property
    def last(self) -> CheckResult:
        return self._last

    def check_on_start(self) -> CheckResult | None:
        """At startup, if `updates.check_on_start` allows it."""
        if not self.settings.updates.check_on_start:
            return None
        return self.check()

    def check(self) -> CheckResult:
        """Ask GitHub, unless it asked DataLab to wait or the last check was a moment ago."""
        with self._lock:
            now = self._clock()
            last = self._last
            if last.retry_at is not None and now < last.retry_at:
                return last
            if self._checked_at is not None and now - self._checked_at < MIN_CHECK_SECONDS:
                return last
            self._checked_at = now
            self._last = self._check(now)
            return self._last

    def _check(self, now: float) -> CheckResult:
        channel = self.settings.updates.channel
        base = CheckResult("up-to-date", "", self.current, channel, checked_at=_iso(now))
        try:
            releases, skipped = releases_from(self.source.list(), self.source.repository)
            for why in skipped:
                log.info("update check skipped %s", why)
            release = choose(releases, self.current, channel)
            if release is None:
                return replace(base, message=self._up_to_date(channel))
            sums = parse_sums(
                self.source.read_small(release.sums, MAX_SUMS_BYTES).decode("utf-8", "replace")
            )
            expected = check_listing(release, sums)
        except CheckProblem as problem:
            return replace(
                base,
                state=problem.state,
                message=self._problem(problem),
                retry_at=problem.retry_at,
            )
        except ChecksumMismatch as error:
            log.warning("update check: %s", error)
            return replace(
                base,
                state="failed",
                message=f"A newer release was found, but its checksums don't add up ({error}), "
                "so DataLab won't offer it.",
            )
        return replace(
            base,
            state="available",
            message=f"DataLab {release.version} is available.",
            release=release,
            expected=expected,
        )

    def _up_to_date(self, channel: str) -> str:
        which = (
            "release"
            if not include_prereleases(channel, self.current)
            else "release or pre-release"
        )
        return f"DataLab {self.current} is the newest {which}."

    def _problem(self, problem: CheckProblem) -> str:
        if problem.state == "offline":
            return (
                "Couldn't reach GitHub to check for updates (no internet connection?). "
                "DataLab checks again when it next starts."
            )
        if problem.state == "rate-limited":
            until = (
                datetime.fromtimestamp(problem.retry_at).astimezone().strftime("%H:%M")
                if problem.retry_at
                else "later"
            )
            return f"GitHub asked DataLab to wait before checking again (until {until})."
        if problem.state == "not-visible":
            contact = self.settings.repos.access_contact or "the DataLab maintainer"
            return (
                f"Can't check for updates: GitHub doesn't show the releases of "
                f"{self.source.repository} without signing in (the repository may not be "
                f"public yet). Ask {contact} when a new version is out."
            )
        return f"Couldn't check for updates: {problem}"


# ------------------------------------------------------------------ helpers


def _release(item: dict[str, Any], tag: str, repository: str) -> tuple[Release | None, str]:
    version = parse_version(tag)
    if version is None:
        return None, "the tag isn't a version"
    assets: dict[str, Asset] = {}
    wheels: list[Asset] = []
    for raw in item.get("assets") or []:
        asset = _asset(raw, repository)
        if asset is None:
            continue
        assets[asset.name] = asset
        match = _WHEEL.fullmatch(asset.name)
        if match and parse_version(match.group(1)) == version:
            wheels.append(asset)
    missing = [n for n in (CONSTRAINTS, IMAGES, SUMS) if n not in assets]
    if len(wheels) != 1:
        missing.insert(0, f"the DataLab {version} package")
    if missing:
        return None, f"it doesn't have {', '.join(missing)}"
    notes = str(item.get("body") or "")
    return (
        Release(
            tag=tag,
            version=str(version),
            prerelease=bool(item.get("prerelease")) or version.is_prerelease,
            title=str(item.get("name") or f"DataLab {tag}")[:200],
            notes=notes[:MAX_NOTES],
            published_at=_text(item.get("published_at")),
            page=_github_page(item.get("html_url")),
            wheel=wheels[0],
            constraints=assets[CONSTRAINTS],
            images=assets[IMAGES],
            sums=assets[SUMS],
        ),
        "",
    )


def _asset(raw: Any, repository: str) -> Asset | None:
    if not isinstance(raw, dict):
        return None
    name, url = raw.get("name"), raw.get("url")
    if not isinstance(name, str) or not isinstance(url, str) or not _plain_name(name):
        return None
    if not url.startswith(f"{API}/repos/{repository}/releases/assets/"):
        return None
    digest = raw.get("digest")
    sha256 = None
    if isinstance(digest, str) and digest.startswith("sha256:"):
        value = digest.removeprefix("sha256:").lower()
        sha256 = value if _SHA256.fullmatch(value) else None
    size = raw.get("size")
    return Asset(name, url, size if isinstance(size, int) and size >= 0 else 0, sha256)


def _plain_name(name: str) -> bool:
    return (
        bool(name)
        and name not in (".", "..")
        and not any(c in name for c in "/\\\0")
        and len(name) <= 200
    )


def _github_page(value: Any) -> str | None:
    """The release's page, only if it's on github.com (it's shown as a link)."""
    if isinstance(value, str) and value.startswith("https://github.com/"):
        return value
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")
