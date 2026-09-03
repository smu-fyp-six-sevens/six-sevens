"""
synthetic_signature.py — shared "handwritten" signature helper.

Registers the two bundled cursive fonts (Caveat, Dancing Script — both SIL Open
Font License, OFL.txt alongside each .ttf under assets/fonts/) once, and picks one
deterministically per seed, so every synthetic signature across the document set
(FATCA/CRS self-cert, ACRA certificate of incorporation, loan facility letters,
company constitution, passport, source-of-wealth financial statements, ...) looks
hand-signed rather than typed, without each generator re-registering the same
fonts or reimplementing the per-case pick.

Deliberately NOT used everywhere a document mentions "signature": several document
types explicitly print "this is computer-generated and does not require a
signature" (Certificate of Incumbency, payslips) — stamping a cursive name on those
would contradict the document's own text, so they're left alone. Bank statements
and NRIC cards likewise carry no signature field in the real templates they mirror.

Two draw paths, since the document set mixes both ReportLab styles:
  - `signature_paragraph_style()` for Platypus-flowable generators (Paragraph /
    Table / story.append(...)) — most of the document set.
  - `draw_signature()` for raw-canvas generators (coi_generator.py) that draw
    directly onto a reportlab.pdfgen.canvas.Canvas.
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_facts import seeded_rng

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
SIGNATURE_FONTS = ["Caveat", "DancingScript"]
for _name in SIGNATURE_FONTS:
    pdfmetrics.registerFont(TTFont(_name, str(FONT_DIR / f"{_name}.ttf")))


def pick_signature_font(seed: str) -> str:
    """Deterministic per-seed font pick (case id, case+signatory, ...) so a batch
    of documents doesn't all show the identical cursive "hand" on every page."""
    rng = seeded_rng(seed)
    return SIGNATURE_FONTS[rng.randrange(len(SIGNATURE_FONTS))]


def signature_paragraph_style(seed: str, size: float = 20, name: str = "signature") -> ParagraphStyle:
    return ParagraphStyle(name, fontName=pick_signature_font(seed), fontSize=size, leading=size * 1.15)


def draw_signature(c: pdfcanvas.Canvas, text: str, x: float, y: float, seed: str, size: float = 20) -> None:
    """Draw `text` in a cursive font at (x, y) baseline on a raw canvas."""
    c.setFont(pick_signature_font(seed), size)
    c.drawString(x, y, text)
