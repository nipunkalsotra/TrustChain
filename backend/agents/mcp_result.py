"""Normalize MCP adapter text content blocks into the tool's structured payload."""

import json


def tool_payload(result) -> dict:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        result = json.loads(result)
        if isinstance(result, dict):
            return result
    if isinstance(result, list):
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return tool_payload(block["text"])
                except (ValueError, TypeError, KeyError):
                    continue
    raise ValueError("MCP tool did not return a structured object")
