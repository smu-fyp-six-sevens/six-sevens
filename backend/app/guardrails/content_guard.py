"""
Layer 1 of the guardrails: looks at an uploaded document, once at ingest, for text written to steer the models
that read it, and records what it found as the document's `guard_result`.

Three checks, all deterministic:

- Hidden text (PDFs): text drawn so a reader can't see it but text extraction still reads it: invisible render
  mode or zero opacity, white on a white page, under 2pt. Ingestion already drops text under a cover-up box
  (pdf_extractor._visible_segments); this covers the rest. A scan's invisible OCR layer sits over the page image
  and is not hidden text.
- Text inside images embedded in a PDF's text-layer pages, read by OCR: ingestion never reads it, but the vision
  model does.
- A scored dictionary over the text, the hidden runs and the metadata. A single word never flags: genuine KYC
  documents say "approved", "verified", "compliance" and "assistant" (a job title) all the time. A hit needs a
  phrase that only an injection uses, or words that address an AI next to a command or an outcome, or a command
  next to an outcome only a fraud check uses ("no findings", "classify as").

Hidden runs the dictionary flags are also removed from the text the models read. An injection attempt is
evidence of fraud on its own, so detection turns `guard_result` into a finding (prompt_injection_tool).
"""

from __future__ import annotations

import base64
import html
import io
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, cast

import pymupdf
from PIL import Image

GUARD_VERSION = 2  # 2: adds the embedded image text check
_MAX_SPANS = 20
_MAX_QUOTE_CHARS = 300
REMOVED_MARKER = "[hidden text removed]"

# --- normalising ------------------------------------------------------------------------------------

# Zero-width characters, bidirectional controls and soft hyphens: invisible, and enough to split a phrase.
_INVISIBLE_CHARS = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
# Cyrillic and Greek letters drawn like Latin ones ("іgnore" with a Cyrillic і).
_LOOKALIKES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l", "ո": "n", "ԛ": "q", "ԝ": "w",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "Α": "A", "Β": "B", "Ε": "E", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
})


def normalise(text: str) -> str:
    """The text as the dictionary reads it: HTML entities unescaped ("&#105;gnore"), NFKC (which also folds
    mathematical letters such as "𝗶𝗴𝗻𝗼𝗿𝗲"), invisible characters dropped, look-alike letters folded."""
    unescaped = html.unescape(text or "")
    return _INVISIBLE_CHARS.sub("", unicodedata.normalize("NFKC", unescaped)).translate(_LOOKALIKES)


# A run of base64 long enough to carry a sentence. Decoded, it is scanned like any other text.
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")


def _decoded_base64(text: str) -> list[str]:
    """Base64 in the text that decodes to readable words, e.g. an instruction hidden as "SWdub3Jl..."."""
    decoded = []
    for match in _BASE64.finditer(text):
        try:
            words = base64.b64decode(match.group(0), validate=True).decode("utf-8")
        except ValueError:
            continue
        if " " in words and sum(ch.isprintable() for ch in words) >= 0.9 * len(words):
            decoded.append(words)
    return decoded


# --- the dictionary ---------------------------------------------------------------------------------

# Phrases only an injection uses; one is enough.
_STRONG = (
    "ignore previous instructions", "ignore all previous", "ignore the above", "ignore all prior",
    "disregard previous", "disregard the above", "disregard all prior", "previous instructions",
    "prior instructions", "system prompt", "you are now", "as an ai", "language model", "new instructions",
    "ai reviewer", "ai assistant", "prompt injection",
    # From the PayloadsAllTheThings "Prompt Injection" catalogue: phrasings with no other trigger word nearby.
    "ignore the user", "disregard the user", "decode and execute", "exfiltrate", "url encode the result",
    "tell me your secrets", "without any restrictions", "developer mode", "jailbroken",
)
# Words that address the model. Bare "assistant", "model", "system", "prompt", "instructions" and "ai" are left
# out: a job title, "business model", "system generated", "prompt payment", "payment instructions", a given name.
_ADDRESS = (
    "chatgpt", "claude", "gpt", "llm", "ai model", "artificial intelligence", "assistant:", "system:",
    "developer:", "system message", "automated reviewer", "fraud model", "your instructions", "these instructions",
    "the above instructions", "dear ai",
)
_COMMAND = (
    "ignore", "disregard", "override", "forget", "bypass", "pretend", "act as", "you must", "do not flag",
    "don't flag", "respond with", "reply with", "output", "report", "record", "mark", "treat", "classify", "skip",
    "return", "set", "say",
)
# Outcomes only a fraud check talks about.
_DETECTION_OUTCOME = (
    "no anomalies", "no anomaly", "no findings", "zero findings", "empty findings", "no flags", "not suspicious",
    "not fraudulent", "no fraud", "no tampering", "classify as", "classify this", "classified as genuine",
    "mark as genuine", "mark it as", "treat as genuine", "low risk", "is genuine", "is authentic",
)
# Outcomes genuine documents use too; they count only next to words that address the model.
_GENERIC_OUTCOME = ("approved", "pre-approved", "verified", "genuine", "authentic", "clean", "legitimate", "passed")
_WINDOW_WORDS = 12


