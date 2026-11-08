"""The report's palette (the app's own tokens), paragraph styles and page geometry."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm

# The app's palette (frontend/src/styles/base.css, tokens.css).
INK = colors.HexColor("#12203d")
INK_SOFT = colors.HexColor("#5b6478")
STEEL = colors.HexColor("#355f96")
LINE = colors.HexColor("#e3e2de")
PANEL = colors.HexColor("#f7f8fa")
PANEL_LINE = colors.HexColor("#e0e5eb")
TEAL = colors.HexColor("#166d5f")
TEAL_BG = colors.HexColor("#e1f1ec")
AMBER = colors.HexColor("#c6820a")
AMBER_BG = colors.HexColor("#fbf0da")
RED = colors.HexColor("#b3402a")
RED_BG = colors.HexColor("#fbe8e2")
BLUE_BG = colors.HexColor("#eef6ff")
BLUE = colors.HexColor("#2563b8")


def _hex(colour: colors.Color) -> str:
    """A colour as "#rrggbb", for <font color=...> inside Paragraph markup."""
    return "#" + colour.hexval()[2:]


SOFT = _hex(INK_SOFT)

# The crimson risk scale, light to dark, so no level reads as "safe" (tokens.css --risk-*): (badge fill, badge
# text, gauge band). Low's word is ink-soft, as on the case page.
_RISK_LOW_TINT = colors.HexColor("#fdf3f5")
_RISK_MEDIUM_TINT = colors.HexColor("#fbe3d5")
_RISK_MEDIUM_INK = colors.HexColor("#8a3f20")
_RISK_HIGH = colors.HexColor("#c4442a")
RISK_COLOURS: dict[str, tuple[colors.Color, colors.Color, colors.Color]] = {
    "Low": (_RISK_LOW_TINT, INK_SOFT, colors.HexColor("#f6c9b0")),
    "Medium": (_RISK_MEDIUM_TINT, _RISK_MEDIUM_INK, colors.HexColor("#ec9673")),
    "High": (_RISK_HIGH, colors.white, _RISK_HIGH),
    "Critical": (colors.HexColor("#7f1414"), colors.white, colors.HexColor("#7f1414")),
}
# The 0-100 score's bands (case_risk.SCORE_BANDS).
RISK_BANDS = [("Low", 0, 24), ("Medium", 25, 49), ("High", 50, 79), ("Critical", 80, 100)]

# A finding's severity on the same crimson scale: (fill, text).
SEVERITY_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    "high": (_RISK_HIGH, colors.white),
    "medium": (_RISK_MEDIUM_TINT, _RISK_MEDIUM_INK),
    "low": (_RISK_LOW_TINT, INK_SOFT),
}
# A severity's mark on white (a list's dot), the scale's colour for that level.
SEVERITY_MARKS: dict[str, colors.Color] = {
    "high": _RISK_HIGH, "medium": colors.HexColor("#ec9673"), "low": colors.HexColor("#f6c9b0"),
}
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

# The badge's colour for each stage, by who acts next (caseStages.ts tones, case-stage.css --tone-tint and
# --tone-ink): (fill, ink).
_TONES = {
    "idle": ("#f3f6fa", "#43536b"), "system": ("#e8f0fb", "#2f5fa8"), "officer": ("#fff8e8", "#8a5a00"),
    "done": ("#eaf7f3", "#17603c"), "closed": ("#e8edf3", "#34455f"), "failed": ("#fbe8e2", "#9a3522"),
}
_STAGE_TONES = {
    "open": "idle", "processing_documents": "system", "fraud_detection": "system", "pending_review": "officer",
    "documents_incomplete": "officer", "pending_evaluation": "officer",
    "case_analysed": "done", "finalised": "closed", "failed": "failed",
}
CASE_STAGE_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    stage: (colors.HexColor(_TONES[tone][0]), colors.HexColor(_TONES[tone][1])) for stage, tone in _STAGE_TONES.items()
}

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK)
STYLES = {
    "body": _BASE,
    "soft": ParagraphStyle("soft", parent=_BASE, textColor=INK_SOFT),
    "small": ParagraphStyle("small", parent=_BASE, fontSize=8.5, leading=11.5, textColor=INK_SOFT),
    "cell": ParagraphStyle("cell", parent=_BASE, fontSize=8.5, leading=11.5),
    "cell_bold": ParagraphStyle("cell_bold", parent=_BASE, fontSize=8.5, leading=11.5, fontName="Helvetica-Bold"),
    "cell_right": ParagraphStyle("cell_right", parent=_BASE, fontSize=8.5, leading=11.5, alignment=TA_RIGHT),
    "th": ParagraphStyle("th", parent=_BASE, fontSize=7.5, leading=10, fontName="Helvetica-Bold", textColor=INK_SOFT),
    "th_right": ParagraphStyle(
        "th_right", parent=_BASE, fontSize=7.5, leading=10, fontName="Helvetica-Bold", textColor=INK_SOFT,
        alignment=TA_RIGHT,
    ),
    "title": ParagraphStyle("title", parent=_BASE, fontName="Helvetica-Bold", fontSize=22, leading=27),
    "eyebrow": ParagraphStyle(
        "eyebrow", parent=_BASE, fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=STEEL,
    ),
    "headline": ParagraphStyle("headline", parent=_BASE, fontName="Helvetica-Bold", fontSize=11.5, leading=16),
    "kpi_label": ParagraphStyle(
        "kpi_label", parent=_BASE, fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=INK_SOFT,
    ),
    "pill": ParagraphStyle("pill", parent=_BASE, fontName="Helvetica-Bold", fontSize=7, leading=9, alignment=TA_CENTER),
    "notice": ParagraphStyle("notice", parent=_BASE, fontSize=9, leading=13),
}

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
