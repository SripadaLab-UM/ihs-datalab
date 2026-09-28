"""Keeping the launcher's icon the package's own, after an update.

The installer makes the launcher once: on macOS an app bundle
(`/Applications` or `~/Applications`, "DataLab.app" and "DataLab
(practice).app") with its own copy of the icon, on Windows Start menu and
Desktop shortcuts that point at `<install root>\\icons\\*.ico`. An update only
switches `current`, so a new icon in a new version never reached them. This
copies the running (or newly installed) version's icons over the launcher's,
when they differ.

Only DataLab's own launchers are touched: on macOS a bundle whose folders
are no links and this person's own, whose CFBundleIdentifier is DataLab's,
and whose launch script has the very line the installer writes to run this
install's `bin/datalab`; on Windows only the icon files the installer put in
`<install root>\\icons` (no links, junctions included). Every read and
write goes through an open folder (exports.Folder: dir_fd and O_NOFOLLOW;
on Windows the folder's identity checked again before each step), and a
new icon is a file of a random name made with O_EXCL, then renamed over the
old one, so nothing planted in the bundle can redirect the write.

Nothing here needs more rights than the person has: a launcher that isn't
writable is skipped, with a note in the log.
It never raises: an icon is never a reason for an update or a start to fail.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import plistlib
import secrets
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from datalab.exports import ExportError, Folder, is_link

log = logging.getLogger(__name__)

# (the app's name, its bundle id, the packaged icon), as installer/macos/install.sh makes them.
MAC_LAUNCHERS = (
    ("DataLab", "edu.umich.ihs.datalab", "DataLab.icns"),
    ("DataLab (practice)", "edu.umich.ihs.datalab.practice", "DataLab-practice.icns"),
)
# The icons installer/windows/install.ps1 copies to `<install root>\icons`.
WINDOWS_ICONS = ("DataLab.ico", "DataLab-practice.ico")
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
LSREGISTER_SECONDS = 10

Run = Callable[..., object]


def default_app_folders() -> list[Path]:
    """Where the macOS installer puts the app (DATALAB_SYSTEM_APPLICATIONS is
    its own override of /Applications)."""
    system = os.environ.get("DATALAB_SYSTEM_APPLICATIONS") or "/Applications"
    return [Path(system), Path.home() / "Applications"]


def package_branding() -> Path:
    """This package's icons (datalab/branding)."""
    return Path(__file__).resolve().parent / "branding"


def refresh(
    branding: Path | None,
    *,
    root: Path,
    platform: str = sys.platform,
    app_folders: Sequence[Path] | None = None,
    run: Run = subprocess.run,
) -> list[Path]:
    """Copy `branding`'s icons over the launcher's where they differ. Returns
    the icon files it replaced. Never raises."""
    try:
        if branding is None or not branding.is_dir():
            return []
        if platform == "darwin":
            folders = default_app_folders() if app_folders is None else list(app_folders)
            return _refresh_mac(branding, root, folders, run)
        if platform == "win32":
            return _refresh_windows(branding, root)
    except Exception:  # never a reason for an update or a start to fail
        log.exception("couldn't refresh the launcher's icon")
    return []


