"""The drawn building blocks the sections are made of: headings, the risk gauge, pills, notices, tables and cards."""

from __future__ import annotations

from typing import Literal

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

from .formatting import _text
from .theme import (
    AMBER,
    AMBER_BG,
    BLUE,
    BLUE_BG,
    CONTENT_WIDTH,
    INK,
    INK_SOFT,
    LINE,
    PANEL,
    PANEL_LINE,
    RED,
    RED_BG,
    RISK_BANDS,
    RISK_COLOURS,
    SEVERITY_COLOURS,
    SOFT,
    STYLES,
    TEAL,
    TEAL_BG,
)

class SectionHeading(Flowable):
    """A section's number and title over a thin rule, kept with what follows it."""

    def __init__(self, number: int, title: str, note: str | None = None):
        super().__init__()
        self.number, self.title, self.note = number, title, note
        self.height = 30

    def wrap(self, aW, aH):
        self.width = aW
        return aW, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(INK)
        c.roundRect(0, 9, 18, 18, 4, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(9, 15, str(self.number))
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(26, 13, self.title)
        if self.note:
            c.setFillColor(INK_SOFT)
            c.setFont("Helvetica", 8.5)
            c.drawRightString(self.width, 13, self.note)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(0, 2, self.width, 2)


class RiskGauge(Flowable):
    """The four score bands, the current one solid, with a marker at the score (the risk card's gauge)."""

    def __init__(self, score: int | None, level: str | None, width: float):
        super().__init__()
        self.score, self.level, self.width, self.height = score, level, width, 30

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c = self.canv
        gap, bar_y, bar_h = 2, 14, 8
        x = 0.0
        for name, low, high in RISK_BANDS:
            band_width = (self.width - gap * 3) * (high - low + 1) / 101
            c.setFillColor(RISK_COLOURS[name][2])
            c.setFillAlpha(1 if name == self.level else 0.35)
            c.roundRect(x, bar_y, band_width, bar_h, 2, stroke=0, fill=1)
            c.setFillAlpha(1)
            c.setFillColor(INK if name == self.level else INK_SOFT)
            c.setFont("Helvetica-Bold" if name == self.level else "Helvetica", 7)
            c.drawString(x, 3, f"{name} {low}–{high}")
            x += band_width + gap
        if self.score is not None:
            marker_x = (self.width - gap * 3) * min(max(self.score, 0), 100) / 101 + gap * _band_index(self.score)
            c.setFillColor(INK)
            c.roundRect(marker_x - 1.5, bar_y - 3, 3, bar_h + 6, 1.5, stroke=0, fill=1)


def _band_index(score: int) -> int:
    return next(i for i, (_, low, high) in enumerate(RISK_BANDS) if score <= high or i == len(RISK_BANDS) - 1)


def _pill(text: str, fill: colors.Color, ink: colors.Color, width: float = 17 * mm) -> Table:
    style = ParagraphStyle("pill_coloured", parent=STYLES["pill"], textColor=ink)
    table = Table([[Paragraph(_text(text.upper()), style)]], colWidths=[width], rowHeights=[13])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _severity_pill(severity: str) -> Table:
    fill, ink = SEVERITY_COLOURS.get(severity.lower(), SEVERITY_COLOURS["low"])
    return _pill(severity, fill, ink, width=15 * mm)


def _risk_pill(level: str) -> Table:
    fill, ink, _ = RISK_COLOURS[level]
    return _pill(level, fill, ink, width=20 * mm)


NoticeTone = Literal["info", "warning", "error", "success", "empty"]
_NOTICE_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    "info": (BLUE_BG, BLUE),
    "warning": (AMBER_BG, AMBER),
    "error": (RED_BG, RED),
    "success": (TEAL_BG, TEAL),
    "empty": (PANEL, colors.HexColor("#98a2b3")),
}


def _notice(title: str, body: str | None = None, tone: NoticeTone = "empty", body_is_markup: bool = False) -> Table:
    """
    A tinted box with a coloured edge: an empty section's placeholder, or a warning about the results shown.
    The body is plain text, escaped here, unless body_is_markup says the caller already escaped and marked it up.
    """
    fill, edge = _NOTICE_COLOURS[tone]
    content = f"<b>{_text(title)}</b>"
    if body:
        content += f"<br/><font color='{SOFT}'>{body if body_is_markup else _text(body)}</font>"
    table = Table([["", Paragraph(content, STYLES["notice"])]], colWidths=[3, CONTENT_WIDTH - 3])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), edge),
        ("BACKGROUND", (1, 0), (1, 0), fill),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def _data_table(header: list[Paragraph], rows: list[list], widths: list[float], gutter: float = 5) -> Table:
    """A header row and zebra-striped rows that repeat their header on a new page. `gutter` pads each cell's sides."""
    table = Table([header, *rows], colWidths=widths, repeatRows=1)
    commands = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), gutter),
        ("RIGHTPADDING", (0, 0), (-1, -1), gutter),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, PANEL_LINE),
    ]
    for index in range(1, len(rows) + 1):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), PANEL))
    table.setStyle(TableStyle(commands))
    return table


