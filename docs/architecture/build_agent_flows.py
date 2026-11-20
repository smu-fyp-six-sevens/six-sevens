"""Generates docs/architecture/six-sevens-agent-flows.drawio: three pages, one per worker (doc-worker, detection-worker,
case-worker), for the agentic-architecture slides. Edit here, then run from the repo root:
    python docs/architecture/build_agent_flows.py docs/architecture/six-sevens-agent-flows.drawio

Everything sits on the 10 px grid. Every arrow is a straight line that is exactly vertical (`down`/`up`, at an x that both
boxes span) or exactly horizontal (`across`, at a y that both boxes span); `_check` stops the build if a point is
off the grid or outside a box.
"""

import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(sys.argv[1])

KIND = {  # fill, stroke, tag
    "code": ("#ffffff", "#333333", "CODE"),
    "sonnet": ("#dae8fc", "#6c8ebf", "SONNET"),
    "haiku": ("#d5e8d4", "#82b366", "HAIKU"),
    "mixed": ("#dae8fc", "#6c8ebf", "SONNET + HAIKU"),
    "officer": ("#ffe6cc", "#d79b00", "OFFICER"),
    "noai": ("#f5f5f5", "#666666", "NO AI"),
}
BOX = "rounded=1;whiteSpace=wrap;html=1;arcSize=8;strokeWidth=2;fillColor={};strokeColor={};"
EVENT = "rounded=1;whiteSpace=wrap;html=1;arcSize=50;strokeWidth=2;fillColor=#e1d5e7;strokeColor=#9673a6;fontSize=14;"
BAR = "rounded=1;whiteSpace=wrap;html=1;arcSize=30;strokeWidth=2;fillColor=#ffffff;strokeColor=#333333;fontSize=14;"
EDGE = "html=1;endArrow=block;endSize=8;strokeWidth=2;fontSize=13;labelBackgroundColor=#ffffff;"


def label(kind: str, title: str, detail: str = "") -> str:
    tag = KIND[kind][2]
    return (
        f"<font style='font-size:11px;color:#777777'>{tag}</font><br><b style='font-size:20px'>{title}</b>"
        + (f"<br><font style='font-size:14px'>{detail}</font>" if detail else "")
    )


class Page:
    def __init__(self, name: str, width: int, height: int, title: str, subtitle: str):
        self.name, self.width, self.height = name, width, height
        self.cells: list[str] = []
        self.geo: dict[str, tuple[int, int, int, int]] = {}
        self.n = 1
        self.text(title, 40, 20, 1200, 40, "fontSize=28;fontStyle=1;")
        self.text(subtitle, 40, 70, 1400, 30, "fontSize=16;fontColor=#666666;")

    def _id(self) -> str:
        self.n += 1
        return f"p{self.name[0]}{self.n}"

    def _add(self, value, style, x, y, w, h):
        assert all(v % 10 == 0 for v in (x, y, w, h)), f"off the 10 px grid: {value!r} {(x, y, w, h)}"
        cid = self._id()
        self.geo[cid] = (x, y, w, h)
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(value)} style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return cid

    def text(self, value, x, y, w, h, extra=""):
        return self._add(value, "text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;" + extra, x, y, w, h)

    def box(self, kind, title, detail, x, y, w, h, compact=False):
        fill, stroke, _ = KIND[kind]
        text = (  # compact: a smaller box with no model tag, for a tool group
            f"<b style='font-size:16px'>{title}</b><br><font style='font-size:12px'>{detail}</font>"
            if compact else label(kind, title, detail)
        )
        return self._add(text, BOX.format(fill, stroke), x, y, w, h)

    def event(self, value, x, y, w, h):
        return self._add(value, EVENT, x, y, w, h)

    def bar(self, value, x, y, w, h):
        return self._add(value, BAR, x, y, w, h)

    def legend(self, x, y, kinds):
        for i, kind in enumerate(kinds):
            fill, stroke, tag = KIND[kind]
            left = x + i * 150
            self._add("", f"rounded=1;html=1;strokeWidth=2;fillColor={fill};strokeColor={stroke};", left, y, 20, 20)
            self.text(tag.title() if kind != "mixed" else "Sonnet + Haiku", left + 30, y, 110, 20, "fontSize=13;")

    def _check(self, value: int, lo: int, size: int, what: str):
        if value % 10 or not lo <= value <= lo + size:
            raise ValueError(f"{what}: {value} is off the grid or outside the box ({lo}..{lo + size})")

    def _edge(self, src, dst, text, dashed, exit, entry):
        cid = self._id()
        style = (
            EDGE + ("dashed=1;" if dashed else "")
            + f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        )
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(text)} style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>'
        )

    def vertical(self, src, dst, x, text="", dashed=False):
        """A straight vertical arrow at absolute x, from the nearer edge of src to the nearer edge of dst."""
        (sx, sy, sw, sh), (dx, dy, dw, dh) = self.geo[src], self.geo[dst]
        self._check(x, sx, sw, "vertical start")
        self._check(x, dx, dw, "vertical end")
        down = dy > sy
        self._edge(src, dst, text, dashed, ((x - sx) / sw, 1 if down else 0), ((x - dx) / dw, 0 if down else 1))

    def across(self, src, dst, y, text="", dashed=False):
        """A straight horizontal arrow at absolute y, from the nearer edge of src to the nearer edge of dst."""
        (sx, sy, sw, sh), (dx, dy, dw, dh) = self.geo[src], self.geo[dst]
        self._check(y, sy, sh, "horizontal start")
        self._check(y, dy, dh, "horizontal end")
        right = dx > sx
        self._edge(src, dst, text, dashed, (1 if right else 0, (y - sy) / sh), (0 if right else 1, (y - dy) / dh))

    def xml(self) -> str:
        return (
            f'<diagram name={quoteattr(self.name)} id="page-{self.name[0]}">'
            f'<mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
            f'fold="1" page="1" pageScale="1" pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(self.cells)}</root></mxGraphModel></diagram>'
        )


