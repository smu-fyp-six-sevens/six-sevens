"""Validated output contract for the extraction second-look agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SecondLookField(BaseModel):
    value: Any = None
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    source: str = ""


class SecondLookReply(BaseModel):
    fields: dict[str, SecondLookField] = Field(default_factory=dict)
