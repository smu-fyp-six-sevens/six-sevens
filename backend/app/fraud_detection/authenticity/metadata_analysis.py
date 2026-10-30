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
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from ..shared.check_result import CheckResult
from ...ingestion.metadata_extractor import EXTRACTION_ERROR_KEY

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

# IMPORTANT, confirmed empirically (a constructed PDF with both a classic
# PDF Info dict and an XMP packet, run through the real exiftool -json we
# use — no -G, i.e. flattened): when a document has BOTH a classic
# /Creator (the authoring tool, e.g. "Canva") AND an XMP dc:creator (the
# person, e.g. "Jane Tan"), exiftool's flat tag name for both is the SAME
# "Creator" — and it resolved to the XMP/person value, silently discarding
# the tool name. So our "creator" field can hold a human name instead of a
# tool name whenever both are present; a real Canva-exported document is
# exactly this shape. "software" (via the CreatorTool alias, a distinct
# tag name that never collides) is what actually survives intact, which
# is why _SOFTWARE_BEARING_FIELDS scans "software" separately rather than
# treating "creator" as authoritative for "what tool made this."

# AC2: the fixed set of metadata fields this check looks at. Kept to the
# original six because it also decides Unable to Assess (AC7) — the
# provenance fields below are an enrichment, not something whose absence
# should make a document assessable or not.
SUPPORTED_METADATA_FIELDS: tuple[str, ...] = tuple(_FIELD_ALIASES)

# XMP media-management provenance. Only some producers write these at all —
# verified against real files: Word/Adobe do, ReportLab and iLovePDF don't —
# so absence means nothing and is never itself a finding.
#
# DocumentID is stamped once when the document is first created and rides
# along through later saves; InstanceID is re-stamped on every save. The two
# differing therefore means the file was written at least once after its
# original production. History* records the actual chain of save/edit
# events, including which tool performed each one.
_PROVENANCE_ALIASES: dict[str, tuple[str, ...]] = {
    "xmp_document_id": ("OriginalDocumentID", "DocumentID"),
    "xmp_instance_id": ("InstanceID",),
    "edit_history_tools": ("HistorySoftwareAgent",),
    "edit_history_actions": ("HistoryAction",),
}

# Free-text descriptive fields. No rule inspects them: they are read out for the specialist to judge
# (a subject of "modified copy" says more than any pattern could). Like the provenance fields, they are
# not among the core six, so their absence never changes whether the check can assess a document.
_DESCRIPTIVE_ALIASES: dict[str, tuple[str, ...]] = {
    "subject": ("Subject",),
    "title": ("Title",),
}
DESCRIPTIVE_METADATA_FIELDS: tuple[str, ...] = tuple(_DESCRIPTIVE_ALIASES)

_ALL_ALIASES: dict[str, tuple[str, ...]] = {**_FIELD_ALIASES, **_PROVENANCE_ALIASES, **_DESCRIPTIVE_ALIASES}

# PDF 1.0 shipped in 1993, so nothing genuine predates it. Anything earlier
# is a fabricated or corrupted value rather than an old document.
_PDF_FORMAT_EPOCH = datetime(1993, 1, 1)

# A machine-generated document is written in one pass — verified on real
# files: a ReportLab statement has CreateDate == ModifyDate exactly, and a
# single conversion pass produced a 1-second gap. A tolerance keeps that
# same-pass noise out while still catching a genuine later re-save.
_SAME_PASS_TOLERANCE_SECONDS = 60

