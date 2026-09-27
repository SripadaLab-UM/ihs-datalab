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
    ],
)
def test_what_isnt_plain_data_is_refused(text, message, line):
    problem = refused(text)
    assert message in problem.message and problem.line == line


def test_size_and_depth_are_capped():
    assert "larger than 1 KB" in refused("a: " + "x" * 2000 + "\n", max_bytes=1024).message
    # The top-level mapping is one level.
    deep = "a: " + "[" * 32 + "]" * 32 + "\n"
    assert "nested more than 32 levels" in refused(deep).message
    assert safeyaml.load("a: " + "[" * 31 + "]" * 31 + "\n", max_bytes=1024)
    assert "nested more than 3" in refused("a: {b: {c: {d: 1}}}\n", max_depth=3).message
