"""The launcher's icon follows the package's after an update (launcher_icons.py).

Every bundle and icons folder here is a fake one in tmp_path; lsregister is a
recording stand-in.
"""

from __future__ import annotations

import os
import plistlib
import stat
import sys
from pathlib import Path

import pytest

from datalab import exports, launcher_icons
from datalab.launcher_icons import refresh

# The Mac's checks: links, owners and read-only folders as a Mac has them.
mac = pytest.mark.skipif(sys.platform == "win32", reason="the Mac's app bundles")

NEW_ICNS, OLD_ICNS = b"new real icns", b"old d. icns"
NEW_PRACTICE, OLD_PRACTICE = b"new practice icns", b"old practice icns"


class Recorder:
    def __init__(self, fail: BaseException | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, command, **_options):
        self.calls.append(list(command))
        if self.fail is not None:
            raise self.fail


@pytest.fixture
def root(tmp_path) -> Path:
    # A space and a quote, as a real path can have ("Application Support", o'brien).
    return tmp_path / "o'brien" / "Application Support" / "DataLab" / "app"


@pytest.fixture
def branding(tmp_path) -> Path:
    folder = tmp_path / "package" / "datalab" / "branding"
    folder.mkdir(parents=True)
    (folder / "DataLab.icns").write_bytes(NEW_ICNS)
    (folder / "DataLab-practice.icns").write_bytes(NEW_PRACTICE)
    (folder / "DataLab.ico").write_bytes(b"new real ico")
    (folder / "DataLab-practice.ico").write_bytes(b"new practice ico")
    return folder


def make_bundle(
    folder: Path,
    root: Path,
    *,
    name: str = "DataLab",
    bundle_id: str = "edu.umich.ihs.datalab",
    icon: bytes = OLD_ICNS,
) -> Path:
    """An app bundle as installer/macos/install.sh makes it."""
    bundle = folder / f"{name}.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir()
    (bundle / "Contents" / "Resources" / "DataLab.icns").write_bytes(icon)
    info = {"CFBundleIdentifier": bundle_id, "CFBundleIconFile": "DataLab"}
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    quoted = str(root / "bin" / "datalab").replace("'", "'\\''")
    (bundle / "Contents" / "MacOS" / "DataLab").write_text(
        f"#!/bin/sh\nexec osascript - '{quoted}' <<'OSA'\nOSA\n", encoding="utf-8"
    )
    return bundle


def icon_of(bundle: Path) -> bytes:
    return (bundle / "Contents" / "Resources" / "DataLab.icns").read_bytes()


@mac
def test_our_bundles_get_the_packages_icon_and_launchservices_is_told(
    tmp_path, root, branding, monkeypatch
):
    system, user = tmp_path / "Applications", tmp_path / "home" / "Applications"
    real = make_bundle(system, root)
    practice = make_bundle(
        user,
        root,
        name="DataLab (practice)",
        bundle_id="edu.umich.ihs.datalab.practice",
        icon=OLD_PRACTICE,
    )
    os.utime(real, (0, 0))
    monkeypatch.setattr(launcher_icons, "LSREGISTER", str(tmp_path / "lsregister"))
    (tmp_path / "lsregister").write_text("")
    run = Recorder()

    replaced = refresh(branding, root=root, platform="darwin", app_folders=[system, user], run=run)

    assert icon_of(real) == NEW_ICNS and icon_of(practice) == NEW_PRACTICE
    assert sorted(replaced) == sorted(
        [real / "Contents/Resources/DataLab.icns", practice / "Contents/Resources/DataLab.icns"]
    )
    assert real.stat().st_mtime > 0  # touched, so Finder looks again
    assert run.calls == [
        [str(tmp_path / "lsregister"), "-f", str(real)],
        [str(tmp_path / "lsregister"), "-f", str(practice)],
    ]
    # No temporary file left behind.
    assert sorted(p.name for p in (real / "Contents" / "Resources").iterdir()) == ["DataLab.icns"]


@mac
def test_identical_icons_are_left_as_they_are(tmp_path, root, branding):
    bundle = make_bundle(tmp_path, root, icon=NEW_ICNS)
    before = (bundle / "Contents" / "Resources" / "DataLab.icns").stat().st_mtime_ns
    run = Recorder()
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path], run=run) == []
    assert (bundle / "Contents" / "Resources" / "DataLab.icns").stat().st_mtime_ns == before
    assert run.calls == []


