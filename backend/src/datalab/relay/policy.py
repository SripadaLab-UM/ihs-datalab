"""Which model requests a session may make.

The U-M GPT endpoint will run hosted tools (web search, remote MCP servers,
code interpreter) and fetch URLs if asked. Code inside a container could ask
directly, bypassing Codex's own settings (the spike demonstrated this), and
each of those is a way for data to leave. So the relay allows only the request
shapes Codex itself sends, and refuses everything else. See docs/SAFETY.md.
"""

from __future__ import annotations

import json
import re
from typing import Any

from datalab.sessions.tokens import SessionKind

ALLOWED_FIELDS = frozenset(
    {
        "model",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "store",
        "stream",
        "include",
        "prompt_cache_key",
        "text",
        "client_metadata",
        "service_tier",
        "max_output_tokens",
        "temperature",
        "top_p",
        "truncation",
    }
)
ALLOWED_INCLUDES = frozenset({"reasoning.encrypted_content"})
CLIENT_TOOL_TYPES = frozenset({"function", "custom"})
# Hosted web search is the only hosted tool, and only in research sessions.
RESEARCH_TOOL_TYPES = frozenset({"web_search", "web_search_preview"})


# Only OpenAI's GPT and o-series text models are approved for study data. The
# U-M endpoint also serves other companies' models; a request for one of
# them is refused, whatever sent it.
_OPENAI_TEXT_MODEL = re.compile(r"(gpt-[0-9][a-z0-9.-]*|o[0-9][a-z0-9-]*)", re.ASCII)
_NOT_TEXT = ("image", "audio", "realtime", "tts", "transcribe", "embedding", "search")
# Other companies' names: never approved, even inside an OpenAI-looking name.
_OTHER_VENDORS = (
    "claude", "gemini", "llama", "kimi", "deepseek", "mistral", "qwen", "grok", "oss",
    "phi", "command", "anthropic", "google", "meta",
)  # fmt: skip


class Refused(ValueError):
    """The request isn't allowed. The message says why, for logs and the agent."""


def model_allowed(model: Any, allowed: tuple[str, ...] | None = None) -> bool:
    """Is `model` approved? `allowed` (from the lab's settings) overrides the default rule."""
    if not isinstance(model, str) or not _OPENAI_TEXT_MODEL.fullmatch(model):
        return False
    if any(word in model for word in (*_NOT_TEXT, *_OTHER_VENDORS)):
        return False
    # A lab's own list can only narrow this rule, never widen it.
    return allowed is None or model in allowed


def parse_request(raw: bytes) -> Any:
    """The request body, refusing duplicate keys.

    Parsers disagree about which copy of a duplicate key wins, so a body that
    passes here could mean something else upstream.
    """

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise Refused("duplicate keys aren't allowed")
        return dict(pairs)

    return json.loads(raw, object_pairs_hook=no_duplicates)


def check_responses_request(
    body: Any, kind: SessionKind, allowed_models: tuple[str, ...] | None = None
) -> None:
    if not isinstance(body, dict):
        raise Refused("the request body must be a JSON object")
    if not model_allowed(body.get("model"), allowed_models):
        raise Refused(f"model {body.get('model')!r} isn't approved for DataLab")
    unknown = sorted(set(body) - ALLOWED_FIELDS)
    if unknown:
        raise Refused(f"field {unknown[0]!r} isn't allowed")
    if body.get("store") is not False:
        raise Refused("store must be false")
    for item in body.get("include") or []:
        if item not in ALLOWED_INCLUDES:
            raise Refused(f"include {item!r} isn't allowed")
    for tool in body.get("tools") or []:
        _check_tool(tool, kind)
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") not in (
        *CLIENT_TOOL_TYPES,
        "allowed_tools",
        *(RESEARCH_TOOL_TYPES if kind == "research" else ()),
    ):
        raise Refused("tool_choice type isn't allowed")
    for index, item in enumerate(body.get("input") or []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "item_reference":
            raise Refused("references to stored items aren't allowed")
        for field in ("content", "output"):
            _check_content(item.get(field), f"input[{index}].{field}")


def _check_tool(tool: Any, kind: SessionKind) -> None:
    kind_of_tool = tool.get("type") if isinstance(tool, dict) else None
    if kind_of_tool in CLIENT_TOOL_TYPES:
        return
    if kind_of_tool == "tool_search" and tool.get("execution") == "client":
        return  # Codex's client-side deferred tool search
    if kind == "research" and kind_of_tool in RESEARCH_TOOL_TYPES:
        return
    raise Refused(f"tool type {kind_of_tool!r} isn't allowed")


def _check_content(parts: Any, where: str) -> None:
    if not isinstance(parts, list):
        return
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "input_image":
            url = part.get("image_url") or ""
            if part.get("file_id") or not isinstance(url, str) or not url.startswith("data:"):
                raise Refused(f"{where}: images must be inline data, not links")
        if part.get("type") == "input_file" and (part.get("file_url") or part.get("file_id")):
            raise Refused(f"{where}: files must be inline, not links")
