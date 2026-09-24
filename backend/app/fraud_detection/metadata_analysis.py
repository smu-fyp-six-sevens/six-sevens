# metadata_analysis.py
"""
Digital Authenticity Specialist — analyses a document's own file metadata
(author/creator/producer/dates/software) for signs it was fabricated or
altered after the fact, rather than genuinely produced by the party it
claims to be from.

Deliberately does NOT re-extract metadata from the file: exiftool already
runs once, up front, for every document (see ingestion.metadata_extractor /
orchestrator._ingest_document_text) and the raw result is persisted to
documents.metadata independent of classification/extraction succeeding —
this module is a pure, local analysis pass over whatever was already
captured there. See routers/detection.py's get_metadata_anomaly for the
real per-document entry point, which reads document.metadata rather than
downloading and re-processing the file.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# Canonical field name -> the exiftool tag names that can supply it, tried
# in order. exiftool normalises PDF's CreationDate/ModDate to CreateDate/
# ModifyDate (and does the same for EXIF's date family), so those are the
# primary names across both the PDF and image documents this pipeline
# actually generates; the extra aliases are defensive fallbacks grounded in
# exiftool's own documented tag names for formats not covered by this
# repo's own doc_gen_scripts today.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "author": ("Author", "Artist", "XMPAuthor"),
    "creator": ("Creator", "XMPCreator"),
    "producer": ("Producer",),
    "creation_date": ("CreateDate", "CreationDate", "DateTimeOriginal"),
    "modification_date": ("ModifyDate", "ModDate"),
    "software": ("Software", "CreatorTool", "Application"),
}

# AC2: the fixed set of metadata fields this check looks at.
SUPPORTED_METADATA_FIELDS: tuple[str, ...] = tuple(_FIELD_ALIASES)

# AC4/AC8 look at whichever of these fields actually names the generating
# tool — not just "software". A PDF's Producer/Creator fields (e.g.
# Producer: "Skia/PDF m120", Creator: "Google Docs") are where that
# information realistically shows up in practice; exiftool's "Software" tag
# is primarily an EXIF/image convention and is rarely populated on a PDF at
# all. Restricting this to "software" alone would leave AC4/AC8 almost dead
# for this pipeline's mostly-PDF document set, so this is a deliberate
# widening of a literal single-field reading — flagged here as the "changed
# it" item for this story.
_SOFTWARE_BEARING_FIELDS: tuple[str, ...] = ("software", "creator", "producer")

# AC3: seed set of placeholder/generic values that shouldn't appear as a
# real author/creator/producer on a genuine corporate document — a starter
# list (same spirit as domain_terms.py's own seed allow-list), meant to be
# extended as real false positives/negatives turn up, not an exhaustive
# rulebook.
_GENERIC_IDENTITY_PATTERN = re.compile(
    r"^\s*(user|admin|administrator|owner|test|unknown|n/?a|guest|default|untitled)\s*$",
    re.IGNORECASE,
)

# Anywhere in the value, not just as the whole of it: a value like
# "FraudTests" or "invoice_template_final" is exactly as telling as a bare
# "test", but the anchored pattern above only matches when the placeholder
# IS the entire field. Kept as a short, deliberately unambiguous token
# list — these words don't appear in a real issuer's or person's name, so
# a substring match is safe here in a way that a broader list wouldn't be.
_TEST_ARTEFACT_PATTERN = re.compile(
    r"(test|fraud|fake|dummy|sample|lorem|placeholder|template)",
    re.IGNORECASE,
)

_PLACEHOLDER_REASON = "{field} is a generic placeholder value rather than a real {kind}"
_ARTEFACT_REASON = (
    "{field} looks like a test/template artefact rather than a real {kind} — a genuine "
    "document's metadata wouldn't carry it"
)


def _value_patterns(field_label: str, kind: str) -> list[tuple[re.Pattern[str], str]]:
    return [
        (_GENERIC_IDENTITY_PATTERN, _PLACEHOLDER_REASON.format(field=field_label, kind=kind)),
        (_TEST_ARTEFACT_PATTERN, _ARTEFACT_REASON.format(field=field_label, kind=kind)),
    ]


SUSPICIOUS_VALUE_PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "author": _value_patterns("Author", "name"),
    "creator": _value_patterns("Creator", "person/application"),
    "producer": _value_patterns("Producer", "application"),
}

# The document types that should reach us exactly as their issuer
# generated them — a bank, a credit bureau, IRAS, ACRA, an employer, a
# government ID. Nobody has a legitimate reason to open one of these in an
# editor at all, so PDF_EDITOR_MARKERS below is only applied to these.
#
# Everything else is left out deliberately, not by oversight: a FATCA/CRS
# declaration is a form the applicant is *supposed* to fill in (Acrobat in
# its metadata is the expected workflow, not evidence of anything), a
# company constitution is drafted by the company's own corporate
# secretary, a source_of_wealth document may be accountant-prepared, and
# an unrecognised document gives us nothing to reason about. Flagging
# those would bury the findings that actually matter under routine ones.
#
# Values mirror extraction_prompts.KNOWN_DOCUMENT_TYPES.
INSTITUTION_ORIGINATED_DOCUMENT_TYPES: frozenset[str] = frozenset({
    "bank_statement",
    "certificate_of_incorporation",
    "certificate_of_incumbency",
    "credit_report",
    "list_of_loans",
    "notice_of_assessment",
    "payslip",
    "proof_of_address",
    "passport",
    "nric",
})

# AC4, high-confidence tier: tools that repaint or redraw a document's
# actual content. No form-filling or signing workflow rasterises a
# document, so unlike the PDF editors below these stay suspicious on every
# document type — including the fillable ones. Matched case-insensitively
# as a substring, in the order listed.
CONTENT_EDITOR_MARKERS: list[tuple[str, str]] = [
    ("photoshop", "Adobe Photoshop can repaint document content pixel-by-pixel"),
    ("gimp", "GIMP can repaint document content pixel-by-pixel"),
    ("illustrator", "Adobe Illustrator can redraw vector/PDF content"),
    ("snagit", "Snagit is a screen-capture/annotation tool, not a document originator"),
]

# AC4, lower-confidence tier: PDF editors and annotators. Completing,
# signing or annotating a form with one of these is ordinary applicant
# behaviour, so this tier ONLY applies to
# INSTITUTION_ORIGINATED_DOCUMENT_TYPES above — a bank statement that has
# been through Acrobat is worth a look; a declaration form that has been
# is just a filled-in form. Marker -> display name.
PDF_EDITOR_MARKERS: list[tuple[str, str]] = [
    # "adobe acrobat" as well as "acrobat pro": a real Acrobat install
    # writes plain "Adobe Acrobat", "Adobe Acrobat 23.0" or "Adobe Acrobat
    # Pro DC" depending on version/edition, and matching only the "pro"
    # spelling missed the most common one of the three. Deliberately NOT
    # bare "acrobat", which would also hit Acrobat Distiller — a legitimate
    # PostScript-to-PDF generator plenty of institutions produce their real
    # statements with.
    ("adobe acrobat", "Adobe Acrobat"),
    ("acrobat pro", "Acrobat Pro"),
    ("acrobat pdfmaker", "Acrobat PDFMaker"),
    ("pdf-xchange editor", "PDF-XChange Editor"),
    ("foxit phantompdf", "Foxit PhantomPDF"),
    ("nitro pro", "Nitro Pro"),
    ("pdfelement", "PDFelement"),
    ("smallpdf", "Smallpdf"),
    ("ilovepdf", "iLovePDF"),
]

# AC8: markers of AI content/document generation tools — checked in
# addition to (not instead of) SUSPICIOUS_SOFTWARE_MARKERS above, since a
# hit here needs to be recorded as an AI-generation indicator specifically,
# on top of being a regular metadata anomaly.
AI_GENERATION_SOFTWARE_MARKERS: list[tuple[str, str]] = [
    ("chatgpt", "ChatGPT"),
    ("gpt-4", "GPT-4"),
    ("gpt-5", "GPT-5"),
    ("openai", "OpenAI"),
    ("dall-e", "DALL-E"),
    ("dall·e", "DALL-E"),
    ("midjourney", "Midjourney"),
    ("stable diffusion", "Stable Diffusion"),
    ("adobe firefly", "Adobe Firefly"),
    ("canva magic", "Canva Magic Studio"),
    ("copilot", "Microsoft Copilot"),
    ("gemini", "Google Gemini"),
    ("claude", "Claude"),
]

# exiftool's own date format ("YYYY:MM:DD HH:MM:SS", with an optional
# trailing UTC offset it doesn't always include) rather than ISO 8601.
_EXIF_DATE_RE = re.compile(
    r"^(?P<year>\d{4}):(?P<month>\d{2}):(?P<day>\d{2})[ T]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)


class MetadataAnomaly(BaseModel):
    field: str
    value: str | None
    reason: str
    # How much weight this finding deserves on its own, so the lead agent
    # aggregating across checks can tell "this document was rasterised in
    # Photoshop" apart from "its author field says 'user'". "high" is a
    # finding with no innocent explanation in a KYC workflow; "low" is
    # weak corroborating evidence that shouldn't move the needle alone.
    severity: Literal["high", "medium", "low"]


class MetadataAnalysisResult(BaseModel):
    document_id: str
    # AC1/AC2/AC6: the normalised subset of raw exiftool output this check
    # actually looks at, not the full raw dict (already stored separately
    # as documents.metadata) — only the fields SUPPORTED_METADATA_FIELDS
    # names, present or not.
    extracted_metadata: dict[str, str | None]
    anomalies: list[MetadataAnomaly]
    # AC8: the subset of `anomalies` that specifically indicate AI content/
    # document generation — duplicated here (not just filterable from
    # anomalies) so a future lead agent can pull AI-generation signals
    # directly without re-scanning every anomaly's reason text.
    ai_generation_indicators: list[MetadataAnomaly]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


def _resolve_field(raw_metadata: dict, canonical: str) -> str | None:
    for tag in _FIELD_ALIASES[canonical]:
        value = raw_metadata.get(tag)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def extract_supported_metadata(raw_metadata: dict) -> dict[str, str | None]:
    """AC2: pulls the fixed, named subset of fields this check looks at out
    of exiftool's full (and very format-dependent) tag dump."""
    return {field: _resolve_field(raw_metadata, field) for field in _FIELD_ALIASES}