def _sha256(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _owner_ok(info: os.stat_result) -> bool:
    """Made by this person (on a Mac; Windows has no owner ids here)."""
    getuid = getattr(os, "getuid", None)
    return getuid is None or info.st_uid == getuid()


def _open_dir(parent: Folder | None, path: Path, name: str | None = None) -> Folder:
    """A folder that isn't a link, opened (by handle where the system can) and
    owned by this person; ExportError or OSError otherwise."""
    folder = Folder.at(path) if parent is None else parent.child(name or path.name)
    try:
        info = folder.stat()
        if not stat.S_ISDIR(info.st_mode) or not _owner_ok(info):
            raise OSError(f"{folder.path} isn't this person's own folder")
        if parent is None and is_link(path, os.lstat(path)):
            raise OSError(f"{folder.path} is a link")
    except BaseException:
        folder.close()
        raise
    return folder


def _read(folder: Folder, name: str, *, owned: bool = True) -> bytes | None:
    """A file in `folder`, never through a link: None if it isn't a plain file
    (of this person's, with `owned`)."""
    try:
        info = folder.lstat(name)
        if is_link(folder.path / name, info) or not stat.S_ISREG(info.st_mode):
            return None
        fd = folder.open_file(name, os.O_RDONLY)
    except (OSError, ExportError):
        return None
    with os.fdopen(fd, "rb") as file:
        opened = os.fstat(file.fileno())
        if not stat.S_ISREG(opened.st_mode) or (owned and not _owner_ok(opened)):
            return None
        return file.read()


def _replace(folder: Folder, name: str, data: bytes) -> bool:
    """`name` in `folder` becomes `data`: a new file of a random name, made
    there (never through a link, never over something already there), then
    renamed over it. False (and a note) if it can't be written."""
    temporary = f".{name}.{secrets.token_hex(8)}.new"
    made = False
    try:
        fd = folder.open_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        made = True
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        folder.replace(temporary, name)
    except (OSError, ExportError) as error:
        if made:
            with contextlib.suppress(OSError, ExportError):
                folder.unlink(temporary)
        log.info("left the launcher's icon %s as it is: %s", folder.path / name, error)
        return False
    return True


# ------------------------------------------------------------------ macOS


def _launch_line(root: Path) -> str:
    """The line installer/macos/install.sh writes in the app's launch script:
    this install's bin/datalab, single-quoted, a quote as '\\''."""
    shim = str(root / "bin" / "datalab").replace("'", "'\\''")
    return f"exec osascript - '{shim}' <<'OSA'"


def _bundle_id(info_plist: bytes | None) -> str | None:
    if info_plist is None:
        return None
    with contextlib.suppress(plistlib.InvalidFileException, ValueError, TypeError):
        info = plistlib.loads(info_plist)
        value = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
        return value if isinstance(value, str) else None
    return None


def _refresh_mac(branding: Path, root: Path, folders: Sequence[Path], run: Run) -> list[Path]:
    replaced: list[Path] = []
    for name, bundle_id, icon in MAC_LAUNCHERS:
        source = branding / icon
        for folder in folders:
            bundle = folder / f"{name}.app"
            try:
                if not bundle.is_dir() or bundle.is_symlink():
                    if bundle.is_symlink():
                        log.info("left %s alone: it's a link", bundle)
                    continue
                if _refresh_bundle(bundle, bundle_id, source, root, run):
                    replaced.append(bundle / "Contents" / "Resources" / "DataLab.icns")
            except (OSError, ExportError) as error:
                log.info("left %s alone: %s", bundle, error)
    return replaced


def _refresh_bundle(bundle: Path, bundle_id: str, source: Path, root: Path, run: Run) -> bool:
    """One app bundle: True if its icon was replaced."""
    with contextlib.ExitStack() as stack:
        app = stack.enter_context(_open_dir(None, bundle))
        contents = stack.enter_context(_open_dir(app, bundle / "Contents"))
        if _bundle_id(_read(contents, "Info.plist")) != bundle_id:
            log.info("left %s alone: it isn't this DataLab's", bundle)
            return False
        macos = stack.enter_context(_open_dir(contents, bundle / "Contents" / "MacOS"))
        script = _read(macos, "DataLab")
        lines = script.decode("utf-8", "replace").splitlines() if script else []
        if _launch_line(root) not in lines:
            log.info("left %s alone: it doesn't open this DataLab", bundle)
            return False
        resources = stack.enter_context(_open_dir(contents, bundle / "Contents" / "Resources"))
        new = source.read_bytes()
        if _sha256(_read(resources, "DataLab.icns", owned=False)) == _sha256(new):
            return False
        with contextlib.suppress(ExportError, FileNotFoundError):
            info = resources.lstat("DataLab.icns")
            if is_link(resources.path / "DataLab.icns", info) or not stat.S_ISREG(info.st_mode):
                log.info("left %s alone: its icon isn't an ordinary file", bundle)
                return False
        if not os.access(resources.path, os.W_OK):
            log.info("left %s's icon as it is: not writable without an administrator", bundle)
            return False
        if not _replace(resources, "DataLab.icns", new):
            return False
    log.info("updated %s's icon", bundle)
    # So Finder, the Dock and Spotlight notice: a newer bundle, and
    # LaunchServices told again. Best effort, and time-limited.
    with contextlib.suppress(OSError, NotImplementedError):
        os.utime(bundle, follow_symlinks=False)
    if Path(LSREGISTER).is_file():
        try:
            run(
                [LSREGISTER, "-f", str(bundle)],
                capture_output=True,
                timeout=LSREGISTER_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.info("lsregister didn't run for %s: %s", bundle, error)
    return True


# ------------------------------------------------------------------ Windows


def _refresh_windows(branding: Path, root: Path) -> list[Path]:
    path = root / "icons"
    try:
        if not path.is_dir() or is_link(path, os.lstat(path)):
            return []
        icons = _open_dir(None, path)
    except (OSError, ExportError) as error:
        log.info("left %s alone: %s", path, error)
        return []
    replaced: list[Path] = []
    with icons:
        for name in WINDOWS_ICONS:
            try:
                # Only the icons the installer put there: the shortcuts point at them.
                try:
                    info = icons.lstat(name)
                except FileNotFoundError:
                    continue
                if is_link(path / name, info) or not stat.S_ISREG(info.st_mode):
                    log.info("left %s alone: it isn't an ordinary file", path / name)
                    continue
                new = (branding / name).read_bytes()
                if _sha256(_read(icons, name, owned=False)) == _sha256(new):
                    continue
                if _replace(icons, name, new):
                    replaced.append(path / name)
                    log.info("updated the launcher's icon %s", path / name)
            except (OSError, ExportError) as error:
                log.info("left %s as it is: %s", path / name, error)
    if replaced and sys.platform == "win32":
        # Tell Explorer the icons changed (SHCNE_ASSOCCHANGED), so shortcuts redraw.
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    return replaced
