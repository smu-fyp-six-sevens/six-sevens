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