def doc_worker() -> Page:
    p = Page("1 Document agents", 1250, 1130, "1 · Document agents", "doc-worker: reads each upload, classifies it, extracts its fields")
    x, w, h, ah = 300, 480, 100, 170  # both agent rows are the same height, each with its tools on the right
    tx, tw, th = 860, 330, 70
    ys = [150, 290, 520, 730, 870]
    start = p.event("<b>uploaded</b><br>event", 60, ys[0] + 20, 150, 60)
    ingest = p.box("code", "Ingest", "PDF text / OCR, metadata, content guard", x, ys[0], w, h)
    classify = p.box("haiku", "Classifier agent", "Rules run first; the agent may ask for a second opinion or look at the pages", x, ys[1], w, ah)
    c_llm = p.box("haiku", "llm_classify", "Haiku second opinion on the type", tx, ys[1], tw, th)
    c_vis = p.box("sonnet", "vision_llm", "Reads the page images when there is little or no text", tx, ys[1] + 100, tw, th)
    extract = p.box("sonnet", "Extractor agent", "A text pass fills the fields with a confidence; weak fields get a bounded second look (90 s limit)", x, ys[2], w, ah)
    e_lay = p.box("code", "layout_lookup", "Free: cached text lines with positions, OCR for scans, page tables", tx, ys[2], tw, th)
    e_vis = p.box("sonnet", "vision_read", "At most one visual read, cropped to the field's lines", tx, ys[2] + 100, tw, th)
    validate = p.box("code", "Validate replies", "Contract check: ok · repaired · fallback", x, ys[3], w, h)
    flag = p.event("<b>different reading?</b><br>the text value stays, confidence drops, the officer sees both", tx, ys[3], tw, h)
    review = p.box("officer", "Review &amp; approve", "Fix fields, retype, approve or reject", x, ys[4], w, h)
    end = p.event("<b>approved</b><br>event", tx, ys[4] + 20, 150, 60)
    p.across(start, ingest, ys[0] + 50)
    p.vertical(ingest, classify, 540)
    p.vertical(classify, extract, 440)
    p.vertical(extract, classify, 640, "wrong type?<br>retype once (×1)", dashed=True)
    p.vertical(extract, validate, 540)
    p.vertical(validate, review, 540)
    p.across(review, end, ys[4] + 50)
    p.across(classify, c_llm, ys[1] + 40, dashed=True)
    p.across(classify, c_vis, ys[1] + 140, dashed=True)
    p.across(extract, e_lay, ys[2] + 40, "1st", dashed=True)
    p.across(extract, e_vis, ys[2] + 140, "2nd, once", dashed=True)
    p.vertical(e_vis, flag, tx + 160)
    p.bar("Every reply is checked by code before it reaches a field · past 90 s, or on a failure, the text pass stands · approved documents are locked", 50, 1010, 1140, 60)
    p.legend(50, 1090, ["code", "haiku", "sonnet", "officer"])
    return p


