"""Per-document-type adjustments to layout detection.

A profile drops signals that a known document type explains by design. It
applies only when the classifier is confident in the type; otherwise every
document gets the same generic checks. Rules stay about structure (cards,
tables, forms), not one template's coordinates, so they carry over to other
issuers of the same document type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...config import settings
from .common import LayoutAnomaly


def _indent_against_one_line(anomaly: LayoutAnomaly) -> bool:
    """An indent whose expected edge comes from one other line, and that line disagrees.

    On an identity card the fields on each face sit in loose stacks of two
    or three, so one neighbour's left edge is not a column. Measured on the
    NRIC templates: removes the back-of-card "NRIC No." finding every card
    produced, at the cost of a moved card title that has the same evidence.
    """
    evidence = anomaly.evidence
    return (
        evidence.get("kind") == "indentation"
        and evidence.get("comparable_count", 99) <= 2
        and evidence.get("matching_count", 99) < 2
    )


def _chart_axis_label(anomaly: LayoutAnomaly) -> bool:
    """A month or value label on a usage chart ("MAR", "120") lines up with the
    chart, not with the text around it. Measured on the proof-of-address
    templates: removes the chart-label finding and no caught shift."""
    return (
        anomaly.evidence.get("kind") == "indentation"
        and bool(anomaly.evidence.get("unanchored"))
        and sum(ch.isalnum() for ch in anomaly.text) <= 3
    )


@dataclass(frozen=True)
class LayoutProfile:
    document_type: str
    # Each rule takes a finding and returns True to drop it.
    explained: tuple[Callable[[LayoutAnomaly], bool], ...] = ()

    def explains(self, anomaly: LayoutAnomaly) -> bool:
        return any(rule(anomaly) for rule in self.explained)


PROFILES: dict[str, LayoutProfile] = {
    "nric": LayoutProfile("nric", explained=(_indent_against_one_line,)),
    "proof_of_address": LayoutProfile("proof_of_address", explained=(_chart_axis_label,)),
}


def confident_type(
    document_type: str | None, classification_confidence: float | None
) -> str | None:
    """The document type when the classifier is sure of it, else None.

    Same gate as the classification stage: below the confidence threshold
    the type is a guess, and a wrong profile or reference would hide real
    findings.
    """
    if document_type is None or classification_confidence is None:
        return None
    if classification_confidence < settings.CLASSIFICATION_CONFIDENCE_THRESHOLD:
        return None
    return document_type


def profile_for(
    document_type: str | None, classification_confidence: float | None
) -> LayoutProfile | None:
    """The profile for a confidently classified type, or None for the generic checks."""
    known = confident_type(document_type, classification_confidence)
    return PROFILES.get(known) if known else None
