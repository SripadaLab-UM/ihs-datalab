"""Where the catalog comes from: the settings' folder, the knowledge base's
clone, or DataLab's own; read again after a sync, with no restart; and what
everything says while there's none."""

from __future__ import annotations

import dataclasses
import functools
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from datalab import app as app_module
from datalab import datalock, db
from datalab.api.knowledge import KnowledgeServices
from datalab.app import create_app
from datalab.config import OracleSettings, QueryLimits, RepoSettings, Settings, load_settings
from datalab.data.access_log import AccessLog
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.catalog_source import RETRY_SECONDS, CatalogSource, knowledge_clone
from datalab.data.oracle import QueryFailed
from datalab.data.service import DataService
from datalab.data.sql_drafts import DraftInvalid, SqlDrafts
from datalab.data.sqlcheck import SqlRejected
from datalab.repos.git import Clone
from datalab.repos.github import Account, Tokens, TokenStore
from tests.conftest import COHORTS, FakeDatabase, sample_catalog
from tests.kb_fixtures import Remote, git, sample_kb

REPO = "SripadaLab-UM/ihs-knowledge"
# The query the Windows tester's DataLab refused: columns named.
COLUMN_SQL = (
    "SELECT RECORD_DATE, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA "
    "WHERE PARTICIPANTIDENTIFIER = :pid"
)
ME = Account("yfang", 42, "Yu Fang")


def fitbit(schema: str = "IHS_2025", *columns: str) -> TableInfo:
    names = columns or ("PARTICIPANTIDENTIFIER", "RECORD_DATE", "TRACKERSTEPS")
    typed = [Column(n, "VARCHAR2(64)" if n.startswith("P") else "NUMBER") for n in names]
    return TableInfo(schema, "VFITBITDAILYDATA", "VIEW", "Fitbit daily summary", typed)


def schema_file(table: TableInfo) -> bytes:
    return yaml.safe_dump(asdict(table), sort_keys=False).encode()


def kb_files(*tables: TableInfo) -> dict[str, bytes]:
    files = dict(sample_kb())
    for table in tables or (fitbit(),):
        files[f"generated/schema/{table.schema}/{table.name}.yml"] = schema_file(table)
    return files


def real(tmp_path: Path, **changes) -> Settings:
    settings = Settings(
        profile="real",
        data_dir=tmp_path / "data",
        oracle=OracleSettings(
            host="127.0.0.1",
            port=1,
            service="X",
            user="U",
            keychain_service="test",
            read_only_roles=("IHS_2025_RO",),
            allowed_schemas=COHORTS,
        ),
        repos=RepoSettings(knowledge=REPO, client_id="Iv23liTESTCLIENT"),
    )
    return dataclasses.replace(settings, **changes)


def synced_clone(settings: Settings, remote: Remote) -> Clone:
    """The clone as the installer's `datalab repos sync` leaves it."""
    default = knowledge_clone(settings)
    assert default is not None
    clone = Clone(default.path, remote.url, allow_local=True)
    clone.sync()
    return clone


# Where it comes from ---------------------------------------------------------


def test_the_knowledge_bases_catalog_is_found_in_its_clone(tmp_path: Path) -> None:
    settings = real(tmp_path)
    remote = Remote(tmp_path, kb_files())
    clone = synced_clone(settings, remote)
    assert clone.path == tmp_path / "data" / "repos" / "ihs-knowledge"

    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is True
    assert source.origin == "knowledge" and source.head == remote.head()
    columns = catalog.column_index()["IHS_2025"]["VFITBITDAILYDATA"]
    assert set(columns) == {"PARTICIPANTIDENTIFIER", "RECORD_DATE", "TRACKERSTEPS"}
    assert catalog.missing is None and source.detail is None


def test_the_repo_name_comes_from_the_settings_not_a_fixed_one(tmp_path: Path) -> None:
    settings = real(tmp_path, repos=RepoSettings(knowledge="OtherLab/lab-kb"))
    clone = knowledge_clone(settings)
    assert clone is not None and clone.path == tmp_path / "data" / "repos" / "lab-kb"
    # Where the installers' `datalab repos sync` and the Knowledge tab clone it.
    from types import SimpleNamespace

    from datalab.repos.sync import RepoSync

    auth = SimpleNamespace(token_for_git=lambda: None)
    installer = RepoSync("knowledge", "OtherLab/lab-kb", settings.data_dir, auth, None)  # type: ignore[arg-type]
    assert installer.clone.path == clone.path


