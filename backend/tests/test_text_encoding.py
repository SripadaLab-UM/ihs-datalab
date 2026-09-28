"""Every text file DataLab reads or writes says it is UTF-8.

Without ``encoding=``, Python uses the computer's locale encoding: cp1252 on
most Windows computers, where writing "→" raises UnicodeEncodeError (the
Safety check once returned a 500 for exactly that). Ruff's PLW1514 catches
the calls whose receiver it can tell is a path; the scan here also catches
``something.read_text()`` on any object, ``os.fdopen`` and text-mode
``subprocess`` calls.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNED = [REPO / "backend" / "src", REPO / "scripts", REPO / "synthetic", REPO / "evals"]

# Calls named ``open`` that aren't file opens (os.open returns a descriptor).
NOT_FILES = {"os", "tarfile", "webbrowser", "zipfile", "gate", "_approvals", "self"}
SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call"}
TEMPFILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}


def _binary(mode: ast.expr | None) -> bool:
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value


def _is_true(value: ast.expr | None) -> bool:
    return isinstance(value, ast.Constant) and value.value is True


def _receiver(func: ast.Attribute) -> str:
    value = func.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


def _problem(call: ast.Call) -> str | None:
    """Why this call reads or writes text without saying UTF-8, if it does."""
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    if "encoding" in keywords:
        return None
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name in {"read_text", "write_text"}:
        return name
    if name == "open":
        if isinstance(func, ast.Attribute):
            if _receiver(func) in NOT_FILES:
                return None
            mode = call.args[0] if call.args else keywords.get("mode")
        else:
            mode = call.args[1] if len(call.args) > 1 else keywords.get("mode")
        return None if _binary(mode) else "open in text mode"
    if name == "fdopen":
        mode = call.args[1] if len(call.args) > 1 else keywords.get("mode")
        return None if _binary(mode) else "fdopen in text mode"
    if name in TEMPFILES:
        mode = call.args[0] if call.args else keywords.get("mode")
        return "temporary file in text mode" if mode is not None and not _binary(mode) else None
    if name in {"FileHandler", "TextIOWrapper"}:
        return name
    text = _is_true(keywords.get("text")) or _is_true(keywords.get("universal_newlines"))
    if (
        text
        and name in SUBPROCESS
        and isinstance(func, ast.Attribute)
        and _receiver(func) == "subprocess"
    ):
        return f"subprocess.{name} with text=True"
    return None


def _problems() -> list[str]:
    found = []
    for root in SCANNED:
        for path in sorted(root.rglob("*.py")):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            # os.fdopen(thing.open(), "rb"): the inner open returns a descriptor.
            descriptors = {
                id(arg)
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "fdopen"
                for arg in node.args[:1]
            }
            for node in ast.walk(tree):
                if id(node) in descriptors:
                    continue
                if isinstance(node, ast.Call) and (why := _problem(node)):
                    found.append(f"{path.relative_to(REPO)}:{node.lineno}: {why}")
    return found


def test_every_text_file_is_opened_as_utf8() -> None:
    assert _problems() == [], "say encoding='utf-8' (and errors= for a tool's output)"


def test_the_scan_sees_the_mistakes_it_is_for() -> None:
    def problem(source: str) -> str | None:
        call = ast.parse(source).body[0]
        assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
        return _problem(call.value)

    assert problem("report.write_text(text)")
    assert problem("open(path, 'w')")
    assert problem("path.open('a')")
    assert problem("os.fdopen(fd, newline='')")
    assert problem("subprocess.run(['git'], capture_output=True, text=True)")
    assert problem("tempfile.NamedTemporaryFile('w')")
    assert problem("logging.FileHandler(path)")
    assert not problem("report.write_text(text, encoding='utf-8')")
    assert not problem("open(path, 'rb')")
    assert not problem("os.open(path, os.O_RDONLY)")
    assert not problem("subprocess.run(['git'], capture_output=True)")
    assert not problem("subprocess.run(['git'], encoding='utf-8', errors='replace')")


# A slice of DataLab, run where the locale's encoding can't write "→" (as
# cp1252 can't): the Safety check's report, the catalog and settings.toml.
SLICE = r"""
import json, locale, sys, tempfile
from pathlib import Path

folder = Path(tempfile.mkdtemp())
try:
    (folder / "probe").write_text("→")
except UnicodeEncodeError:
    pass
else:
    print("utf-8 locale:", locale.getencoding())
    sys.exit(0)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.safety import build_safety_router
from datalab.config import load_settings
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.safety import CheckResult, SafetyReport

ARROW = "Blocked → ok ✓ “quoted”"


class Check:
    async def run(self):
        return SafetyReport("start", "end", [CheckResult("net", "p", ARROW, "pass", ARROW)])


app = FastAPI()
app.include_router(build_safety_router(Check(), folder / "logs" / "safety.json"))
client = TestClient(app)
assert client.post("/api/safety/check").status_code == 200
assert client.get("/api/safety/last").json()["results"][0]["detail"] == ARROW

table = TableInfo("IHS_2025", "MOOD", "TABLE", ARROW, [Column("SCORE", "NUMBER", True, ARROW)])
Catalog([table]).save(folder / "catalog")
again = Catalog.load(folder / "catalog").get("IHS_2025.MOOD")
assert again is not None and again.comment == ARROW and again.columns[0].comment == ARROW

(folder / "data").mkdir()
(folder / "data" / "settings.toml").write_text(f"# {ARROW}\nport = 8799\n", encoding="utf-8")
import os
os.environ["DATALAB_DATA_DIR"] = str(folder / "data")
assert load_settings("practice").port == 8799
print("ok")
"""


def _non_utf8_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHON"))}
    env["PYTHONUTF8"] = "0"
    # Else, on Linux, Python turns the C locale into C.UTF-8 by itself.
    env["PYTHONCOERCECLOCALE"] = "0"
    if sys.platform != "win32":
        # Latin-1 where the computer has it (macOS does), else plain C: ASCII,
        # once UTF-8 mode is off. Windows' own default is cp1252.
        env["LC_ALL"] = "en_US.ISO8859-1" if sys.platform == "darwin" else "C"
        env["LANG"] = env["LC_ALL"]
    return env


def test_a_slice_of_datalab_works_when_the_locale_is_not_utf8() -> None:
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", SLICE],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=_non_utf8_environment(),
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    if result.stdout.startswith("utf-8 locale"):
        pytest.skip(f"couldn't switch off UTF-8 here ({result.stdout.strip()})")
    assert result.stdout.strip() == "ok"
