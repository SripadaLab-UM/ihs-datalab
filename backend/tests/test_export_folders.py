"""Export folders: finding sync folders, checking folders, the Test write, and the words.

Every home folder here is a fake one in tmp_path; nothing looks at the
computer's own Dropbox.
"""

import dataclasses
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datalab import export_folders, exports
from datalab.api.workflows import _delivery_fields
from datalab.app import create_app
from datalab.export_folders import Target, check_folder, check_new_folder, describe, sync_roots
from datalab.sessions import picker
from datalab.sessions.inputs import NotAttachable
from datalab.workflows.runner import delivery_message
from tests.conftest import FakeDatabase

# Mac and Linux paths and permissions (/Volumes, chmod, display paths): not Windows.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths and permissions")


@pytest.fixture(params=[True, False], ids=["dir_fd", "by-path"])
def by_fd(request, monkeypatch):
    """Both ways of writing inside a checked folder: relative to the open
    folder (Mac, Linux), and by path with the folder re-checked before each
    step (Windows, which has no dir_fd)."""
    if request.param and not exports._BY_FD:
        pytest.skip("this system can't write relative to an open folder")
    monkeypatch.setattr(exports, "_BY_FD", request.param)
    return request.param


def link_folder(link: Path, target: Path) -> None:
    link.symlink_to(target, target_is_directory=True)


def remove_link(link: Path) -> None:
    try:
        link.unlink()
    except (IsADirectoryError, PermissionError):
        link.rmdir()  # a directory link on Windows


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder, and no system-folder rules (tmp_path is in /private/var)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())
    monkeypatch.setattr("datalab.sessions.inputs._CONTAINERS_POSIX", ())
    return Path(os.path.realpath(home))


def cloud(home: Path, name: str) -> Path:
    folder = home / "Library" / "CloudStorage" / name
    folder.mkdir(parents=True)
    return folder


# Finding sync folders --------------------------------------------------------