def test_a_catalog_folder_in_the_settings_wins(tmp_path: Path) -> None:
    folder = tmp_path / "lab-catalog"
    Catalog([fitbit("IHS_2024", "STEPS")]).save(folder)
    settings = real(tmp_path, catalog_dir=folder)
    Remote(tmp_path, kb_files())  # a knowledge base there too: not read
    assert knowledge_clone(settings) is None

    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is True and source.origin == "setting"
    assert catalog.schemas == ["IHS_2024"]


def test_an_empty_catalog_folder_in_the_settings_isnt_passed_over(tmp_path: Path) -> None:
    settings = real(tmp_path, catalog_dir=tmp_path / "nothing-here")
    Catalog([fitbit()]).save(tmp_path / "data" / "catalog")
    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is False
    assert "catalog_dir" in (catalog.missing or "") and str(tmp_path) not in (catalog.missing or "")
    assert str(tmp_path / "nothing-here") in (source.detail or "")


def test_datalabs_own_folder_is_the_fallback(tmp_path: Path) -> None:
    Catalog([fitbit()]).save(tmp_path / "data" / "catalog")
    # No knowledge base set, or set but not downloaded yet.
    for settings in (real(tmp_path, repos=RepoSettings()), real(tmp_path)):
        catalog = Catalog([])
        source = CatalogSource(settings, catalog)
        assert source.load() is True and source.origin == "data folder"


def test_practice_reads_only_its_own_folder(tmp_path: Path) -> None:
    Catalog([fitbit()]).save(tmp_path / "data" / "catalog")
    settings = dataclasses.replace(real(tmp_path), profile="practice")
    assert knowledge_clone(settings) is None
    catalog = Catalog([])
    assert CatalogSource(settings, catalog).load() is True
    assert app_module.catalog_folder(settings) == tmp_path / "data" / "catalog"


# The repo's files are data -----------------------------------------------------


def test_files_that_arent_catalog_tables_are_left_out(tmp_path: Path) -> None:
    good = fitbit()
    bad = {
        # A value where metadata belongs.
        "generated/schema/IHS_2025/LEAK.yml": b"schema: IHS_2025\nname: LEAK\ntype: VIEW\n"
        b"rows: [[1, 2]]\n",
        # Names that don't match the file's place.
        "generated/schema/IHS_2025/OTHER.yml": schema_file(fitbit()),
        "generated/schema/IHS_2025/BROKEN.yml": b"schema: [unclosed\n",
        # Aliases (a YAML bomb's building block).
        "generated/schema/IHS_2025/ALIAS.yml": b"schema: &s IHS_2025\nname: ALIAS\ntype: VIEW\n"
        b"comment: *s\n",
        "generated/schema/IHS_2025/COLS.yml": b"schema: IHS_2025\nname: COLS\ntype: VIEW\n"
        b"columns:\n- name: A\n  type: NUMBER\n  value: 7\n",
        # Not <SCHEMA>/<TABLE>.yml.
        "generated/schema/IHS_2025/deeper/X.yml": schema_file(fitbit()),
        "generated/schema/bad name/X.yml": schema_file(fitbit()),
    }
    remote = Remote(tmp_path, {**kb_files(good), **bad})
    # A link in the repo, to a file outside it.
    (tmp_path / "outside.yml").write_bytes(schema_file(fitbit("IHS_2025", "SECRET")))
    os.symlink(tmp_path / "outside.yml", remote.other / "generated/schema/IHS_2025/LINK.yml")
    git("add", "-A", cwd=remote.other)
    git("commit", "-q", "-m", "A link", cwd=remote.other)
    git("push", "-q", "origin", "HEAD:main", cwd=remote.other)
    settings = real(tmp_path)
    synced_clone(settings, remote)

    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is True
    assert catalog.names("IHS_2025") == ["VFITBITDAILYDATA"]
    assert "SECRET" not in catalog.column_index()["IHS_2025"]["VFITBITDAILYDATA"]
    left_out = {line.split(":")[0] for line in source.skipped}
    names = ("LEAK", "OTHER", "BROKEN", "ALIAS", "COLS", "LINK")
    assert left_out == {f"IHS_2025/{n}.yml" for n in names} | {"bad name/X.yml"}


