"""Export folders on this computer: where they are, whether DataLab can write
there now, and what to tell the person after it has.

A folder inside a sync app's folder (Dropbox, OneDrive, Box, Google Drive,
iCloud Drive) is still a folder on this computer. DataLab writes the files
there and that's all it can know: the sync app uploads them later, if it's
running and signed in. So nothing here ever says a file was synced or
uploaded, only that it was saved on this computer and who will upload it.

There is no Dropbox API and no key: DataLab uses the folder the Dropbox app
already keeps in sync.

Folders are still chosen in the computer's own picker (sessions/picker.py),
so a web page can't name a path. The sync folders found here only say where
the picker opens. They're found by name in the home folder and, on a Mac,
`~/Library/CloudStorage`; nothing inside them is listed.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from datalab.config import Settings, default_data_dir
from datalab.exports import Destination, DestinationStore, ExportError
from datalab.sessions.inputs import NotAttachable, check_attachable

Provider = Literal["dropbox", "onedrive", "box", "google_drive", "icloud"]
PROVIDER_NAMES: dict[str, str] = {
    "dropbox": "Dropbox",
    "onedrive": "OneDrive",
    "box": "Box",
    "google_drive": "Google Drive",
    "icloud": "iCloud Drive",
}
# Where a folder is: an ordinary folder on this computer, inside a sync
# app's folder, or on a removable or external drive.
Location = Literal["this_computer", "sync_folder", "external_drive"]
# Whether DataLab can write there now.
Status = Literal["ready", "missing", "not_a_folder", "not_writable", "online_only", "refused"]

PRACTICE_ID = "practice"
PRACTICE_NAME = "Practice exports"
NAME_MAX = 80

# Folder names each sync app uses, in the home folder (all systems) and in
# ~/Library/CloudStorage (a Mac's File Provider apps). Case doesn't matter.
_HOME_PATTERNS: list[tuple[Provider, re.Pattern[str]]] = [
    ("dropbox", re.compile(r"Dropbox( \(.+\))?", re.IGNORECASE)),
    ("onedrive", re.compile(r"OneDrive( - .+)?", re.IGNORECASE)),
    ("box", re.compile(r"Box( Sync)?", re.IGNORECASE)),
    ("google_drive", re.compile(r"Google Drive", re.IGNORECASE)),
]
_CLOUD_STORAGE_PATTERNS: list[tuple[Provider, re.Pattern[str]]] = [
    ("dropbox", re.compile(r"Dropbox(-.+)?", re.IGNORECASE)),
    ("onedrive", re.compile(r"OneDrive(-.+)?", re.IGNORECASE)),
    ("box", re.compile(r"Box(-.+)?", re.IGNORECASE)),
    ("google_drive", re.compile(r"GoogleDrive(-.+)?", re.IGNORECASE)),
]
_ICLOUD = Path("Library/Mobile Documents/com~apple~CloudDocs")
# Files the Dropbox app keeps at the top of its folder.
_DROPBOX_MARKERS = (".dropbox", ".dropbox.cache")

# A macOS File Provider file or folder whose contents are only in the cloud.
_SF_DATALESS = 0x40000000
# Windows: a cloud placeholder (OneDrive / Dropbox "online-only").
_WIN_ONLINE_ONLY = (
    0x00400000 | 0x00040000 | 0x00001000
)  # RECALL_ON_DATA_ACCESS, RECALL_ON_OPEN, OFFLINE

_UNPRINTABLE = re.compile(
    "[\\x00-\\x1f\\x7f\\u0085\\u061c\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u2069\\ufeff]"
)


@dataclass(frozen=True)
class SyncRoot:
    """A sync app's folder found on this computer (by its name only)."""

    id: str  # e.g. "dropbox:Dropbox-UniversityofMichigan": stable, names no path
    provider: Provider
    label: str  # "Dropbox (UniversityofMichigan)"
    path: Path

    @property
    def where(self) -> str:
        return display_path(self.path)


@dataclass(frozen=True)
class FolderCheck:
    status: Status
    message: str | None  # a plain explanation when it isn't ready

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class FolderInfo:
    """Where a folder is and what kind of place it is."""

    location: Location
    sync_provider: Provider | None
    # One sentence for Settings, e.g. "Inside your Dropbox folder: Dropbox
    # will upload it when its app is running and signed in. DataLab can't
    # confirm the upload."
    note: str


@dataclass(frozen=True)
class Target:
    """A destination checked just now, ready to write to."""

    id: str | None
    name: str
    path: Path
    sync_provider: Provider | None