# AC4/AC8 look at whichever of these fields actually names the generating
# tool — not just "software". A PDF's Producer/Creator fields (e.g.
# Producer: "Skia/PDF m120", Creator: "Google Docs") are where that
# information realistically shows up in practice; exiftool's "Software" tag
# is primarily an EXIF/image convention and is rarely populated on a PDF at
# all. Restricting this to "software" alone would leave AC4/AC8 almost dead
# for this pipeline's mostly-PDF document set, so this is a deliberate
# widening of a literal single-field reading — flagged here as the "changed
# it" item for this story.
#
# edit_history_tools is scanned alongside the live fields on purpose: XMP
# retains the tools that touched the file earlier even when Producer has
# since been overwritten with something innocuous, so a forger who cleans
# up the headline field can still be given away by the history.
_SOFTWARE_BEARING_FIELDS: tuple[str, ...] = (
    "software",
    "creator",
    "producer",
    "edit_history_tools",
)

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
# generated them, as a single machine-produced PDF — a bank, a credit
# bureau, IRAS, ACRA, an employer. Nobody has a legitimate reason to open
# one of these in an editor at all, so PDF_EDITOR_MARKERS below, and the
# resave/edit-history checks that key off this same set, are only applied
# to these.
#
# Everything else is left out deliberately, not by oversight: a FATCA/CRS
# declaration is a form the applicant is *supposed* to fill in (Acrobat in
# its metadata is the expected workflow, not evidence of anything), a
# company constitution is drafted by the company's own corporate
# secretary, a source_of_wealth document may be accountant-prepared, and
# an unrecognised document gives us nothing to reason about.
#
# passport/nric are excluded for a different reason, confirmed against a
# real scanned NRIC (Creator/Producer: "PDFium", no institutional producer
# at all): there is no issuer-generated PDF for an identity document in
# the first place. A physical card is always turned into a PDF by
# whatever camera/scanner app the applicant used — a common one (Adobe
# Scan, for instance) writing Acrobat-branded metadata is the NORMAL case
# here, not evidence of tampering, so this tier must not apply to them.
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
})

