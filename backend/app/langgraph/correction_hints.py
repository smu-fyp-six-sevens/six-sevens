"""
Short hints from officers' corrections (corrections_repository), for the classifier and the extractor. Each goes after
the prompt's cache breakpoint, so the cached prefix stays the same for every document.

* Classifier: when its guess (the rule classifier's top type, or a classify tool's) is the "from" side of a recurring
  correction, it is told which type officers changed it to, and what tells that type apart (SIGNALS, written by the
  team; without one the hint just names the confusion).
* Extractor: per document type, the fields officers recurringly add (look for them) and delete (extract only when the
  document shows one). Field names only.

A lookup that fails gives no hint: classification and extraction go ahead as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import TypeVar, cast

from .. import corrections_repository
from ..config import settings
from ..corrections_repository import FieldEdit, TypeCorrection

logger = logging.getLogger(__name__)

T = TypeVar("T")
# Both lookups scan audit_events, and the counts move slowly: every document in a burst shares one read.
_cache: dict[tuple, tuple[float, object]] = {}


async def _cached(key: tuple, read: Callable[[], list[T]]) -> list[T]:
    """A failed read raises and isn't cached, so the next document tries again."""
    ttl = settings.CORRECTION_HINTS_CACHE_SECONDS
    hit = _cache.get(key)
    if ttl > 0 and hit is not None and hit[0] > time.monotonic():
        return cast(list[T], hit[1])
    value = await asyncio.to_thread(read)
    if ttl > 0:
        _cache[key] = (time.monotonic() + ttl, value)
    return value

# What tells the "to" type apart from the "from" type, for confusions the team expects. Keyed (from_type, to_type).
SIGNALS: dict[tuple[str, str], str] = {
    ("certificate_of_incorporation", "certificate_of_incumbency"): (
        "lists the company's current directors, officers or shareholders and is dated recently"
    ),
    ("certificate_of_incumbency", "certificate_of_incorporation"): (
        "only records that the company was incorporated, with its registration number and date, and lists no directors"
    ),
    ("certificate_of_incorporation", "company_constitution"): (
        "sets out the company's rules in numbered articles or clauses over several pages"
    ),
    ("company_constitution", "certificate_of_incorporation"): (
        "is a short registry certificate stating the incorporation date and registration number"
    ),
    ("bank_statement", "list_of_loans"): (
        "lists the applicant's borrowings, one row per facility with the lender, limit or outstanding balance and "
        "monthly instalment, rather than one account's dated transactions"
    ),
    ("list_of_loans", "credit_report"): (
        "comes from a credit bureau, with enquiries, account status and payment history, rather than a schedule the "
        "applicant prepared"
    ),
    ("credit_report", "list_of_loans"): (
        "is a schedule the applicant or company prepared, without a bureau's enquiries or payment history"
    ),
    ("bank_statement", "source_of_wealth"): (
        "is a letter or declaration explaining where the wealth came from (a business, a property sale, an "
        "inheritance), even when statements are attached as support"
    ),
    ("source_of_wealth", "bank_statement"): (
        "is the bank's own record of one account's dated transactions and running balance, not a letter explaining them"
    ),
    ("list_of_loans", "bank_statement"): "lists dated transactions with a running balance over a statement period",
    ("bank_statement", "credit_report"): (
        "comes from a credit bureau and summarises credit accounts and enquiries rather than one account's transactions"
    ),
    ("payslip", "notice_of_assessment"): (
        "is issued by IRAS for a year of assessment and states chargeable income and tax payable"
    ),
    ("notice_of_assessment", "payslip"): "comes from an employer for one pay period, with gross pay, deductions and net pay",
    # A Singapore passport prints the NRIC number too, so that number doesn't tell them apart.
    ("passport", "nric"): (
        "is an identity card, front and back, with the holder's address on the back and no machine-readable zone"
    ),
    ("nric", "passport"): "has a machine-readable zone (two lines of '<' characters) and a passport number",
    ("proof_of_address", "bank_statement"): (
        "lists the account's dated transactions with a running balance; classify it by that, not by the address on it"
    ),
}

