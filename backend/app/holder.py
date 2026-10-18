"""
Who holds an account or is billed on a bank statement or utility bill: an individual or a corporate.

Extraction reads the holder as entity_name / entity_address / entity_type. What is stored keeps the field names
the rest of the app already reads: company_name and company_address for a corporate, individual_name and
individual_address for an individual, plus entity_type. Documents stored before entity_type existed are read
from those older fields (see holder_kind).
"""

from __future__ import annotations

import re
from typing import Any

ENTITY_TYPES = ("individual", "corporate")
# The types whose one holder can be either kind.
ENTITY_DOCUMENT_TYPES = frozenset({"bank_statement", "proof_of_address"})

# A legal-form word anywhere in the name: PTE LTD, LTD, LLP, BHD, INC and the like.
_CORPORATE_NAME_RE = re.compile(
    r"\b(PTE|LTD|LIMITED|LLP|LLC|BHD|BERHAD|SDN|INC|INCORPORATED|PLC|CORP|CORPORATION)\b\.?", re.IGNORECASE
)


def looks_corporate(name: object) -> bool:
    return isinstance(name, str) and _CORPORATE_NAME_RE.search(name) is not None


def holder_kind(document_type: str | None, fields: dict[str, Any] | None) -> str | None:
    """'individual' or 'corporate' for a bank statement or bill, or None when its fields don't say."""
    fields = fields or {}
    stored = str(fields.get("entity_type") or "").strip().lower()
    if stored in ENTITY_TYPES:
        return stored
    if document_type == "bank_statement":
        if fields.get("company_name"):
            return "corporate"
        if fields.get("individual_name"):
            return "individual"
    return None