# Finding sync folders ------------------------------------------------------


def sync_roots(home: Path | None = None, platform: str | None = None) -> list[SyncRoot]:
    """The sync apps' folders on this computer, found by name.

    Only the home folder and (on a Mac) ~/Library/CloudStorage are listed,
    for names; nothing inside a sync folder is. Folders reached twice (the
    old ~/Dropbox is often a link to the CloudStorage one) are listed once.
    """
    home = home or Path.home()
    platform = platform or sys.platform
    found: list[SyncRoot] = []
    seen: set[str] = set()

    def add(provider: Provider, path: Path, label: str) -> None:
        try:
            if not path.is_dir():
                return
            real = os.path.realpath(path)
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        found.append(SyncRoot(f"{provider}:{path.name}", provider, label, path))

    if platform == "darwin":
        for name in _names(home / "Library" / "CloudStorage"):
            provider = _match(name, _CLOUD_STORAGE_PATTERNS)
            if provider:
                add(
                    provider, home / "Library" / "CloudStorage" / name, _cloud_label(provider, name)
                )
        icloud = home / _ICLOUD
        add("icloud", icloud, PROVIDER_NAMES["icloud"])
    for name in _names(home):
        provider = _match(name, _HOME_PATTERNS)
        if provider:
            add(provider, home / name, _home_label(provider, name))
    if platform == "win32":
        # OneDrive says where its folders are.
        for variable in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            value = os.environ.get(variable)
            if value:
                add("onedrive", Path(value), _home_label("onedrive", Path(value).name))
    return sorted(found, key=lambda r: (list(PROVIDER_NAMES).index(r.provider), r.label.lower()))


def _names(folder: Path) -> list[str]:
    try:
        with os.scandir(folder) as entries:
            return sorted(e.name for e in entries if not e.name.startswith("."))
    except OSError:
        return []


def _match(name: str, patterns: list[tuple[Provider, re.Pattern[str]]]) -> Provider | None:
    return next((p for p, pattern in patterns if pattern.fullmatch(name)), None)


def _cloud_label(provider: Provider, name: str) -> str:
    # "Dropbox-UniversityofMichigan" → "Dropbox (UniversityofMichigan)"
    base = PROVIDER_NAMES[provider]
    _, _, rest = name.partition("-")
    if provider == "box" and rest.lower() == "box":
        rest = ""
    return f"{base} ({rest})" if rest else base


def _home_label(provider: Provider, name: str) -> str:
    # "Dropbox (Personal)", "OneDrive - University of Michigan"
    base = PROVIDER_NAMES[provider]
    for separator in (" (", " - "):
        if separator in name:
            rest = name.split(separator, 1)[1].rstrip(")")
            return f"{base} ({rest})"
    return base


def display_path(path: Path) -> str:
    """A path with the home folder shown as ~."""
    home = str(Path.home())
    text = str(path)
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home) :]
    return text


# What kind of place a folder is ---------------------------------------------


def sync_provider(path: Path, *, home: Path | None = None) -> Provider | None:
    """Which sync app's folder `path` is in, going by where it is.

    By its place among the sync folders (~/Library/CloudStorage/Dropbox…,
    ~/Dropbox (Team), ~/OneDrive - Org, iCloud Drive), or, anywhere else, by
    the `.dropbox` file the Dropbox app keeps at the top of its folder. Only
    names are looked at, never the folder's contents.
    """
    home = home or Path.home()
    try:
        relative = path.relative_to(home)
    except ValueError:
        relative = None
    if relative is not None:
        parts = relative.parts
        if parts[:2] == ("Library", "CloudStorage") and len(parts) >= 3:
            found = _match(parts[2], _CLOUD_STORAGE_PATTERNS)
            if found:
                return found
        if len(parts) >= 3 and Path(*parts[:3]) == _ICLOUD:
            return "icloud"
        if parts:
            found = _match(parts[0], _HOME_PATTERNS)
            if found:
                return found
    for folder in [path, *path.parents]:
        if folder == home or folder.parent == folder:
            break
        for marker in _DROPBOX_MARKERS:
            with contextlib.suppress(OSError):
                if os.path.lexists(folder / marker):
                    return "dropbox"
    return None


