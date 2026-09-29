"""The update check: GitHub's releases, versions, channels, checksums, and
being offline, rate-limited, or unable to see the releases."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from datalab import releases
from datalab.config import RepoSettings, Settings, UpdateSettings
from datalab.releases import ChecksumMismatch, ReleaseSource, UpdateChecker
from tests.release_fakes import (
    OTHER_PRIVATE,
    REPO,
    TEST_PUBLIC,
    FakeGitHub,
    sha256,
    sums_for,
    wheel_bytes,
)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


def settings_for(tmp_path: Path, **updates) -> Settings:
    return Settings(
        profile="real",
        data_dir=tmp_path / "data",
        oracle=None,
        updates=UpdateSettings(**updates),
        repos=RepoSettings(access_contact="Ali, the DataLab maintainer"),
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def checker(
    tmp_path, github, *, current="0.1.0a2", clock=None, keys=(TEST_PUBLIC,), **updates
) -> UpdateChecker:
    return UpdateChecker(
        settings_for(tmp_path, **updates),
        source=ReleaseSource(REPO, http=github.client()),
        current=current,
        clock=clock or Clock(),
        keys=keys,
    )


# ------------------------------------------------------------------ versions


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("v0.1.0-alpha.3", "0.1.0a2", True),
        ("v0.1.0", "0.1.0a2", True),  # the release a pre-release leads up to
        ("v0.1.0-alpha.2", "0.1.0a2", False),  # the same one, in the tag's spelling
        ("v0.1.0-alpha.1", "0.1.0a2", False),
        ("v0.1.0-rc.1", "0.1.0b9", True),
        ("v0.10.0", "0.9.0", True),  # not compared as text
        ("v0.2.0.dev1", "0.1.0", True),
        ("latest", "0.1.0a2", False),
        ("v0.2.0", "not-a-version", False),
    ],
)
def test_versions_compare_as_pep_440(candidate, current, newer):
    assert releases.is_newer(candidate, current) is newer


def test_tags_read_as_the_package_version():
    assert str(releases.parse_version("v0.1.0-alpha.2")) == "0.1.0a2"
    assert releases.parse_version("nightly") is None


# ------------------------------------------------------------------ parsing


def test_it_offers_only_complete_releases_newer_than_this_one(tmp_path, github):
    github.release("v0.1.0-alpha.1", prerelease=True)  # older
    github.release("v0.1.0-alpha.3", prerelease=True)
    github.release("v0.1.0-alpha.4", prerelease=True, draft=True)  # a draft: never
    github.release("v0.1.0-alpha.5", prerelease=True, omit=("SHA256SUMS",))
    github.release("v0.1.0-alpha.6", prerelease=True, omit=("images.json",))
    github.release("nightly", prerelease=True, version="0.1.0a7")  # not a version tag
    found = checker(tmp_path, github).check()
    assert found.state == "available"
    assert found.release is not None and found.release.version == "0.1.0a3"
    assert found.release.tag == "v0.1.0-alpha.3"
    assert found.release.notes == "What's new."
    assert found.release.page == f"https://github.com/{REPO}/releases/tag/v0.1.0-alpha.3"
    assert found.message == "DataLab 0.1.0a3 is available."
    # The files an update downloads, each with the checksum it must have.
    assert set(found.expected) == {
        "datalab-0.1.0a3-py3-none-any.whl",
        "requirements.txt",
        "images.json",
    }


def test_a_release_whose_package_is_for_another_version_is_skipped(tmp_path, github):
    github.release(
        "v0.2.0",
        files={
            "datalab-0.1.9-py3-none-any.whl": wheel_bytes("0.1.9"),
            "requirements.txt": b"",
            "images.json": b"{}",
        },
    )
    assert checker(tmp_path, github, current="0.1.0").check().state == "up-to-date"


def test_assets_of_another_repository_are_ignored():
    raw = [
        {
            "tag_name": "v0.2.0",
            "assets": [
                {
                    "name": name,
                    "url": f"https://api.github.com/repos/someone/else/releases/assets/{i}",
                    "size": 1,
                }
                for i, name in enumerate(
                    [
                        "datalab-0.2.0-py3-none-any.whl",
                        "requirements.txt",
                        "images.json",
                        "SHA256SUMS",
                        "SHA256SUMS.sig",
                    ]
                )
            ],
        }
    ]
    found, skipped = releases.releases_from(raw, REPO)
    assert found == [] and skipped and "doesn't have" in skipped[0]


@pytest.mark.parametrize(
    ("channel", "current", "offered"),
    [
        ("auto", "0.1.0a2", "0.2.0a1"),  # a pre-release gets pre-releases
        ("auto", "0.1.0", "0.1.1"),  # a release gets only releases
        ("stable", "0.1.0a2", "0.1.1"),
        ("pre-release", "0.1.0", "0.2.0a1"),
    ],
)
def test_the_channel_decides_whether_pre_releases_are_offered(
    tmp_path, github, channel, current, offered
):
    github.release("v0.1.1")
    github.release("v0.2.0-alpha.1", prerelease=True)
    found = checker(tmp_path, github, current=current, channel=channel).check()
    assert found.release is not None and found.release.version == offered


def test_a_pre_release_version_counts_even_if_github_isnt_told(tmp_path, github):
    github.release("v0.2.0-rc.1", prerelease=False)
    assert checker(tmp_path, github, current="0.1.0").check().state == "up-to-date"


def test_up_to_date_says_so(tmp_path, github):
    github.release("v0.1.0-alpha.2", prerelease=True)
    found = checker(tmp_path, github).check()
    assert found.state == "up-to-date" and found.release is None
    assert found.message == "DataLab 0.1.0a2 is the newest release or pre-release."


# ------------------------------------------------------------------ checksums


def test_sha256sums_is_read_as_sha256sum_writes_it():
    a, b = "a" * 64, "B" * 64
    assert releases.parse_sums(f"{a}  one.whl\n{b} *two.txt\n\n") == {
        "one.whl": a,
        "two.txt": b.lower(),
    }


@pytest.mark.parametrize(
    "text",
    [
        "not a checksum line\n",
        f"{'a' * 64}  ../escape.whl\n",
        f"{'a' * 64}  dir/file\n",
        f"{'a' * 64}  same\n{'b' * 64}  same\n",
        f"{'a' * 63}  short\n",
    ],
)
def test_a_sha256sums_that_isnt_plain_is_refused(text):
    with pytest.raises(ChecksumMismatch):
        releases.parse_sums(text)


def test_a_release_whose_sums_dont_list_its_package_isnt_offered(tmp_path, github):
    files = {
        "datalab-0.1.0a3-py3-none-any.whl": wheel_bytes("0.1.0a3"),
        "requirements.txt": b"",
        "images.json": b"{}",
    }
    github.release(
        "v0.1.0-alpha.3",
        prerelease=True,
        files=files,
        sums=sums_for({"requirements.txt": b"", "images.json": b"{}"}),
    )
    found = checker(tmp_path, github).check()
    assert found.state == "failed" and found.release is None
    assert "checksums don't add up" in found.message


def test_sums_that_disagree_with_githubs_own_checksums_arent_trusted(tmp_path, github):
    github.release(
        "v0.1.0-alpha.3",
        prerelease=True,
        digests={"requirements.txt": "sha256:" + "0" * 64},
    )
    found = checker(tmp_path, github).check()
    assert found.state == "failed" and "SHA256SUMS and GitHub" in found.message


def test_a_sha256sums_file_changed_after_upload_isnt_read(tmp_path, github):
    github.release("v0.1.0-alpha.3", prerelease=True, digests={"SHA256SUMS": "sha256:" + "1" * 64})
    assert checker(tmp_path, github).check().state == "failed"


# ------------------------------------------------------------------ quietly


def test_offline_is_a_quiet_state_not_an_error(tmp_path, github):
    def offline(request):
        raise httpx.ConnectError("no route to host", request=request)

    github.answer = offline
    found = checker(tmp_path, github).check()
    assert found.state == "offline" and found.release is None
    assert "Couldn't reach GitHub" in found.message
    assert "no route" not in found.message


def test_rate_limited_waits_until_github_says(tmp_path, github):
    clock = Clock()
    github.answer = lambda request: httpx.Response(
        403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(clock.now) + 900)},
        json={"message": "API rate limit exceeded"},
    )
    check = checker(tmp_path, github, clock=clock)
    found = check.check()
    assert found.state == "rate-limited" and "wait before checking again" in found.message
    asked = len(github.requests)
    clock.now += 600  # still inside the window, and past the minute between checks
    assert check.check().state == "rate-limited"
    assert len(github.requests) == asked  # GitHub wasn't asked again
    github.answer = None
    github.release("v0.1.0-alpha.3", prerelease=True)
    clock.now += 400
    assert check.check().state == "available"


def test_429_is_rate_limited_too(tmp_path, github):
    github.answer = lambda request: httpx.Response(429, headers={"retry-after": "30"})
    assert checker(tmp_path, github).check().state == "rate-limited"


def test_releases_github_wont_show_are_a_quiet_cant_check(tmp_path, github):
    """While the app repo was private, GitHub answered 404 without a sign-in."""
    github.answer = lambda request: httpx.Response(404, json={"message": "Not Found"})
    found = checker(tmp_path, github).check()
    assert found.state == "not-visible" and found.release is None
    assert "Can't check for updates" in found.message
    assert "Ali, the DataLab maintainer" in found.message


def test_the_check_never_sends_a_token(tmp_path, github):
    github.release("v0.1.0-alpha.3", prerelease=True)
    checker(tmp_path, github).check()
    assert github.requests
    assert all("authorization" not in r.headers for r in github.requests)
    assert all(r.url.scheme == "https" for r in github.requests)


def test_asking_again_within_a_minute_uses_the_last_answer(tmp_path, github):
    clock = Clock()
    check = checker(tmp_path, github, clock=clock)
    check.check()
    asked = len(github.requests)
    clock.now += 30
    check.check()
    assert len(github.requests) == asked
    clock.now += 31
    check.check()
    assert len(github.requests) > asked


def test_check_on_start_off_means_no_request_at_start(tmp_path, github):
    check = checker(tmp_path, github, check_on_start=False)
    assert check.check_on_start() is None
    assert github.requests == []
    assert check.last.state == "not-checked" and "check_on_start" in check.last.message
    assert "about once an hour" in check.last.message
    check.set_every_hour(False)
    assert "once an hour" not in check.last.message
    github.release("v0.1.0-alpha.3", prerelease=True)
    assert check.check().state == "available"  # "Check now" still works


def test_a_list_github_cant_explain_is_failed_not_a_crash(tmp_path, github):
    github.answer = lambda request: httpx.Response(200, content=b"<html>")
    assert checker(tmp_path, github).check().state == "failed"
    github.answer = lambda request: httpx.Response(500)
    assert checker(tmp_path, github).check().state == "failed"


# ------------------------------------------------------------------ downloads


def available(tmp_path, github):
    github.release("v0.1.0-alpha.3", prerelease=True)
    found = checker(tmp_path, github).check()
    assert found.release is not None
    return found


def test_a_download_is_checked_before_it_lands(tmp_path, github):
    found = available(tmp_path, github)
    source = ReleaseSource(REPO, http=github.client())
    release = found.release
    assert release is not None
    target = tmp_path / "dl" / "requirements.txt"
    source.download(release.requirements, target, sha256=found.expected["requirements.txt"])
    assert target.read_bytes().startswith(b"# This file was autogenerated by uv")
    assert all("authorization" not in r.headers for r in github.requests)

    wrong = tmp_path / "dl" / "images.json"
    with pytest.raises(ChecksumMismatch):
        source.download(release.images, wrong, sha256=sha256(b"something else"))
    assert not wrong.exists()
    assert [p.name for p in wrong.parent.iterdir()] == ["requirements.txt"]  # no partial file


def test_a_download_redirected_off_https_is_refused(tmp_path, github):
    found = available(tmp_path, github)
    release = found.release
    assert release is not None
    github.storage = "http://release-assets.githubusercontent.com"
    with pytest.raises(releases.CheckProblem):
        ReleaseSource(REPO, http=github.client()).download(
            release.requirements, tmp_path / "c.txt", sha256=found.expected["requirements.txt"]
        )
    assert not (tmp_path / "c.txt").exists()


def test_only_the_repos_own_release_files_are_downloaded(tmp_path, github):
    found = available(tmp_path, github)
    release = found.release
    assert release is not None
    elsewhere = replace(release.requirements, url="https://example.com/evil")
    with pytest.raises(ChecksumMismatch):
        ReleaseSource(REPO, http=github.client()).download(
            elsewhere, tmp_path / "c.txt", sha256="0" * 64
        )
    assert not any(r.url.host == "example.com" for r in github.requests)


def test_release_notes_are_kept_as_text_and_capped(tmp_path, github):
    github.release("v0.1.0-alpha.3", prerelease=True, notes="<script>x</script>" + "n" * 30_000)
    found = checker(tmp_path, github).check()
    assert found.release is not None
    assert found.release.notes.startswith("<script>")  # the page shows it as text
    assert len(found.release.notes) == releases.MAX_NOTES


def test_the_real_release_list_shape_parses():
    """GitHub's answer for v0.1.0-alpha.1 (2026-09-26): it predates SHA256SUMS
    and images.json, so it's skipped, never offered."""
    raw = json.loads(
        """[{"tag_name": "v0.1.0-alpha.1", "draft": false, "prerelease": true,
        "assets": [{"name": "datalab-0.1.0-py3-none-any.whl", "size": 504088,
        "url": "https://api.github.com/repos/SripadaLab-UM/ihs-datalab/releases/assets/590266241",
        "digest": "sha256:d5b9d3373566989a848443086910111ca582b0253e9f4bc2ee18dc1c72cc1ece"}]}]"""
    )
    found, skipped = releases.releases_from(raw, REPO)
    assert found == [] and len(skipped) == 1