def _parse_exif_datetime(value: str | None) -> datetime | None:
    """
    Only the date+time component is parsed (any trailing UTC offset is
    ignored) — AC5 only cares about the two dates' ordering, and both
    values being compared come from the same file/tool run anyway, so a
    missing or inconsistent offset can't flip the comparison in practice.
    """
    if not value:
        return None
    match = _EXIF_DATE_RE.match(value.strip())
    if not match:
        return None
    try:
        return datetime(
            int(match["year"]), int(match["month"]), int(match["day"]),
            int(match["hour"]), int(match["minute"]), int(match["second"]),
        )
    except ValueError:
        return None


def _check_suspicious_values(extracted: dict[str, str | None]) -> list[MetadataAnomaly]:
    """AC3. Low severity: a placeholder author is weak corroborating
    evidence — plenty of genuine PDFs are produced by a system account."""
    anomalies: list[MetadataAnomaly] = []
    for field, patterns in SUSPICIOUS_VALUE_PATTERNS.items():
        value = extracted.get(field)
        if not value:
            continue
        for pattern, reason in patterns:
            if pattern.search(value):
                anomalies.append(
                    MetadataAnomaly(field=field, value=value, reason=reason, severity="low")
                )
                break
    return anomalies


def _match_marker(lowered: str, markers: list[tuple[str, str]]) -> str | None:
    for marker, text in markers:
        if marker in lowered:
            return text
    return None


