"""
Reading what a model or tool returned: its text, a JSON object out of that text,
and a check of that object against the shape the caller expects. Nothing here raises.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from contextvars import ContextVar
from typing import Annotated, Any, Generic, Literal, TypeVar

from langchain_core.messages import AIMessage
from pydantic import BaseModel, BeforeValidator, ValidationError

from .run_context import record_repair

logger = logging.getLogger("classifier_pipeline.llm_io")

T = TypeVar("T", bound=BaseModel)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def message_text(content: Any) -> str:
    """The text of a reply's content, which is a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block if isinstance(block, str) else block.get("text", "") if isinstance(block, dict) else ""
        for block in content
    )


def thinking_summaries(message: object) -> list[str]:
    """
    The summaries of a reply's reasoning (its `thinking` blocks), for the traces; empty when the model didn't think.
    Never send them anywhere else, and never edit or drop thinking blocks in a conversation that continues: a block is
    only valid in the unchanged conversation that produced it.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    return [
        block["thinking"].strip() for block in content
        if isinstance(block, dict) and block.get("type") == "thinking" and str(block.get("thinking") or "").strip()
    ]


def parse_json_object(text: str) -> dict | None:
    """
    The JSON object in a reply, tolerating <think> blocks, a code fence and prose around it.
    None if there isn't one (a list or a bare value is not an object).
    """
    text = _FENCE_RE.sub("", _THINK_RE.sub("", text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    parsed: Any = None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        try:
            # Prose after the object that itself contains a brace: read just the first object.
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
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


def salvage_entries(text: str, key: str) -> dict[str, Any]:
    """
    The complete `"name": value` entries of the JSON object under `key` in a reply that was cut off before
    it closed. Empty when there are none. The entry being written when the reply stopped is left out.
    """
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', text)
    if not match:
        return {}
    decoder = json.JSONDecoder()
    index, found = match.end(), {}
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= len(text) or text[index] != '"':
            break
        try:
            name, index = decoder.raw_decode(text, index)
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text) or text[index] != ":":
                break
            index += 1
            while index < len(text) and text[index].isspace():
                index += 1
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        found[str(name)] = value
    return found


def forced_args(response: Any, tool_name: str) -> dict | None:
    """The arguments of the tool the model was made to call, or None when it didn't."""
    for call in getattr(response, "tool_calls", None) or []:
        if call.get("name") == tool_name and isinstance(call.get("args"), dict):
            return call["args"]
    return None


def was_cut_off(response: Any) -> bool:
    metadata = getattr(response, "response_metadata", None) or {}
    return metadata.get("stop_reason") == "max_tokens"


class ModelRefused(Exception):
    """Claude declined the request (stop_reason "refusal"). The message is written for a loan officer."""


def refusal_reason(metadata: dict | None) -> str | None:
    """Why Claude declined, from a reply's metadata, or None when it didn't."""
    if not metadata or metadata.get("stop_reason") != "refusal":
        return None
    details = metadata.get("stop_details")
    category = details.get("category") if isinstance(details, dict) else getattr(details, "category", None)
    kind = f" ({category} safety category)" if category else ""
    return f"Claude declined to process this content{kind}. Review the document manually."


# --- field types that accept what a model tends to send ----------------------------------------


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    raise ValueError("expected text")


def _to_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip()]
    raise ValueError("expected a list of text")


def _to_text_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v.strip() for k, v in value.items() if isinstance(v, str) and v.strip()}


def _to_probability(value: Any) -> float:
    """A number from 0 to 1. Text such as "high", a boolean or NaN is not one, so the field is ignored."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("expected a number")
    number = float(value)
    if number != number:
        raise ValueError("not a number")
    return max(0.0, min(1.0, number))


def _to_escalation(value: Any) -> dict | None:
    """An object, or a bare reason, which becomes one."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        return {"reason": value.strip()}
    return None


def _to_dict_or_none(value: Any) -> dict | None:
    return value if isinstance(value, dict) else None


Text = Annotated[str, BeforeValidator(_to_text)]
TextList = Annotated[list[str], BeforeValidator(_to_text_list)]
TextMap = Annotated[dict[str, str], BeforeValidator(_to_text_map)]
DictOrNone = Annotated[dict | None, BeforeValidator(_to_dict_or_none)]
Probability = Annotated[float, BeforeValidator(_to_probability)]
Escalation = Annotated[dict | None, BeforeValidator(_to_escalation)]


# The items a before-validator dropped during the current validate() call, so a drop is never silent.
_drops: ContextVar[list[str] | None] = ContextVar("llm_io_drops", default=None)


def keep_valid(item: type[BaseModel]) -> Callable[[Any], list]:
    """A before-validator for a list field: keeps the items that validate and drops the rest, instead of failing the list."""

    def check(value: Any) -> list:
        drops = _drops.get()
        if not isinstance(value, list):
            if drops is not None and value not in (None, ""):
                drops.append(f"expected a list of {item.__name__}")
            return []
        kept = []
        for raw in value:
            try:
                kept.append(item.model_validate(raw))
            except ValidationError:
                if drops is not None:
                    drops.append(f"dropped an unreadable {item.__name__}")
        return kept

    return check


# --- checking a parsed object ----------------------------------------------------------------


@dataclass
class Checked(Generic[T]):
    """
    `ok`: valid as returned. `degraded`: usable, with the parts that didn't fit replaced by defaults
    (listed in `issues`). `failed`: nothing usable; the caller applies its own fallback.
    """

    value: T | None
    status: Literal["ok", "degraded", "failed"]
    issues: list[str] = field(default_factory=list)


def validate(model: type[T], data: Any, *, site: str = "", raw: str | None = None) -> Checked[T]:
    """
    `data` against `model`. A field that doesn't fit is dropped so its default applies; a missing
    required field, or data that isn't an object, fails. Never raises. `raw` is the reply the data came
    from: when the result is not "ok", it is kept next to the repaired value in the run's audit record.
    """
    if not isinstance(data, dict):
        return _checked(site, raw, Checked(None, "failed", ["not a JSON object"]))
    working, issues = dict(data), []
    for _ in range(len(model.model_fields) + 1):
        drops: list[str] = []
        token = _drops.set(drops)
        try:
            value = model.model_validate(working)
        except ValidationError as exc:
            errors = exc.errors()
        else:
            issues = [*issues, *drops]
            return _checked(site, raw, Checked(value, "degraded" if issues else "ok", issues))
        finally:
            _drops.reset(token)
        if any(e["type"] == "missing" for e in errors):
            missing = sorted({str(e["loc"][0]) for e in errors if e["type"] == "missing" and e["loc"]})
            return _checked(site, raw, Checked(None, "failed", [*issues, f"missing {', '.join(missing)}"]))
        bad = {e["loc"][0] for e in errors if e["loc"]}
        if not bad.intersection(working):
            return _checked(site, raw, Checked(None, "failed", [*issues, "could not be read"]))
        for name in bad:
            working.pop(name, None)
            issues.append(f"ignored {name}")
    return _checked(site, raw, Checked(None, "failed", issues))


_AUDIT_CHARS = 4000


def _checked(site: str, raw: str | None, checked: Checked) -> Checked:
    if checked.status != "ok":
        logger.warning("Output of %s was %s: %s", site or "a model call", checked.status, "; ".join(checked.issues))
        record_repair({
            "site": site or "a model call",
            "status": checked.status,
            "issues": checked.issues,
            "raw": (raw or "")[:_AUDIT_CHARS],
            "used": json.dumps(checked.value.model_dump(), default=str)[:_AUDIT_CHARS] if checked.value else None,
        })
    return checked