# ------------------------------------------------------------------ signatures


def test_without_a_pinned_key_nothing_is_ever_offered(tmp_path, github):
    """release_keys.py still has its placeholder: updates aren't set up."""
    github.release("v0.1.0-alpha.3", prerelease=True)
    check = checker(tmp_path, github, keys=())
    assert check.last.state == "not-configured"
    found = check.check()
    assert found.state == "not-configured" and found.release is None
    assert "Updates aren't set up" in found.message
    assert github.requests == []  # GitHub isn't even asked


def test_the_keys_the_package_pins_are_real_keys_and_no_test_key():
    """Holds before and after the maintainer pastes the real key in."""
    from datalab import release_keys, signing
    from tests.release_fakes import OTHER_PUBLIC

    pinned = release_keys.trusted_keys()
    assert all(signing.valid_public(key) for key in pinned)
    assert not {TEST_PUBLIC, OTHER_PUBLIC} & set(pinned)
    assert release_keys.PLACEHOLDER not in pinned
    settings = Settings(profile="real", data_dir=Path("/nonexistent"), oracle=None)
    expected = "not-checked" if pinned else "not-configured"
    assert UpdateChecker(settings).last.state == expected


@pytest.mark.parametrize("bad", ["not base64!", "AAAA", "A" * 42 + "==", TEST_PUBLIC + "AAAA", ""])
def test_a_pinned_key_that_isnt_one_turns_updates_off_and_says_so(tmp_path, github, bad):
    github.release("v0.1.0-alpha.3", prerelease=True)
    check = checker(tmp_path, github, keys=(TEST_PUBLIC, bad))
    assert check.last.state == "not-configured"
    assert "isn't a valid key" in check.last.message
    assert check.check().release is None and github.requests == []