def _check_software_markers(
    extracted: dict[str, str | None],
    document_type: str | None,
) -> tuple[list[MetadataAnomaly], list[MetadataAnomaly]]:
    """
    AC4 + AC8. Returns (all_software_anomalies, ai_generation_subset).

    At most one finding per field: the tiers below are ordered by how much
    they tell us, so the first match wins rather than flagging the same
    value two or three times over.
    """
    anomalies: list[MetadataAnomaly] = []
    ai_indicators: list[MetadataAnomaly] = []
    institution_originated = document_type in INSTITUTION_ORIGINATED_DOCUMENT_TYPES

    for field in _SOFTWARE_BEARING_FIELDS:
        value = extracted.get(field)
        if not value:
            continue
        lowered = value.lower()

        tool_name = _match_marker(lowered, AI_GENERATION_SOFTWARE_MARKERS)
        if tool_name is not None:
            anomaly = MetadataAnomaly(
                field=field,
                value=value,
                reason=f"Metadata references {tool_name}, an AI content/document generation tool",
                severity="high",
            )
            anomalies.append(anomaly)
            ai_indicators.append(anomaly)
            continue

        reason = _match_marker(lowered, CONTENT_EDITOR_MARKERS)
        if reason is not None:
            anomalies.append(
                MetadataAnomaly(field=field, value=value, reason=reason, severity="high")
            )
            continue

        if not institution_originated:
            continue

        editor_name = _match_marker(lowered, PDF_EDITOR_MARKERS)
        if editor_name is not None:
            label = (document_type or "").replace("_", " ")
            anomalies.append(
                MetadataAnomaly(
                    field=field,
                    value=value,
                    reason=(
                        f"{editor_name} can edit existing PDF content, and a {label} should "
                        "reach us exactly as its issuer generated it"
                    ),
                    severity="medium",
                )
            )

    return anomalies, ai_indicators


