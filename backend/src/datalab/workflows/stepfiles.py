"""A step's files, as the step contract has them: DataLab's step wrapper
(run_step.R, mounted into every step at /run/datalab), its declared outputs copied
back (regular files only, never links) with checksums, its result.json read
and capped, kept inputs copied and checked, and the facts recorded about each
file (never its values)."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import math
import os
import re
import shutil
import stat
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from datalab import __version__
from datalab.workflows.model import resolve_ref
from datalab.workflows.runstate import Output, StepFailed

MAX_RESULT_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 500
MAX_MESSAGES = 50
WRAPPER_NAME = "run_step.R"


def wrapper_bytes() -> bytes:
    return resources.files(__package__).joinpath(WRAPPER_NAME).read_bytes()


def wrapper_sha256() -> str:
    return hashlib.sha256(wrapper_bytes()).hexdigest()


def runner_version() -> str:
    return f"datalab {__version__}; {WRAPPER_NAME} sha256:{wrapper_sha256()}"


def lookup_output(ref: str, done: Mapping[str, Mapping[str, Output]]) -> Output:
    names = {step: dict.fromkeys(outputs, "") for step, outputs in done.items()}
    resolved = resolve_ref(ref, names)
    if isinstance(resolved, str):
        raise StepFailed(resolved)
    return done[resolved[0]][resolved[1]]


def input_ref(output: Output) -> dict[str, Any]:
    return {
        "step": output.step,
        "output": output.name,
        "file": output.file,
        "sha256": output.facts.get("sha256"),
    }


def number_or_none(value: Any) -> int | float | bool | None:
    return value if isinstance(value, int | float | bool) else None


def finite_or_none(value: Any) -> int | float | bool | None:
    number = number_or_none(value)
    return None if isinstance(number, float) and not math.isfinite(number) else number


def spec_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    return {k: facts[k] for k in ("sha256", "bytes", "rows", "columns") if k in facts}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_facts(path: Path) -> dict[str, Any]:
    """Checksum and size, and for a CSV its row count and header. No values."""
    facts: dict[str, Any] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    if path.suffix.lower() == ".csv":
        with (
            contextlib.suppress(UnicodeDecodeError, csv.Error),
            path.open(newline="", encoding="utf-8") as handle,
        ):
            reader = csv.reader(handle)
            header = next(reader, [])
            facts["rows"] = sum(1 for _ in reader)
            facts["columns"] = header
    return facts


def matches(path: Path, sha256: str) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and sha256_file(path) == sha256
    except OSError:
        return False


def copy_checked(source: Path, target: Path, sha256: str) -> None:
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as reader, open(target, "xb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    if digest.hexdigest() != sha256:
        target.unlink(missing_ok=True)
        raise StepFailed(f"The kept input {source.name} doesn't match its recorded checksum.")


def read_result(path: Path) -> dict[str, Any]:
    """result.json, capped and checked; its messages are the step's, shown as data."""
    failed: dict[str, Any] = {"status": "failed", "messages": [], "checks": []}
    try:
        info = os.lstat(path)
    except OSError:
        return {**failed, "messages": [{"level": "error", "text": "The step wrote no result."}]}
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
        return {
            **failed,
            "messages": [{"level": "error", "text": "The step's result was refused."}],
        }
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            result = json.loads(handle.read(MAX_RESULT_BYTES))
    except (OSError, ValueError):
        return {**failed, "messages": [{"level": "error", "text": "The step's result isn't JSON."}]}
    if not isinstance(result, dict):
        return failed
    result = dict(result)
    raw_messages, raw_checks = result.get("messages"), result.get("checks")
    messages: list[Any] = raw_messages if isinstance(raw_messages, list) else []
    counts = result.get("counts")
    raw_counts: dict[Any, Any] = counts if isinstance(counts, dict) else {}
    checks: list[Any] = raw_checks if isinstance(raw_checks, list) else []
    return {
        "status": result.get("status") if result.get("status") in ("ok", "failed") else "failed",
        # Numbers only, like a check's observed and expected.
        "counts": {
            str(name)[:100]: number
            for name, raw in list(raw_counts.items())[:100]
            if (number := finite_or_none(raw)) is not None
        },
        "messages": [_capped_message(m) for m in messages[:MAX_MESSAGES]],
        "checks": [_capped_check(c) for c in checks[:100] if isinstance(c, dict)],
        "r_version": str(result.get("r_version", ""))[:100],
        "rng_kind": result.get("rng_kind") if isinstance(result.get("rng_kind"), list) else [],
    }