def test_a_folder_never_follows_links_out_of_it(tmp_path: Path) -> None:
    folder = tmp_path / "catalog"
    Catalog([fitbit()]).save(folder)
    outside = tmp_path / "elsewhere"
    Catalog([fitbit("IHS_2024")]).save(outside)
    os.symlink(outside / "IHS_2024", folder / "IHS_2024")
    os.symlink(outside / "IHS_2024" / "VFITBITDAILYDATA.yml", folder / "IHS_2025" / "X.yml")
    catalog, skipped = Catalog.read(folder)
    assert catalog.schemas == ["IHS_2025"] and catalog.names("IHS_2025") == ["VFITBITDAILYDATA"]
    assert skipped == ["IHS_2024/: a link, not a folder", "IHS_2025/X.yml: not a plain file"]


def test_a_catalog_datalab_saved_reads_back_the_same(tmp_path: Path) -> None:
    quoted = Column("Black tea", "NUMBER", False, "x")
    table = TableInfo("IHS_2025", "T", "TABLE", "", [quoted], ["Black tea"])
    Catalog([table, *sample_catalog()._tables.values()]).save(tmp_path / "c")
    catalog, skipped = Catalog.read(tmp_path / "c")
    assert skipped == [] and catalog.get("IHS_2025.T") == table
    assert len(catalog) == len(sample_catalog()) + 1


# Read again after a sync, with no restart ---------------------------------------


def test_a_sync_that_changes_generated_schema_is_read_again(tmp_path: Path) -> None:
    settings = real(tmp_path)
    remote = Remote(tmp_path, kb_files())
    clone = synced_clone(settings, remote)
    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    source.load()
    first = source.head

    assert source.refresh() is True and source.head == first  # nothing new
    remote.write({"generated/schema/IHS_2025/VW_DAILY_MOOD.yml": schema_file(
        TableInfo("IHS_2025", "VW_DAILY_MOOD", "VIEW", "", [Column("MOOD", "NUMBER")])
    )}, "Add mood")  # fmt: skip
    clone.sync()
    assert source.refresh() is True and source.head == remote.head() != first
    assert catalog.names("IHS_2025") == ["VFITBITDAILYDATA", "VW_DAILY_MOOD"]


def test_while_there_is_none_it_looks_again_now_and_then(tmp_path: Path) -> None:
    settings, now = real(tmp_path), [0.0]
    remote = Remote(tmp_path, kb_files())
    catalog = Catalog([])
    source = CatalogSource(settings, catalog, clock=lambda: now[0])
    assert source.ensure() is False  # not downloaded yet
    synced_clone(settings, remote)
    assert source.ensure() is False  # not again at once
    now[0] += RETRY_SECONDS
    assert source.ensure() is True and len(catalog)


# What everything says while there's none ------------------------------------------


def setup_error(text: str) -> None:
    assert "no catalog" in text and "can't check queries" in text
    assert "sign in to GitHub in Settings → Connections" in text
    assert "maintainer" in text
    assert "could not be resolved" not in text