def detection() -> Page:
    p = Page("2 Detection agent", 1040, 1130, "2 · Detection agent", "detection-worker: three specialists look at each approved document")
    sw, gap = 260, 30
    xs = [100 + i * (sw + gap) for i in range(3)]  # 100, 390, 680: the three specialists side by side, centred on x=520
    start = p.event("<b>approved</b><br>event", 440, 110, 160, 50)
    lead = p.box("sonnet", "Lead plans", "Briefs each specialist, skips irrelevant checks", 100, 200, 840, 80)
    names = [
        ("Layout &amp; Rendering", "hidden text · fonts · layout · image checks"),
        ("Content Validation", "spelling · dates · pages · numbers · external lookups"),
        ("Digital Authenticity", "prompt injection · metadata · AI generation"),
    ]
    tool_lists = [
        "hidden_text<br>font_consistency<br>layout_anomalies<br>image_manipulation<br>image_tampering",
        "spelling<br>date_consistency<br>page_consistency<br>numerical_checks<br>external_validation",
        "prompt_injection<br>metadata<br>ai_generation",
    ]
    specs, tools = [], []
    for i, (name, detail) in enumerate(names):
        specs.append(p.box("haiku", name, detail, xs[i], 330, sw, 100))
        tools.append(p.box("code", "Tools", tool_lists[i], xs[i] + 40, 480, 180, 150))  # centred under its specialist
    review = p.box("sonnet", "Lead reviews", "Sets severity, may ask one specialist for one second look", 100, 680, 840, 90)
    merge = p.box("code", "Merge &amp; save", "Hard breaches can't be dismissed", 320, 820, 400, 100)
    end = p.event("<b>all documents<br>ready</b>", 780, 840, 160, 60)
    p.vertical(start, lead, 520)
    for i in range(3):
        p.vertical(lead, specs[i], xs[i] + 130)
        p.vertical(specs[i], tools[i], xs[i] + 130)
        p.vertical(tools[i], review, xs[i] + 130)
    p.vertical(review, specs[2], xs[2] + 240, "second look<br>(×1)", dashed=True)
    p.vertical(review, merge, 520)
    p.across(merge, end, 870)  # both centred on y=870
    p.bar("LangGraph · at most 30 model calls per detection · tool timeouts · specialists can't invent findings", 50, 950, 940, 60)
    p.legend(50, 1030, ["code", "sonnet", "haiku"])
    return p


def case_analysis() -> Page:
    p = Page("3 Case analysis", 1300, 1430, "3 · Case analysis", "case-worker: cross-checks the case once every document is decided and scanned")
    x, w, tx, tw = 280, 480, 840, 400  # the step column, and the tools on its right
    ys = [140, 280, 420, 810, 950, 1090]
    start = p.event("<b>analysis<br>requested</b>", 60, ys[0] + 20, 150, 60)
    compare = p.box("code", "Compare documents", "Deterministic: name, address, UEN, IDs", x, ys[0], w, 100)
    planner = p.box("sonnet", "Planner agent", "At most 3 hypotheses, each with a counter-check (a refutation probe)", x, ys[1], w, 100)
    tools = p.box("code", "Evidence tools", "22 read-only tools. They do the counting and comparing; the model only chooses what to look at.<br><br>Every fact carries a reference id, and a hard breach carries a typed flag: the only way a finding can reach High.", x, ys[2], w, 350)
    judge = p.box("sonnet", "Judge agent", "Weighs the evidence, writes verdicts: supported, refuted or inconclusive", x, ys[3], w, 100)
    verify = p.box("code", "Verify", "Real citations? Bias-free? Severity capped (High only for a hard breach)", x, ys[4], w, 100)
    score = p.box("noai", "Risk score", "Low · Medium · High · Critical, from code, never from a model", x, ys[5], w, 160)
    groups = [
        ("Reading · 3", "list_documents · get_fields · get_business_profile"),
        ("Transactions · 7", "stats · query · top counterparties · monthly flow · repeated payments · card usage · recurring amounts"),
        ("Comparisons · 4", "periods · identifiers · people · linked people"),
        ("Consistency · 2", "company name · address"),
        ("Cross-checks · 6", "salary vs credits · revenue vs bank · assessed income vs profit · round trips · customer concentration · statement coverage"),
    ]
    for i, (name, detail) in enumerate(groups):
        group = p.box("code", name, detail, tx, ys[2] + i * 70, tw, 60, compact=True)
        p.across(tools, group, ys[2] + i * 70 + 30, dashed=True)
    requests = p.box("haiku", "Evidence requests (Haiku)", "Suggests documents to ask the client for; code re-scores what each would change", tx, 1100, tw, 60, compact=True)
    summary = p.box("haiku", "Summary writer (Haiku)", "Plain-language summary of the findings; never sets the score", tx, 1180, tw, 60, compact=True)
    p.across(start, compare, ys[0] + 50)
    for a, b in zip((compare, planner, tools, judge, verify), (planner, tools, judge, verify, score)):
        p.vertical(a, b, 520)
    p.across(score, requests, 1130, dashed=True)
    p.across(score, summary, 1210, dashed=True)
    p.text("at most 7 model calls per case", tx, ys[1] + 40, tw, 20, "fontSize=14;fontColor=#666666;")
    p.bar("Protected attributes never reach a model · every verdict is verified by code · the score is deterministic", 50, 1290, 1200, 60)
    p.legend(50, 1370, ["code", "sonnet", "haiku", "noai"])
    return p


pages = [doc_worker(), detection(), case_analysis()]
OUT.write_text(
    '<mxfile host="app.diagrams.net">' + "".join(pg.xml() for pg in pages) + "</mxfile>", encoding="utf-8"
)
print(f"wrote {OUT}")