_LABEL = {"nric": "NRIC", "fatca_crs_declaration": "FATCA/CRS declaration"}


def label(document_type: str) -> str:
    return _LABEL.get(document_type, document_type.replace("_", " "))


def a(document_type: str) -> str:
    """The type's label with its article: "a payslip", "an NRIC", "an employment letter"."""
    name = label(document_type)
    return f"{'an' if name[:1].lower() in 'aeiou' or name == 'NRIC' else 'a'} {name}"


def _cases(n: int) -> str:
    return f"{n} case" if n == 1 else f"{n} cases"


# --- classifier -----------------------------------------------------------------------------------------------------


async def load_type_corrections() -> list[TypeCorrection]:
    try:
        return await _cached(("types",), corrections_repository.type_corrections)
    except Exception:
        logger.exception("Failed to read the classifier's corrections")
        return []


def type_hint(guess: str | None, corrections: Iterable[TypeCorrection], shown: Iterable[str] = ()) -> str:
    """The hint for a guess officers recurringly correct, or "" (no guess, no recurring pair, or already shown)."""
    if not guess or guess in set(shown):
        return ""
    lines = []
    for pair in corrections:
        if pair.from_type != guess:
            continue
        signal = SIGNALS.get((pair.from_type, pair.to_type))
        lines.append(
            f"- Officers corrected {a(pair.from_type)} to {a(pair.to_type)} in {_cases(pair.cases)}."
            + (f" {a(pair.to_type)[:1].upper()}{a(pair.to_type)[1:]} {signal}." if signal else f" Check it isn't {a(pair.to_type)}.")
        )
    if not lines:
        return ""
    return (
        f"Loan officers' corrections on earlier cases, for documents classified as {label(guess)}. This is a common "
        "confusion, not evidence about this document: decide from the document itself.\n" + "\n".join(lines)
    )


def top_type(result: object) -> str | None:
    """The top candidate's type in a classify tool's result (llm_classify_tool, vision_llm) or the rule result."""
    candidates = result.get("top_candidates") if isinstance(result, dict) else None
    first = candidates[0] if isinstance(candidates, list) and candidates else None
    value = first.get("document_type") if isinstance(first, dict) else None
    return value if isinstance(value, str) and value else None


# --- extractor ------------------------------------------------------------------------------------------------------


async def load_field_edits(document_type: str) -> list[FieldEdit]:
    try:
        return await _cached(("fields", document_type), lambda: corrections_repository.field_edits(document_type))
    except Exception:
        logger.exception("Failed to read the extractor's corrections for %s", document_type)
        return []


def field_hint(document_type: str, edits: Iterable[FieldEdit]) -> str:
    """
    The extractor's hint for this type, or "". A field officers both add and delete keeps the side more cases agree
    on, and none when they tie.
    """
    by_field: dict[str, dict[str, int]] = {}
    for edit in edits:
        if edit.document_type == document_type:
            by_field.setdefault(edit.field_name, {})[edit.edit] = edit.cases
    added, deleted = [], []
    for name, counts in sorted(by_field.items()):
        add, delete = counts.get("added", 0), counts.get("deleted", 0)
        if add > delete:
            added.append(f"`{name}` ({_cases(add)})")
        elif delete > add:
            deleted.append(f"`{name}` ({_cases(delete)})")
    if not added and not deleted:
        return ""
    lines = [f"Loan officers' corrections to earlier {label(document_type)} extractions (field names only):"]
    if added:
        lines.append(f"- They often added {', '.join(added)}: look for these, and extract them when the document shows them.")
    if deleted:
        lines.append(f"- They often deleted {', '.join(deleted)}: extract these only when the document clearly shows one.")
    lines.append("This never changes the schema above, and never lets you invent a value the document doesn't show.")
    return "\n".join(lines)