@mac
@pytest.mark.parametrize(
    "why",
    [
        "another bundle id",
        "another install's launcher",
        "the path only in a comment",
        "a linked launch script",
        "a linked Info.plist",
        "no Info.plist",
        "a real/practice mix",
    ],
)
def test_a_bundle_that_isnt_this_datalabs_is_left_alone(tmp_path, root, branding, why):
    if why == "another bundle id":
        bundle = make_bundle(tmp_path, root, bundle_id="com.example.datalab")
    elif why == "another install's launcher":
        bundle = make_bundle(tmp_path, tmp_path / "elsewhere")
    elif why == "the path only in a comment":
        bundle = make_bundle(tmp_path, tmp_path / "elsewhere")
        script = bundle / "Contents" / "MacOS" / "DataLab"
        quoted = str(root / "bin" / "datalab").replace("'", "'\\''")
        script.write_text(script.read_text() + f"# exec osascript - '{quoted}' <<'OSA' (not)\n")
    elif why == "a linked launch script":
        bundle = make_bundle(tmp_path, root)
        script = bundle / "Contents" / "MacOS" / "DataLab"
        real = tmp_path / "script-elsewhere"
        script.rename(real)
        script.symlink_to(real)
    elif why == "a linked Info.plist":
        bundle = make_bundle(tmp_path, root)
        info = bundle / "Contents" / "Info.plist"
        info.rename(tmp_path / "Info.plist")
        info.symlink_to(tmp_path / "Info.plist")
    elif why == "no Info.plist":
        bundle = make_bundle(tmp_path, root)
        (bundle / "Contents" / "Info.plist").unlink()
    else:  # "DataLab.app" with practice's id
        bundle = make_bundle(tmp_path, root, bundle_id="edu.umich.ihs.datalab.practice")
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    assert icon_of(bundle) == OLD_ICNS


@mac
def test_a_linked_bundle_is_left_alone(tmp_path, root, branding):
    elsewhere = make_bundle(tmp_path / "elsewhere", root)
    apps = tmp_path / "Applications"
    apps.mkdir()
    (apps / "DataLab.app").symlink_to(elsewhere)
    assert refresh(branding, root=root, platform="darwin", app_folders=[apps]) == []
    assert icon_of(elsewhere) == OLD_ICNS


@mac
def test_a_linked_icon_is_left_alone(tmp_path, root, branding):
    bundle = make_bundle(tmp_path, root)
    target = tmp_path / "someone-elses.icns"
    target.write_bytes(OLD_ICNS)
    icon = bundle / "Contents" / "Resources" / "DataLab.icns"
    icon.unlink()
    icon.symlink_to(target)
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    assert target.read_bytes() == OLD_ICNS


@mac
def test_a_bundle_it_cant_write_is_skipped_without_sudo(tmp_path, root, branding, caplog):
    bundle = make_bundle(tmp_path, root)
    resources = bundle / "Contents" / "Resources"
    resources.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        run = Recorder()
        caplog.set_level("INFO", logger="datalab.launcher_icons")
        assert (
            refresh(branding, root=root, platform="darwin", app_folders=[tmp_path], run=run) == []
        )
        assert icon_of(bundle) == OLD_ICNS and run.calls == []
        assert "not writable" in caplog.text
    finally:
        resources.chmod(stat.S_IRWXU)


@mac
def test_a_planted_temporary_file_never_redirects_the_write(tmp_path, root, branding, monkeypatch):
    # Someone who can write the bundle's folder plants a link where the new
    # icon would be made: it's never followed (O_EXCL, O_NOFOLLOW).
    bundle = make_bundle(tmp_path / "Applications", root)
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"not DataLab's")
    monkeypatch.setattr(launcher_icons.secrets, "token_hex", lambda n: "planted")
    planted = bundle / "Contents" / "Resources" / ".DataLab.icns.planted.new"
    planted.symlink_to(victim)
    assert (
        refresh(branding, root=root, platform="darwin", app_folders=[tmp_path / "Applications"])
        == []
    )
    assert victim.read_bytes() == b"not DataLab's"
    assert icon_of(bundle) == OLD_ICNS
    assert planted.is_symlink()  # not ours to remove


