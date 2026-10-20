"""Write a dev copy's settings.toml from the real DataLab's (scripts/dev-copy.sh).

Only the lab's settings are copied: the database's address and account, the
lab repos and GitHub App, the Dropbox app, Windows' admin page. Nothing else
of the real DataLab's (no data, no catalog folder, no agent image or model
overrides) reaches the dev copy. It gets its own port, and never checks for
updates: it runs the checkout, not a release.

Usage: python3 dev_copy_settings.py <real settings.toml> <dev settings.toml> <port>
An existing dev settings.toml is left as it is.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

# The sections a lab's settings are made of (the install page's defaults).
COPIED = ("oracle", "repos", "dropbox", "windows")


def dev_settings(real: dict, port: int) -> dict:
    copied = {name: real[name] for name in COPIED if isinstance(real.get(name), dict)}
    return {
        "port": port,
        **copied,
        "updates": {"check_on_start": False, "check_every_hour": False},
    }


def to_toml(settings: dict) -> str:
    """TOML for these settings' shapes: top-level values, then tables of
    strings, numbers, booleans and lists of strings."""
    lines = [
        "# A dev copy of the real DataLab (scripts/dev-copy.sh): the lab's settings,",
        "# copied once from the real DataLab's. Edit freely; it's only this copy's.",
    ]
    tables = {k: v for k, v in settings.items() if isinstance(v, dict)}
    for key, value in settings.items():
        if key not in tables:
            lines.append(f"{key} = {_value(value)}")
    for name, table in tables.items():
        lines += ["", f"[{name}]"]
        lines += [f"{key} = {_value(value)}" for key, value in table.items()]
    return "\n".join(lines) + "\n"


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "[" + ", ".join(_string(v) for v in value) + "]"
    raise ValueError(f"Can't copy a setting of this kind: {value!r}")


def _string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    if any(ord(c) < 32 for c in escaped):
        raise ValueError("A setting has a control character in it.")
    return f'"{escaped}"'


def main(argv: list[str]) -> int:
    real_file, dev_file, port = Path(argv[1]), Path(argv[2]), int(argv[3])
    if dev_file.exists():
        print(f"Using the dev copy's own settings: {dev_file}")
        return 0
    if not real_file.exists():
        print(
            f"No real DataLab settings at {real_file}: install DataLab first, or write the "
            "dev copy's settings.toml yourself.",
            file=sys.stderr,
        )
        return 1
    real = tomllib.loads(real_file.read_text(encoding="utf-8"))
    text = to_toml(dev_settings(real, port))
    tomllib.loads(text)  # what's written must read back
    dev_file.parent.mkdir(parents=True, exist_ok=True)
    dev_file.write_text(text, encoding="utf-8")
    print(f"Copied the lab's settings ({', '.join(n for n in COPIED if n in real)}) to {dev_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