def _capped_message(message: Any) -> dict[str, str]:
    if not isinstance(message, dict):
        return {"level": "info", "text": str(message)[:MAX_MESSAGE_CHARS]}
    return {
        "level": str(message.get("level", "info"))[:10],
        "text": str(message.get("text", ""))[:MAX_MESSAGE_CHARS],
    }


def _capped_check(check: dict[str, Any]) -> dict[str, Any]:
    """A custom check's result, as kept in the run record. `observed` and
    `expected` are kept only as numbers: text there could carry values (a
    participant id), and the record is shown in the Workflows tab."""
    return {
        "id": str(check.get("id", ""))[:100],
        "status": "pass" if check.get("status") == "pass" else "fail",
        "observed": finite_or_none(check.get("observed")),
        "expected": finite_or_none(check.get("expected")),
        "message": str(check.get("message", ""))[:MAX_MESSAGE_CHARS],
    }


def container_failure(outcome: Any, result: dict[str, Any], missing: list[str]) -> str:
    if outcome.timed_out:
        return "The step ran past its time limit and was stopped."
    if outcome.over_cap:
        return "The step wrote more than the run's disk cap and was stopped."
    if outcome.exit_code == 3 or any(c["status"] == "fail" for c in result.get("checks", [])):
        failed = [c["id"] for c in result.get("checks", []) if c["status"] == "fail"]
        return f"Failed checks: {', '.join(failed)}."
    errors = [m["text"] for m in result.get("messages", []) if m.get("level") == "error"]
    if errors:
        return f"The R code stopped with an error: {errors[-1]}"
    if missing:
        return f"The step didn't write {', '.join(missing)}."
    return f"The step exited with code {outcome.exit_code}."


def collect(
    scratch: Path, out_dir: Path, outputs: Mapping[str, str], budget: int
) -> tuple[dict[str, Output], list[str], int]:
    """Copy the declared outputs, regular files only, never links; count the rest."""
    collected: dict[str, Output] = {}
    missing: list[str] = []
    for name, file in outputs.items():
        source = scratch / file
        try:
            info = os.lstat(source)
        except OSError:
            missing.append(file)
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > budget:
            missing.append(file)
            continue
        budget -= info.st_size
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as reader, open(out_dir / file, "xb") as writer:
            shutil.copyfileobj(reader, writer)
        collected[name] = Output("", name, file, out_dir, file_facts(out_dir / file))
    try:
        present = set(os.listdir(scratch))
    except OSError:
        present = set()
    return collected, missing, len(present - set(outputs.values()))


def copy_tree(source: Path, target: Path) -> None:
    """Copy a source tree; links and dot-files aren't copied."""
    target.mkdir(parents=True)
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and not (Path(folder) / d).is_symlink()
        )
        relative = Path(folder).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            path = Path(folder) / name
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            shutil.copyfile(path, target / relative / name, follow_symlinks=False)


def package_name(source: Path) -> str:
    text = (source / "DESCRIPTION").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^Package:\s*([A-Za-z][A-Za-z0-9.]*)\s*$", text, re.MULTILINE)
    if not match:
        raise StepFailed("The package's DESCRIPTION doesn't name it.")
    return match.group(1)


def remove_quietly(path: Path) -> None:
    """Remove a folder, retrying briefly (Windows: a scanner may hold a file open)."""
    for _ in range(3):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            continue