@posix_only
def test_sync_folders_are_found_by_name_on_a_mac(home, monkeypatch):
    dropbox = cloud(home, "Dropbox-UniversityofMichigan")
    (dropbox / "study.csv").write_text("never listed")
    cloud(home, "OneDrive-Personal")
    cloud(home, "Box-Box")
    cloud(home, "GoogleDrive-someone@example.org")
    cloud(home, "SomethingElse")
    (home / "Dropbox").symlink_to(dropbox)  # the old place: the same folder, listed once
    (home / "Dropbox (Personal)").mkdir()
    (home / "Documents").mkdir()
    (home / "Dropboxes").mkdir()
    (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs").mkdir(parents=True)

    scanned: list[str] = []
    real_scandir = os.scandir

    def watching(path):
        scanned.append(os.path.realpath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", watching)
    roots = sync_roots(home, "darwin")
    found = {(r.provider, r.label) for r in roots}
    assert found == {
        ("dropbox", "Dropbox (UniversityofMichigan)"),
        ("dropbox", "Dropbox (Personal)"),
        ("onedrive", "OneDrive (Personal)"),
        ("box", "Box"),
        ("google_drive", "Google Drive (someone@example.org)"),
        ("icloud", "iCloud Drive"),
    }
    assert roots[0].provider == "dropbox"  # Dropbox first
    # Only the home folder and CloudStorage were listed, never a sync folder's insides.
    assert set(scanned) == {str(home), str(home / "Library" / "CloudStorage")}
    # Ids are unique, and name where each is under the home folder.
    ids = [r.id for r in roots]
    assert len(set(ids)) == len(ids)
    assert "dropbox:Library/CloudStorage/Dropbox-UniversityofMichigan" in ids


def test_place_ids_dont_collide(home):
    """~/Dropbox and ~/Library/CloudStorage/Dropbox are two different folders here."""
    cloud(home, "Dropbox")
    (home / "Dropbox").mkdir()
    ids = [r.id for r in sync_roots(home, "darwin")]
    assert sorted(ids) == ["dropbox:Dropbox", "dropbox:Library/CloudStorage/Dropbox"]


def test_sync_folders_on_windows_are_in_the_home_folder(home, monkeypatch):
    for name in ("Dropbox", "OneDrive - University of Michigan", "Box", "Pictures"):
        (home / name).mkdir()
    cloud(home, "Dropbox")  # not looked at on Windows
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    roots = sync_roots(home, "win32")
    assert [(r.provider, r.label, r.path) for r in roots] == [
        ("dropbox", "Dropbox", home / "Dropbox"),
        (
            "onedrive",
            "OneDrive (University of Michigan)",
            home / "OneDrive - University of Michigan",
        ),
        ("box", "Box", home / "Box"),
    ]


def test_no_sync_apps_means_no_places(home):
    (home / "Documents").mkdir()
    assert sync_roots(home, "darwin") == []


@posix_only
def test_what_kind_of_folder_it_is(home, tmp_path):
    inside = cloud(home, "Dropbox-Lab") / "IHS" / "exports"
    inside.mkdir(parents=True)
    info = describe(inside)
    assert info.location == "sync_folder" and info.sync_provider == "dropbox"
    assert info.note == (
        "Inside your Dropbox folder: Dropbox will upload it when its app is running and "
        "signed in. DataLab can't confirm the upload."
    )
    assert describe(home / "Dropbox (Team)" / "x").sync_provider == "dropbox"
    assert describe(home / "OneDrive - UM" / "x").sync_provider == "onedrive"
    # A Dropbox folder somewhere else is known by the file its app keeps at the top.
    elsewhere = tmp_path / "Work" / "Lab box"
    (elsewhere / "sub").mkdir(parents=True)
    (elsewhere / ".dropbox").write_text("{}")
    assert describe(elsewhere / "sub").sync_provider == "dropbox"
    plain = home / "Documents" / "exports"
    plain.mkdir(parents=True)
    assert describe(plain).location == "this_computer" and describe(plain).sync_provider is None
    assert describe(Path("/Volumes/USB/exports")).location == "external_drive"


# Checking folders ------------------------------------------------------------


def test_a_ready_folder(home):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    assert check_folder(folder, protected=[]).status == "ready"


@posix_only
def test_a_missing_folder_says_why_in_plain_words(home):
    gone = check_folder(home / "Library" / "CloudStorage" / "Dropbox" / "IHS", protected=[])
    assert gone.status == "missing"
    assert "Dropbox app is installed" in (gone.message or "")
    unplugged = check_folder(Path("/Volumes/No Such Drive/exports"), protected=[])
    assert unplugged.status == "missing" and "drive" in (unplugged.message or "")
    moved = check_folder(home / "Documents" / "gone", protected=[])
    assert moved.status == "missing" and "moved, renamed or deleted" in (moved.message or "")


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions; root writes anywhere"
)
def test_a_folder_that_cant_be_written(home):
    folder = home / "Documents" / "locked"
    folder.mkdir(parents=True)
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        checked = check_folder(folder, protected=[])
        assert checked.status == "not_writable" and "can't save files" in (checked.message or "")
        tried = export_folders.write_test_file(folder, protected=[])
        assert not tried.ok and tried.status == "not_writable"
    finally:
        folder.chmod(stat.S_IRWXU)


def test_online_only_folders(home, monkeypatch):
    class Stat:
        st_flags = 0x40000000  # SF_DATALESS
        st_file_attributes = 0

    assert export_folders._online_only(Stat())  # type: ignore[arg-type]
    Stat.st_flags, Stat.st_file_attributes = 0, 0x00400000  # RECALL_ON_DATA_ACCESS
    assert export_folders._online_only(Stat())  # type: ignore[arg-type]
    # "Always keep on this device" in OneDrive: not online-only.
    Stat.st_file_attributes = 0x00400000 | 0x00080000
    assert not export_folders._online_only(Stat())  # type: ignore[arg-type]
    Stat.st_file_attributes = 0
    assert not export_folders._online_only(Stat())  # type: ignore[arg-type]

    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    monkeypatch.setattr(export_folders, "_online_only", lambda info: True)
    # A warning, not a refusal: saving usually works, and Test settles it.
    checked = check_folder(folder, protected=[])
    assert checked.status == "ready" and checked.message is None
    assert "online-only in Dropbox" in (checked.warning or "")
    assert "Test folder" in (checked.warning or "")
    assert export_folders.write_test_file(folder, protected=[]).ok


@posix_only
def test_datalab_and_system_folders_are_refused(home, tmp_path, monkeypatch):
    data = tmp_path / "datalab-data"
    (data / "exports").mkdir(parents=True)
    checked = check_folder(data / "exports", protected=[data])
    assert checked.status == "refused" and "DataLab's own data folder" in (checked.message or "")
    with pytest.raises(NotAttachable):
        check_new_folder(data, protected=[data])
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", (str(tmp_path / "sys"),))
    (tmp_path / "sys" / "x").mkdir(parents=True)
    system = check_folder(tmp_path / "sys" / "x", protected=[])
    assert system.status == "refused" and "system folder" in (system.message or "")
    file = home / "a.txt"
    file.write_text("x")
    assert check_folder(file, protected=[]).status in ("not_a_folder", "refused")


@posix_only
def test_links_that_leave_the_sync_folder_are_refused(home, tmp_path):
    dropbox = cloud(home, "Dropbox")
    outside = tmp_path / "outside"
    outside.mkdir()
    (dropbox / "Looks like Dropbox").symlink_to(outside)
    with pytest.raises(NotAttachable, match="leads out of your Dropbox folder"):
        check_new_folder(dropbox / "Looks like Dropbox", protected=[])
    # The old ~/Dropbox link to the CloudStorage folder is the same Dropbox: fine.
    (dropbox / "IHS").mkdir()
    (home / "Dropbox").symlink_to(dropbox)
    assert check_new_folder(home / "Dropbox" / "IHS", protected=[]) == dropbox / "IHS"
    # A saved folder later replaced by a link is refused when it's used.
    (dropbox / "IHS").rmdir()
    (dropbox / "IHS").symlink_to(outside)
    replaced = check_folder(dropbox / "IHS", protected=[])
    assert replaced.status == "refused" and "link" in (replaced.message or "")


def test_names(home):
    assert export_folders.check_name("  Lab   Dropbox ") == "Lab Dropbox"
    for bad in ("", "   ", "x" * 81, "evil‮name", "nul\x00"):
        with pytest.raises(ValueError):
            export_folders.check_name(bad)


# The Test write ----------------------------------------------------------------


def test_the_test_writes_a_synthetic_file_and_removes_it(home, monkeypatch):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    (folder / "keep.csv").write_text("the person's own file")
    seen = {}
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        seen["name"] = Path(path).name
        fd = os.open(path, os.O_RDONLY, dir_fd=kwargs.get("dir_fd"))
        with os.fdopen(fd, encoding="utf-8") as reader:
            seen["text"] = reader.read()
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    result = export_folders.write_test_file(folder, protected=[])
    assert result.ok and result.removed and result.status == "ready"
    assert re.fullmatch(r"datalab-test-\d{8}-\d{6}-[0-9a-f]{6}\.txt", result.test_file or "")
    assert seen["name"] == result.test_file
    assert seen["text"].startswith("SYNTHETIC TEST FILE") and "no study data" in seen["text"]
    assert sorted(p.name for p in folder.iterdir()) == [
        "keep.csv"
    ]  # gone again, nothing else touched


def test_the_test_says_when_it_cant_write(home):
    result = export_folders.write_test_file(home / "nowhere", protected=[])
    assert not result.ok and result.status == "missing" and result.test_file is None


# The words ------------------------------------------------------------------------


def test_the_words_never_say_synced():
    assert export_folders.saved_to("Lab Dropbox") == "Saved to Lab Dropbox (on this computer)"
    assert export_folders.sync_note(None) is None
    assert export_folders.sync_note("dropbox", files=3) == (
        "Dropbox will upload them when its app is running and signed in. "
        "DataLab can't confirm the upload."
    )
    plain = Target("dest_1", "Reports", Path("/x"), None)
    assert delivery_message(plain, 1) == "Saved to Reports (on this computer): 1 file."
    dropbox = Target("dest_2", "Lab Dropbox", Path("/x"), "dropbox")
    message = delivery_message(dropbox, 2)
    assert message.startswith(
        "Saved to Lab Dropbox (on this computer): 2 files. Dropbox will upload them"
    )
    for text in (
        message,
        *(export_folders.sync_note(p) or "" for p in export_folders.PROVIDER_NAMES),
    ):
        assert "synced" not in text.lower() and "uploaded" not in text.lower()


def test_delivery_records_say_where_they_were_saved():
    row = {
        "id": "dl_1",
        "destination_key": "lab-dropbox",
        "destination_path": "/x",
        "files": [{"path": "a.csv"}, {"path": "b.csv"}],
        "destination_name": "Lab Dropbox",
        "sync_provider": "dropbox",
    }
    out = _delivery_fields(row)
    assert out["saved_to"] == "Saved to Lab Dropbox (on this computer)"
    assert out["sync_note"].startswith("Dropbox will upload them")
    # A delivery from before names were recorded falls back to its key.
    old = _delivery_fields({**row, "destination_name": None, "sync_provider": None})
    assert old["saved_to"] == "Saved to lab-dropbox (on this computer)" and old["sync_note"] is None


# Over the API ---------------------------------------------------------------------


def make_app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


@pytest.fixture
def real_settings(settings):
    return dataclasses.replace(settings, profile="real")


def no_sync_words(response) -> None:
    text = json.dumps(response.json()).lower()
    assert "synced" not in text and "uploaded" not in text


@posix_only
def test_adding_naming_testing_and_removing_a_dropbox_folder(
    real_settings, catalog, home, monkeypatch
):
    dropbox = cloud(home, "Dropbox-UniversityofMichigan")
    folder = dropbox / "IHS exports"
    folder.mkdir()
    (folder / "earlier.csv").write_text("an earlier export")
    asked = {}

    async def chosen(kind, *, start_in=None):
        asked["start_in"] = start_in
        return [folder]

    monkeypatch.setattr(picker, "pick", chosen)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        places = client.get("/api/export-destinations/places").json()
        assert places["can_add"] is True
        [place] = places["places"]
        assert place["provider"] == "dropbox" and place["label"] == "Dropbox (UniversityofMichigan)"
        assert place["where"] == "~/Library/CloudStorage/Dropbox-UniversityofMichigan"

        added = client.post(
            "/api/export-destinations", json={"name": "Lab Dropbox", "start_in": place["id"]}
        )
        assert added.status_code == 201, added.text
        assert asked["start_in"] == dropbox  # the picker opened in Dropbox; the person chose
        info = added.json()
        assert info["name"] == "Lab Dropbox" and info["path"] == str(folder)
        assert info["status"] == "ready" and info["available"] is True
        assert info["location"] == "sync_folder" and info["sync_provider"] == "dropbox"
        assert info["location_note"].startswith(
            "Inside your Dropbox folder: Dropbox will upload it"
        )
        no_sync_words(added)
        did = info["id"]

        tested = client.post(f"/api/export-destinations/{did}/test")
        assert tested.status_code == 200
        body = tested.json()
        assert body["ok"] and body["removed"] and body["test_file"].startswith("datalab-test-")
        assert body["note"] is None
        assert body["saved_to"] == "Saved to Lab Dropbox (on this computer)"
        assert body["sync_note"].startswith("Dropbox will upload it when its app is running")
        no_sync_words(tested)
        assert [p.name for p in folder.iterdir()] == ["earlier.csv"]

        # Rename; names are unique, and checked.
        renamed = client.patch(f"/api/export-destinations/{did}", json={"name": "IHS Dropbox"})
        assert renamed.json()["name"] == "IHS Dropbox"
        other = home / "Documents" / "Reports"
        other.mkdir(parents=True)
        monkeypatch.setattr(picker, "pick", lambda kind, **_: _async([other]))
        second = client.post("/api/export-destinations", json={}).json()
        assert second["name"] == "Reports" and second["location"] == "this_computer"
        assert second["location_note"] == "A folder on this computer."
        clash = client.patch(
            f"/api/export-destinations/{second['id']}", json={"name": "ihs dropbox"}
        )
        assert clash.status_code == 409
        blank = client.patch(f"/api/export-destinations/{did}", json={"name": "  "})
        assert blank.status_code == 422

        # An export says where it was saved, and who will upload it.
        cid = prepare(app, client)
        exported = client.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": did, "files": [{"root": "outputs", "path": "t.csv"}],
                  "checkpoint": shown(client, cid)},
        )  # fmt: skip
        assert exported.status_code == 201, exported.text
        out = exported.json()
        assert out["saved_to"] == "Saved to IHS Dropbox (on this computer)"
        assert out["destination_name"] == "IHS Dropbox" and out["sync_provider"] == "dropbox"
        assert out["sync_note"].startswith("Dropbox will upload it")
        no_sync_words(exported)
        plain = client.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": second["id"], "files": [{"root": "outputs", "path": "t.csv"}],
                  "checkpoint": shown(client, cid)},
        ).json()  # fmt: skip
        assert (
            plain["saved_to"] == "Saved to Reports (on this computer)"
            and plain["sync_note"] is None
        )

        # Turned off: kept, not offered, and nothing is written there.
        off = client.patch(f"/api/export-destinations/{did}", json={"offered": False}).json()
        assert off["offered"] is False and off["available"] is False and off["status"] == "ready"
        refused = client.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": did, "files": [{"root": "outputs", "path": "t.csv"}],
                  "checkpoint": shown(client, cid)},
        )  # fmt: skip
        assert refused.status_code == 422 and "turned off" in refused.json()["detail"]
        # Test still works, and says it's switched off.
        tested_off = client.post(f"/api/export-destinations/{did}/test").json()
        assert tested_off["ok"] and "switched off" in tested_off["note"]
        client.patch(f"/api/export-destinations/{did}", json={"offered": True})

        # Removing forgets the folder; it and everything in it stay.
        before = sorted(p.name for p in folder.iterdir())
        assert client.delete(f"/api/export-destinations/{did}").status_code == 204
        assert folder.is_dir() and sorted(p.name for p in folder.iterdir()) == before
        assert (folder / "earlier.csv").read_text() == "an earlier export"
        assert [d["id"] for d in client.get("/api/export-destinations").json()] == [second["id"]]
        assert client.post(f"/api/export-destinations/{did}/test").status_code == 404


