"""Asking for a key or password in a terminal, showing one * per character.

A hidden prompt (getpass) shows nothing at all, so after a paste nobody can
tell whether it worked. This one shows a * for each character, typed or
pasted, and afterwards says how many characters arrived. It never shows any
part of the value itself.

When the input isn't a terminal (a pipe, tests, CI) it reads a line as
getpass always did: no echo and no *s.
"""

from __future__ import annotations

import getpass
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

MASK_CAP = 64  # at most this many *s, then "…", so a long paste doesn't wrap
_PENDING_WAIT = 0.05  # seconds: what arrives this soon after Enter is part of a paste
_ATTEMPTS = 3


@dataclass(frozen=True)
class Entry:
    """What was entered at a masked prompt."""

    value: str
    # Input that arrived straight after Enter, as part of the same paste:
    # only line breaks and spaces (extra_breaks counts them), or more text,
    # which means the paste had more than one line.
    multiline: bool = False
    extra_breaks: int = 0


def mask(count: int) -> str:
    """What the prompt shows for `count` characters."""
    return "*" * min(count, MASK_CAP) + ("…" if count > MASK_CAP else "")


def read_masked(
    read_char: Callable[[], str],
    write: Callable[[str], None],
    pending: Callable[[], bool],
) -> Entry:
    """The masking loop, apart from the terminal.

    `read_char` returns the next character ("" at end of input), `write`
    puts text on the screen, and `pending` says whether more input is
    already waiting. Enter finishes, Backspace removes a character, Ctrl-U
    clears the line and Ctrl-C raises KeyboardInterrupt. Only *s (and the
    backspaces that remove them) are ever written.
    """
    chars: list[str] = []
    shown = ""

    def redraw() -> None:
        nonlocal shown
        new = mask(len(chars))
        keep = 0
        while keep < min(len(shown), len(new)) and shown[keep] == new[keep]:
            keep += 1
        write("\b \b" * (len(shown) - keep) + new[keep:])
        shown = new

    while True:
        char = read_char()
        if char in ("", "\x04"):  # end of input, or Ctrl-D
            write("\n")
            return Entry("".join(chars))
        if char in ("\r", "\n"):
            rest: list[str] = []
            while pending():
                more = read_char()
                if more == "":
                    break
                rest.append(more)
            write("\n")
            extra = "".join(rest)
            if extra.strip():
                return Entry("".join(chars), multiline=True)
            breaks = extra.count("\n") + extra.count("\r") - extra.count("\r\n")
            return Entry("".join(chars), extra_breaks=breaks)
        if char == "\x03":  # Ctrl-C
            write("\n")
            raise KeyboardInterrupt
        if char in ("\x7f", "\x08"):  # Backspace (Delete on a Mac keyboard)
            if chars:
                chars.pop()
        elif char == "\x15":  # Ctrl-U
            chars.clear()
        elif char == "\x1b":  # an escape sequence (an arrow key): ignored
            _skip_escape(read_char, pending)
            continue
        elif char == "\t" or (ord(char) >= 32 and ord(char) != 127):
            chars.append(char)
        else:
            continue  # other control keys: ignored
        redraw()


def _skip_escape(read_char: Callable[[], str], pending: Callable[[], bool]) -> None:
    if not pending():
        return
    if read_char() not in ("[", "O"):
        return
    while pending():
        if "@" <= read_char() <= "~":  # the sequence's final character
            return


def ask_secret(label: str) -> str:
    """Ask for a key or password; return it with spaces and line breaks at
    its ends removed, or "" when nothing was entered.

    Says how many characters arrived, never what they were. Refuses a paste
    of more than one line and asks again. Ctrl-C raises KeyboardInterrupt.
    """
    for _ in range(_ATTEMPTS):
        entry = _read(label)
        if entry.multiline:
            print(
                "That paste had more than one line, so it wasn't used. "
                "Copy just the one line and paste it again."
            )
            continue
        return confirm(entry)
    print("Nothing was saved.")
    return ""


def confirm(entry: Entry) -> str:
    """Report what arrived, without revealing any of it; return the value to save."""
    value = entry.value.strip()
    if not value:
        print("Nothing was entered, so nothing was saved.")
        return ""
    raw = entry.value
    start = raw[: len(raw) - len(raw.lstrip())]
    end = raw[len(raw.rstrip()) :] + "\n" * entry.extra_breaks
    plural = "" if len(value) == 1 else "s"
    print(f"✓ Received {len(value)} character{plural}.")
    for removed, where in ((start, "start"), (end, "end")):
        if removed:
            print(f"  Removed {_whitespace(removed)} from the {where}.")
    return value


def _whitespace(text: str) -> str:
    breaks = text.count("\n") + text.count("\r") - text.count("\r\n")
    spaces = len(text.replace("\r\n", "").replace("\n", "").replace("\r", ""))
    parts = []
    if spaces:
        parts.append("a space" if spaces == 1 else f"{spaces} spaces")
    if breaks:
        parts.append("a line break" if breaks == 1 else f"{breaks} line breaks")
    return " and ".join(parts)


def _read(label: str) -> Entry:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return Entry(getpass.getpass(f"{label} (input hidden): "))
    sys.stdout.write(f"{label}: ")
    sys.stdout.flush()
    return _read_windows() if sys.platform == "win32" else _read_posix()


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _read_posix() -> Entry:
    assert sys.platform != "win32"
    import codecs
    import select
    import termios

    fd = sys.stdin.fileno()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read_char() -> str:
        while True:
            byte = os.read(fd, 1)
            if not byte:
                return decoder.decode(b"", final=True)
            char = decoder.decode(byte)
            if char:
                return char

    def pending() -> bool:
        return bool(select.select([fd], [], [], _PENDING_WAIT)[0])

    saved = termios.tcgetattr(fd)
    quiet = termios.tcgetattr(fd)
    # No echo, a character at a time, and Ctrl-C/Ctrl-U/Ctrl-V as plain
    # characters (the loop handles them). Output processing is left on.
    quiet[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
    quiet[6][termios.VMIN] = 1
    quiet[6][termios.VTIME] = 0
    try:
        termios.tcsetattr(fd, termios.TCSANOW, quiet)
        return read_masked(read_char, _write, pending)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)


def _read_windows() -> Entry:
    assert sys.platform == "win32"
    import msvcrt

    def pending() -> bool:
        deadline = time.monotonic() + _PENDING_WAIT
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def read_char() -> str:
        while True:
            char = msvcrt.getwch()
            # Arrow and function keys come as "\x00" or "\xe0" and a scan
            # code; a typed "à" is "\xe0" on its own.
            if char == "\x00" or (char == "\xe0" and msvcrt.kbhit()):
                msvcrt.getwch()
                continue
            return char

    return read_masked(read_char, _write, pending)