# AC4, high-confidence tier: tools that repaint or redraw a document's
# actual content, or design one from scratch. No form-filling or signing
# workflow explains these, so unlike the PDF editors below they stay
# suspicious on every document type — including the fillable ones. Matched
# case-insensitively as a substring, in the order listed.
CONTENT_EDITOR_MARKERS: list[tuple[str, str]] = [
    ("photoshop", "Adobe Photoshop can repaint document content pixel-by-pixel"),
    ("gimp", "GIMP can repaint document content pixel-by-pixel"),
    ("illustrator", "Adobe Illustrator can redraw vector/PDF content"),
    ("snagit", "Snagit is a screen-capture/annotation tool, not a document originator"),
    # Confirmed against a real user-supplied sample. Canva is a from-scratch
    # design tool (templates, graphics, layout) — checked against the
    # actual 13-type KYC taxonomy (extraction_prompts.KNOWN_DOCUMENT_TYPES),
    # none of which is a "design something" category, so unlike Acrobat
    # there's no legitimate reason it touches any of them.
    ("canva", "Canva is a graphic design tool, not a document originator for a KYC document"),
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


class MetadataAnalysisResult(CheckResult):
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


def _resolve_field(raw_metadata: dict, canonical: str) -> str | None:
    for tag in _ALL_ALIASES[canonical]:
        value = raw_metadata.get(tag)
        if value is None:
            continue
        # A repeated XMP property (an edit history with several save
        # events) comes back as a list — joined rather than str()'d so it
        # reads as text and still substring-matches against the tool
        # markers, instead of rendering as "['Adobe Photoshop', ...]".
        if isinstance(value, (list, tuple)):
            text = "; ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()
        if text:
            return text
    return None


def extract_supported_metadata(raw_metadata: dict) -> dict[str, str | None]:
    """AC2: pulls the fixed, named subset of fields this check looks at out
    of exiftool's full (and very format-dependent) tag dump, plus the XMP
    provenance fields used by the edit-history checks."""
    return {field: _resolve_field(raw_metadata, field) for field in _ALL_ALIASES}


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


def _check_date_relationship(
    extracted: dict[str, str | None],
    document_type: str | None,
) -> list[MetadataAnomaly]:
    """
    AC5 plus the "re-saved after creation" case.

    Modified *before* created is high severity everywhere — a file cannot
    naturally do that, so no workflow explains it away. Modified *after*
    created is ordinary for anything a human edits, so it's only reported
    for institution-issued documents, which are written in one machine pass
    and shouldn't be touched again afterwards.
    """
    creation = _parse_exif_datetime(extracted.get("creation_date"))
    modification = _parse_exif_datetime(extracted.get("modification_date"))
    if creation is None or modification is None:
        return []

    if modification < creation:
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

    if document_type not in INSTITUTION_ORIGINATED_DOCUMENT_TYPES:
        return []

    gap = (modification - creation).total_seconds()
    if gap <= _SAME_PASS_TOLERANCE_SECONDS:
        return []

    return [
        MetadataAnomaly(
            field="modification_date",
            value=extracted.get("modification_date"),
            reason=(
                f"Re-saved {_describe_gap(gap)} after it was created "
                f"({extracted['creation_date']} -> {extracted['modification_date']}), but a "
                f"{(document_type or '').replace('_', ' ')} is written once by its issuer and "
                "shouldn't be edited afterwards"
            ),
            severity="medium",
        )
    ]


def _describe_gap(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} minute(s)"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hour(s)"
    return f"{int(seconds // 86400)} day(s)"


def _check_impossible_dates(extracted: dict[str, str | None]) -> list[MetadataAnomaly]:
    """
    A date that couldn't physically belong to a real document: earlier than
    the PDF format itself, or in the future. Both mean the value was
    fabricated or a clock was tampered with, so neither has an innocent
    reading. A small future tolerance absorbs clock skew and timezone
    offsets that _parse_exif_datetime deliberately ignores.
    """
    horizon = datetime.now() + timedelta(days=1)
    anomalies: list[MetadataAnomaly] = []

    for field in ("creation_date", "modification_date"):
        raw = extracted.get(field)
        parsed = _parse_exif_datetime(raw)
        if parsed is None:
            continue
        if parsed < _PDF_FORMAT_EPOCH:
            anomalies.append(
                MetadataAnomaly(
                    field=field,
                    value=raw,
                    reason=(
                        f"{raw} predates the PDF format itself (1993) — the value cannot be "
                        "genuine"
                    ),
                    severity="high",
                )
            )
        elif parsed > horizon:
            anomalies.append(
                MetadataAnomaly(
                    field=field,
                    value=raw,
                    reason=f"{raw} is in the future — the value cannot be genuine",
                    severity="high",
                )
            )

    return anomalies


def _extract_parse_warnings(raw_metadata: dict) -> list[str]:
    """
    exiftool's -json output includes a "Warning" key (or "Warning (1)",
    "Warning (2)", ... when there are several) whenever it hit a structural
    problem parsing the file — confirmed empirically against a deliberately
    corrupted PDF, which produced {"Warning": "Invalid xref table"} in the
    real JSON output, not just its human-readable CLI text.
    """
    return [
        str(value)
        for key, value in raw_metadata.items()
        if key == "Warning" or key.startswith("Warning (")
    ]


def _check_parse_warnings(raw_metadata: dict) -> list[MetadataAnomaly]:
    """
    Confirmed against a real user-supplied sample: a genuine bank statement
    that was opened, annotated, and re-saved came back with every one of
    its original Producer/Creator/dates gone AND exiftool warning of an
    invalid xref table / a stream that wouldn't inflate — the resaving
    tool rewrote the file without properly regenerating its own internal
    structure.

    Not scoped to institution-issued types like the PDF-editor tier — this
    isn't about which tool touched the file, it's evidence the file's own
    structure is damaged, which a normal scan, export, or edit by
    mainstream software does not produce regardless of document type.
    Medium rather than high: unlike the content-editor/AI-marker/impossible
    -date rules, an innocent explanation does exist (some legacy scanners
    and fax-to-PDF gateways produce mildly malformed output on their own),
    so this is suggestive rather than conclusive on a single real sample.
    """
    warnings = _extract_parse_warnings(raw_metadata)
    if not warnings:
        return []
    joined = "; ".join(warnings)
    return [
        MetadataAnomaly(
            field="parse_warnings",
            value=joined,
            reason=(
                f"exiftool could not fully parse this file's structure ({joined}) — a "
                "normal scan, export, or edit by mainstream software doesn't produce this; "
                "it can indicate a lossy re-save that didn't properly regenerate the file"
            ),
            severity="medium",
        )
    ]


def _check_edit_history(
    extracted: dict[str, str | None],
    document_type: str | None,
) -> list[MetadataAnomaly]:
    """
    XMP provenance: DocumentID is stamped at original creation and carried
    forward, InstanceID is re-stamped on every save, so the two differing
    proves at least one save after the document was first produced.

    Institution-issued documents only — verified against a real FATCA/CRS
    form whose IDs legitimately differ because someone authored it in Word
    over an hour. Only for a document that should have been machine-written
    once does a post-creation save mean anything.
    """
    if document_type not in INSTITUTION_ORIGINATED_DOCUMENT_TYPES:
        return []

    document_id = extracted.get("xmp_document_id")
    instance_id = extracted.get("xmp_instance_id")
    if not document_id or not instance_id or document_id == instance_id:
        return []

    return [
        MetadataAnomaly(
            field="xmp_instance_id",
            value=instance_id,
            reason=(
                "XMP instance ID differs from the original document ID "
                f"({document_id}), so the file was saved at least once after it was first "
                f"produced — a {(document_type or '').replace('_', ' ')} shouldn't be"
            ),
            severity="medium",
        )
    ]


# exiftool's FileType values for raster images. These rarely carry authorship
# or editing metadata at all: screenshots, phone exports and images passed
# through messaging apps or web tools are routinely written without it.
_IMAGE_FILE_TYPES = frozenset({"PNG", "JPEG", "GIF", "WEBP", "TIFF", "BMP", "HEIC"})


def _unable_to_assess_reason(raw_metadata: dict | None) -> str:
    extraction_error = (raw_metadata or {}).get(EXTRACTION_ERROR_KEY)
    if extraction_error:
        return (
            f"The file's metadata couldn't be read when it was uploaded: {extraction_error}. "
            "This check didn't run, so it says nothing about whether the file was edited."
        )
    if not raw_metadata:
        # Documents uploaded before extraction failures were recorded: exiftool
        # always returns basic file details, so an empty record means it didn't
        # run, not that the file has no metadata.
        return (
            "No metadata was recorded for this document, which means exiftool wasn't "
            "available when it was uploaded. This check didn't run, so it says nothing "
            "about whether the file was edited."
        )
    file_type = str(raw_metadata.get("FileType") or "").upper()
    if file_type in _IMAGE_FILE_TYPES:
        return (
            f"This {file_type} image carries no authorship or editing metadata (no author, "
            "software, or creation/modification dates). That's normal for screenshots, scans, "
            "and images saved by phones, messaging apps or web tools, so there is nothing for "
            "this check to assess. Missing metadata is not a sign of tampering on its own."
        )
    return (
        "None of the supported metadata fields (author, creator, producer, creation date, "
        "modification date, software) were present"
    )


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
    parse_warning_anomalies = _check_parse_warnings(raw_metadata or {})

    # Judged on the AC2 six PLUS parse warnings: a file carrying nothing
    # but an XMP instance ID still has no authorship or dates to assess and
    # stays Unable to Assess, but a file exiftool couldn't fully parse has
    # something concrete to report even when every named field is gone —
    # confirmed against a real sample where drawing on a bank statement and
    # re-saving it stripped Producer/Creator/dates entirely. Reporting
    # "Unable to Assess" on a document we know was altered would be worse
    # than reporting nothing.
    if not any(extracted[field] for field in SUPPORTED_METADATA_FIELDS) and not parse_warning_anomalies:
        return MetadataAnalysisResult(
            document_id=document_id,
            extracted_metadata=extracted,
            anomalies=[],
            ai_generation_indicators=[],
            unable_to_assess=True,
            unable_to_assess_reason=_unable_to_assess_reason(raw_metadata),
        )

    anomalies = _check_suspicious_values(extracted)
    software_anomalies, ai_indicators = _check_software_markers(extracted, document_type)
    anomalies.extend(software_anomalies)
    anomalies.extend(_check_date_relationship(extracted, document_type))
    anomalies.extend(_check_impossible_dates(extracted))
    anomalies.extend(_check_edit_history(extracted, document_type))
    anomalies.extend(parse_warning_anomalies)

    return MetadataAnalysisResult(
        document_id=document_id,
        extracted_metadata=extracted,
        anomalies=anomalies,
        ai_generation_indicators=ai_indicators,
        unable_to_assess=False,
    )