async def test_no_catalog_is_a_setup_error_everywhere(tmp_path: Path) -> None:
    settings = real(tmp_path)  # the knowledge base isn't downloaded yet
    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is False
    setup_error(catalog.missing_message())
    assert "sign in to GitHub" in (source.detail or "")

    log = AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")
    database = FakeDatabase()
    service = DataService(database, log, QueryLimits(), COHORTS, catalog, catalog_source=source)
    assert await service.ensure_catalog() is False
    setup_error(service.catalog_missing())
    for sql in (COLUMN_SQL, "SELECT COUNT(*) FROM IHS_2025.VFITBITDAILYDATA"):
        with pytest.raises(QueryFailed) as refused:
            await service.run_query(
                session_id="s1", sql=sql, binds={"pid": "x"}, results_dir=tmp_path / "r"
            )
        setup_error(str(refused.value))
    assert database.calls == []
    assert {r.status for r in log.for_session("s1")} == {"rejected"}

    # The SQL Playground's editor check, and the chat's proposed queries.
    from datalab.playground import Playground

    report = Playground(settings, service, catalog, log).check(COLUMN_SQL)
    [error] = report.errors
    setup_error(error.message)
    drafts = SqlDrafts(None, catalog, lambda: COHORTS)
    drafts.turn_running = lambda _: True

    class Store:
        def last(self, *_):
            return type("E", (), {"seq": 1})() if _[1] == "user_message" else None

        def events_of_types_after(self, *_):
            return []

    drafts._store = Store()
    with pytest.raises(DraftInvalid) as draft:
        drafts.propose("c1", sql=COLUMN_SQL, title="Steps")
    setup_error(str(draft.value))


async def test_the_agents_catalog_tools_say_what_is_missing(tmp_path: Path) -> None:
    from datalab.data import agent_tools

    catalog = Catalog([])
    source = CatalogSource(real(tmp_path), catalog)
    source.load()
    log = AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")
    service = DataService(
        FakeDatabase(), log, QueryLimits(), COHORTS, catalog, catalog_source=source
    )
    with pytest.raises(ToolError) as refused:
        await agent_tools._require_catalog(service)
    setup_error(str(refused.value))


def test_the_check_still_refuses_what_it_refused_once_the_catalog_loads(tmp_path: Path) -> None:
    from datalab.data.sqlcheck import check_sql

    settings = real(tmp_path)
    synced_clone(settings, Remote(tmp_path, kb_files()))
    catalog = Catalog([])
    CatalogSource(settings, catalog).load()
    columns = catalog.column_index()
    check_sql(COLUMN_SQL, allowed_schemas=COHORTS, columns=columns)
    for refused in (
        "SELECT NOPE FROM IHS_2025.VFITBITDAILYDATA",
        "SELECT TRACKERSTEPS FROM IHS_2025.NOT_A_TABLE",
        "SELECT TRACKERSTEPS FROM OTHER_SCHEMA.VFITBITDAILYDATA",
        "DELETE FROM IHS_2025.VFITBITDAILYDATA",
        "SELECT TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA; DROP TABLE X",
    ):
        with pytest.raises(SqlRejected):
            check_sql(refused, allowed_schemas=COHORTS, columns=columns)


# The app: at startup, after an in-app sync, after a late sign-in ------------------


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Remote:
    """The knowledge base on a local bare repo standing in for GitHub."""
    remote = Remote(tmp_path, kb_files())
    monkeypatch.setattr(
        app_module, "KnowledgeServices", functools.partial(KnowledgeServices, remote=remote.url)
    )
    return remote


def sign_in() -> None:
    """What Settings → GitHub's device flow leaves in the keychain."""
    TokenStore().save(Tokens("ghu_x", time.time() + 8 * 3600, "ghr_x", time.time() + 1e7, ME))


def check(client: TestClient, sql: str = COLUMN_SQL) -> dict:
    return client.post("/api/sql/check", json={"sql": sql}).json()


def started(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, manage_containers=False, protect_api=False))


