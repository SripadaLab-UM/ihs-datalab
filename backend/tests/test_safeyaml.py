"""YAML from text DataLab didn't write: refused before anything is built."""

from __future__ import annotations

import time

import pytest

from datalab import safeyaml

# Seven levels of nine: 9**7 (about 4.8 million) strings from a few hundred
# bytes. yaml.safe_load builds it; walking it takes minutes and gigabytes.
BOMB = (
    "name: bomb\n"
    "a: &a [x, x, x, x, x, x, x, x, x]\n"
    "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
    "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
    "d: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
    "e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d]\n"
    "f: &f [*e, *e, *e, *e, *e, *e, *e, *e, *e]\n"
    "steps: [*f, *f, *f, *f, *f, *f, *f, *f, *f]\n"
)


def refused(text: str, **kwargs) -> safeyaml.YamlRefused:
    with pytest.raises(safeyaml.YamlRefused) as caught:
        safeyaml.load(text, max_bytes=kwargs.pop("max_bytes", 256 * 1024), **kwargs)
    return caught.value


def test_plain_yaml_loads():
    assert safeyaml.load("a: 1\nb: [x, {c: d}]\n", max_bytes=100) == {
        "a": 1,
        "b": ["x", {"c": "d"}],
    }


def test_an_alias_bomb_is_refused_at_once():
    assert len(BOMB) < 400
    started = time.monotonic()
    problem = refused(BOMB)
    assert time.monotonic() - started < 0.5
    assert "anchors or aliases" in problem.message and problem.line == 2


@pytest.mark.parametrize(
    ("text", "message", "line"),
    [
        ("a: &x 1\n", "anchors or aliases", 1),
        ("a: 1\nb: *x\n", "anchors or aliases", 2),  # refused before it's looked up
        ("a: 1\n<<: {b: 2}\n", "plain names", 2),  # a merge key, even without an alias
        ("? [a, b]\n: 1\n", "plain names", 1),  # a list as a key
        ("? {a: 1}\n: 1\n", "plain names", 1),
        ("status: draft\nx: 1\nstatus: reviewed\n", "'status' is given twice", 3),
        ("m:\n  k: 1\n  k: 2\n", "'k' is given twice", 3),  # at any depth
        ("l: [{k: 1, k: 2}]\n", "'k' is given twice", 1),
        # Different text for the same key: the dict would keep only the last.
        ("1: a\n0x1: b\n", "'0x1' is given twice (as '1' too", 2),
        ("yes: a\ntrue: b\n", "'true' is given twice (as 'yes' too", 2),
        ("~: a\nnull: b\n", "'null' is given twice (as '~' too", 2),
        ("0.5: a\n.5: b\n", "'.5' is given twice (as '0.5' too", 2),
        ("m:\n  1: a\n  1.0: b\n", "'1.0' is given twice", 3),
        ("1: a\ntrue: b\n", "'true' is given twice", 2),  # True == 1 in a dict
    ],
)
def test_what_isnt_plain_data_is_refused(text, message, line):
    problem = refused(text)
    assert message in problem.message and problem.line == line


def test_keys_that_only_look_alike_are_both_kept():
    # The text "1" and the number 1 are two keys in the dict too.
    assert safeyaml.load("'1': a\n1: b\n", max_bytes=100) == {"1": "a", 1: "b"}
    assert safeyaml.load("yes: c\n'yes': d\n", max_bytes=100) == {True: "c", "yes": "d"}


def test_size_and_depth_are_capped():
    assert "larger than 1 KB" in refused("a: " + "x" * 2000 + "\n", max_bytes=1024).message
    # The top-level mapping is one level.
    deep = "a: " + "[" * 32 + "]" * 32 + "\n"
    assert "nested more than 32 levels" in refused(deep).message
    assert safeyaml.load("a: " + "[" * 31 + "]" * 31 + "\n", max_bytes=1024)
    assert "nested more than 3" in refused("a: {b: {c: {d: 1}}}\n", max_depth=3).message


@pytest.mark.parametrize(
    ("max_bytes", "said"),
    [(100, "100 bytes"), (1, "1 byte"), (1500, "1,500 bytes"), (2048, "2 KB"), (1024**2, "1 MB")],
)
def test_the_size_limit_is_said_as_it_is_never_rounded_down(max_bytes, said):
    assert refused("a: " + "x" * (max_bytes + 10) + "\n", max_bytes=max_bytes).message == (
        f"It's larger than {said}."
    )


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("a: 1\nb: \ud800\n", 2),  # in the text itself, as JSON can carry it
        ("\udfff: 1\n", 1),
        ('a: 1\nb: "\\ud800"\n', 2),  # a YAML escape that makes one
        ('"\\udc00x": 1\n', 1),
        # PyYAML keeps each half of an escaped pair on its own.
        ('a: "\\ud83d\\ude00"\n', 1),
    ],
)
def test_text_that_isnt_utf8_is_refused_not_an_error(text, line):
    problem = refused(text)
    assert "isn't text" in problem.message and problem.line == line


def test_characters_beyond_the_bmp_are_fine_written_or_escaped():
    assert safeyaml.load('a: "\\U0001F600"\nb: \U0001f600\n', max_bytes=100) == {
        "a": "\U0001f600",
        "b": "\U0001f600",
    }


def test_a_value_python_wont_build_is_refused_not_an_error():
    assert refused("a: " + "1" * 5000 + "\n").message == "A number in it is too long to read."
    assert "can't be read" in refused("2025-13-45: a\n").message