@pytest.mark.parametrize(
    "how",
    ["other key", "no signature", "garbage", "signature of other sums"],
)
def test_a_release_not_signed_by_a_pinned_key_isnt_offered(tmp_path, github, how):
    from datalab import signing

    options: dict = {
        "other key": {"key": OTHER_PRIVATE},
        "no signature": {"key": None},
        "garbage": {"signature": b"not a signature\n"},
        "signature of other sums": {"signature": signing.sign(b"other", OTHER_PRIVATE)},
    }[how]
    github.release("v0.1.0-alpha.3", prerelease=True, **options)
    found = checker(tmp_path, github).check()
    assert found.release is None
    if how == "no signature":
        assert found.state == "up-to-date"  # skipped: it doesn't have SHA256SUMS.sig
    else:
        assert found.state == "failed" and "isn't signed by the lab's release key" in found.message


def test_any_pinned_key_will_do_so_keys_can_roll_over(tmp_path, github):
    """A release signed with the next key passes once the installed version
    pins both (a release signed with the current key carried it)."""
    from tests.release_fakes import OTHER_PUBLIC

    github.release("v0.1.0-alpha.3", prerelease=True, key=OTHER_PRIVATE)
    assert checker(tmp_path, github).check().state == "failed"
    assert checker(tmp_path, github, keys=(TEST_PUBLIC, OTHER_PUBLIC)).check().state == "available"


