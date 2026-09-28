"""YAML from text DataLab didn't write: an agent's, or the lab repos'.

`yaml.safe_load` builds no objects it shouldn't, but it still follows
anchors and aliases: a few hundred bytes of nested aliases stand for a
structure far too large to walk, and whatever reads the result then takes
minutes and gigabytes. It also keeps the last of two equal keys without a
word, which lets a second `status: reviewed` line hide behind the first.

`load` reads the event stream first and refuses, before anything is built:

- text that can't be written as UTF-8 (a lone surrogate, which JSON can
  carry), whether in the text or from a double-quoted `"\\ud800"` escape;
- text over `max_bytes`, or nested deeper than `max_depth`;
- anchors (`&a`) and aliases (`*a`), and so merge keys (`<<: *a`);
- then, on the composed nodes: a key that isn't a plain scalar (a merge
  key, or a mapping or list used as a key), and a key given twice, as the
  same text or as text that means the same (`1` and `0x1`, `yes` and
  `true`, `~` and `null`), which would also leave only the last.

What's left is plain data, built by the safe loader.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.constructor import SafeConstructor
from yaml.resolver import Resolver

from datalab.textcheck import lone_surrogate, size_text

MAX_DEPTH = 32
# Integers: Python won't turn one of more than 4,300 decimal digits into text
# (and a long sexagesimal 1:2:3... takes quadratic time to build), so an
# integer written longer, or bigger than 14,000 bits (0x and 3,500 digits),
# is refused.
MAX_INT_CHARS = 4300
MAX_INT_BITS = 14_000
_INT_TAG = "tag:yaml.org,2002:int"
_RESOLVER = Resolver()
_TOO_LONG = "A number in it is too long to read."


class YamlRefused(ValueError):
    """Why the text was refused; `line` is 1-based, or None for the whole text."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


def load(text: str, *, max_bytes: int, max_depth: int = MAX_DEPTH) -> Any:
    """The data in `text`, or YamlRefused (yaml.YAMLError if it isn't YAML)."""
    if (not_text := lone_surrogate(text)) is not None:
        raise YamlRefused(not_text.message, not_text.line)
    if len(text.encode()) > max_bytes:
        raise YamlRefused(f"It's larger than {size_text(max_bytes)}.")
    depth = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if (
            isinstance(event, yaml.ScalarEvent)
            and (not_text := lone_surrogate(event.value)) is not None
        ):
            raise YamlRefused(not_text.message, _line(event))
        if isinstance(event, yaml.ScalarEvent) and len(event.value) > MAX_INT_CHARS:
            tag = event.tag or _RESOLVER.resolve(yaml.ScalarNode, event.value, event.implicit)
            if tag == _INT_TAG:
                raise YamlRefused(_TOO_LONG, _line(event))
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise YamlRefused(
                "Don't use YAML anchors or aliases (& and *): write each value out.",
                _line(event),
            )
        if isinstance(event, yaml.MappingStartEvent | yaml.SequenceStartEvent):
            depth += 1
            if depth > max_depth:
                raise YamlRefused(f"It's nested more than {max_depth} levels deep.", _line(event))
        elif isinstance(event, yaml.MappingEndEvent | yaml.SequenceEndEvent):
            depth -= 1
    try:
        for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
            if document is not None:
                _check_keys(document)
        data = yaml.safe_load(text)
    except YamlRefused:
        raise
    except ValueError as error:
        # What PyYAML doesn't catch: a date that doesn't exist (2025-13-45),
        # or a value its tag can't be (`!!float x`), shown shortened.
        raise YamlRefused(f"A value in it can't be read ({_shown(str(error))}).") from None
    _check_ints(data)
    return data


def _check_ints(data: Any) -> None:
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
            raise YamlRefused(_TOO_LONG)
        if isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def _shown(text: str, most: int = 80) -> str:
    """Text from the file, in a message: at most `most` characters of it."""
    return text if len(text) <= most else text[: most - 1] + "…"


def _check_keys(root: yaml.Node) -> None:
    # Keys are compared as the values they become, as the dict built from
    # them will compare them: 1, 0x1, 1.0 and true are one key there.
    constructor = SafeConstructor()
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen: dict[object, str] = {}
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode) or key.tag == "tag:yaml.org,2002:merge":
                    message = "Keys must be plain names (no <<, lists or maps)."
                    raise YamlRefused(message, _line(key))
                resolved = constructor.construct_object(key, deep=True)
                if resolved in seen:
                    first = seen[resolved]
                    same = f" (as {_shown(first)!r} too: both mean the same)"
                    same = "" if first == key.value else same
                    raise YamlRefused(f"{_shown(key.value)!r} is given twice{same}.", _line(key))
                seen[resolved] = key.value
                stack.append(value)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)


def _line(item: yaml.Event | yaml.Node) -> int | None:
    mark = item.start_mark
    return mark.line + 1 if mark is not None else None