@posix_only
def test_unavailable_folders_are_listed_with_why(real_settings, catalog, home, monkeypatch):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    monkeypatch.setattr(picker, "pick", lambda kind, **_: _async([folder]))
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        did = client.post("/api/export-destinations", json={"name": "Lab"}).json()["id"]
        folder.rmdir()
        [row] = client.get("/api/export-destinations").json()
        assert row["status"] == "missing" and row["available"] is False
        assert "Dropbox app is installed" in row["status_message"]
        tested = client.post(f"/api/export-destinations/{did}/test").json()
        assert (
            tested["ok"] is False and tested["status"] == "missing" and tested["saved_to"] is None
        )


@posix_only
def test_datalab_folders_and_escaping_links_cant_be_added(
    real_settings, catalog, home, monkeypatch, tmp_path
):
    real_settings.data_dir.mkdir(parents=True, exist_ok=True)
    inside = real_settings.data_dir / "exports"
    inside.mkdir()
    dropbox = cloud(home, "Dropbox")
    outside = tmp_path / "outside"
    outside.mkdir()
    (dropbox / "sneaky").symlink_to(outside)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        for chosen in (inside, dropbox / "sneaky"):
            monkeypatch.setattr(picker, "pick", lambda kind, c=chosen, **_: _async([c]))
            assert client.post("/api/export-destinations").status_code == 422
        assert (
            client.post("/api/export-destinations", json={"start_in": "dropbox:Nope"}).status_code
            == 404
        )


