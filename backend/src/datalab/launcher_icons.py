"""Keeping the launcher's icon the package's own, after an update.

The installer makes the launcher once: on macOS an app bundle
(`/Applications` or `~/Applications`, "DataLab.app" and "DataLab
(practice).app") with its own copy of the icon, on Windows Start menu and
Desktop shortcuts that point at `<install root>\\icons\\*.ico`. An update only
switches `current`, so a new icon in a new version never reached them. This
copies the running (or newly installed) version's icons over the launcher's,
when they differ.

Only DataLab's own launchers are touched: on macOS a bundle that isn't a
link, whose CFBundleIdentifier is DataLab's, and whose launch script runs
this install's `bin/datalab`; on Windows only the icon files the installer
put in `<install root>\\icons`. Nothing here needs more rights than the
person has: a launcher that isn't writable is skipped, with a note in the log.
It never raises: an icon is never a reason for an update or a start to fail.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

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


def _sha256(path: Path) -> str | None:
    with contextlib.suppress(OSError):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def _replace(source: Path, target: Path) -> bool:
    """`target` becomes a copy of `source`, by a temporary file and a rename.
    False (and a note) if it can't be written."""
    data = source.read_bytes()
    temporary = target.with_name(f".{target.name}.new")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        log.info("left the launcher's icon %s as it is: %s", target, error)
        return False
    return True


# ------------------------------------------------------------------ macOS


def _launches(bundle: Path, root: Path) -> bool:
    """Whether the bundle's launch script runs this install's bin/datalab (the
    installer writes it single-quoted, a quote as '\\'')."""
    shim = str(root / "bin" / "datalab").replace("'", "'\\''")
    script = bundle / "Contents" / "MacOS" / "DataLab"
    with contextlib.suppress(OSError, UnicodeDecodeError):
        return f"'{shim}'" in script.read_text(encoding="utf-8")
    return False


def _bundle_id(bundle: Path) -> str | None:
    with contextlib.suppress(OSError, plistlib.InvalidFileException, ValueError):
        with (bundle / "Contents" / "Info.plist").open("rb") as file:
            info = plistlib.load(file)
        value = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
        return value if isinstance(value, str) else None
    return None


def _refresh_mac(branding: Path, root: Path, folders: Sequence[Path], run: Run) -> list[Path]:
    replaced: list[Path] = []
    for name, bundle_id, icon in MAC_LAUNCHERS:
        source = branding / icon
        if not source.is_file():
            continue
        for folder in folders:
            bundle = folder / f"{name}.app"
            contents = bundle / "Contents"
            resources = contents / "Resources"
            if not bundle.is_dir():
                continue
            if any(p.is_symlink() for p in (bundle, contents, resources)):
                log.info("left %s alone: it's a link", bundle)
                continue
            if _bundle_id(bundle) != bundle_id or not _launches(bundle, root):
                log.info("left %s alone: it isn't this DataLab's", bundle)
                continue
            target = resources / "DataLab.icns"
            if target.is_symlink() or not resources.is_dir():
                log.info("left %s alone: its icon isn't an ordinary file", bundle)
                continue
            if _sha256(target) == _sha256(source):
                continue
            if not os.access(resources, os.W_OK):
                log.info("left %s's icon as it is: not writable without an administrator", bundle)
                continue
            if not _replace(source, target):
                continue
            replaced.append(target)
            log.info("updated %s's icon", bundle)
            # So Finder, the Dock and Spotlight notice: a newer bundle, and
            # LaunchServices told again. Best effort, and time-limited.
            with contextlib.suppress(OSError):
                os.utime(bundle)
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
    return replaced


# ------------------------------------------------------------------ Windows


def _refresh_windows(branding: Path, root: Path) -> list[Path]:
    icons = root / "icons"
    if not icons.is_dir() or icons.is_symlink():
        return []
    replaced: list[Path] = []
    for name in WINDOWS_ICONS:
        source, target = branding / name, icons / name
        # Only the icons the installer put there: the shortcuts point at them.
        if not source.is_file() or not target.is_file() or target.is_symlink():
            continue
        if _sha256(target) == _sha256(source):
            continue
        if _replace(source, target):
            replaced.append(target)
            log.info("updated the launcher's icon %s", target)
    if replaced and sys.platform == "win32":
        # Tell Explorer the icons changed (SHCNE_ASSOCCHANGED), so shortcuts redraw.
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    return replaced