@mac
def test_resources_swapped_for_a_link_after_the_check_never_redirects_the_write(
    tmp_path, root, branding, monkeypatch
):
    bundle = make_bundle(tmp_path / "Applications", root)
    resources = bundle / "Contents" / "Resources"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "DataLab.icns").write_bytes(b"someone else's")
    access = os.access

    def swap(path, mode):  # between the checks and the write
        resources.rename(tmp_path / "moved")
        resources.symlink_to(elsewhere)
        return access(tmp_path / "moved", mode)

    monkeypatch.setattr(launcher_icons.os, "access", swap)
    refresh(branding, root=root, platform="darwin", app_folders=[tmp_path / "Applications"])
    # The write went to the folder that was checked (wherever it is now).
    assert (elsewhere / "DataLab.icns").read_bytes() == b"someone else's"
    assert (tmp_path / "moved" / "DataLab.icns").read_bytes() == NEW_ICNS


@mac
@pytest.mark.parametrize("folder", ["", "Contents", "Contents/Resources"])
def test_a_bundle_owned_by_someone_else_is_left_alone(
    tmp_path, root, branding, monkeypatch, folder
):
    bundle = make_bundle(tmp_path, root)
    theirs = (bundle / folder).stat().st_ino
    real_fstat, real_lstat = os.fstat, os.lstat

    def owned_by_another(info):
        if info.st_ino != theirs:
            return info
        values = list(info)
        values[stat.ST_UID] = info.st_uid + 1
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", lambda fd: owned_by_another(real_fstat(fd)))
    monkeypatch.setattr(os, "lstat", lambda p, **kw: owned_by_another(real_lstat(p, **kw)))
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    assert icon_of(bundle) == OLD_ICNS


@mac
def test_someone_elses_uid_means_no_bundle_is_touched(tmp_path, root, branding, monkeypatch):
    bundle = make_bundle(tmp_path, root)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(bundle).st_uid + 1)
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    assert icon_of(bundle) == OLD_ICNS


@mac
def test_a_leftover_temporary_file_or_a_missing_icon_never_blocks_the_rest(
    tmp_path, root, branding
):
    real = make_bundle(tmp_path, root)
    practice = make_bundle(
        tmp_path, root, name="DataLab (practice)", bundle_id="edu.umich.ihs.datalab.practice"
    )
    (real / "Contents" / "Resources" / ".DataLab.icns.0123456789abcdef.new").write_bytes(b"crash")
    (branding / "DataLab-practice.icns").unlink()  # practice's can't be read
    replaced = refresh(branding, root=root, platform="darwin", app_folders=[tmp_path])
    assert replaced == [real / "Contents" / "Resources" / "DataLab.icns"]
    assert icon_of(real) == NEW_ICNS and icon_of(practice) == OLD_ICNS


@mac
def test_nothing_escapes(tmp_path, root, branding, monkeypatch):
    bundle = make_bundle(tmp_path, root)
    monkeypatch.setattr(launcher_icons, "LSREGISTER", str(tmp_path / "lsregister"))
    (tmp_path / "lsregister").write_text("")
    # lsregister failing, or timing out, is only a note.
    assert refresh(
        branding, root=root, platform="darwin", app_folders=[tmp_path], run=Recorder(OSError("no"))
    ) == [bundle / "Contents" / "Resources" / "DataLab.icns"]

    # Anything unexpected is logged, never raised.
    def broken(*_args, **_kwargs):
        raise RuntimeError("surprise")

    monkeypatch.setattr(launcher_icons, "_refresh_mac", broken)
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    # No package icons, or none at all: nothing to do.
    assert refresh(tmp_path / "missing", root=root, platform="darwin") == []
    assert refresh(None, root=root, platform="darwin") == []


def test_windows_icons_beside_bin_are_replaced_when_they_differ(root, branding):
    icons = root / "icons"
    icons.mkdir(parents=True)
    (icons / "DataLab.ico").write_bytes(b"old real ico")
    (icons / "DataLab-practice.ico").write_bytes(b"new practice ico")  # already the same

    replaced = refresh(branding, root=root, platform="win32")

    assert replaced == [icons / "DataLab.ico"]
    assert (icons / "DataLab.ico").read_bytes() == b"new real ico"
    assert (icons / "DataLab-practice.ico").read_bytes() == b"new practice ico"
    assert sorted(p.name for p in icons.iterdir()) == ["DataLab-practice.ico", "DataLab.ico"]
    # Only the icons the installer put there (the shortcuts point at them).
    (icons / "DataLab-practice.ico").unlink()
    assert refresh(branding, root=root, platform="win32") == []
    assert not (icons / "DataLab-practice.ico").exists()