@pytest.mark.parametrize(
    "name", ["datalab-0.1.0a3-py3-none-any.whl", "SHA256SUMS", "SHA256SUMS.sig"]
)
def test_a_file_without_githubs_checksum_makes_the_release_unusable(tmp_path, github, name):
    github.release("v0.1.0-alpha.3", prerelease=True, digests={name: None})
    found = checker(tmp_path, github).check()
    assert found.state == "up-to-date" and found.release is None


def test_signatures_only_verify_what_was_signed():
    from datalab import signing

    private, public = signing.new_key()
    signature = signing.sign(b"sums", private)
    assert signing.verify(b"sums", signature, [public])
    assert not signing.verify(b"sums!", signature, [public])
    assert not signing.verify(b"sums", signature, [TEST_PUBLIC, "not-a-key"])
    assert signing.public_of(private) == public


# ------------------------------------------------------------------ download hosts


@pytest.mark.parametrize(
    "storage",
    ["https://evil.example.com", "https://githubusercontent.com.evil.example"],
)
def test_downloads_follow_redirects_only_to_githubs_hosts(tmp_path, github, storage):
    found = available(tmp_path, github)
    release = found.release
    assert release is not None
    github.storage = storage
    with pytest.raises(releases.CheckProblem, match="somewhere unexpected"):
        ReleaseSource(REPO, http=github.client()).download(
            release.requirements, tmp_path / "r.txt", sha256=found.expected["requirements.txt"]
        )
    assert not any("evil" in (r.url.host or "") for r in github.requests)


