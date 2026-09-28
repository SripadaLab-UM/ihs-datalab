"""The launcher's icon follows the package's after an update (launcher_icons.py).

Every bundle and icons folder here is a fake one in tmp_path; lsregister is a
recording stand-in.
"""

from __future__ import annotations

import os
import plistlib
import stat
from pathlib import Path

import pytest

from datalab import launcher_icons
from datalab.launcher_icons import refresh

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


def test_identical_icons_are_left_as_they_are(tmp_path, root, branding):
    bundle = make_bundle(tmp_path, root, icon=NEW_ICNS)
    before = (bundle / "Contents" / "Resources" / "DataLab.icns").stat().st_mtime_ns
    run = Recorder()
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path], run=run) == []
    assert (bundle / "Contents" / "Resources" / "DataLab.icns").stat().st_mtime_ns == before
    assert run.calls == []


@pytest.mark.parametrize(
    "why",
    ["another bundle id", "another install's launcher", "no Info.plist", "a real/practice mix"],
)
def test_a_bundle_that_isnt_this_datalabs_is_left_alone(tmp_path, root, branding, why):
    if why == "another bundle id":
        bundle = make_bundle(tmp_path, root, bundle_id="com.example.datalab")
    elif why == "another install's launcher":
        bundle = make_bundle(tmp_path, tmp_path / "elsewhere")
    elif why == "no Info.plist":
        bundle = make_bundle(tmp_path, root)
        (bundle / "Contents" / "Info.plist").unlink()
    else:  # "DataLab.app" with practice's id
        bundle = make_bundle(tmp_path, root, bundle_id="edu.umich.ihs.datalab.practice")
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    assert icon_of(bundle) == OLD_ICNS


def test_a_linked_bundle_is_left_alone(tmp_path, root, branding):
    elsewhere = make_bundle(tmp_path / "elsewhere", root)
    apps = tmp_path / "Applications"
    apps.mkdir()
    (apps / "DataLab.app").symlink_to(elsewhere)
    assert refresh(branding, root=root, platform="darwin", app_folders=[apps]) == []
    assert icon_of(elsewhere) == OLD_ICNS


def test_a_linked_icon_is_left_alone(tmp_path, root, branding):
    bundle = make_bundle(tmp_path, root)
    target = tmp_path / "someone-elses.icns"
    target.write_bytes(OLD_ICNS)
    icon = bundle / "Contents" / "Resources" / "DataLab.icns"
    icon.unlink()
    icon.symlink_to(target)
    assert refresh(branding, root=root, platform="darwin", app_folders=[tmp_path]) == []
    assert target.read_bytes() == OLD_ICNS


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