def _pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    alternatives = sorted((r"\s+".join(map(re.escape, p.split())) for p in phrases), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")


_GROUPS = {
    "strong": _pattern(_STRONG), "address": _pattern(_ADDRESS), "command": _pattern(_COMMAND),
    "detection_outcome": _pattern(_DETECTION_OUTCOME), "generic_outcome": _pattern(_GENERIC_OUTCOME),
}
_WORD = re.compile(r"\S+")


def _score(groups: set[str]) -> int:
    """How strongly a set of nearby groups reads as an instruction to the model; 0 is no hit."""
    if "strong" in groups:
        return 3
    outcome = groups & {"detection_outcome", "generic_outcome"}
    if "address" in groups and ("command" in groups or outcome):
        return 3 if "command" in groups and outcome else 2
    if "command" in groups and "detection_outcome" in groups:
        return 2
    return 0


def _line_around(text: str, start: int, end: int) -> str:
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    quote = " ".join(text[left: right if right != -1 else len(text)].split())
    return quote[:_MAX_QUOTE_CHARS]


def instruction_hits(text: str) -> list[dict[str, Any]]:
    """Places in `text` that read as an instruction to the model: each with a quote (its line) and a score."""
    # Decoded base64 is read as extra lines, so an encoded instruction is quoted in plain words.
    normalised = normalise("\n".join([text or "", *_decoded_base64(text or "")]))
    lowered = normalised.lower()
    # Quotes keep the document's capitals, unless lowering changed the length (offsets would no longer line up).
    quoted = normalised if len(normalised) == len(lowered) else lowered
    word_starts = [m.start() for m in _WORD.finditer(lowered)]

    def word_index(offset: int) -> int:
        lo, hi = 0, len(word_starts)
        while lo < hi:
            mid = (lo + hi) // 2
            if word_starts[mid] <= offset:
                lo = mid + 1
            else:
                hi = mid
        return max(0, lo - 1)

    matches = sorted(
        (m.start(), m.end(), group, word_index(m.start()))
        for group, pattern in _GROUPS.items() for m in pattern.finditer(lowered)
    )
    # One hit per line that starts one, spanning the words that made it: a long run of injected lines stays
    # one span per line rather than one span for the whole page.
    hits: dict[int, tuple[int, int, int]] = {}  # line start -> (start, end, score)
    for start, end, group, index in matches:
        nearby = [m for m in matches if abs(m[3] - index) <= _WINDOW_WORDS]
        score = _score({m[2] for m in nearby})
        if not score or group not in {"strong", "address", "command"}:
            continue
        line = lowered.rfind("\n", 0, start) + 1
        hit = (min(m[0] for m in nearby), max(m[1] for m in nearby), score)
        prev = hits.get(line)
        hits[line] = (min(prev[0], hit[0]), max(prev[1], hit[1]), max(prev[2], score)) if prev else hit
    quotes: dict[str, int] = {}
    for s, e, score in hits.values():
        quote = _line_around(quoted, s, e)
        quotes[quote] = max(score, quotes.get(quote, 0))
    return [{"quote": quote, "score": score} for quote, score in quotes.items()]


# --- hidden text (PDF) ------------------------------------------------------------------------------

_TINY_POINTS = 2.0
# Each channel at least this light reads as white on a white page.
_NEAR_WHITE = 0.92
_MIN_REMOVABLE_CHARS = 8
# A hidden span starting more than this many of its own font sizes below the run's last line starts a new run.
_RUN_BREAK_LINES = 1.5


def _rgb(color: Any) -> tuple[float, float, float] | None:
    values = tuple(float(c) for c in (color or ()))
    if len(values) == 1:
        return values[0], values[0], values[0]
    if len(values) == 3:
        return cast(tuple[float, float, float], values)
    if len(values) == 4:
        c, m, y, k = values
        return (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    return None


def _is_near_white(color: Any) -> bool:
    rgb = _rgb(color)
    return rgb is not None and min(rgb) >= _NEAR_WHITE


def _backdrops(page: pymupdf.Page) -> tuple[list[tuple[int, pymupdf.Rect]], list[tuple[int, pymupdf.Rect]]]:
    """Coloured fills and images, each with its place in the drawing order (shared with text spans)."""
    fills = [
        (d["seqno"], pymupdf.Rect(d["rect"])) for d in page.get_drawings()
        if d.get("fill") is not None and not _is_near_white(d.get("fill"))
    ]
    images = [(seqno, pymupdf.Rect(box)) for seqno, (kind, box) in enumerate(page.get_bboxlog()) if kind == "fill-image"]
    return fills, images


def _behind(seqno: int, x: float, y: float, shapes: list[tuple[int, pymupdf.Rect]]) -> bool:
    return any(s < seqno and r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1 for s, r in shapes)


def _why_hidden(span: dict, fills: list, images: list, page_rect: pymupdf.Rect) -> str | None:
    x0, y0, x1, y1 = span["bbox"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if span.get("type") == 3 or float(span.get("opacity", 1.0)) == 0:
        # A scan's OCR layer: invisible text laid over the page image.
        return None if _behind(span["seqno"], cx, cy, images) else "invisible"
    if x1 < page_rect.x0 or x0 > page_rect.x1 or y1 < page_rect.y0 or y0 > page_rect.y1:
        return "off_page"
    if float(span.get("size") or 0) < _TINY_POINTS:
        return "tiny"
    if _is_near_white(span.get("color")) and not _behind(span["seqno"], cx, cy, fills + images):
        return "white_on_white"
    return None


def hidden_runs(file_bytes: bytes) -> list[dict[str, Any]]:
    """Runs of text a reader can't see: each with its page, why it is hidden, and the span texts it joins."""
    runs: list[dict[str, Any]] = []
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        for page_number, page in enumerate(cast(Iterable[pymupdf.Page], doc), start=1):
            fills, images = _backdrops(page)
            current: dict[str, Any] | None = None
            for span in page.get_texttrace():
                text = "".join(chr(ch[0]) for ch in span["chars"]).strip()
                if not text:
                    continue
                why = _why_hidden(span, fills, images, page.rect)
                if why is None:
                    current = None
                    continue
                # Consecutive hidden spans on the same or the next line are one run, so a sentence split over
                # several spans reads whole; one further down the page starts a new run.
                top, size = span["bbox"][1], float(span.get("size") or 0)
                if current is None or top - current["bottom"] > _RUN_BREAK_LINES * max(size, 1.0):
                    current = {"page": page_number, "why": set(), "spans": []}
                    runs.append(current)
                current["why"].add(why)
                current["spans"].append(text)
                current["bottom"] = span["bbox"][3]
    for run in runs:
        run.pop("bottom", None)
        run["why"] = sorted(run["why"])
        run["text"] = " ".join(run["spans"])
    return [run for run in runs if sum(ch.isalnum() for ch in run["text"]) >= 3]


# --- text inside embedded images (PDF) --------------------------------------------------------------

# Ingestion reads only a page's text layer when it has one, so text drawn inside an image on that page (a pasted
# note, a stamp) is never screened, yet the vision model sees it. A page with no text layer is OCRed whole at
# ingestion, so its images' text is in raw_text already.
_MIN_IMAGE_PX = 48  # smaller on either side: no readable line of text
_MAX_IMAGES_OCRED = 12  # the largest first; each OCR takes about a second
# OCR misreads small lettering: a line of 12pt text in an 84px-high image came back as noise, and read
# correctly at twice the size (but not at three times). An image this short is read at both sizes.
_SMALL_IMAGE_PX = 200
# OCR reads "AI" as "Al" or "A1"; put it back where an AI is being addressed, so the dictionary sees it.
_OCR_AI = re.compile(r"\bA[l1|](?=\s+(?:reviewer|assistant|model|system|agent)\b)")


def _ocr_lines(image: Image.Image, page_number: int) -> list[str]:
    from ..ingestion.ocr_extractor import extract_image_with_ocr  # local import: loads the OCR model on first use

    passes = [image]
    if image.height < _SMALL_IMAGE_PX:
        passes.append(image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS))
    lines: list[str] = []
    for picture in passes:
        for element in extract_image_with_ocr(picture, page_number)["elements"]:
            line = _OCR_AI.sub("AI", element["text"].strip())
            if line and line not in lines:
                lines.append(line)
    return lines


def _as_rgb_image(doc: pymupdf.Document, xref: int) -> Image.Image:
    pixmap = pymupdf.Pixmap(doc, xref)
    if pixmap.n - pixmap.alpha > 3:  # CMYK
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    if pixmap.alpha:  # a logo with transparency: OCR wants plain RGB
        pixmap = pymupdf.Pixmap(pixmap, 0)
    return Image.open(io.BytesIO(pixmap.tobytes("png")))


def image_texts(file_bytes: bytes) -> list[dict[str, Any]]:
    """
    The OCR text of the images embedded in a PDF's text-layer pages: one line per OCR box, with its page. An image
    that can't be decoded is skipped and listed as {"page", "unreadable": True}, so the others are still read.
    """
    found: list[dict[str, Any]] = []
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        candidates: dict[int, tuple[int, int]] = {}  # xref -> (pixels, first page), so a repeated logo is read once
        for page_number, page in enumerate(cast(Iterable[pymupdf.Page], doc), start=1):
            if not cast(str, page.get_text("text")).strip():
                continue
            for xref, _smask, width, height, *_ in page.get_images(full=True):
                if min(width, height) >= _MIN_IMAGE_PX and xref not in candidates:
                    candidates[xref] = (width * height, page_number)
        largest = sorted(candidates.items(), key=lambda item: -item[1][0])[:_MAX_IMAGES_OCRED]
        for xref, (_, page_number) in largest:
            try:
                image = _as_rgb_image(doc, xref)
            except Exception:
                found.append({"page": page_number, "text": "", "unreadable": True})
                continue
            lines = _ocr_lines(image, page_number)
            if lines:
                found.append({"page": page_number, "text": "\n".join(lines)})
    return found


# --- the result -------------------------------------------------------------------------------------


def not_run(reason: str) -> dict[str, Any]:
    """A result for a guard that could not run: detection reads it as unable to assess, never as clean."""
    return {
        "version": GUARD_VERSION, "risk": None, "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks_run": [], "spans": [], "spans_dropped": 0, "error": reason[:300],
    }


def _risk(spans: list[dict[str, Any]]) -> str:
    """The severities in the design: an instruction in hidden text, metadata or an embedded image (which only the
    vision model would read) is high; hidden text with no instruction is medium; an instruction in visible text
    only is low (genuine forms can trip it)."""
    if any(s["caught_by"] == "dictionary" and s["where"] in ("hidden", "metadata", "image") for s in spans):
        return "high"
    if any(s["where"] == "hidden" for s in spans):
        return "medium"
    return "low" if spans else "none"


def scan(
    file_bytes: bytes, content_type: str, raw_text: str, metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """
    The document's guard_result, and the span texts to remove from what the models read (hidden runs the
    dictionary flagged). CPU-bound and opens the PDF: run through cpu_pool.run_cpu.
    """
    checks_run = ["dictionary"]
    errors: list[str] = []
    spans: list[dict[str, Any]] = []
    removals: list[str] = []
    hidden_normalised: list[str] = []

    runs: list[dict[str, Any]] = []
    if content_type == "application/pdf":
        try:
            runs = hidden_runs(file_bytes)
            checks_run.insert(0, "hidden_text")
        except Exception as exc:  # a file that won't open as a PDF: the dictionary still runs on its text
            errors.append(f"hidden text check could not run: {type(exc).__name__}")
        else:
            try:
                images = image_texts(file_bytes)
                unreadable = sum(1 for image in images if image.get("unreadable"))
                if unreadable:
                    errors.append(f"{unreadable} embedded image(s) could not be read")
                for image in images:
                    spans.extend(
                        {"quote": hit["quote"], "where": "image", "page": image["page"], "caught_by": "dictionary",
                         "score": hit["score"], "removed": False}
                        for hit in instruction_hits(image["text"])
                    )
                checks_run.insert(1, "image_text")
            except Exception as exc:
                errors.append(f"image text check could not run: {type(exc).__name__}")
    for run in runs:
        hits = instruction_hits(run["text"])
        hidden_normalised.append(" ".join(normalise(run["text"]).lower().split()))
        span = {
            "quote": " ".join(run["text"].split())[:_MAX_QUOTE_CHARS], "where": "hidden", "page": run["page"],
            "why_hidden": run["why"],
        }
        if hits:
            removable = [t for t in run["spans"] if len(t) >= _MIN_REMOVABLE_CHARS]
            removals.extend(removable)
            spans.append({**span, "caught_by": "dictionary", "score": max(h["score"] for h in hits),
                          "removed": bool(removable)})
        else:
            spans.append({**span, "caught_by": "hidden_text", "score": 1, "removed": False})

    for hit in instruction_hits(raw_text):
        # Hidden text is in raw_text too; it was counted above.
        quote = hit["quote"].lower()
        if not any(quote in hidden or hidden in quote for hidden in hidden_normalised):
            spans.append({"quote": hit["quote"], "where": "visible", "page": None, "caught_by": "dictionary",
                          "score": hit["score"]})

    for key, value in (metadata or {}).items():
        # A multi-valued field (XMP keywords, subject) comes as a list of strings: each item is one line.
        items = [value] if isinstance(value, str) else [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
        if items and not key.startswith("_"):
            spans.extend(
                {"quote": hit["quote"], "where": "metadata", "field": key, "page": None, "caught_by": "dictionary",
                 "score": hit["score"]}
                for hit in instruction_hits("\n".join(items))
            )

    spans.sort(key=lambda s: (s["caught_by"] != "dictionary", s["where"] == "visible", -s["score"]))
    result = {
        "version": GUARD_VERSION, "risk": _risk(spans), "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks_run": checks_run, "spans": spans[:_MAX_SPANS], "spans_dropped": max(0, len(spans) - _MAX_SPANS),
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result, removals


def blocking_reason(result: dict[str, Any]) -> str | None:
    """
    Why the document is stopped at ingest, before any model reads it, or None. Any text worded as an instruction
    to the AI stops it, wherever it was. Hidden text with no such wording does not: it goes on, to manual review.
    """
    span = next((s for s in result.get("spans") or [] if s.get("caught_by") == "dictionary"), None)
    if span is None:
        return None
    where = {
        "hidden": f"hidden text on page {span.get('page')}",
        "image": f"text inside an image on page {span.get('page')}",
        "metadata": f"the metadata field {span.get('field')}",
        "visible": "text in the document",
    }.get(str(span.get("where")), "text in the document")
    quote = " ".join(str(span.get("quote") or "").split())[:120]
    return f'Blocked by the content guard: {where} is worded as an instruction to the AI ("{quote}"). No AI model read this document.'


_MARKERS = re.compile(rf"(?:{re.escape(REMOVED_MARKER)}\s*)+")


def clean_text(raw_text: str, removals: list[str]) -> str:
    """The text the models read: invisible characters dropped, and each flagged hidden span replaced by a marker."""
    text = _INVISIBLE_CHARS.sub("", raw_text or "")
    for span_text in removals:
        text = text.replace(span_text, REMOVED_MARKER)
    return _MARKERS.sub(REMOVED_MARKER + "\n", text) if removals else text