@posix_only
def test_practice_keeps_to_its_own_folder(settings, catalog, home, monkeypatch):
    cloud(home, "Dropbox")

    async def never(kind, **_):
        raise AssertionError("practice never opens the picker for an export folder")

    monkeypatch.setattr(picker, "pick", never)
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        places = client.get("/api/export-destinations/places").json()
        assert places == {
            "can_add": False,
            "why_not": places["why_not"],
            "places": [],  # practice doesn't even look
        }
        assert places["why_not"].startswith("Available on the real DataLab.")
        assert client.post("/api/export-destinations", json={"name": "x"}).status_code == 403
        assert (
            client.patch("/api/export-destinations/practice", json={"name": "x"}).status_code == 404
        )
        assert client.delete("/api/export-destinations/practice").status_code == 404
        [only] = client.get("/api/export-destinations").json()
        assert only["id"] == "practice" and only["practice"] is True and only["available"]
        tested = client.post("/api/export-destinations/practice/test").json()
        assert tested["ok"] and tested["saved_to"] == "Saved to Practice exports (on this computer)"
        assert tested["sync_note"] is None
        assert list((settings.data_dir / "practice-exports").iterdir()) == []
        cid = prepare(app, client)
        out = client.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": "practice", "files": [{"root": "outputs", "path": "t.csv"}],
                  "checkpoint": shown(client, cid)},
        ).json()  # fmt: skip
        assert out["saved_to"] == "Saved to Practice exports (on this computer)"
        assert out["sync_note"] is None