def test_a_late_sign_in_and_sync_bring_the_catalog_without_a_restart(
    tmp_path: Path, lab: Remote
) -> None:
    settings = real(tmp_path)
    with started(settings) as client:
        # Before: the setup error, not "could not be resolved".
        health = client.get("/api/health").json()
        assert health["catalog_tables"] == 0 and health["catalog_state"] == "empty"
        status = client.get("/api/catalog/status").json()
        assert "sign in to GitHub" in status["detail"]
        [error] = check(client)["errors"]
        setup_error(error["message"])
        # A workflow isn't drafted from SQL that couldn't be checked either.
        drafted = client.post(
            "/api/workflows/drafts",
            json={"name": "steps", "queries": [{"sql": COLUMN_SQL, "binds": {"pid": "x"}}]},
        )
        assert drafted.status_code == 422
        setup_error(drafted.json()["detail"])

        # Settings → GitHub: sign in, then its automatic Sync.
        sign_in()
        synced = client.post("/api/knowledge/sync").json()
        assert synced["repo"] == "in sync"

        assert check(client)["ok"] is True
        assert client.get("/api/health").json()["catalog_state"] == "ready"
        assert client.get("/api/catalog/status").json()["detail"] is None
        hits = client.get("/api/sql/catalog/search", params={"q": "fitbit"}).json()
        assert [h["name"] for h in hits] == ["VFITBITDAILYDATA"]
        services = client.app.state.services  # type: ignore[attr-defined]
        assert services.catalog.get("IHS_2025.VFITBITDAILYDATA") is not None

        # A later sync with a new table: read again, still no restart.
        lab.write({"generated/schema/IHS_2025/VW_DAILY_MOOD.yml": schema_file(
            TableInfo("IHS_2025", "VW_DAILY_MOOD", "VIEW", "", [Column("MOOD", "NUMBER")])
        )}, "Add mood")  # fmt: skip
        client.post("/api/knowledge/sync")
        assert check(client, "SELECT MOOD FROM IHS_2025.VW_DAILY_MOOD")["ok"] is True
        assert check(client, "SELECT NOPE FROM IHS_2025.VW_DAILY_MOOD")["ok"] is False


