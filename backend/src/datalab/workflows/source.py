"""Where workflow files come from: the synced `ihs-pipelines` repo, or a folder.

`workflows_folder` picks it: `[workflows] folder` if set in settings.toml;
else the clone of `repos.pipelines` (`<data folder>/repos/ihs-pipelines`,
synced from the Pipelines tab), when that's set and has been synced; else
`<data folder>/workflows-local/` (and it says why, until the first sync).
Either way it's laid out like the lab's `ihs-pipelines` repo:

    workflows/*.yaml                          the workflow files
    ihsDataR/                                 the pipelines package
    ihsDataR/inst/pipelines/<name>/pipeline.yaml, run.R

or simply holds the YAML files.

For each file DataLab records where it came from: when the folder is a git
checkout and the file is committed as it is, the commit and the file's git
blob id (the same on Mac and Windows, whatever the line endings in the
working tree); otherwise `sha256:<hex>` of the file.

DataLab never runs anything from the folder on the host: git is asked only
for object ids, with hooks and fsmonitor turned off.

**A run reads a snapshot, never the folder.** When a run starts, its
workflow files and the package are copied into the run's own folder
(`snapshot`): from the clone, GitHub's `main` as last synced, under the
clone's lock, so a Sync or Save & share meanwhile can't mix commits; from
any other folder, the files as they are then. The run reads only that copy,
and records the commit it came from.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from datalab.config import Settings
from datalab.repos.git import Clone, git_env
from datalab.textcheck import size_text
from datalab.workflows.model import (
    MAX_FILE_BYTES,
    Pipeline,
    PipelineLookup,
    WorkflowInvalid,
    load_pipeline_file,
)

PACKAGE = "ihsDataR"
# A pipeline's name: its folder in inst/pipelines/ (as a workflow step names it).
PIPELINE_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
_GIT = [
    "git",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.allow=never",
]  # fmt: skip
# The package tree, and the pipelines in it, stay small: text files only.
_MAX_PACKAGE_FILES = 5000
_MAX_PACKAGE_BYTES = 50 * 1024**2


class SourceError(RuntimeError):
    pass


def workflows_folder(settings: Settings) -> WorkflowFolder:
    """Where workflow files and the pipelines package are read from (see the
    module docstring). The default folder is made if it's missing; the
    pipelines clone isn't, until its first sync."""
    if settings.workflows.folder:
        return WorkflowFolder(Path(settings.workflows.folder).expanduser())
    local = settings.data_dir / "workflows-local"
    local.mkdir(parents=True, exist_ok=True)
    if settings.repos.pipelines and settings.profile != "practice":
        path = settings.data_dir / "repos" / settings.repos.pipelines.split("/")[1]
        # Only ever read here (never fetched): the Pipelines tab syncs it.
        return WorkflowFolder(path, clone=Clone(path, ""), fallback=local)
    return WorkflowFolder(local)


@dataclass(frozen=True)
class WorkflowFile:
    path: str  # relative to the folder, with "/"
    text: str
    source: str  # "git" or "file"
    blob: str  # git blob id, or sha256:<hex>
    commit: str | None


@dataclass(frozen=True)
class PackageTree:
    """The pipelines package's source, as a checksum over its files."""

    root: Path
    name: str
    tree_sha256: str


# What a snapshot of the pipelines clone copies: the rest is there to read.
_RUN_PATHS = ("workflows/", f"{PACKAGE}/")


