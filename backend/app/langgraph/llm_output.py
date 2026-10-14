"""Reading a model reply: its text, and a JSON object out of that text."""

from __future__ import annotations

import json
import re
from typing import Any


def message_text(content: Any) -> str:
    """The text of a reply's content, which is a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def parse_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a reply, tolerating a code fence. None if there isn't one."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def salvage_list(text: str, key: str) -> list[dict]:
    """
    The complete objects of the JSON array under `key` in a reply that was cut
    off before it closed (the model ran out of tokens). Empty when there are none.
    """
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not match:
        return []
    decoder = json.JSONDecoder()
    index, found = match.end(), []
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= len(text) or text[index] != "{":
            break
        try:
            item, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        if isinstance(item, dict):
            found.append(item)
    return found