def _check_date_relationship(extracted: dict[str, str | None]) -> list[MetadataAnomaly]:
    """AC5. High severity: a file cannot naturally be modified before it
    was created, so unlike the software rules there's no ordinary workflow
    this could be explained away by."""
    creation = _parse_exif_datetime(extracted.get("creation_date"))
    modification = _parse_exif_datetime(extracted.get("modification_date"))
    if creation is None or modification is None or modification >= creation:
        return []
    return [
        MetadataAnomaly(
            field="modification_date",
            value=extracted.get("modification_date"),
            reason=(
                f"Modification date ({extracted['modification_date']}) is before "
                f"creation date ({extracted['creation_date']})"
            ),
            severity="high",
        )
    ]


def check_metadata_anomalies(
    document_id: str,
    raw_metadata: dict | None,
    document_type: str | None = None,
) -> MetadataAnalysisResult:
    """
    AC1/AC6/AC7: reads the document's own already-captured file metadata
    (see module docstring) and checks it for authenticity anomalies,
    entirely locally — same "no LLM needed" spirit as spelling_anomaly.py,
    since these are concrete pattern/rule checks rather than an ambiguous
    judgement call. AC7: Unable to Assess when none of the supported fields
    (AC2) resolved to anything at all.

    document_type decides whether the PDF-editor rules apply at all (see
    INSTITUTION_ORIGINATED_DOCUMENT_TYPES) — an applicant completing a
    declaration form in Acrobat is the expected workflow, so the same
    marker means nothing there and everything on a bank statement. Passed
    as the officer-confirmed type: classification is human-verified before
    a document reaches this check, so a wrong type silently applying the
    wrong rule set isn't a live concern. None (no type known, e.g. the
    dev-tool endpoint) falls back to applying only the type-independent
    rules, which is the conservative direction for a false-positive-prone
    tier.
    """
    extracted = extract_supported_metadata(raw_metadata or {})

    if not any(extracted.values()):
        reason = (
            "No metadata was captured for this document"
            if not raw_metadata
            else "None of the supported metadata fields (author, creator, producer, "
            "creation date, modification date, software) were present"
        )
        return MetadataAnalysisResult(
            document_id=document_id,
            extracted_metadata=extracted,
            anomalies=[],
            ai_generation_indicators=[],
            unable_to_assess=True,
            unable_to_assess_reason=reason,
        )

    anomalies = _check_suspicious_values(extracted)
    software_anomalies, ai_indicators = _check_software_markers(extracted, document_type)
    anomalies.extend(software_anomalies)
    anomalies.extend(_check_date_relationship(extracted))

    return MetadataAnalysisResult(
        document_id=document_id,
        extracted_metadata=extracted,
        anomalies=anomalies,
        ai_generation_indicators=ai_indicators,
        unable_to_assess=False,
    )