def describe(path: Path, *, home: Path | None = None) -> FolderInfo:
    provider = sync_provider(path, home=home)
    if provider is not None:
        app = PROVIDER_NAMES[provider]
        return FolderInfo(
            "sync_folder",
            provider,
            f"Inside your {app} folder: {app} will upload it when its app is running and "
            "signed in. DataLab can't confirm the upload.",
        )
    if _on_external_drive(path):
        return FolderInfo(
            "external_drive",
            None,
            "On a separate drive: it's there only while the drive is connected.",
        )
    return FolderInfo("this_computer", None, "A folder on this computer.")


def _on_external_drive(path: Path) -> bool:
    if sys.platform == "win32":
        system_drive = os.environ.get("SYSTEMDRIVE", "C:").upper()
        return bool(path.drive) and path.drive.upper() != system_drive
    parts = path.parts
    return len(parts) >= 3 and parts[1] in ("Volumes", "media", "mnt")


# Checking a folder ------------------------------------------------------------


def protected_folders(settings: Settings) -> list[Path]:
    """DataLab's own data folders: never an export folder, nor inside one."""
    return [settings.data_dir, default_data_dir("real"), default_data_dir("practice")]


def check_folder(path: Path, *, protected: list[Path]) -> FolderCheck:
    """Whether DataLab can write to a saved export folder now, and if not, why not."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return FolderCheck("missing", _missing_reason(path))
    except OSError:
        return FolderCheck("missing", "DataLab can't see this folder right now.")
    if stat.S_ISLNK(info.st_mode) or os.path.realpath(path) != str(path):
        # Saved folders are always real paths: a link here was put in since.
        return FolderCheck(
            "refused",
            "This folder has been replaced by a link to somewhere else, so DataLab won't "
            "write there. Choose the folder again.",
        )
    if not stat.S_ISDIR(info.st_mode):
        return FolderCheck("not_a_folder", "Something that isn't a folder is there now.")
    try:
        check_attachable(path, protected=protected)
    except NotAttachable as error:
        return FolderCheck("refused", _refusal(str(error)))
    if _online_only(info):
        app = PROVIDER_NAMES.get(sync_provider(path) or "", "your sync app")
        return FolderCheck(
            "online_only",
            f"This folder is online-only in {app}: it isn't kept on this computer. Make it "
            "available offline (right-click it in Finder or File Explorer), then test again.",
        )
    if not os.access(path, os.W_OK | os.X_OK):
        return FolderCheck(
            "not_writable",
            "Your account can't save files in this folder. Choose another, or change the "
            "folder's permissions.",
        )
    return FolderCheck("ready", None)


def check_new_folder(chosen: Path, *, protected: list[Path]) -> Path:
    """The real folder to save for a chosen path, or NotAttachable saying why not.

    The same places are off limits as for attaching (system folders, app
    data, DataLab's own data). A link that looks as if it's in a sync
    folder but leads out of it is refused, so the folder's kind can't be
    disguised.
    """
    real, kind = check_attachable(chosen, protected=protected)
    if kind != "folder":
        raise NotAttachable("Choose a folder.")
    if real != chosen:
        seems = sync_provider(chosen)
        if seems is not None and sync_provider(real) != seems:
            raise NotAttachable(
                f"That folder is a link that leads out of your {PROVIDER_NAMES[seems]} folder, "
                f"to {display_path(real)}. Choose the folder it leads to instead, if that's "
                "where you want exports to go."
            )
    return real


def _missing_reason(path: Path) -> str:
    provider = sync_provider(path)
    if provider is not None:
        app = PROVIDER_NAMES[provider]
        return (
            f"This folder isn't on this computer now. Check that the {app} app is installed "
            f"and signed in, and that the folder is still in your {app}."
        )
    if _on_external_drive(path):
        return "The drive this folder is on isn't connected. Connect it, then test again."
    return "This folder isn't there any more: it may have been moved, renamed or deleted."


def _refusal(reason: str) -> str:
    if "DataLab's own data folder" in reason:
        return "This is inside DataLab's own data folder, which can't be an export folder."
    if "System files" in reason:
        return "This is a system folder, which can't be an export folder."
    return reason.replace("attached", "used for exports").replace("Attach", "Choose")


def _online_only(info: os.stat_result) -> bool:
    flags = getattr(info, "st_flags", 0) or 0
    attributes = getattr(info, "st_file_attributes", 0) or 0
    return bool(flags & _SF_DATALESS) or bool(attributes & _WIN_ONLINE_ONLY)


def check_name(name: str) -> str:
    """A folder's friendly name, tidied, or ValueError saying what's wrong."""
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        raise ValueError("Give the folder a name.")
    if len(name) > NAME_MAX:
        raise ValueError(f"Keep the name to {NAME_MAX} characters.")
    if _UNPRINTABLE.search(name):
        raise ValueError("The name has characters DataLab can't show.")
    return name


# Test and write -----------------------------------------------------------------

TEST_TEXT = (
    "SYNTHETIC TEST FILE written by IHS DataLab.\n"
    "It contains no study data. DataLab saves it to check it can write to this\n"
    "folder, and removes it straight away. If you can see it, it's safe to delete.\n"
)


@dataclass(frozen=True)
class WriteTest:
    ok: bool
    status: Status
    message: str | None
    test_file: str | None  # the name it used
    removed: bool  # whether the test file is gone again


def write_test_file(path: Path, *, protected: list[Path], skip_checks: bool = False) -> WriteTest:
    """Save a small synthetic file in the folder, read it back, and remove it.

    `skip_checks` is for practice's own folder, which lives in DataLab's
    data folder and so would be refused as an export folder anywhere else.
    """
    if not skip_checks:
        checked = check_folder(path, protected=protected)
        if not checked.ready:
            return WriteTest(False, checked.status, checked.message, None, True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"datalab-test-{stamp}-{secrets.token_hex(3)}.txt"
    target = path / name
    body = TEST_TEXT.encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except PermissionError:
        return WriteTest(
            False,
            "not_writable",
            "Your account can't save files in this folder. Choose another, or change the "
            "folder's permissions.",
            name,
            True,
        )
    except OSError as error:
        return WriteTest(
            False,
            "not_writable",
            f"DataLab couldn't save a file here: {error.strerror}.",
            name,
            True,
        )
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        with open(target, "rb") as back:
            same = back.read() == body
    except OSError as error:
        same = False
        failure = f"DataLab couldn't finish saving a file here: {error.strerror}."
    else:
        failure = "The file DataLab saved didn't read back the same."
    removed = True
    try:
        os.unlink(target)
    except FileNotFoundError:
        pass
    except OSError:
        removed = False
    if not same:
        return WriteTest(False, "not_writable", failure, name, removed)
    return WriteTest(True, "ready", None, name, removed)


def saved_to(name: str) -> str:
    """What an export, delivery or test says it did: never "synced"."""
    return f"Saved to {name} (on this computer)"


def sync_note(provider: str | None, *, files: int = 1) -> str | None:
    """Who will upload the files, for a folder inside a sync app's folder."""
    if provider not in PROVIDER_NAMES:
        return None
    app = PROVIDER_NAMES[provider]
    them = "it" if files == 1 else "them"
    return (
        f"{app} will upload {them} when its app is running and signed in. "
        "DataLab can't confirm the upload."
    )


# The folder an export or delivery goes to -----------------------------------------


def target(settings: Settings, destinations: DestinationStore, destination_id: str) -> Target:
    """The folder a destination id names, checked again now. Raises ExportError
    (LookupError when there's no such folder).

    Every export (conversations, the SQL Playground) goes through here.
    Practice exports only to its own practice folder, so nothing from it can
    end up somewhere real.
    """
    if settings.profile == "practice":
        if destination_id != PRACTICE_ID:
            raise LookupError("No such export folder.")
        return practice_target(settings)
    destination = destinations.get(destination_id)
    if destination is None:
        raise LookupError("No such export folder.")
    return _checked(settings, destination)


def target_for_key(settings: Settings, destinations: DestinationStore, key: str) -> Target:
    """The folder a workflow's destination key names on this computer. Raises ExportError."""
    if settings.profile == "practice":
        return practice_target(settings)
    destination = destinations.by_key(key)
    if destination is None:
        raise ExportError(
            f"No export folder is set for {key!r} on this computer. Choose one in Settings."
        )
    return _checked(settings, destination)


def practice_target(settings: Settings) -> Target:
    folder = settings.data_dir / "practice-exports"
    folder.mkdir(parents=True, exist_ok=True)
    return Target(None, PRACTICE_NAME, folder, None)


def _checked(settings: Settings, destination: Destination) -> Target:
    if not destination.offered:
        raise ExportError(
            f"{destination.name} is turned off as a destination. Turn it on in Settings → "
            "Export folders."
        )
    folder = Path(destination.path)
    checked = check_folder(folder, protected=protected_folders(settings))
    if not checked.ready:
        raise ExportError(f"DataLab can't save to {destination.name} now. {checked.message}")
    return Target(destination.id, destination.name, folder, sync_provider(folder))
