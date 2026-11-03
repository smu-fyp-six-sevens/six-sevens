"""Results of the cross-page checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from ..numerical_validation.models import Severity, SkippedCheck

SEVERITY_RANK: dict[str, int] = {"impossible": 0, "inconsistent": 1, "implausible": 2, "signal": 3}

# Where a piece of evidence points: ("after", n) is the boundary after physical page n (0 = before page 1),
# ("page", n) one page, ("pages", n, m, ...) a group of pages. Evidence with the same key is one cause.
EvidenceKey = tuple


@dataclass(frozen=True)
class Place:
    """Where on a page the evidence is printed, for the preview to box (points, origin top-left)."""
    page: int
    bbox: tuple[float, float, float, float]
    text: str


@dataclass(frozen=True)
class Evidence:
    """One check's observation, before evidence about the same page or boundary is merged."""
    check_code: str
    severity: Severity
    key: EvidenceKey
    pages: tuple[int, ...]
    summary: str
    stated: str | None = None
    expected: str | None = None
    # Empty when the check reads the page as a whole, or the page has no text layer (OCR has no positions).
    places: tuple[Place, ...] = ()


@dataclass
class CheckOutcome:
    evidence: list[Evidence] = field(default_factory=list)
    skipped: list[SkippedCheck] = field(default_factory=list)


class PageFinding(BaseModel):
    check_code: str
    # Every check that saw this cause, the lead (check_code) first, so per-check counts include the merged ones.
    check_codes: list[str] = []
    severity: Severity
    pages: list[int]  # 1-based physical pages, as an officer counts them
    summary: str
    stated: str | None = None
    expected: str | None = None
    # What other checks saw at the same page or boundary: one cause, reported once.
    evidence: list[str] = []
    # Where the merged evidence is printed, in page order.
    places: list[Place] = []


class PageConsistencyResult(BaseModel):
    document_id: str
    pages_checked: int
    findings: list[PageFinding]
    skipped_checks: list[SkippedCheck]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