@mac  # (making a symlink on Windows needs Developer Mode)
def test_a_linked_windows_icon_or_icons_folder_is_left_alone(tmp_path, root, branding):
    icons = root / "icons"
    icons.mkdir(parents=True)
    victim = tmp_path / "victim.ico"
    victim.write_bytes(b"not DataLab's")
    (icons / "DataLab.ico").symlink_to(victim)
    assert refresh(branding, root=root, platform="win32") == []
    assert victim.read_bytes() == b"not DataLab's"
    (icons / "DataLab.ico").unlink()
    icons.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "DataLab.ico").write_bytes(b"not DataLab's")
    icons.symlink_to(elsewhere)
    assert refresh(branding, root=root, platform="win32") == []
    assert (elsewhere / "DataLab.ico").read_bytes() == b"not DataLab's"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows's")
def test_a_junction_for_the_icons_folder_is_never_followed(tmp_path, root, branding):
    import _winapi  # pyright: ignore[reportMissingImports]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "DataLab.ico").write_bytes(b"not DataLab's")
    root.mkdir(parents=True)
    _winapi.CreateJunction(str(elsewhere), str(root / "icons"))  # pyright: ignore
    assert refresh(branding, root=root, platform="win32") == []
    assert (elsewhere / "DataLab.ico").read_bytes() == b"not DataLab's"


def test_the_icons_folder_swapped_after_the_check_is_never_written(
    tmp_path, root, branding, monkeypatch
):
    # Windows has no dir_fd: the folder's identity is checked again before
    # each step. (Elsewhere, the same path is run by turning dir_fd off.)
    monkeypatch.setattr(exports, "_BY_FD", False)
    icons = root / "icons"
    icons.mkdir(parents=True)
    (icons / "DataLab.ico").write_bytes(b"old real ico")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "DataLab.ico").write_bytes(b"not DataLab's")
    read = launcher_icons._read

    def swap(folder, name, **kw):  # after the checks, before the write
        data = read(folder, name, **kw)
        icons.rename(tmp_path / "moved")
        elsewhere.rename(icons)
        return data

    monkeypatch.setattr(launcher_icons, "_read", swap)
    assert refresh(branding, root=root, platform="win32") == []
    assert (icons / "DataLab.ico").read_bytes() == b"not DataLab's"
    assert sorted(p.name for p in icons.iterdir()) == ["DataLab.ico"]


def test_windows_without_an_icons_folder_is_a_no_op(root, branding):
    assert refresh(branding, root=root, platform="win32") == []
    assert not (root / "icons").exists()


def test_other_platforms_do_nothing(tmp_path, root, branding):
    bundle = make_bundle(tmp_path, root)
    assert refresh(branding, root=root, platform="linux", app_folders=[tmp_path]) == []
    assert icon_of(bundle) == OLD_ICNS


def test_the_package_has_the_icons_it_copies():
    branding = launcher_icons.package_branding()
    for _, _, icon in launcher_icons.MAC_LAUNCHERS:
        assert (branding / icon).is_file()
    for icon in launcher_icons.WINDOWS_ICONS:
        assert (branding / icon).is_file()


def test_the_tests_never_reach_the_real_applications_folders():
    assert launcher_icons.default_app_folders() == []


def test_an_installed_datalab_refreshes_its_launcher_when_it_starts(monkeypatch, tmp_path):
    from datalab import cli, updater

    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(updater, "default_root", lambda: tmp_path)
    monkeypatch.setattr(
        launcher_icons, "refresh", lambda branding, *, root: calls.append((branding, root))
    )
    # Run from the installer's layout: its own package's icons.
    monkeypatch.setattr(updater.Layout, "running_version", lambda self: "0.2.0b5")
    cli._refresh_launcher_icons()
    assert calls == [(launcher_icons.package_branding(), tmp_path)]
    # A checkout, or tests: launchers are left alone.
    monkeypatch.setattr(updater.Layout, "running_version", lambda self: None)
    cli._refresh_launcher_icons()
    assert len(calls) == 1

    # And nothing stops the start.
    def broken(self):
        raise RuntimeError("surprise")

    monkeypatch.setattr(updater.Layout, "running_version", broken)
    cli._refresh_launcher_icons()
