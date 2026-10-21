"""The lab's processed data (dropbox/): the file list, datasets, fetching one
file for an agent, the Dropbox sign-in and API client, and how a
conversation's data sources limit its tools and folders.

Dropbox itself is never reached: its API is answered by httpx's
MockTransport, with response shapes from Dropbox's API documentation."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from datalab import db
from datalab.app import create_app
from datalab.config import DropboxSettings, Settings
from datalab.data.access_log import AccessLog
from datalab.dropbox.auth import DropboxAuth, SignInNeeded, TokenStore, pkce_pair
from datalab.dropbox.client import ContentHasher, DropboxClient, DropboxRefused, content_hash
from datalab.dropbox.families import families, is_clutter, pattern, years_in
from datalab.dropbox.index import ProcessedIndex
from datalab.dropbox.service import LoadRefused, NotAvailable, ProcessedData
from datalab.dropbox.sources import (
    Changes,
    CursorTooOld,
    DropboxSource,
    FileEntry,
    LocalSource,
    Progress,
    practice_folder,
    safe_relative,
)
from datalab.knowledge.check import name_findings
from datalab.sessions import modes
from datalab.sessions.tokens import DATABASE_TOOLS, FILE_TOOLS, SessionAccess, tools_for_sources
from tests.conftest import FakeDatabase
from tests.lab_shaped_folder import lab_shaped_folder
from tests.test_agent_tools import mcp_session, payload
from tests.test_agent_tools import server as server
from tests.test_inputs_api import FakeContainers

# --- Datasets -----------------------------------------------------------------


def test_a_pattern_replaces_dates_years_ids_and_numbers():
    assert pattern("Fitbit/2025/daily_SYN0042.csv") == "fitbit/{year}/daily_{id}.csv"
    assert pattern("surveys/phq9_wave2_2025-07-01.csv") == "surveys/phq{n}_wave{n}_{date}.csv"
    assert pattern("exports/P-12345 sleep 20240131.tsv") == "exports/{id} sleep {date}.tsv"


def test_no_participant_id_survives_grouping():
    paths = [f"mood/2025/IHS{n:05d}_daily.csv" for n in range(40)] + [
        "fitbit/steps_2024.csv",
        "fitbit/steps_2025.csv",
    ]
    found = families(FileEntry(p, p, 10) for p in paths)
    assert [f.pattern for f in found] == ["fitbit/steps_{year}.csv", "mood/{year}/{id}_daily.csv"]
    assert found[1].files == 40
    for family in found:
        assert not name_findings(family.pattern), family.pattern


def test_far_off_years_are_plain_numbers():
    assert pattern("x/batch_20981231.csv") == "x/batch_{n}.csv"
    assert pattern("x/run_2037_1995.csv") == "x/run_{n}_{n}.csv"
    assert pattern("x/steps_2024.csv") == "x/steps_{year}.csv"
    assert years_in("2085 data/f_1995_20240131.csv") == [2024]


def test_code_and_caches_are_in_no_dataset():
    for path in ("p/code/clean.py", "p/code/model.R", "p/code/__pycache__/m.cpython-311.pyc",
                 "p/.Rhistory", "p/out/~$results.xlsx", "p/.DS_Store", "p/run.sas"):  # fmt: skip
        assert is_clutter(path), path
    for path in ("p/out/results.xlsx", "p/out/fit.RData", "p/report.html", "p/a.sas7bdat"):
        assert not is_clutter(path), path
    found = families([FileEntry("p/code/clean.py", "1", 1), FileEntry("p/out/r.csv", "2", 1)])
    assert [f.pattern for f in found] == ["p/out/r.csv"]


def test_names_that_dont_group_merge_only_when_many():
    def names(count: int) -> list[str]:
        paths = [f"2018 data/surveys/responses/{'xyz'[i % 3] * (i + 2)}.xlsx" for i in range(count)]
        return [f.pattern for f in families(FileEntry(p, p, 1) for p in paths)]

    # Nine could be a folder's distinct measures; ten unmatched names are names.
    assert len(names(9)) == 9
    assert names(10) == ["{year} data/surveys/responses/{name}.xlsx"]
    # Near the top, names are the lab's own and stay.
    shallow = [FileEntry(f"Dictionary/{'abcdefghijkl'[i] * 3}.xlsx", str(i), 1) for i in range(12)]
    assert len(families(shallow)) == 12


def test_grouping_on_a_folder_shaped_like_the_labs():
    lab = lab_shaped_folder()
    found = families(lab.entries)
    patterns = {f.pattern for f in found}
    assert lab.kept <= patterns
    # Thousands of files, a few dozen datasets.
    assert len(lab.entries) > 2000 and len(found) < 40
    for family in found:
        segments = family.pattern.split("/")
        stems = [segment.rsplit(".", 1)[0] for segment in segments]
        assert not set(segments + stems) & lab.person_names, family.pattern
        assert not name_findings(family.pattern), family.pattern
        assert all(2000 <= year <= 2030 for year in family.years), family.pattern
    members = [path for f in found for path in f.members]
    assert len(members) == len(set(members))  # each file in one dataset
    assert set(members) | lab.clutter == {e.path for e in lab.entries}
    assert not set(members) & lab.clutter


def test_a_number_that_never_varies_stays_in_the_name():
    paths = [
        "surveys/phq9_wave1_2025.csv",
        "surveys/phq9_wave2_2025.csv",
        "surveys/gad7_2024.csv",  # one file: its 7 stays, its year doesn't
        "mood/2025/IHS00042_v2.csv",  # one file: its ID is hidden even so
        "logs/run_3.txt",
        "logs/run_14.txt",
    ]
    names = [f.pattern for f in families(FileEntry(p, p, 1) for p in paths)]
    assert names == [
        "logs/run_{n}.txt",
        "mood/{year}/{id}_v2.csv",
        "surveys/gad7_{year}.csv",
        "surveys/phq9_wave{n}_{year}.csv",
    ]
    for name in names:
        assert not name_findings(name), name


def test_unsafe_relative_paths_are_refused():
    for bad in ("", "/etc/passwd", "a/../b", "./a", "a//b", "a\\b", "C:/x", "a/\x07b"):
        assert not safe_relative(bad), bad
    assert safe_relative("fitbit/2025/steps.csv")


# --- The file list --------------------------------------------------------------


@pytest.fixture
def connection(tmp_path: Path):
    connection = db.connect(tmp_path / "datalab.sqlite")
    yield connection
    connection.close()


class ScriptedSource:
    """A source that answers with prepared listings and changes."""

    label = "scripted"

    def __init__(self, listing: Changes, changes: list[Changes | Exception]) -> None:
        self._listing = listing
        self._changes = changes
        self.listed = 0

    def listing(self, progress=None) -> Changes:
        self.listed += 1
        return self._listing

    def changes(self, cursor: str) -> Changes:
        answer = self._changes.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fetch(self, entry, dest, *, max_bytes, deadline):  # pragma: no cover
        raise AssertionError("not fetched here")


def test_the_list_follows_changes_and_deleted_folders(connection):
    index = ProcessedIndex(connection)
    index.choose("id:folder", "/Study Data", "ns1")
    first = Changes(
        [
            FileEntry("a/one.csv", "id:1", 10),
            FileEntry("a/sub/two.csv", "id:2", 20),
            FileEntry("a_b/three.csv", "id:3", 30),
        ],
        [],
        "c1",
    )
    later = Changes([FileEntry("new.csv", "id:4", 5)], ["a"], "c2")
    source = ScriptedSource(first, [later, CursorTooOld("reset")])
    assert index.refresh(source) == 3
    assert index.chosen().cursor == "c1"
    # Deleting folder "a" removes what's under it, and not "a_b" (LIKE's _ is escaped).
    assert index.refresh(source) == 2
    assert sorted(e.path for e in index.files()) == ["a_b/three.csv", "new.csv"]
    # A cursor Dropbox no longer knows: everything is listed again.
    assert index.refresh(source) == 3
    assert source.listed == 2


def test_choosing_another_folder_empties_the_list(connection):
    index = ProcessedIndex(connection)
    index.choose("id:1", "/One", None)
    index.refresh(ScriptedSource(Changes([FileEntry("x.csv", "id:x", 1)], [], "c"), []))
    index.choose("id:2", "/Two", None)
    assert index.totals() == (0, 0)


# --- Practice DataLab's folder, and fetching one file ------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # Practice offers its synthetic folder only where the lab has a Dropbox
    # app, as the real DataLab does: these tests are of a DataLab that has one.
    return replace(settings, dropbox=DropboxSettings(app_key="abcdefghij12"))


def test_practice_offers_nothing_without_the_labs_dropbox_app(settings, connection):
    bare = replace(settings, dropbox=DropboxSettings(app_key=None))
    data = ProcessedData(bare, connection, AccessLog(connection, settings.data_dir / "audit.jsonl"))
    status = data.status()
    assert status.source == "off" and not status.ready
    with pytest.raises(NotAvailable):
        data.start_refresh()


@pytest.fixture
def processed(settings: Settings, connection) -> ProcessedData:
    log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    data = ProcessedData(settings, connection, log)
    data.refresh()
    return data


def test_practice_uses_its_own_synthetic_folder(processed):
    status = processed.status()
    assert status.source == "practice" and status.ready
    assert status.files == len(LocalSource(practice_folder()).listing().changed) > 5
    patterns = {f.pattern for f in processed.datasets()}
    assert "fitbit/daily_steps_{year}.csv" in patterns
    assert "mood/{year}/{id}_daily_mood.csv" in patterns
    assert [f.pattern for f in processed.datasets("surveys")] == ["surveys/phq9_wave{n}_{year}.csv"]
    # A dataset's files can be asked for by its name, or by its pattern.
    by_name = processed.files_of("surveys/phq9_wave{n}_{year}.csv")
    assert len(by_name) == 2 and by_name == processed.files_of("surveys/phq{n}_wave{n}_{year}.csv")
    _, _, dataset = processed.describe("surveys/phq9_wave1_2025.csv")
    assert dataset == "surveys/phq9_wave{n}_{year}.csv"


def test_a_file_is_fetched_once_and_logged(processed, connection, settings, tmp_path):
    staging = tmp_path / "sess" / "dropbox"
    loaded = processed.load("c_one", staging, "fitbit/daily_steps_2025.csv")
    assert loaded.container_path == "/data/dropbox/fitbit/daily_steps_2025.csv"
    assert (
        loaded.host_path.read_bytes()
        == (practice_folder() / "fitbit/daily_steps_2025.csv").read_bytes()
    )
    assert not loaded.cached
    again = processed.load(
        "c_two", tmp_path / "other" / "dropbox", "/data/dropbox/fitbit/daily_steps_2025.csv"
    )
    assert again.cached
    log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    [entry] = log.files_for_session("c_one")
    assert entry.status == "succeeded" and entry.path == "fitbit/daily_steps_2025.csv"
    audit = (settings.data_dir / "logs" / "audit.jsonl").read_text().splitlines()
    loads = [json.loads(line) for line in audit if '"file_load"' in line]
    assert len(loads) == 2
    # The audit log never names the file: a fingerprint only.
    assert all("daily_steps" not in line for line in audit)


@pytest.mark.parametrize("path", ["../secrets.csv", "nothing/here.csv", "fitbit", ""])
def test_files_not_in_the_list_are_refused_and_logged(
    processed, connection, settings, tmp_path, path
):
    with pytest.raises(LoadRefused):
        processed.load("c_one", tmp_path / "s", path)
    log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    assert [e.status for e in log.files_for_session("c_one")] == ["refused"]
    assert not (tmp_path / "s").exists() or not any((tmp_path / "s").rglob("*"))


def test_a_file_over_the_limit_is_refused_before_fetching(settings, connection, tmp_path):
    root = tmp_path / "folder"
    (root / "big").mkdir(parents=True)
    (root / "big" / "huge.csv").write_bytes(b"x" * (2 * 1024**2))
    (root / ".env").write_text("SECRET=1")
    small = replace(
        settings, dropbox=replace(settings.dropbox, max_file_bytes=1024**2, max_cache_bytes=1024**2)
    )
    log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    data = ProcessedData(small, connection, log, practice_root=root)
    data.refresh()
    with pytest.raises(LoadRefused, match="more than"):
        data.load("c_one", tmp_path / "s", "big/huge.csv")
    assert not (small.data_dir / "processed-cache").exists()
    # Dot files aren't listed at all.
    assert data.index.get(".env") is None


# --- Data sources and tools ----------------------------------------------------------


def test_sources_limit_the_tools():
    analysis = modes.MODES["analysis"]
    both = tools_for_sources(analysis.allowed_tools, ("database", "dropbox"))
    assert both >= FILE_TOOLS and "query" in both
    database = tools_for_sources(analysis.allowed_tools, ("database",))
    assert not FILE_TOOLS & database
    dropbox = tools_for_sources(analysis.allowed_tools, ("dropbox",))
    assert not DATABASE_TOOLS & dropbox and dropbox >= FILE_TOOLS
    assert "suggest_kb_update" in dropbox


def test_only_the_workspace_data_modes_offer_dropbox():
    offered = {m.id for m in modes.MODES.values() if "dropbox" in m.sources}
    assert offered == {"analysis", "extraction", "engineering"}
    assert modes.MODES["research"].sources == ()


def test_the_instructions_say_which_sources():
    both = modes.instructions("analysis", data_sources=("database", "dropbox"))
    assert "Data sources for this conversation" in both
    only = modes.instructions("analysis", data_sources=("dropbox",))
    assert "query and\ncatalog tools are off" in only
    assert "Data source" not in modes.instructions("analysis")
    # An empty /data/dropbox isn't "no data": the agent lists the datasets
    # (it once looked on disk and said there were none).
    for text in (both, only):
        assert "/data/dropbox starts empty" in text and "call\nlist_data_files" in text


# --- Signing in to Dropbox ------------------------------------------------------------


class FakeDropbox:
    """Dropbox's OAuth and API endpoints, as MockTransport handlers."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.refresh_refused = False
        self.busy = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/oauth2/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["GOODCODE"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                assert form["code_verifier"][0]
                return httpx.Response(
                    200,
                    json={"access_token": "sl.first", "expires_in": 14400, "refresh_token": "rt-1"},
                )
            if self.refresh_refused:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "sl.fresh", "expires_in": 14400})
        if path == "/2/users/get_current_account":
            return httpx.Response(
                200,
                json={
                    "name": {"display_name": "Test Person"},
                    "root_info": {
                        ".tag": "team",
                        "root_namespace_id": "100",
                        "home_namespace_id": "200",
                    },
                },
            )
        if path == "/2/auth/token/revoke":
            return httpx.Response(200, json=None)
        if path == "/2/files/list_folder":
            if self.busy:
                self.busy -= 1
                return httpx.Response(429, headers={"Retry-After": "1"}, json={})
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            ".tag": "folder",
                            "name": "Study Data",
                            "id": "id:f",
                            "path_display": "/Study Data",
                            "path_lower": "/study data",
                        },
                    ],
                    "cursor": "c1",
                    "has_more": False,
                },
            )
        if path == "/2/files/get_metadata":
            return httpx.Response(409, json={"error_summary": "path/not_found/.."})
        if path == "/2/files/download":
            body = b"a,b\n1,2\n"
            return httpx.Response(
                200,
                content=body,
                headers={
                    "Dropbox-API-Result": json.dumps({"size": len(body), "content_hash": "bad"})
                },
            )
        return httpx.Response(404)