async def _async(value):
    return value


def prepare(app, client) -> str:
    cid = client.post("/api/conversations", json={"title": "Sleep study"}).json()["id"]
    work = app.state.services.sessions.paths(cid).work
    (work / "outputs").mkdir(parents=True)
    (work / "outputs" / "t.csv").write_text("synthetic,1\n")
    app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)
    return cid


def shown(client, cid: str) -> int:
    return client.get(f"/api/conversations/{cid}/files").json()[0]["checkpoint"]


# Folders swapped after they were checked ---------------------------------------


def swap_for_link(folder: Path, elsewhere: Path) -> Path:
    """Move `folder` aside and put a link to `elsewhere` in its place."""
    aside = folder.with_name(folder.name + " (moved)")
    folder.rename(aside)
    link_folder(folder, elsewhere)
    return aside


def test_an_export_to_a_folder_swapped_after_its_check_is_refused(by_fd, home, tmp_path):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    fake_data = tmp_path / "fake-datalab-data"
    fake_data.mkdir()
    checked = check_folder(folder, protected=[fake_data])
    target = Target("dest_1", "Lab", folder, "dropbox", checked.identity)
    swap_for_link(folder, fake_data)
    with pytest.raises(exports.ExportError, match="changed after DataLab checked it"):
        export_folders.open_target(target)
    # Swapped for another real folder: refused too.
    remove_link(folder)
    folder.mkdir()
    with pytest.raises(exports.ExportError, match="changed after DataLab checked it"):
        export_folders.open_target(target)
    assert list(fake_data.iterdir()) == []


