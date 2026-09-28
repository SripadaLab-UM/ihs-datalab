"""The masked prompt `datalab setup` uses for keys and passwords."""

from __future__ import annotations

import io
import os
import sys

import pytest

from datalab import secret_prompt
from datalab.secret_prompt import MASK_CAP, Entry, ask_secret, confirm, mask, read_masked

SECRET = "sk-test-not-a-real-key"


class FakeTerminal:
    """Input as bursts: the characters of one burst arrive together (a paste,
    or a single key press); the next burst only once they're all read."""

    def __init__(self, *bursts: str):
        self.bursts = [list(b) for b in bursts if b]
        self.output: list[str] = []

    def read_char(self) -> str:
        while self.bursts and not self.bursts[0]:
            self.bursts.pop(0)
        return self.bursts[0].pop(0) if self.bursts else ""

    def pending(self) -> bool:
        return bool(self.bursts and self.bursts[0])

    def write(self, text: str) -> None:
        self.output.append(text)

    def run(self) -> Entry:
        return read_masked(self.read_char, self.write, self.pending)

    @property
    def screen(self) -> str:
        """What's left on the line, backspaces applied."""
        line: list[str] = []
        for char in "".join(self.output):
            if char == "\b":
                line.pop()
            else:
                line.append(char)
        return "".join(line).replace(" ", "")  # "\b \b" leaves a blank to erase

    @property
    def written(self) -> str:
        return "".join(self.output)


def keys(text: str) -> list[str]:
    return list(text)  # one burst per key press


def test_typing_shows_a_star_per_character():
    term = FakeTerminal(*keys("abc"), "\r")
    assert term.run() == Entry("abc")
    assert term.screen == "***\n"


def test_backspace_removes_one_and_ctrl_u_clears():
    term = FakeTerminal(*keys("abcd"), "\x7f", "\x08", *keys("x"), "\r")
    assert term.run().value == "abx"
    assert term.screen == "***\n"
    term = FakeTerminal(*keys("abc"), "\x15", *keys("z"), "\r")
    assert term.run().value == "z"
    assert term.screen == "*\n"
    # Backspace on an empty line does nothing.
    assert FakeTerminal("\x7f", "q", "\r").run().value == "q"


def test_a_paste_arrives_as_a_burst_of_stars():
    term = FakeTerminal(SECRET, "\r")
    assert term.run().value == SECRET
    assert term.screen == "*" * len(SECRET) + "\n"
    # A paste that ends with its own line break finishes the entry.
    term = FakeTerminal(SECRET + "\n")
    assert term.run() == Entry(SECRET)


def test_the_stars_stop_at_the_cap():
    long = "k" * 200
    term = FakeTerminal(long, "\r")
    assert term.run().value == long
    assert term.screen == "*" * MASK_CAP + "…\n"
    assert mask(MASK_CAP) == "*" * MASK_CAP
    assert mask(MASK_CAP + 1) == "*" * MASK_CAP + "…"
    # Back under the cap, the "…" goes again.
    term = FakeTerminal("k" * (MASK_CAP + 1), "\x7f", "\r")
    term.run()
    assert term.screen == "*" * MASK_CAP + "\n"


def test_ctrl_c_aborts():
    term = FakeTerminal(*keys("ab"), "\x03", *keys("cd"))
    with pytest.raises(KeyboardInterrupt):
        term.run()
    assert term.written.endswith("\n")


def test_utf8_characters_are_one_star_each():
    term = FakeTerminal("pässwörd€😀", "\r")
    assert term.run().value == "pässwörd€😀"
    assert term.screen == "*" * 10 + "\n"


def test_arrow_keys_and_other_controls_are_ignored():
    term = FakeTerminal("a", "\x1b[D", "\x1bOA", "\x01", "b", "\r")
    assert term.run().value == "ab"


def test_end_of_input_finishes():
    assert FakeTerminal("ab").run().value == "ab"
    assert FakeTerminal("ab", "\x04").run().value == "ab"


def test_a_multi_line_paste_is_noticed():
    assert FakeTerminal(SECRET + "\nsecond line\n").run().multiline
    # Only more line breaks after it: just a trailing line break.
    entry = FakeTerminal(SECRET + "\n\n").run()
    assert not entry.multiline and entry.value == SECRET and entry.extra_breaks == 1


def test_the_value_is_never_written():
    for bursts in ([SECRET, "\r"], [*keys(SECRET), "\r"], [SECRET + "\nmore"]):
        term = FakeTerminal(*bursts)
        term.run()
        assert set(term.written) <= set("*…\b \n")


def test_confirm_says_how_many_never_what(capsys):
    assert confirm(Entry(SECRET)) == SECRET
    out = capsys.readouterr().out
    assert out == f"✓ Received {len(SECRET)} characters.\n"
    assert confirm(Entry("x")) == "x"
    assert "Received 1 character." in capsys.readouterr().out


def test_confirm_strips_spaces_and_line_breaks_and_says_so(capsys):
    assert confirm(Entry(f"  {SECRET} \n", extra_breaks=1)) == SECRET
    out = capsys.readouterr().out
    assert f"Received {len(SECRET)} characters." in out
    assert "Removed 2 spaces from the start." in out
    assert "Removed a space and 2 line breaks from the end." in out
    assert SECRET not in out and "sk-" not in out and "key" not in out


def test_nothing_entered(capsys):
    for value in ("", "   ", "\t"):
        assert confirm(Entry(value)) == ""
        assert capsys.readouterr().out == "Nothing was entered, so nothing was saved.\n"


