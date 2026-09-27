"""YAML from text DataLab didn't write: an agent's, or the lab repos'.

`yaml.safe_load` builds no objects it shouldn't, but it still follows
anchors and aliases: a few hundred bytes of nested aliases stand for a
structure far too large to walk, and whatever reads the result then takes
minutes and gigabytes. It also keeps the last of two equal keys without a
word, which lets a second `status: reviewed` line hide behind the first.

`load` reads the event stream first and refuses, before anything is built:

- text over `max_bytes`, or nested deeper than `max_depth`;
- anchors (`&a`) and aliases (`*a`), and so merge keys (`<<: *a`);
- then, on the composed nodes: a key that isn't a plain scalar (a merge
  key, or a mapping or list used as a key), and a key given twice.

What's left is plain data, built by the safe loader.
"""

from __future__ import annotations

from typing import Any

import yaml

MAX_DEPTH = 32


class YamlRefused(ValueError):
    """Why the text was refused; `line` is 1-based, or None for the whole text."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


def load(text: str, *, max_bytes: int, max_depth: int = MAX_DEPTH) -> Any:
    """The data in `text`, or YamlRefused (yaml.YAMLError if it isn't YAML)."""
    if len(text.encode()) > max_bytes:
        raise YamlRefused(f"It's larger than {max_bytes // 1024} KB.")
    depth = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
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
    for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
        if document is not None:
            _check_keys(document)
    return yaml.safe_load(text)


def _check_keys(root: yaml.Node) -> None:
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode) or key.tag == "tag:yaml.org,2002:merge":
                    message = "Keys must be plain names (no <<, lists or maps)."
                    raise YamlRefused(message, _line(key))
                if key.value in seen:
                    raise YamlRefused(f"{key.value!r} is given twice.", _line(key))
                seen.add(key.value)
                stack.append(value)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)


def _line(item: yaml.Event | yaml.Node) -> int | None:
    mark = item.start_mark
    return mark.line + 1 if mark is not None else None