def test_a_download_larger_than_github_said_is_refused(tmp_path, github):
    found = available(tmp_path, github)
    release = found.release
    assert release is not None
    smaller = replace(release.requirements, size=release.requirements.size - 1)
    with pytest.raises(ChecksumMismatch, match="larger than GitHub said"):
        ReleaseSource(REPO, http=github.client()).download(
            smaller, tmp_path / "r.txt", sha256=found.expected["requirements.txt"]
        )


# ------------------------------------------------------------------ hourly


class Waits:
    """The hourly check's wait, faked: records each wait and says "stop" after `rounds`."""

    def __init__(self, rounds: int, clock: Clock | None = None) -> None:
        self.rounds = rounds
        self.clock = clock
        self.waited: list[float] = []

    def __call__(self, seconds: float) -> bool:
        if len(self.waited) >= self.rounds:
            return True
        self.waited.append(seconds)
        if self.clock is not None:
            self.clock.now += seconds
        return False


def hourly_for(check, waits, **kwargs) -> releases.HourlyCheck:
    kwargs.setdefault("pick", lambda low, high: 120.0)
    return releases.HourlyCheck(check, wait=waits, **kwargs)


def test_the_hourly_check_asks_about_once_an_hour_with_a_little_jitter(tmp_path, github):
    github.release("v0.1.0-alpha.3")
    clock = Clock()
    check = checker(tmp_path, github, clock=clock)
    picked: list[tuple[float, float]] = []

    def pick(low, high):
        picked.append((low, high))
        return 90.0

    waits = Waits(3, clock)
    hourly_for(check, waits, pick=pick).run()
    assert waits.waited == [3690.0] * 3
    assert set(picked) == {(0, releases.HOURLY_JITTER_SECONDS)}
    lists = [r for r in github.requests if r.url.path.endswith("/releases")]
    assert len(lists) == 3
    assert check.last.state == "available"