def test_a_multi_line_paste_is_refused_and_asked_again(monkeypatch, capsys):
    entries = iter([Entry(SECRET, multiline=True), Entry(SECRET)])
    monkeypatch.setattr(secret_prompt, "_read", lambda label: next(entries))
    assert ask_secret("U-M GPT API key") == SECRET
    out = capsys.readouterr().out
    assert "more than one line" in out and SECRET not in out

    monkeypatch.setattr(secret_prompt, "_read", lambda label: Entry("a\nb", multiline=True))
    assert ask_secret("U-M GPT API key") == ""
    assert capsys.readouterr().out.endswith("Nothing was saved.\n")


def test_off_a_terminal_it_reads_a_line_as_getpass_did(monkeypatch, capsys):
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return f" {SECRET}"

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(secret_prompt.getpass, "getpass", fake_getpass)
    assert ask_secret("U-M GPT API key") == SECRET
    assert prompts == ["U-M GPT API key (input hidden): "]
    out = capsys.readouterr().out
    assert "*" not in out and SECRET not in out
    assert "Removed a space from the start." in out


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX terminal")
def test_a_real_pseudo_terminal(monkeypatch, capsys):
    import termios

    leader, follower = os.openpty()
    try:
        # Input written before the prompt starts would otherwise meet the
        # terminal's own line editing (canonical mode), so it starts without
        # that. (Echo is left on: the prompt must put it back.)
        attrs = termios.tcgetattr(follower)
        attrs[3] &= ~(termios.ICANON | termios.ISIG | termios.IEXTEN)
        attrs[3] |= termios.ECHO
        termios.tcsetattr(follower, termios.TCSANOW, attrs)
        before = termios.tcgetattr(follower)

        class Stdin:
            def fileno(self):
                return follower

            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdin", Stdin())
        # Typed and pasted UTF-8, a Backspace, then a paste with a line break inside.
        os.write(leader, "pä€x\x7f".encode() + b"\r")
        assert secret_prompt._read_posix() == Entry("pä€")
        os.write(leader, f"{SECRET}\nmore\n".encode())
        assert secret_prompt._read_posix().multiline
        os.write(leader, b"\x03")
        with pytest.raises(KeyboardInterrupt):
            secret_prompt._read_posix()
        assert termios.tcgetattr(follower) == before  # the terminal is put back
        out = capsys.readouterr().out
        assert "*" in out and SECRET not in out and "pä" not in out
    finally:
        os.close(leader)
        os.close(follower)


def test_ctrl_c_during_setup_cancels_cleanly(tmp_path, monkeypatch, memory_keychain, capsys):
    from datalab import setup

    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "practice"))

    def interrupted(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(secret_prompt.getpass, "getpass", interrupted)
    assert setup.setup("practice", None, update=True) == 130
    assert "Cancelled. Nothing more was saved." in capsys.readouterr().out
    assert memory_keychain.writes == 0


def test_a_password_keeps_its_spaces_but_not_its_line_breaks(capsys):
    password = " zq xv "
    assert confirm(Entry(password + "\n"), keep_spaces=True, what="password") == password
    out = capsys.readouterr().out
    assert f"Received {len(password)} characters." in out
    assert "Removed a line break from the end." in out
    assert "Your password starts and ends with a space; it was kept as typed." in out
    assert "zq" not in out and "xv" not in out

    assert confirm(Entry("pw "), keep_spaces=True, what="password") == "pw "
    out = capsys.readouterr().out
    assert "Your password ends with a space; it was kept as typed." in out
    assert "Removed" not in out
    assert confirm(Entry("\r\npw"), keep_spaces=True, what="password") == "pw"
    assert "space" not in capsys.readouterr().out
    # The U-M GPT key still loses spaces at its ends.
    assert confirm(Entry(" key ")) == "key"
    assert "kept as typed" not in capsys.readouterr().out


def test_setup_keeps_the_database_passwords_spaces(tmp_path, monkeypatch, memory_keychain):
    from datalab import setup

    monkeypatch.delenv("DATALAB_ORACLE_PASSWORD", raising=False)
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "real"))
    lab = tmp_path / "lab.toml"
    lab.write_text('[oracle]\nhost = "h"\nservice = "s"\nuser = "U"\nallowed_schemas = ["X"]\n')
    answers = iter([f" {SECRET} ", " pass word "])
    monkeypatch.setattr(secret_prompt.getpass, "getpass", lambda prompt: next(answers))
    assert setup.setup("real", lab, update=True) == 0
    assert SECRET in memory_keychain.saved.values()
    assert memory_keychain.saved[("datalab-oracle", "U")] == " pass word "


class FakeConsole:
    """msvcrt.getwch and kbhit: keys as bursts, like FakeTerminal."""

    def __init__(self, *bursts: str):
        self.term = FakeTerminal(*bursts)

    def keys(self) -> secret_prompt.WindowsKeys:
        return secret_prompt.WindowsKeys(self.term.read_char, self.term.pending)

    def run(self) -> Entry:
        keys = self.keys()
        return read_masked(keys.read_char, self.term.write, keys.waiting)


def test_windows_special_keys_are_ignored():
    # Left arrow, Delete, Ctrl-Right ("\xe0" prefix), F1 ("\x00" prefix).
    console = FakeConsole("a", "\xe0K", "\xe0S", "\xe0t", "\x00;", "b", "\r")
    assert console.run() == Entry("ab")
    assert console.term.screen == "**\n"
    # "\x00" is always a prefix, whatever follows.
    assert FakeConsole("\x00x", "c", "\r").run().value == "c"


def test_windows_keeps_a_typed_a_grave():
    # On its own, and followed at once by a character that isn't a scan code.
    assert FakeConsole("\xe0", "b", "\r").run().value == "àb"
    assert FakeConsole("\xe0b\xe0", "\r").run().value == "àbà"
    console = FakeConsole("x\xe0\r")  # a paste ending in "à" and a line break
    assert console.run() == Entry("xà")
    assert console.term.screen == "**\n"