def test_a_swap_during_an_export_doesnt_redirect_it(by_fd, home, tmp_path):
    """Once open, everything goes into the folder that was checked, wherever it moves."""
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    fake_data = tmp_path / "fake-datalab-data"
    fake_data.mkdir()
    checked = check_folder(folder, protected=[fake_data])
    target = Target("dest_1", "Lab", folder, "dropbox", checked.identity)
    with export_folders.open_target(target) as opened:
        aside = swap_for_link(folder, fake_data)
        if not by_fd:
            # By path, the folder is checked again before each step: refused.
            with pytest.raises(exports.ExportError, match="changed after DataLab checked it"):
                exports.export(opened, title="t", sources=[], about={})
            assert list(fake_data.iterdir()) == []
            return
        result = exports.export(
            opened, title="t", sources=[], extra_files={"a/b.txt": b"synthetic"}, about={}
        )
    assert list(fake_data.iterdir()) == []
    [made] = list(aside.iterdir())
    assert (made / "a" / "b.txt").read_bytes() == b"synthetic"
    assert (made / exports.MANIFEST).is_file() and result.manifest_sha256


def test_the_test_write_never_follows_a_swapped_folder(by_fd, home, tmp_path, monkeypatch):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    fake_data = tmp_path / "fake-datalab-data"
    fake_data.mkdir()
    real_check = export_folders.check_folder

    def check_then_swap(path, **kwargs):
        checked = real_check(path, **kwargs)
        swap_for_link(folder, fake_data)
        return checked

    monkeypatch.setattr(export_folders, "check_folder", check_then_swap)
    result = export_folders.write_test_file(folder, protected=[fake_data])
    assert not result.ok and result.status == "refused"
    assert list(fake_data.iterdir()) == []


def test_the_test_leaves_a_file_that_isnt_its_own(by_fd, home, monkeypatch):
    """Removed only if it's still the file it wrote."""
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    real_lstat = exports.Folder.lstat

    def replaced(self, name):
        (folder / name).unlink()
        (folder / name).write_text("the person's own file")
        return real_lstat(self, name)

    monkeypatch.setattr(exports.Folder, "lstat", replaced)
    result = export_folders.write_test_file(folder, protected=[])
    assert result.ok and not result.removed
    [left] = list(folder.iterdir())
    assert left.read_text() == "the person's own file"


