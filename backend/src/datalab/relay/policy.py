"""Which model requests a session may make.

The U-M GPT endpoint will run hosted tools (web search, remote MCP servers,
code interpreter) and fetch URLs if asked. Code inside a container could ask
directly, bypassing Codex's own settings (the spike demonstrated this), and
each of those is a way for data to leave. So the relay allows only the request
shapes Codex itself sends, and refuses everything else. See docs/SAFETY.md.
"""

from __future__ import annotations

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


class Refused(ValueError):
    """The request isn't allowed. The message says why, for logs and the agent."""


def check_responses_request(body: Any, kind: SessionKind) -> None:
    if not isinstance(body, dict):
        raise Refused("the request body must be a JSON object")
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
