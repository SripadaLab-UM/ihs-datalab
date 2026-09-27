"""GitHub sign-in: the device flow, and tokens kept in the keychain and rotated.

GitHub is never reached: its endpoints are answered by a mock transport.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from datalab.repos.github import (
    Account,
    GitHubAuth,
    GitHubUnavailable,
    SignInNeeded,
    Tokens,
    TokenStore,
    access_message,
    saved_token_values,
)

CLIENT_ID = "Iv23liTESTCLIENT"


class FakeGitHub:
    """The device-flow, token, and API endpoints, scripted per test."""

    def __init__(self) -> None:
        self.polls: list[dict] = []  # token-endpoint answers, in order
        self.refreshes = 0
        self.requests: list[tuple[str, dict]] = []
        self.issued = 0
        self.repo_status = 200
        self.fail_refresh: str | None = None
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("offline")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append((request.url.path, form))
        if request.url.path == "/login/device/code":
            assert form == {"client_id": CLIENT_ID}  # no secret, ever
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-123",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        if request.url.path == "/login/oauth/access_token":
            assert "client_secret" not in form
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                if self.fail_refresh:
                    return httpx.Response(200, json={"error": self.fail_refresh})
                assert form["refresh_token"] == f"ghr_refresh_{self.issued}"
                return httpx.Response(200, json=self._issue())
            answer = self.polls.pop(0)
            return httpx.Response(200, json=answer if answer != "ok" else self._issue())
        if request.url.path == "/user":
            assert request.headers["authorization"] == f"Bearer ghu_access_{self.issued}"
            return httpx.Response(200, json={"login": "yfang", "id": 42, "name": "Yu Fang"})
        if request.url.path == "/repos/SripadaLab-UM/ihs-knowledge":
            return httpx.Response(
                self.repo_status, json={"permissions": {"push": True, "pull": True}}
            )
        return httpx.Response(404)

    def _issue(self) -> dict:
        self.issued += 1
        return {
            "access_token": f"ghu_access_{self.issued}",
            "expires_in": 28800,
            "refresh_token": f"ghr_refresh_{self.issued}",
            "refresh_token_expires_in": 15897600,
            "token_type": "bearer",
            "scope": "",
        }


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def auth(github, clock):
    http = httpx.Client(transport=httpx.MockTransport(github.handler))
    return GitHubAuth(CLIENT_ID, http=http, clock=clock)


def saved(keychain) -> dict:
    return json.loads(keychain.saved[("datalab-github", "user-token")])


def test_the_device_flow_signs_in_and_keeps_the_tokens_in_the_keychain(
    auth, github, clock, github_keychain
):
    github.polls = [{"error": "authorization_pending"}, {"error": "slow_down"}, "ok"]
    started = auth.start()
    assert (started.state, started.user_code, started.interval) == ("waiting", "ABCD-1234", 5)
    assert started.verification_uri == "https://github.com/login/device"
    # Too soon: GitHub isn't asked.
    assert auth.poll().state == "waiting" and len(github.requests) == 1
    clock.now += 5
    assert auth.poll().state == "waiting"  # authorization_pending
    clock.now += 5
    slowed = auth.poll()  # slow_down: wait longer from now on
    assert slowed.state == "waiting" and slowed.interval == 10
    clock.now += 5
    assert auth.poll().state == "waiting" and len(github.requests) == 3
    clock.now += 5
    done = auth.poll()
    assert done.state == "signed in"
    assert done.account == Account("yfang", 42, "Yu Fang")
    assert done.account.email == "42+yfang@users.noreply.github.com"
    tokens = saved(github_keychain)
    assert tokens["access_token"] == "ghu_access_1"
    assert tokens["refresh_token"] == "ghr_refresh_1"
    assert tokens["access_expires_at"] == clock.now + 28800
    assert auth.status().state == "signed in"
    assert sorted(saved_token_values()) == ["ghr_refresh_1", "ghu_access_1"]


@pytest.mark.parametrize(
    ("answer", "state"),
    [({"error": "expired_token"}, "expired"), ({"error": "access_denied"}, "denied")],
)
def test_a_flow_that_ends_on_github_says_how(auth, github, clock, answer, state):
    github.polls = [answer]
    auth.start()
    clock.now += 5
    assert auth.poll().state == state
    assert auth.status().state == state
    assert auth.poll().state == state  # nothing more is asked
    assert len(github.requests) == 2


def test_an_expired_code_isnt_polled(auth, github, clock):
    auth.start()
    clock.now += 901
    assert auth.poll().state == "expired"
    assert len(github.requests) == 1


def test_cancel_and_sign_out(auth, github, clock, github_keychain):
    auth.start()
    assert auth.cancel().state == "signed out"
    assert auth.poll().state == "signed out"
    github.polls = ["ok"]
    auth.start()
    clock.now += 5
    assert auth.poll().state == "signed in"
    assert auth.sign_out().state == "signed out"
    assert github_keychain.saved == {}
    with pytest.raises(SignInNeeded):
        auth.access_token()


def signed_in(auth, github, clock):
    github.polls = ["ok"]
    auth.start()
    clock.now += 5
    auth.poll()


def test_refreshing_rotates_both_tokens_and_saves_them_before_use(
    auth, github, clock, github_keychain
):
    signed_in(auth, github, clock)
    assert auth.access_token() == "ghu_access_1"
    assert github.refreshes == 0
    # Near the end of the 8 hours: refreshed, with the refresh token GitHub
    # gave last (FakeGitHub refuses any other).
    clock.now += 8 * 3600 - 60
    assert auth.access_token() == "ghu_access_2"
    tokens = saved(github_keychain)
    assert (tokens["access_token"], tokens["refresh_token"]) == ("ghu_access_2", "ghr_refresh_2")
    assert tokens["account"]["login"] == "yfang"  # who signed in is kept
    clock.now += 8 * 3600
    assert auth.access_token() == "ghu_access_3"
    assert saved(github_keychain)["refresh_token"] == "ghr_refresh_3"
    assert github.refreshes == 2
    # The old refresh tokens were never sent again.
    sent = [
        f["refresh_token"] for p, f in github.requests if f.get("grant_type") == "refresh_token"
    ]
    assert sent == ["ghr_refresh_1", "ghr_refresh_2"]


def test_a_refused_refresh_token_means_signing_in_again(auth, github, clock, github_keychain):
    signed_in(auth, github, clock)
    clock.now += 9 * 3600
    github.fail_refresh = "bad_refresh_token"
    with pytest.raises(SignInNeeded):
        auth.access_token()
    assert github_keychain.saved == {}
    assert auth.status().state == "signed out"


def test_a_refresh_token_past_its_expiry_isnt_sent(auth, github, clock, github_keychain):
    signed_in(auth, github, clock)
    clock.now += 185 * 24 * 3600  # past the refresh token's ~184 days
    with pytest.raises(SignInNeeded):
        auth.access_token()
    assert github.refreshes == 0
    assert auth.status().state == "signed out"


def test_github_being_unreachable_keeps_the_sign_in(auth, github, clock, github_keychain):
    signed_in(auth, github, clock)
    clock.now += 9 * 3600
    github.down = True
    with pytest.raises(GitHubUnavailable):
        auth.access_token()
    assert saved(github_keychain)["refresh_token"] == "ghr_refresh_1"
    github.down = False
    assert auth.access_token() == "ghu_access_2"


def test_missing_access_is_a_404_from_the_repository(auth, github, clock):
    signed_in(auth, github, clock)
    assert auth.repo_access("SripadaLab-UM/ihs-knowledge") == "write"
    github.repo_status = 404
    assert auth.repo_access("SripadaLab-UM/ihs-knowledge") == "none"
    message = access_message("SripadaLab-UM/ihs-knowledge", "yfang", "Ali")
    assert "@yfang" in message and "Ask Ali" in message and "datalab-users" in message


def test_tokens_that_cant_be_read_count_as_signed_out(github_keychain):
    github_keychain.saved[("datalab-github", "user-token")] = "not json"
    assert TokenStore().load() is None
    assert Tokens.from_json(json.dumps({"no": "token"})) is None
    assert saved_token_values() == []


@pytest.mark.parametrize("error", ["bad_refresh_token", "invalid_grant"])
def test_only_a_refresh_token_github_calls_bad_signs_out(auth, github, clock, error):
    signed_in(auth, github, clock)
    clock.now += 9 * 3600
    github.fail_refresh = error
    with pytest.raises(SignInNeeded):
        auth.access_token()
    assert auth.status().state == "signed out"


def test_other_refresh_answers_keep_the_sign_in(auth, github, clock, github_keychain):
    signed_in(auth, github, clock)
    clock.now += 9 * 3600
    github.fail_refresh = "slow_down"  # anything but a refused token
    with pytest.raises(GitHubUnavailable):
        auth.access_token()
    github.fail_refresh = None
    real = github.handler

    def rate_limited(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(429, json={"message": "rate limited"})
        return real(request)

    limited = GitHubAuth(
        CLIENT_ID, http=httpx.Client(transport=httpx.MockTransport(rate_limited)), clock=clock
    )
    with pytest.raises(GitHubUnavailable):
        limited.access_token()
    assert saved(github_keychain)["refresh_token"] == "ghr_refresh_1"
    assert auth.access_token() == "ghu_access_2"


def test_a_keychain_that_wont_save_the_rotated_tokens_doesnt_lose_them(
    auth, github, clock, github_keychain
):
    signed_in(auth, github, clock)
    clock.now += 9 * 3600
    real_save = github_keychain.set_password

    def broken(*args):
        raise RuntimeError("keychain locked")

    github_keychain.set_password = broken
    assert auth.access_token() == "ghu_access_2"  # rotated, kept in memory
    assert saved(github_keychain)["refresh_token"] == "ghr_refresh_1"
    status = auth.status()
    assert status.state == "signed in" and "couldn't save" in (status.message or "")
    # git's helper reads the keychain, so git waits until it's saved.
    with pytest.raises(GitHubUnavailable):
        auth.token_for_git()
    clock.now += 9 * 3600
    assert auth.access_token() == "ghu_access_3"  # refreshed with the kept token
    github_keychain.set_password = real_save
    auth.token_for_git()  # saved on the next use
    assert saved(github_keychain)["refresh_token"] == "ghr_refresh_3"
    assert auth.status().message is None