def test_a_delivery_subfolder_that_is_a_link_is_refused(by_fd, home, tmp_path):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link_folder(folder / "weekly", elsewhere)
    with exports.Folder.at(folder) as root, pytest.raises(exports.ExportError):
        root.child("weekly")
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows'")
@pytest.mark.parametrize("made_by", ["_winapi", "mklink"])
def test_a_junction_is_never_followed(home, tmp_path, made_by):
    """lstat reports a junction as a plain folder: it's refused all the same."""
    import subprocess

    folder = home / "Dropbox" / "IHS"
    folder.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    junction = folder / "weekly"
    if made_by == "_winapi":
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(elsewhere), str(junction))
    else:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(elsewhere)], check=True)
    assert exports.is_link(junction, os.lstat(junction))
    with exports.Folder.at(folder) as root, pytest.raises(exports.ExportError):
        root.child("weekly")
    with pytest.raises(exports.ExportError):
        exports.Folder.at(junction)
    assert check_folder(junction, protected=[]).status == "refused"
    # A delivery's subfolder that's a junction: nothing is written through it.
    target = Target("d", "Lab", folder, "dropbox", check_folder(folder, protected=[]).identity)
    with (
        export_folders.open_target(target) as root,
        pytest.raises(exports.ExportError),
        root.child("weekly") as sub,
    ):
        exports.export(sub, title="t", sources=[], about={})
    assert list(elsewhere.iterdir()) == []


def test_datalab_data_folder_spelt_decomposed_is_still_refused(home, tmp_path):
    data = tmp_path / "Caf\u00e9 data"  # composed é
    (data / "exports").mkdir(parents=True)
    decomposed = tmp_path / "Cafe\u0301 data" / "exports"
    if not decomposed.exists():  # a disk that tells them apart (Linux)
        decomposed.mkdir(parents=True)
    with pytest.raises(NotAttachable, match="DataLab's own data folder"):
        check_new_folder(decomposed, protected=[data])
    assert check_folder(decomposed, protected=[data]).status == "refused"


def test_the_same_folder_by_another_name_is_refused(home, tmp_path):
    """Compared by what's on disk: a hard-to-spot second path to the data folder."""
    data = tmp_path / "datalab-data"
    (data / "exports").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    link = other / "looks-harmless"
    link_folder(link, data)
    with pytest.raises(NotAttachable):
        check_new_folder(link / "exports", protected=[data])


def test_usable_means_switched_on_and_ready(settings, home):
    folder = cloud(home, "Dropbox") / "IHS"
    folder.mkdir()
    on = exports.Destination("d1", "Lab", str(folder), "then")
    assert export_folders.usable(settings, on)
    assert not export_folders.usable(settings, dataclasses.replace(on, offered=False))
    gone = dataclasses.replace(on, path=str(home / "gone"))
    assert not export_folders.usable(settings, gone)
    inside = settings.data_dir / "exports"
    inside.mkdir(parents=True)
    assert not export_folders.usable(settings, dataclasses.replace(on, path=str(inside)))


def test_junction_like_folders_are_links_everywhere(home, monkeypatch):
    """What Windows reports for a junction (a reparse tag, isjunction) counts as
    a link in the by-path code, checked here on any system."""
    monkeypatch.setattr(exports, "_BY_FD", False)
    folder = home / "Dropbox" / "IHS"
    (folder / "weekly").mkdir(parents=True)

    class Tagged:
        st_mode = stat.S_IFDIR
        st_reparse_tag = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT: a junction

    assert exports.is_link(folder, Tagged())  # type: ignore[arg-type]
    monkeypatch.setattr(os.path, "isjunction", lambda p: Path(p).name == "weekly", raising=False)
    assert exports.is_link(folder / "weekly", os.lstat(folder / "weekly"))
    with exports.Folder.at(folder) as root, pytest.raises(exports.ExportError):
        root.child("weekly")
    with pytest.raises(exports.ExportError):
        exports.Folder.at(folder / "weekly")