class WorkflowFolder:
    def __init__(
        self,
        root: Path,
        *,
        clone: Clone | None = None,
        fallback: Path | None = None,
        commit: str | None = None,
        ids: dict[str, tuple[str, str]] | None = None,
        package_problem: str | None = None,
    ) -> None:
        self._root = root
        # The pipelines clone (root is its checkout), and the folder read
        # until it's been synced.
        self._clone = clone
        self._fallback = fallback
        # A snapshot: every file is as in `commit`, or has the ids in `ids`.
        self._commit = commit
        self._ids = ids
        # A snapshot whose package couldn't be copied, and why: only a run's
        # pipeline steps need it, and they say so.
        self._package_problem = package_problem

    @property
    def root(self) -> Path:
        if self._clone is not None and self._fallback is not None and not self._clone.exists():
            return self._fallback
        return self._root

    @property
    def shared(self) -> bool:
        """Whether the files are the synced pipelines clone's: new ones are
        saved with Save & share, never written here."""
        return self._clone is not None and self.root == self._root

    @property
    def waiting_for_sync(self) -> bool:
        """Whether the files will come from the pipelines clone once it's synced."""
        return self._clone is not None and self.root == self._fallback

    @property
    def note(self) -> str | None:
        """Why the files come from somewhere other than expected, if they do."""
        if self.root == self._fallback:
            return (
                "The pipelines repo hasn't been synced yet (Pipelines tab), so workflow files "
                f"come from {self._fallback} until it is."
            )
        return None

    def snapshot(self, dest: Path, *, workflow: str | None = None) -> WorkflowFolder:
        """The workflow files (only `workflow`, if given) and the package as
        they are now, copied into `dest` (a new folder), for a run to read
        instead of this one."""
        dest.mkdir(parents=True)
        clone = self._clone
        if clone is not None and self.root == self._root:
            with clone.lock:
                head = clone.remote_head()
                if head is None:
                    raise SourceError("The pipelines repo has no main branch yet: sync it first.")
                clone.copy_tree(head, dest, skip=lambda path: not path.startswith(_RUN_PATHS))
            return WorkflowFolder(dest, commit=head)
        ids: dict[str, tuple[str, str]] = {}
        for relative in [workflow] if workflow is not None else self.paths():
            if workflow is not None:
                self.read(relative)  # the checks a workflow file gets (inside, .yaml, size)
            try:
                data = _read_limited(self._inside(relative), MAX_FILE_BYTES)
            except SourceError:
                if workflow is not None:
                    raise
                continue  # one file too large or gone never blocks the others
            blob, commit = self._git_ids(self.root / relative, data)
            if blob is not None and commit is not None:
                ids[relative] = (blob, commit)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        problem = None
        package = self.package_dir
        if package.is_dir() and not package.is_symlink():
            problem = _copy_package(package, dest / PACKAGE)
        return WorkflowFolder(dest, ids=ids, package_problem=problem)

    @property
    def workflows_dir(self) -> Path:
        nested = self.root / "workflows"
        return nested if nested.is_dir() else self.root

    @property
    def package_dir(self) -> Path:
        return self.root / PACKAGE

    def paths(self) -> list[str]:
        """The workflow files, as paths relative to the folder."""
        folder = self.workflows_dir
        if not folder.is_dir():
            return []
        found = []
        for entry in sorted(folder.iterdir()):
            if (
                entry.suffix.lower() in (".yaml", ".yml")
                and not entry.name.startswith(".")
                and entry.is_file()
            ):
                found.append(entry.relative_to(self.root).as_posix())
        return found

    def read(self, path: str) -> WorkflowFile:
        """One workflow file: only one of `paths()`, the .yaml files (not
        dotfiles) directly in the workflows folder. Refuses anything else."""
        full = self._inside(path)
        if full.parent.resolve() != self.workflows_dir.resolve():
            raise SourceError("Workflow files are read only from the workflows folder.")
        if full.suffix.lower() not in (".yaml", ".yml") or full.name.startswith("."):
            raise SourceError("A workflow file is a .yaml file.")
        if path not in self.paths():
            raise SourceError(f"There's no workflow file {path}.")
        data = _read_limited(full, MAX_FILE_BYTES)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceError(f"{path} isn't UTF-8 text.") from None
        blob, commit = self._git_ids(full, data)
        if blob is not None:
            return WorkflowFile(path, text, "git", blob, commit)
        return WorkflowFile(path, text, "file", f"sha256:{hashlib.sha256(data).hexdigest()}", None)

    def add(self, name: str, text: str) -> str:
        """Write a new workflow file, `<name>.yaml`, into the workflows folder,
        and give its path. Never replaces a file (whatever its case, or .yml),
        and never writes into the pipelines clone: that's Save & share's."""
        if self._clone is not None and self.root == self._root:
            raise SourceError("Workflow files in the pipelines repo are saved with Save & share.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
            raise SourceError("A workflow's name is lower case letters, digits, - and _.")
        folder = self.workflows_dir
        folder.mkdir(parents=True, exist_ok=True)
        taken = {entry.name.lower() for entry in folder.iterdir()}
        if {f"{name}.yaml", f"{name}.yml"} & taken:
            raise FileExistsError(f"There's already a workflow file called {name}.yaml.")
        data = text.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SourceError(f"The file is larger than {size_text(MAX_FILE_BYTES)}.")
        target = folder / f"{name}.yaml"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return target.relative_to(self.root).as_posix()

    def pipeline(self, name: str) -> Pipeline | None:
        """A pipeline from the package, or None if there's none by that name."""
        if self._package_problem is not None or not PIPELINE_NAME.fullmatch(name):
            return None
        folder = self.package_dir / "inst" / "pipelines" / name
        spec_file, script_file = folder / "pipeline.yaml", folder / "run.R"
        if not (_plain_file(spec_file) and _plain_file(script_file)):
            return None
        try:
            spec = load_pipeline_file(_read_limited(spec_file, MAX_FILE_BYTES).decode("utf-8"))
            script = _read_limited(script_file, MAX_FILE_BYTES).decode("utf-8")
        except (WorkflowInvalid, UnicodeDecodeError, SourceError):
            return None
        if spec.name != name:
            return None
        return Pipeline(name=name, spec=spec, script=script)

    def pipeline_problems(self, name: str) -> list[str]:
        """Why a pipeline can't be loaded, for the workflow check's message.

        The name comes from the workflow file, which may be the agent's
        (check_workflow), so it's checked before it goes into a path, and
        only paths relative to the folder are ever named: never where the
        folder is on this computer, and nothing about files outside it."""
        if self._package_problem is not None:
            return [self._package_problem]
        if not PIPELINE_NAME.fullmatch(name):
            return ["Pipeline names are lower case letters, digits and _ (at most 48)."]
        shown = f"{PACKAGE}/inst/pipelines/{name}/pipeline.yaml"
        spec_file = self.package_dir / "inst" / "pipelines" / name / "pipeline.yaml"
        try:
            inside = spec_file.resolve().is_relative_to(self.root.resolve())
            if not inside or not _plain_file(spec_file):
                return [f"{shown} isn't there."]
            load_pipeline_file(_read_limited(spec_file, MAX_FILE_BYTES).decode("utf-8"))
        except WorkflowInvalid as error:
            return [str(p) for p in error.problems]
        except UnicodeDecodeError:
            return [f"{shown} isn't UTF-8 text."]
        except SourceError:
            return [f"{shown} is larger than {size_text(MAX_FILE_BYTES)}."]
        except (OSError, RuntimeError, ValueError):
            return [f"{shown} can't be read."]
        return []

    def package(self) -> PackageTree:
        """The package's source tree and its checksum, for building and pinning."""
        if self._package_problem is not None:
            raise SourceError(self._package_problem)
        root = self.package_dir
        if not root.is_dir() or root.is_symlink():
            raise SourceError(f"There's no {PACKAGE} package in the workflows folder.")
        description = root / "DESCRIPTION"
        if not _plain_file(description):
            raise SourceError(f"{PACKAGE}/DESCRIPTION isn't there.")
        match = re.search(
            r"^Package:\s*([A-Za-z][A-Za-z0-9.]*)\s*$",
            _read_limited(description, 64 * 1024).decode("utf-8", "replace"),
            re.MULTILINE,
        )
        if not match:
            raise SourceError(f"{PACKAGE}/DESCRIPTION doesn't name the package.")
        return PackageTree(root=root, name=match.group(1), tree_sha256=tree_sha256(root))

    def _inside(self, path: str) -> Path:
        if not path or path.startswith(("/", "\\")) or "\\" in path or ":" in path:
            raise SourceError("Give a path inside the workflows folder.")
        parts = path.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise SourceError("Give a path inside the workflows folder.")
        full = self.root.joinpath(*parts)
        if not _plain_file(full):
            raise SourceError(f"There's no workflow file {path}.")
        return full

    def _git_ids(self, full: Path, data: bytes) -> tuple[str | None, str | None]:
        """(blob id, commit) if the file is committed exactly as it is, else (None, None)."""
        if self._commit is not None:
            return git_blob_id(data), self._commit  # copied from that commit, byte for byte
        if self._ids is not None:
            known = self._ids.get(full.relative_to(self.root).as_posix())
            return known if known is not None and known[0] == git_blob_id(data) else (None, None)
        try:
            top = _git(self.root, "rev-parse", "--show-toplevel")
            commit = _git(self.root, "rev-parse", "--verify", "HEAD^{commit}")
            in_repo = full.resolve().relative_to(Path(top).resolve()).as_posix()
            committed = _git(self.root, "rev-parse", f"HEAD:{in_repo}")
        except (SourceError, ValueError):
            return None, None
        # Line endings: git may check out LF files as CRLF on Windows.
        current = {git_blob_id(data), git_blob_id(data.replace(b"\r\n", b"\n"))}
        if committed not in current:
            return None, None
        return committed, commit


def git_blob_id(data: bytes) -> str:
    """What `git hash-object` gives for these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def tree_sha256(root: Path) -> str:
    """A checksum over a folder's files (paths and contents), links refused."""
    digest = hashlib.sha256()
    count = total = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                raise SourceError(f"{path.relative_to(root).as_posix()} isn't a plain file.")
            data = path.read_bytes()
            count += 1
            total += len(data)
            if count > _MAX_PACKAGE_FILES or total > _MAX_PACKAGE_BYTES:
                raise SourceError("The package is too large to build here.")
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def _copy_package(source: Path, dest: Path) -> str | None:
    """Copy the package's folder as `package()` sees it, within its limits:
    dot-files and dot-folders left out (as its checksum and the runner's build
    copy leave them), links copied as links (`package()` then refuses them).
    Why it couldn't be, or None."""
    count = total = 0
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        target = dest / Path(folder).relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        names = [f for f in files if not f.startswith(".")]
        for name in [*names, *(d for d in dirs if (Path(folder) / d).is_symlink())]:
            path = Path(folder) / name
            if path.is_symlink():
                os.symlink(os.readlink(path), target / name)
                continue
            count += 1
            total += path.stat().st_size
            if count > _MAX_PACKAGE_FILES or total > _MAX_PACKAGE_BYTES:
                shutil.rmtree(dest, ignore_errors=True)
                return (
                    f"The {PACKAGE} package is too large to run here "
                    f"(over {_MAX_PACKAGE_FILES} files or {_MAX_PACKAGE_BYTES // 1024**2} MB)."
                )
            shutil.copyfile(path, target / name, follow_symlinks=False)
    return None


def pipelines_in(read: Callable[[str], bytes | None]) -> PipelineLookup:
    """Find pipelines in a tree that isn't on disk (a commit of the pipelines
    repo): `read(path)` gives a file's bytes, or None. As
    `WorkflowFolder.pipeline`."""

    def find(name: str) -> Pipeline | None:
        if not PIPELINE_NAME.fullmatch(name):
            return None
        folder = f"{PACKAGE}/inst/pipelines/{name}"
        spec, script = read(f"{folder}/pipeline.yaml"), read(f"{folder}/run.R")
        if spec is None or script is None or max(len(spec), len(script)) > MAX_FILE_BYTES:
            return None
        try:
            parsed = load_pipeline_file(spec.decode("utf-8"))
            text = script.decode("utf-8")
        except (WorkflowInvalid, UnicodeDecodeError):
            return None
        return Pipeline(name=name, spec=parsed, script=text) if parsed.name == name else None

    return find


def _plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_limited(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise SourceError(f"{path.name} is larger than {size_text(limit)}.")
    return data


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            [*_GIT, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            # Only the repo's own config (the synced clone's is DataLab's).
            env=git_env({"GIT_OPTIONAL_LOCKS": "0"}),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SourceError(f"git isn't available: {error}") from error
    if done.returncode != 0:
        raise SourceError(done.stderr.strip()[:300])
    return done.stdout.strip()
