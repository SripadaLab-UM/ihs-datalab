"""Signing in to the lab's shared DataLab with GitHub (auth/), and the
browser sign-ins it makes. GitHub is answered by a mock transport, with the
response shapes of the device-flow tests (test_github_auth.py)."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from datalab import db
from datalab.auth.github_web import GitHubWebSignIn, Refused
from datalab.auth.sessions import WebSessions
from datalab.auth.users import LOCAL, Users
from datalab.config import RepoSettings, ServerSettings

CLIENT_ID = "Iv23liTESTCLIENT"
ADMIN, MEMBER, OUTSIDER = 1001, 1002, 1003


class FakeGitHub:
    """GitHub's web-flow token endpoint, /user and the access repository."""

    def __init__(self, who: int = ADMIN) -> None:
        self.who = who
        self.forms: list[dict[str, str]] = []
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.forms.append(form)
            assert form["client_secret"] == "secret"  # the web flow needs it, refreshes too
            if form.get("code") == "bad":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            self.issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"ghu_access_{self.issued}",
                    "expires_in": 28800,
                    "refresh_token": f"ghr_refresh_{self.issued}",
                    "refresh_token_expires_in": 15897600,
                    "token_type": "bearer",
                    "scope": "",
                },
            )
        if request.url.path == "/user":
            return httpx.Response(
                200, json={"login": f"person{self.who}", "id": self.who, "name": ""}
            )
        if request.url.path == "/repos/SripadaLab-UM/ihs-knowledge":
            if self.who == OUTSIDER:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"permissions": {"push": True, "pull": True}})
        return httpx.Response(404)


@pytest.fixture
def connection(tmp_path):
    connection = db.connect(tmp_path / "datalab.sqlite")
    yield connection
    connection.close()


def server_settings(settings, open_to="admins"):
    server = ServerSettings(public_url="https://lab.example.edu", admins=(ADMIN,), open_to=open_to)
    repos = RepoSettings(knowledge="SripadaLab-UM/ihs-knowledge", client_id=CLIENT_ID)
    return replace(settings, deployment="server", server=server, repos=repos)


def make(settings, connection, github, open_to="admins", clock=lambda: 1_000_000.0):
    users = Users(connection)
    sign_in = GitHubWebSignIn(
        server_settings(settings, open_to),
        connection,
        users,
        client_secret="secret",
        token_key=Fernet(Fernet.generate_key()),
        http=httpx.Client(transport=httpx.MockTransport(github.handler)),
        clock=clock,
    )
    return sign_in, users


def test_the_trip_to_github_carries_state_and_pkce(settings, connection):
    sign_in, _ = make(settings, connection, FakeGitHub())
    started = sign_in.start()
    query = {k: v[0] for k, v in parse_qs(urlparse(started.url).query).items()}
    assert started.url.startswith("https://github.com/login/oauth/authorize?")
    assert query["client_id"] == CLIENT_ID and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "https://lab.example.edu/datalab/auth/github/callback"
    state, _, verifier = started.cookie.partition(".")
    assert query["state"] == state and verifier and "client_secret" not in started.url


def test_an_administrator_signs_in_and_their_tokens_are_kept_encrypted(settings, connection):
    github = FakeGitHub(ADMIN)
    sign_in, _ = make(settings, connection, github)
    started = sign_in.start()
    state = started.cookie.split(".")[0]
    user = sign_in.finish("code-1", state, started.cookie)
    assert user.login == f"person{ADMIN}" and user.admin and user.github_id == ADMIN
    # The PKCE verifier went with the code; the tokens are stored, but never readable as such.
    assert github.forms[0]["code_verifier"] == started.cookie.split(".", 1)[1]
    [row] = connection.execute("SELECT ciphertext FROM github_tokens").fetchall()
    assert b"ghu_access_1" not in bytes(row["ciphertext"])
    assert sign_in.auth_for(user).access_token() == "ghu_access_1"


def test_a_refresh_uses_the_client_secret(settings, connection):
    now = [1_000_000.0]
    github = FakeGitHub(ADMIN)
    sign_in, _ = make(settings, connection, github, clock=lambda: now[0])
    started = sign_in.start()
    user = sign_in.finish("code-1", started.cookie.split(".")[0], started.cookie)
    now[0] += 9 * 3600  # past the 8-hour access token
    assert sign_in.auth_for(user).access_token() == "ghu_access_2"
    assert github.forms[-1]["grant_type"] == "refresh_token"


@pytest.mark.parametrize(
    ("who", "open_to", "reason"),
    [
        (OUTSIDER, "lab", "not-in-lab"),
        (MEMBER, "admins", "admins-only"),
        (OUTSIDER, "admins", "not-in-lab"),
    ],
)
def test_who_may_sign_in(settings, connection, who, open_to, reason):
    sign_in, _ = make(settings, connection, FakeGitHub(who), open_to=open_to)
    started = sign_in.start()
    with pytest.raises(Refused) as refused:
        sign_in.finish("code-1", started.cookie.split(".")[0], started.cookie)
    assert refused.value.reason == reason
    assert not connection.execute("SELECT * FROM github_tokens").fetchall()


def test_once_open_to_the_lab_a_member_signs_in(settings, connection):
    sign_in, _ = make(settings, connection, FakeGitHub(MEMBER), open_to="lab")
    started = sign_in.start()
    user = sign_in.finish("code-1", started.cookie.split(".")[0], started.cookie)
    assert user.login == f"person{MEMBER}" and not user.admin


@pytest.mark.parametrize("cookie", [None, "", "other-state.verifier", "only-state"])
def test_a_callback_without_this_browsers_half_is_refused(settings, connection, cookie):
    sign_in, _ = make(settings, connection, FakeGitHub())
    started = sign_in.start()
    with pytest.raises(Refused, match="expired"):
        sign_in.finish("code-1", started.cookie.split(".")[0], cookie)


def test_a_code_github_wont_trade_is_refused(settings, connection):
    sign_in, _ = make(settings, connection, FakeGitHub())
    started = sign_in.start()
    with pytest.raises(Refused, match="expired"):
        sign_in.finish("bad", started.cookie.split(".")[0], started.cookie)


def test_browser_sign_ins_end_when_they_should(connection):
    now = [1_000_000.0]
    users = Users(connection)
    users.ensure_local()  # as the server does at start
    sessions = WebSessions(connection, users, hours=8, idle_minutes=60, clock=lambda: now[0])
    cookie = sessions.start(LOCAL)
    assert sessions.user_for(cookie) == LOCAL
    assert sessions.user_for("made-up") is None and sessions.user_for(None) is None
    # Only its hash is stored.
    assert not connection.execute(
        "SELECT * FROM web_sessions WHERE id_hash = ?", (cookie,)
    ).fetchall()
    now[0] += 61 * 60  # idle for over an hour
    assert sessions.user_for(cookie) is None
    second = sessions.start(LOCAL)
    for _ in range(9):  # active, but past 8 hours
        now[0] += 55 * 60
        sessions.user_for(second)
    assert sessions.user_for(second) is None
    third = sessions.start(LOCAL)
    sessions.end(third)
    assert sessions.user_for(third) is None
    fourth = sessions.start(LOCAL)
    connection.execute("UPDATE users SET disabled = 1 WHERE id = ?", (LOCAL.id,))
    assert sessions.user_for(fourth) is None