@pytest.fixture
def fake_dropbox() -> FakeDropbox:
    return FakeDropbox()


@pytest.fixture
def auth(fake_dropbox) -> DropboxAuth:
    return DropboxAuth(
        "abcdefghij12", http=httpx.Client(transport=httpx.MockTransport(fake_dropbox))
    )


def test_sign_in_uses_pkce_and_read_only_scopes(auth, fake_dropbox):
    started = auth.start()
    query = parse_qs(urlparse(started.authorize_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["token_access_type"] == ["offline"]
    assert query["scope"] == ["account_info.read files.metadata.read files.content.read"]
    assert "redirect_uri" not in query and "client_secret" not in str(started.authorize_url)
    wrong = auth.finish("NOPE")
    assert wrong.state == "waiting" and "didn't accept" in (wrong.message or "")
    done = auth.finish("GOODCODE")
    assert done.state == "signed in" and done.account.team_space
    sent = parse_qs(fake_dropbox.requests[-2].content.decode())
    assert "client_secret" not in sent
    saved = TokenStore().load()
    assert saved is not None and saved.refresh_token == "rt-1"


def test_a_refused_refresh_signs_out(auth, fake_dropbox):
    auth.start()
    auth.finish("GOODCODE")
    store = TokenStore()
    store.save(replace(store.load(), access_expires_at=0))
    assert auth.access_token() == "sl.fresh"
    store.save(replace(store.load(), access_expires_at=0))
    fake_dropbox.refresh_refused = True
    with pytest.raises(SignInNeeded):
        auth.access_token()
    assert store.load() is None


def test_disconnect_revokes(auth, fake_dropbox):
    auth.start()
    auth.finish("GOODCODE")
    assert auth.sign_out().state == "signed out"
    assert fake_dropbox.requests[-1].url.path == "/2/auth/token/revoke"


def test_pkce_challenge_matches_its_verifier():
    import base64
    import hashlib

    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == digest.decode()


# --- The API client ---------------------------------------------------------------------


@pytest.fixture
def client(auth, fake_dropbox) -> DropboxClient:
    auth.start()
    auth.finish("GOODCODE")
    return DropboxClient(
        auth, http=httpx.Client(transport=httpx.MockTransport(fake_dropbox)), sleep=lambda _: None
    )


def test_calls_name_the_team_space_and_wait_when_asked(client, fake_dropbox):
    fake_dropbox.busy = 2
    [page] = list(client.list_folder(""))
    assert [i.name for i in page.items] == ["Study Data"]
    listing = [r for r in fake_dropbox.requests if r.url.path == "/2/files/list_folder"]
    assert len(listing) == 3
    assert json.loads(listing[-1].headers["Dropbox-API-Path-Root"]) == {
        ".tag": "root",
        "root": "100",
    }
    assert listing[-1].headers["Authorization"] == "Bearer sl.first"


def test_folders_can_be_opened_to_choose_a_subfolder(settings, connection, auth, fake_dropbox):
    auth.start()
    auth.finish("GOODCODE")
    real = replace(settings, profile="real", dropbox=DropboxSettings(app_key="abcdefghij12"))
    log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    data = ProcessedData(real, connection, log, auth=auth)
    data._client = DropboxClient(
        auth, http=httpx.Client(transport=httpx.MockTransport(fake_dropbox)), sleep=lambda _: None
    )
    assert [f.name for f in data.team_folders()] == ["Study Data"]
    data.team_folders("id:f")
    asked = [
        json.loads(r.content)["path"]
        for r in fake_dropbox.requests
        if r.url.path == "/2/files/list_folder"
    ]
    assert asked == ["", "id:f"]


class FolderTree:
    """Dropbox's listing endpoints for a chosen folder `/Study` holding a
    file and three folders, one of them paged; shapes from the API docs."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, bool]] = []
        self.at_once = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    @staticmethod
    def _file(path: str, size: int = 10) -> dict[str, object]:
        return {".tag": "file", "name": path.rsplit("/", 1)[-1], "id": f"id:{path}",
                "path_display": path, "path_lower": path.lower(), "size": size,
                "server_modified": "2025-01-01T00:00:00Z", "content_hash": "h"}  # fmt: skip

    @staticmethod
    def _folder(path: str) -> dict[str, object]:
        return {".tag": "folder", "name": path.rsplit("/", 1)[-1], "id": f"id:{path}",
                "path_display": path, "path_lower": path.lower()}  # fmt: skip

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/2/files/get_metadata":
            return httpx.Response(200, json=self._folder("/Study"))
        if path == "/2/files/list_folder/get_latest_cursor":
            self.asked.append(("cursor", body["path"], body["recursive"]))
            return httpx.Response(200, json={"cursor": "from-before"})
        if path == "/2/files/list_folder/continue":
            self.asked.append(("continue", body["cursor"], True))
            entries = [self._file("/Study/Big/b2.csv")]
            return httpx.Response(
                200, json={"entries": entries, "cursor": "big-2", "has_more": False}
            )
        if path != "/2/files/list_folder":
            return httpx.Response(404)
        self.asked.append(("list", body["path"], body["recursive"]))
        with self._lock:
            self.at_once += 1
            self.most_at_once = max(self.most_at_once, self.at_once)
        time.sleep(0.05)
        with self._lock:
            self.at_once -= 1
        if body["path"] == "id:/Study":
            entries = [self._file("/Study/README.md"), self._folder("/Study/A"),
                       self._folder("/Study/Big"), self._folder("/Study/C")]  # fmt: skip
            return httpx.Response(
                200, json={"entries": entries, "cursor": "top", "has_more": False}
            )
        if body["path"] == "id:/Study/Big":
            entries = [self._folder("/Study/Big"), self._file("/Study/Big/b1.csv")]
            return httpx.Response(
                200, json={"entries": entries, "cursor": "big-1", "has_more": True}
            )
        name = body["path"].rsplit("/", 1)[-1]
        entries = [self._file(f"/Study/{name}/x.csv"), self._file(f"/Study/{name}/sub/y.csv")]
        return httpx.Response(200, json={"entries": entries, "cursor": name, "has_more": False})


def test_a_folder_is_listed_in_parts_at_once(auth):
    auth.start()
    auth.finish("GOODCODE")
    tree = FolderTree()
    client = DropboxClient(auth, http=httpx.Client(transport=httpx.MockTransport(tree)))
    progress = Progress()
    found = DropboxSource(client, "id:/Study", "/Study").listing(progress)
    assert sorted(e.path for e in found.changed) == [
        "A/sub/y.csv", "A/x.csv", "Big/b1.csv", "Big/b2.csv", "C/sub/y.csv", "C/x.csv", "README.md",
    ]  # fmt: skip
    # The cursor is taken before anything is listed, for the whole folder:
    # what changes meanwhile comes back at the next refresh.
    assert tree.asked[0] == ("cursor", "id:/Study", True) and found.cursor == "from-before"
    assert ("list", "id:/Study", False) in tree.asked
    assert {("list", f"id:/Study/{n}", True) for n in ("A", "Big", "C")} <= set(tree.asked)
    assert tree.most_at_once > 1
    assert (progress.files, progress.folders_done, progress.folders_total) == (7, 3, 3)


class SlowSource:
    """A listing that waits until it's let go, counting as it goes."""

    label = "slow"

    def __init__(self) -> None:
        self.go = threading.Event()
        self.listed = 0

    def listing(self, progress=None) -> Changes:
        self.listed += 1
        assert progress is not None
        progress.folders_total = 2
        progress.count(files=3, folders=1)
        self.go.wait(5)
        return Changes([FileEntry(f"f{n}.csv", f"id:{n}", 1) for n in range(5)], [], None)

    def changes(self, cursor: str) -> Changes:
        raise CursorTooOld("none")


def test_a_refresh_runs_in_the_background_and_shows_how_far_it_has_got(processed):
    slow = SlowSource()
    processed._source = lambda: slow
    processed.start_refresh()
    for _ in range(100):
        listing = processed.status().listing
        if listing is not None and listing.files:
            break
        time.sleep(0.01)
    assert listing is not None
    assert (listing.files, listing.folders_done, listing.folders_total) == (3, 1, 2)
    processed.start_refresh()  # joins the one under way
    slow.go.set()
    processed.wait_for_listing(5)
    status = processed.status()
    assert slow.listed == 1 and status.listing is None and status.files == 5
    assert status.listing_error is None


def test_a_background_refresh_that_fails_says_why(processed):
    def unreachable():
        raise NotAvailable("Dropbox couldn't be reached.")

    processed._source = unreachable
    processed.start_refresh()
    processed.wait_for_listing(5)
    status = processed.status()
    assert status.listing is None and status.listing_error == "Dropbox couldn't be reached."


def test_only_a_folder_id_can_be_opened(app):
    with TestClient(app) as client:
        for bad in ("../etc", "/Study Data", "id:a/b"):
            answer = client.get("/api/processed/folders", params={"inside": bad})
            assert answer.status_code == 422, bad


# Dropbox's own words for an app without a write scope, as a live run on
# 2026-10-20 got them (HTTP 400), with the app's id made up.
LIVE_REFUSAL = (
    'Error in call to API function "files/delete_v2": Your app (ID: 1234567) is not '
    "permitted to access this endpoint because it does not have the required scope "
    "'files.content.write'. The owner of the app can enable the scope for the app using "
    "the Permissions tab on the App Console."
)


class WriteAttempts:
    """Dropbox's write endpoints, answering as a read-only app gets them
    answered (`live`: 400 with Dropbox's words; `scope`: 401 missing_scope),
    as a misconfigured app would (`allow`: 200), or with an error that proves
    nothing (`notfound`: 409)."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.writes: list[str] = []
        self.listed: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/2/files/get_metadata":
            asked = json.loads(request.content)["path"]
            if asked != "/Me/Test (synthetic)":
                return httpx.Response(409, json={"error_summary": "path/not_found/.."})
            folder = {".tag": "folder", "id": "id:test", "path_display": asked}
            return httpx.Response(200, json=folder)
        if path in ("/2/files/list_folder", "/2/files/list_folder/continue"):
            if path == "/2/files/list_folder":
                self.listed.append(json.loads(request.content)["path"])
            entry = {".tag": "file", "path_lower": "/t/a.csv", "rev": "1", "content_hash": "h"}
            return httpx.Response(200, json={"entries": [entry], "cursor": "c", "has_more": False})
        self.writes.append(path)
        if self.answer == "allow":
            return httpx.Response(200, json={})
        if self.answer == "live":
            return httpx.Response(400, text=LIVE_REFUSAL)
        if self.answer == "notfound":
            return httpx.Response(409, json={"error_summary": "from_lookup/not_found/.."})
        return httpx.Response(
            401,
            json={"error_summary": "missing_scope/..", "error": {".tag": "missing_scope"}},
        )


@pytest.mark.parametrize("answer", ["live", "scope", "allow", "notfound"])
def test_the_read_only_check(auth, connection, answer):
    from datalab.dropbox.check import check_read_only

    auth.start()
    auth.finish("GOODCODE")
    index = ProcessedIndex(connection)
    index.choose("id:t", "/T", "100")
    dropbox = WriteAttempts(answer)
    lines: list[str] = []
    code = check_read_only(
        auth, index, http=httpx.Client(transport=httpx.MockTransport(dropbox)), say=lines.append
    )
    tried = {"/2/files/upload", "/2/files/create_folder_v2", "/2/files/delete_v2",
             "/2/files/move_v2", "/2/files/copy_v2"}  # fmt: skip
    assert tried <= set(dropbox.writes)
    assert not any("sl.first" in line or "rt-1" in line for line in lines)  # no tokens printed
    marks = [line.split()[0] for line in lines if line.startswith("  ") and ":" in line]
    if answer in ("live", "scope"):
        assert code == 0 and lines[-1] == "PASS: Dropbox refused every change."
        assert marks == ["refused"] * 5
        # Nothing was made, so nothing is cleaned up.
        assert len(dropbox.writes) == 5
    elif answer == "allow":
        assert code == 1 and lines[-1].startswith("FAIL") and marks == ["ALLOWED"] * 5
        # It removes only what it made, by its own made-up names.
        assert dropbox.writes[-2:] == ["/2/files/delete_v2", "/2/files/delete_v2"]
    else:
        # "Not found" doesn't show the app lacks permission: no pass, no clean-up.
        assert code == 1 and lines[-1].startswith("FAIL") and marks == ["UNCLEAR"] * 5
        assert len(dropbox.writes) == 5 and any("not_found" in line for line in lines)


def test_the_read_only_check_can_try_another_folder(auth, connection):
    from datalab.dropbox.check import check_read_only

    auth.start()
    auth.finish("GOODCODE")
    index = ProcessedIndex(connection)
    index.choose("id:study", "/Study", "100")
    dropbox = WriteAttempts("live")
    lines: list[str] = []
    http = httpx.Client(transport=httpx.MockTransport(dropbox))
    code = check_read_only(auth, index, http=http, say=lines.append, folder="/Me/Test (synthetic)")
    assert code == 0 and "/Me/Test (synthetic)" in lines[0]
    # Only the named folder is listed; the chosen one isn't touched.
    assert dropbox.listed == ["id:test", "id:test"]

    lines.clear()
    assert check_read_only(auth, index, http=http, say=lines.append, folder="/Nope") == 2
    assert "isn't a folder" in lines[-1]


def test_the_shape_report_gives_counts_not_names():
    from datalab.dropbox.shape import report, shape_of

    assert shape_of("Steps_P0123.csv") == "a_a9.csv"
    entries = [FileEntry(f"Waves/{name}.csv", f"id:{i}", 2_000_000) for i, name in
               enumerate(["Smith_Jane", "Doe_John", "Roe_Rick"])]  # fmt: skip
    entries += [FileEntry(f"fitbit/steps_{year}.csv", f"id:s{year}", 500) for year in (2024, 2025)]
    entries.append(FileEntry("README", "id:r", 10))
    lines: list[str] = []
    report(entries, say=lines.append)
    text = "\n".join(lines)
    assert lines[0].startswith("6 files, 5.7 MB, in 5 datasets")
    assert "  Waves: 3, 5.7 MB, 3, 3" in lines  # as named, not as its pattern
    assert (
        "  fitbit: 2, 1000 bytes, 1, 0" in lines
        and "  (files at the top): 1, 10 bytes, 1, 1" in lines
    )
    assert "  .csv: 5, 5.7 MB" in lines and "  (none): 1, 10 bytes" in lines
    assert "  1-10 MB: 3, 5.7 MB" in lines and "  2024: 1" in lines and "  2025: 1" in lines
    assert "  1 file: 4, 4" in lines and "  2-10: 1, 2" in lines
    assert "  a_a.csv: 3" in lines
    for name in ("smith", "jane", "doe", "roe", "steps"):
        assert name not in text.lower()


def test_errors_about_a_path_are_refusals(client):
    with pytest.raises(DropboxRefused, match="not_found"):
        client.metadata("/nothing")


def test_a_download_that_doesnt_match_dropbox_is_dropped(client, tmp_path):
    from datalab.dropbox.auth import DropboxUnavailable

    with pytest.raises(DropboxUnavailable, match="didn't match"):
        client.download("id:x", tmp_path / "x.csv", max_bytes=1000, deadline=1e18)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("where", "fetched"),
    [("/study/fitbit/a.csv", True), ("/personal/taxes.csv", False), ("/study-old/a.csv", False),
     (None, False)],
)  # fmt: skip
def test_a_download_from_outside_the_chosen_folder_is_refused(auth, tmp_path, where, fetched):
    from datalab.dropbox.client import OutsideFolder

    auth.start()
    auth.finish("GOODCODE")
    body = b"a,b\n1,2\n"

    def dropbox(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/2/files/get_metadata":
            folder = {".tag": "folder", "name": "Study", "id": "id:s", "path_display": "/Study",
                      "path_lower": "/study"}  # fmt: skip
            return httpx.Response(200, json=folder)
        hasher = ContentHasher()
        hasher.update(body)
        result = {"size": len(body), "content_hash": hasher.hexdigest()}
        if where is not None:
            result["path_lower"] = where
        return httpx.Response(200, content=body, headers={"Dropbox-API-Result": json.dumps(result)})

    client = DropboxClient(auth, http=httpx.Client(transport=httpx.MockTransport(dropbox)))
    source = DropboxSource(client, "id:s", "/Study")
    dest = tmp_path / "a.csv"
    entry = FileEntry("fitbit/a.csv", "id:a", len(body))
    if fetched:
        source.fetch(entry, dest, max_bytes=1000, deadline=1e18)
        assert dest.read_bytes() == body
    else:
        with pytest.raises(OutsideFolder):
            source.fetch(entry, dest, max_bytes=1000, deadline=1e18)
        assert not list(tmp_path.iterdir())  # nothing written, not even a partial


def test_the_content_hash_is_dropboxs(tmp_path):
    # Dropbox's documented example: SHA-256 of each 4 MB block's SHA-256.
    import hashlib

    data = b"x" * (4 * 1024 * 1024 + 10)
    blocks = [data[: 4 * 1024 * 1024], data[4 * 1024 * 1024 :]]
    expected = hashlib.sha256(b"".join(hashlib.sha256(b).digest() for b in blocks)).hexdigest()
    hasher = ContentHasher()
    for start in range(0, len(data), 1_000_003):
        hasher.update(data[start : start + 1_000_003])
    assert hasher.hexdigest() == expected
    (tmp_path / "f").write_bytes(data)
    assert content_hash(tmp_path / "f") == expected


# --- Over the API and the agent tools ---------------------------------------------------


@pytest.fixture
def app(settings, catalog):
    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )
    app.state.services.sessions._containers = lambda conversation: FakeContainers()
    return app


def test_a_conversation_chooses_its_sources(app):
    with TestClient(app) as client:
        modes_out = {m["id"]: m for m in client.get("/api/modes").json()}
        assert modes_out["analysis"]["sources"] == ["database", "dropbox"]
        assert modes_out["sql"]["sources"] == ["database"]
        plain = client.post("/api/conversations", json={}).json()
        assert plain["data_sources"] == ["database"]
        both = client.post(
            "/api/conversations", json={"data_sources": ["dropbox", "database"]}
        ).json()
        assert both["data_sources"] == ["database", "dropbox"]
        refused = client.post(
            "/api/conversations", json={"mode": "sql", "data_sources": ["dropbox"]}
        )
        assert refused.status_code == 422
        changed = client.patch(
            f"/api/conversations/{plain['id']}", json={"data_sources": ["dropbox"]}
        ).json()
        assert changed["data_sources"] == ["dropbox"]
        events = client.get(f"/api/conversations/{plain['id']}/events").json()
        assert [e["data"] for e in events if e["type"] == "sources_changed"] == [
            {"from": ["database"], "to": ["dropbox"]}
        ]
        assert client.get(f"/api/conversations/{plain['id']}/files-accessed").json() == []


def test_without_the_dropbox_app_nothing_offers_the_processed_data(settings, catalog):
    # Installed DataLab, until the lab's central DataLab: no app key shipped.
    real = replace(settings, profile="real", dropbox=DropboxSettings(app_key=None))
    app = create_app(
        real, database=FakeDatabase(), catalog=catalog, manage_containers=False, protect_api=False
    )
    with TestClient(app) as client:
        assert client.get("/api/processed").json()["source"] == "off"
        modes = client.get("/api/modes").json()
        assert modes and all("dropbox" not in m["sources"] for m in modes)
        assert any(m["sources"] == ["database"] for m in modes)
        asked = client.post(
            "/api/conversations", json={"mode": "analysis", "data_sources": ["dropbox"]}
        )
        assert asked.status_code == 409


def test_settings_show_practices_folder(app):
    with TestClient(app) as client:
        status = client.get("/api/processed").json()
        assert status["source"] == "practice" and status["ready"]
        assert client.post("/api/processed/connect").status_code == 409
        started = client.post("/api/processed/refresh").json()
        assert started["listing_error"] is None
        app.state.services.sessions.processed.wait_for_listing(10)
        refreshed = client.get("/api/processed").json()
        assert refreshed["files"] > 5 and refreshed["listing"] is None


def _token(services, tmp_path, sources):
    tools = tools_for_sources(modes.MODES["analysis"].allowed_tools, sources)
    return services.tokens.issue(
        SessionAccess(
            session_id="c_sess1",
            kind="data",
            results_dir=tmp_path / "oracle",
            tools=tools,
            mode_label="Analysis",
            sources=sources,
            mode_sources=modes.MODES["analysis"].sources,
            processed_dir=tmp_path / "dropbox" if "dropbox" in sources else None,
        )
    )


async def test_the_agent_lists_and_fetches_files(server, tmp_path):
    base_url, services, _ = server
    token = _token(services, tmp_path, ("database", "dropbox"))
    async with mcp_session(base_url, token) as session:
        listed = payload(await session.call_tool("list_data_files", {"folder": "fitbit"}))
        names = [d["dataset"] for d in listed["datasets"]]
        assert names == ["fitbit/daily_steps_{year}.csv", "fitbit/sleep_nightly_{year}.csv"]
        files = payload(
            await session.call_tool("list_data_files", {"dataset": "fitbit/daily_steps_{year}.csv"})
        )
        assert {f["path"] for f in files["files"]} == {
            "fitbit/daily_steps_2024.csv",
            "fitbit/daily_steps_2025.csv",
        }
        loaded = payload(
            await session.call_tool("load_data_file", {"path": "fitbit/daily_steps_2025.csv"})
        )
        assert loaded["path"] == "/data/dropbox/fitbit/daily_steps_2025.csv"
        assert loaded["preview"][0] == "participant_id,date,steps"
        assert (tmp_path / "dropbox" / "fitbit" / "daily_steps_2025.csv").is_file()
        refused = await session.call_tool("load_data_file", {"path": "../../etc/passwd"})
        assert refused.is_error


async def test_the_tools_follow_the_conversations_sources(server, tmp_path):
    base_url, services, _ = server
    async with mcp_session(base_url, _token(services, tmp_path, ("database",))) as session:
        refused = await session.call_tool("list_data_files", {})
        assert refused.is_error and "doesn't use the processed data" in refused.content[0].text
    async with mcp_session(base_url, _token(services, tmp_path, ("dropbox",))) as session:
        refused = await session.call_tool("query", {"sql": "SELECT 1 FROM DUAL"})
        assert refused.is_error and "processed data only" in refused.content[0].text
