"""
Read-only evidence tools for the investigators. They do all counting and
comparing; the model only chooses what to look at. Every fact a tool returns
carries a ref id from the run's ledger, and a hard breach carries a typed
`breach_flag` (the only way a finding can reach High - see ../verify.py).
"""

from __future__ import annotations

from typing import Any

from ..evidence import CaseContext
from .common import (
    BREACH_FLAGS, COMPUTED_TOOLS, CROSS_DOCUMENT_TOOLS, IDENTITY_FLAGS, VARIANCE_FLAGS, ToolKit,
)
from .comparisons import build_comparison_tools
from .consistency import build_consistency_tools
from .cross_checks import build_cross_check_tools
from .reading import build_reading_tools
from .transactions import build_transaction_tools

__all__ = [
    "BREACH_FLAGS", "COMPUTED_TOOLS", "CROSS_DOCUMENT_TOOLS", "IDENTITY_FLAGS", "VARIANCE_FLAGS", "build_tools",
]


def build_tools(context: CaseContext, calls: list[dict[str, Any]]) -> list:
    """The investigator's tools for one hypothesis; `calls` collects what it ran."""
    kit = ToolKit(context=context, calls=calls)
    return [
        *build_reading_tools(kit),
        *build_transaction_tools(kit),
        *build_comparison_tools(kit),
        *build_consistency_tools(kit),
        *build_cross_check_tools(kit),
    ]