def _th(text: str, right: bool = False) -> Paragraph:
    return Paragraph(_text(text.upper()), STYLES["th_right" if right else "th"])


class _Mark(Flowable):
    """A drawn tick, cross or dot: Helvetica has none of those glyphs, and a symbol font isn't embedded everywhere."""

    def __init__(self, kind: Literal["tick", "cross", "dot"], colour: colors.Color, size: float = 8):
        super().__init__()
        self.kind, self.colour, self.size = kind, colour, size

    def wrap(self, aW, aH):
        return self.size, self.size + 3

    def draw(self):
        c, s = self.canv, self.size
        c.setStrokeColor(self.colour)
        c.setFillColor(self.colour)
        c.setLineWidth(1.6)
        c.setLineCap(1)
        if self.kind == "tick":
            path = c.beginPath()
            path.moveTo(0.5, s * 0.5)
            path.lineTo(s * 0.38, s * 0.15)
            path.lineTo(s - 0.5, s * 0.9)
            c.drawPath(path, stroke=1, fill=0)
        elif self.kind == "cross":
            c.line(1, 1, s - 1, s - 1)
            c.line(1, s - 1, s - 1, 1)
        else:
            c.circle(s / 2, s / 2, s * 0.32, stroke=0, fill=1)


def _check_mark(ok: bool) -> _Mark:
    return _Mark("tick", TEAL) if ok else _Mark("cross", RED)


class _Chip(Flowable):
    """A rounded label sized to its text, with an optional status dot before it."""

    def __init__(self, text: str, fill: colors.Color, ink: colors.Color, size: float = 8, dot: bool = False):
        super().__init__()
        self.text, self.fill, self.ink, self.size, self.dot = text, fill, ink, size, dot
        self.pad = size * 1.1
        self.dot_space = size * 1.2 if dot else 0
        self.width = stringWidth(text, "Helvetica-Bold", size) + 2 * self.pad + self.dot_space
        self.height = size * 2.3

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c, h = self.canv, self.height
        c.setFillColor(self.fill)
        c.roundRect(0, 0, self.width, h, h / 2, stroke=0, fill=1)
        c.setFillColor(self.ink)
        if self.dot:
            c.circle(self.pad + self.size * 0.3, h / 2, self.size * 0.3, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", self.size)
        c.drawString(self.pad + self.dot_space, h / 2 - self.size * 0.35, self.text)


class _StatCard(Flowable):
    """One summary card: a coloured strip along the top, a label with a dot, a big figure and a line under it."""

    def __init__(self, label: str, value: str, suffix: str, note: str, accent: colors.Color, width: float):
        super().__init__()
        self.label, self.value, self.suffix, self.note, self.accent = label, value, suffix, note, accent
        self.width, self.height = width, 80

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c, w, h = self.canv, self.width, self.height
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(0, 0, w, h, 6)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(colors.white)
        c.rect(0, 0, w, h, stroke=0, fill=1)
        c.setFillColor(self.accent)
        c.rect(0, h - 3.5, w, 3.5, stroke=0, fill=1)
        c.restoreState()
        c.setStrokeColor(PANEL_LINE)
        c.setLineWidth(0.7)
        c.roundRect(0, 0, w, h, 6, stroke=1, fill=0)

        c.setFillColor(self.accent)
        c.circle(12, h - 15.5, 2.6, stroke=0, fill=1)
        c.setFillColor(INK_SOFT)
        c.setFont("Helvetica-Bold", 6.8)
        c.drawString(19, h - 18, self.label)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(10, h - 42, self.value)
        if self.suffix:
            c.setFillColor(INK_SOFT)
            c.setFont("Helvetica", 9)
            c.drawString(12 + stringWidth(self.value, "Helvetica-Bold", 20), h - 42, self.suffix)
        c.setFillColor(self.accent)
        c.setFont("Helvetica-Bold", 7.5)
        # Up to two lines, broken at plain spaces (a no-break space keeps a number with its word).
        lines, line = [], ""
        for word in self.note.split(" "):
            if line and stringWidth(f"{line} {word}", "Helvetica-Bold", 7.5) > w - 20:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
        for index, text in enumerate(lines[:2]):
            c.drawString(10, 17 - index * 9.5, text)