def test_a_beta5_install_finds_the_catalog_after_the_update(
    tmp_path: Path, lab: Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    """beta.5's settings.toml never named a catalog folder; its installer had
    synced the knowledge base. The updated DataLab finds it at startup, and
    settings.toml is left as it was."""
    data = tmp_path / "data"
    data.mkdir()
    toml = (
        '[oracle]\nhost = "127.0.0.1"\nport = 1\nservice = "X"\nuser = "U"\n'
        'allowed_schemas = ["IHS_2024", "IHS_2025"]\n\n'
        f'[repos]\nknowledge = "{REPO}"\nclient_id = "Iv23liTESTCLIENT"\n'
    )
    (data / "settings.toml").write_text(toml, encoding="utf-8")
    monkeypatch.setenv("DATALAB_DATA_DIR", str(data))
    monkeypatch.delenv("DATALAB_CATALOG_DIR", raising=False)
    settings = load_settings("real")
    assert settings.catalog_dir is None
    synced_clone(settings, lab)

    with started(settings) as client:
        assert client.get("/api/health").json()["catalog_state"] == "ready"
        assert check(client)["ok"] is True
    assert (data / "settings.toml").read_text(encoding="utf-8") == toml
    datalock.release_all()


# Review fixes: locks, names, limits --------------------------------------------------


def run_both(*work, timeout: float = 5.0) -> None:
    """Run each in its own thread; fail (not hang) if any is still going after `timeout`."""
    threads = [threading.Thread(target=w, daemon=True) for w in work]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(t.is_alive() for t in threads), "deadlocked"


def test_a_seed_sync_holding_the_clone_and_a_query_dont_deadlock(tmp_path: Path) -> None:
    """The reviewer's case (a): an empty catalog, a conversation's seed sync
    holding the clone's lock (and reading the catalog again under it, as it
    once did), and a query's ensure() at the same time."""
    settings, now = real(tmp_path), [0.0]
    remote = Remote(tmp_path, kb_files())
    catalog = Catalog([])
    source = CatalogSource(settings, catalog, clock=lambda: now[0])
    source.load()
    now[0] += RETRY_SECONDS
    clone = synced_clone(settings, remote)
    answers = []

    def seed() -> None:
        with clone.lock:
            time.sleep(0.3)  # the seed's fetch
            source.refresh()

    def query() -> None:
        time.sleep(0.1)
        answers.append(source.ensure())  # doesn't wait for the sync: no catalog yet

    run_both(seed, query)
    assert answers == [False] and len(catalog)


def test_an_in_app_sync_overlapping_a_seed_doesnt_deadlock(tmp_path: Path) -> None:
    """Case (b): Sync pressed while a seed sync brings a new commit."""
    settings = real(tmp_path)
    remote = Remote(tmp_path, kb_files())
    clone = synced_clone(settings, remote)
    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    source.load()
    remote.write({"generated/schema/IHS_2025/VW_DAILY_MOOD.yml": schema_file(
        TableInfo("IHS_2025", "VW_DAILY_MOOD", "VIEW", "", [Column("MOOD", "NUMBER")])
    )}, "Add mood")  # fmt: skip

    def seed() -> None:
        with clone.lock:
            clone.sync()
            time.sleep(0.2)
            source.refresh()

    def manual() -> None:
        time.sleep(0.05)
        source.refresh()
        source.ensure()

    run_both(seed, manual, manual)
    assert catalog.names("IHS_2025") == ["VFITBITDAILYDATA", "VW_DAILY_MOOD"]


def test_the_seed_reads_the_catalog_again_after_letting_go_of_the_clone(tmp_path: Path) -> None:
    from datalab.knowledge.service import Knowledge
    from datalab.sessions.manager import SessionManager
    from datalab.sessions.store import ConversationStore
    from datalab.sessions.tokens import SessionTokens

    settings = real(tmp_path)
    remote = Remote(tmp_path, kb_files())
    sign_in()
    connection = db.connect(settings.database_file)
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    held_elsewhere = []

    def on_sync() -> None:
        # Another thread can take the clone's lock now: it isn't held.
        got = []

        def take() -> None:
            if knowledge.clone.lock.acquire(timeout=2):
                got.append(True)
                knowledge.clone.lock.release()

        other = threading.Thread(target=take)
        other.start()
        other.join()
        held_elsewhere.append(got)

    knowledge = Knowledge(settings, connection, store, manager, remote=remote.url, on_sync=on_sync)
    knowledge.clone.sync()
    staging = tmp_path / "staging"
    staging.mkdir()
    made = store.create(kind="data", mode="analysis", title="t", model="m")
    assert knowledge._seed(made, staging) == remote.head()  # stale: syncs first
    assert held_elsewhere == [[True]]
    connection.close()


def test_column_names_that_arent_oracle_names_are_left_out(tmp_path: Path) -> None:
    def table(name: str, *columns: str) -> bytes:
        typed = [Column(c, "NUMBER") for c in columns]
        return schema_file(TableInfo("IHS_2025", name, "VIEW", "", typed))

    long = "A" * 129
    files = {
        # Real shapes: $ and #, a quoted lowercase or spaced name, 128 bytes.
        "IHS_2025/GOOD.yml": table("GOOD", "STEPS$", "N#1", "Black tea", "steps", "B" * 128),
        "IHS_2025/QUOTE.yml": table("QUOTE", 'A"B'),
        "IHS_2025/CONTROL.yml": table("CONTROL", "A\nB"),
        "IHS_2025/LONG.yml": table("LONG", long),
        "IHS_2025/KEY.yml": schema_file(
            TableInfo("IHS_2025", "KEY", "VIEW", "", [Column("A", "NUMBER")], ["A\x00"])
        ),
        # Built-ins: the column is left out, the table kept.
        "IHS_2025/BUILTIN.yml": table("BUILTIN", "STEPS", "USER", "UID", "ORA_INVOKING_USER"),
    }
    catalog, skipped = Catalog.from_files({k: v for k, v in files.items()})
    assert catalog.names("IHS_2025") == ["BUILTIN", "GOOD"]
    good = {"STEPS$", "N#1", "Black tea", "steps", "B" * 128}
    assert set(catalog.column_index()["IHS_2025"]["GOOD"]) == good
    assert list(catalog.column_index()["IHS_2025"]["BUILTIN"]) == ["STEPS"]
    assert sorted(s.split(":")[0] for s in skipped) == sorted(
        ["IHS_2025/QUOTE.yml", "IHS_2025/CONTROL.yml", "IHS_2025/LONG.yml", "IHS_2025/KEY.yml"]
        + ["IHS_2025/BUILTIN.yml"] * 3
    )


def test_names_from_the_repo_are_logged_safely(caplog: pytest.LogCaptureFixture) -> None:
    catalog, skipped = Catalog.from_files({"IHS_2025/X\x1b[31m.yml": b"x"})
    assert not len(catalog)
    [line] = skipped
    assert "\x1b" not in line and "\\x1b" in line
    assert "\x1b" not in caplog.text


def test_only_generated_schema_is_listed_and_its_size_is_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datalab.data import catalog_source

    settings = real(tmp_path)
    remote = Remote(tmp_path, kb_files())
    clone = synced_clone(settings, remote)
    listed = []
    real_ls_tree = Clone.ls_tree

    def ls_tree(self, commit, *paths):
        listed.append(paths)
        return real_ls_tree(self, commit, *paths)

    monkeypatch.setattr(Clone, "ls_tree", ls_tree)
    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is True and listed == [("generated/schema/",)]

    # Grown past the limit: not read, and the catalog read before is kept.
    remote.write({"generated/schema/IHS_2025/VW_DAILY_MOOD.yml": schema_file(
        TableInfo("IHS_2025", "VW_DAILY_MOOD", "VIEW", "", [Column("MOOD", "NUMBER")])
    )}, "Add mood")  # fmt: skip
    clone.sync()
    monkeypatch.setattr(catalog_source, "MAX_FILES", 1)
    assert source.refresh() is True
    assert catalog.names("IHS_2025") == ["VFITBITDAILYDATA"]
    assert "too large" in (source.detail or "")
    assert app_module.catalog_problem(settings, catalog, None, source) == source.detail
    monkeypatch.setattr(catalog_source, "MAX_FILES", 20_000)
    monkeypatch.setattr(catalog_source, "MAX_BYTES", 10)
    fresh = Catalog([])
    too_big = CatalogSource(settings, fresh)
    assert too_big.load() is False
    assert "larger than DataLab reads" in fresh.missing_message()


def test_a_submodule_in_generated_schema_is_left_out(tmp_path: Path) -> None:
    remote = Remote(tmp_path, kb_files())
    head = remote.head()
    git("update-index", "--add", "--cacheinfo", f"160000,{head},generated/schema/IHS_2025/SUB.yml",
        cwd=remote.other)  # fmt: skip
    git("commit", "-q", "-m", "A submodule", cwd=remote.other)
    git("push", "-q", "origin", "HEAD:main", cwd=remote.other)
    settings = real(tmp_path)
    synced_clone(settings, remote)
    catalog = Catalog([])
    source = CatalogSource(settings, catalog)
    assert source.load() is True and catalog.names("IHS_2025") == ["VFITBITDAILYDATA"]
    assert source.skipped == ["IHS_2025/SUB.yml: not a plain file"]


def test_a_catalog_dir_that_is_a_link_is_followed_once(tmp_path: Path) -> None:
    Catalog([fitbit()]).save(tmp_path / "real-folder")
    os.symlink(tmp_path / "real-folder", tmp_path / "linked")
    os.symlink(tmp_path / "real-folder" / "IHS_2025", tmp_path / "real-folder" / "IHS_2024")
    catalog = Catalog([])
    source = CatalogSource(real(tmp_path, catalog_dir=tmp_path / "linked"), catalog)
    assert source.load() is True and catalog.schemas == ["IHS_2025"]
    # A linked schema folder inside it isn't followed, and says so.
    assert source.skipped == ["IHS_2024/: a link, not a folder"]


def test_the_status_says_where_the_catalog_comes_from(tmp_path: Path, lab: Remote) -> None:
    with started(real(tmp_path)) as client:
        assert client.get("/api/catalog/status").json()["source"] == "knowledge"
    datalock.release_all()
    empty = tmp_path / "empty"
    empty.mkdir()
    with started(real(tmp_path, catalog_dir=empty)) as client:
        status = client.get("/api/catalog/status").json()
        assert status["source"] == "setting" and "folder in DataLab's settings" in status["detail"]
        [error] = check(client)["errors"]
        assert "Ask the DataLab maintainer" in error["message"]
        assert "sign in" not in error["message"].lower()
    datalock.release_all()
    with started(real(tmp_path, repos=RepoSettings())) as client:
        assert client.get("/api/catalog/status").json()["source"] == "data folder"


def test_without_git_it_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as shutil_module

    settings = real(tmp_path)
    synced_clone(settings, Remote(tmp_path, kb_files()))
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    assert shutil_module.which("git") is None
    catalog = Catalog([])
    assert CatalogSource(settings, catalog).load() is False
    assert "Git isn't installed" in catalog.missing_message()