def test_the_jitter_is_up_to_five_minutes_on_top_of_an_hour(tmp_path, github):
    hourly = releases.HourlyCheck(checker(tmp_path, github))
    for _ in range(50):
        assert 3600 <= hourly.next_wait() <= 3600 + 300


def test_the_hourly_check_is_skipped_while_an_update_installs(tmp_path, github):
    clock = Clock()
    check = checker(tmp_path, github, clock=clock)
    installing = [True]
    waits = Waits(2, clock)
    hourly_for(check, waits, busy=lambda: installing[0]).run()
    assert github.requests == [] and check.last.state == "not-checked"
    installing[0] = False
    hourly_for(check, Waits(1, clock), busy=lambda: installing[0]).run()
    assert check.last.state == "up-to-date"


def test_the_hourly_check_off_asks_nothing(tmp_path, github):
    clock = Clock()
    check = checker(tmp_path, github, clock=clock, check_every_hour=False)
    assert check.every_hour is False
    hourly_for(check, Waits(3, clock)).run()
    assert github.requests == []


def test_the_persons_choice_overrides_the_settings_file_and_is_kept(tmp_path, github):
    check = checker(tmp_path, github)
    assert check.every_hour is True
    check.set_every_hour(False)
    assert check.every_hour is False
    # Kept in the data folder: a new start (a new checker) reads it.
    again = checker(tmp_path, github)
    assert again.every_hour is False
    saved = json.loads((tmp_path / "data" / releases.PREFERENCES).read_text())
    assert saved == {"check_every_hour": False}
    again.set_every_hour(True)
    assert checker(tmp_path, github, check_every_hour=False).every_hour is True


def test_an_unreadable_preferences_file_falls_back_to_the_setting(tmp_path, github):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / releases.PREFERENCES).write_text("{not json")
    assert checker(tmp_path, github).every_hour is True
    (folder / releases.PREFERENCES).write_text('{"check_every_hour": "no"}')
    assert checker(tmp_path, github, check_every_hour=False).every_hour is False


def test_the_hourly_check_respects_githubs_wait(tmp_path, github):
    clock = Clock()
    check = checker(tmp_path, github, clock=clock)
    github.answer = lambda request: httpx.Response(
        403,
        headers={
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": str(int(clock.now) + 3 * 3600),
        },
    )
    hourly_for(check, Waits(3, clock)).run()
    # Asked once; the next two rounds fell inside GitHub's wait.
    assert len(github.requests) == 1 and check.last.state == "rate-limited"


class Broken:
    every_hour = True

    def __init__(self) -> None:
        self.calls = 0
        self.last = releases.CheckResult("not-checked", "", "0.1.0", "auto")

    def check(self):
        self.calls += 1
        raise RuntimeError("something unexpected")


def test_a_failing_round_is_logged_and_the_next_one_still_comes(caplog):
    broken = Broken()
    with caplog.at_level("ERROR"):
        hourly_for(broken, Waits(3)).run()  # type: ignore[arg-type]
    assert broken.calls == 3
    assert "hourly update check failed" in caplog.text


def test_the_hourly_check_logs_only_when_the_answer_changes(tmp_path, github, caplog):
    clock = Clock()
    check = checker(tmp_path, github, clock=clock)
    with caplog.at_level("INFO", logger="datalab.releases"):
        hourly_for(check, Waits(3, clock)).run()
    said = [r for r in caplog.records if r.getMessage().startswith("hourly update check:")]
    assert len(said) == 1 and "up-to-date" in said[0].getMessage()


def test_offline_says_when_it_tries_again(tmp_path, github):
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    github.answer = offline
    assert "within the hour" in checker(tmp_path, github).check().message
    off = checker(tmp_path, github, check_every_hour=False)
    assert "when it next starts" in off.check().message


def test_stopping_ends_the_hourly_thread_at_once(tmp_path, github):
    hourly = releases.HourlyCheck(checker(tmp_path, github))
    hourly.start()
    hourly.start()  # once only
    thread = hourly._thread
    assert thread is not None and thread.is_alive() and thread.daemon
    hourly.stop(timeout=5)
    assert not thread.is_alive()
    assert github.requests == []
