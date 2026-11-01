"""Generates docs/architecture/six-sevens-architecture.drawio (one file, one page per diagram). Edit here, then run
from the repo root:  python docs/architecture/build_diagrams.py docs/architecture/six-sevens-architecture.drawio

Every edge on pages 1, 2, 4, 5 and 6 has explicit attachment points (and waypoints where it bends), so draw.io never
routes an arrow through a box; gaps are sized so each edge label fits between the boxes it joins.
"""

import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(sys.argv[1])

STYLE = {
    "user": "rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontStyle=1;",
    "ui": "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
    "gateway": "rounded=1;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;",
    "service": "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
    "worker": "rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;",
    "bus": "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;",
    "data": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;size=10;fillColor=#d5e8d4;strokeColor=#82b366;",
    "external": "rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;dashed=1;",
    "gcp": "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#4285F4;",
    "ops": "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;",
    "group": "rounded=1;whiteSpace=wrap;html=1;fillColor=none;dashed=1;verticalAlign=top;align=left;spacingLeft=8;spacingTop=4;fontStyle=1;strokeColor=#999999;",
    "region": "rounded=0;whiteSpace=wrap;html=1;fillColor=#f3f8ff;strokeColor=#4285F4;verticalAlign=top;align=left;spacingLeft=8;spacingTop=4;fontStyle=1;",
    "note": "shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#fffbe6;strokeColor=#d6b656;align=left;spacingLeft=6;fontSize=11;",
    "title": "text;html=1;fontSize=20;fontStyle=1;align=left;verticalAlign=top;",
    "text": "text;html=1;fontSize=11;align=left;verticalAlign=top;whiteSpace=wrap;",
    "lifeline": "shape=umlLifeline;perimeter=lifelinePerimeter;whiteSpace=wrap;html=1;container=0;collapsible=0;size=50;fillColor=#dae8fc;strokeColor=#6c8ebf;",
}
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;fontSize=10;labelBackgroundColor=#ffffff;"
DASHED = EDGE + "dashed=1;"
STRAIGHT = "html=1;endArrow=block;fontSize=10;labelBackgroundColor=#ffffff;"


def S(title: str, small: str = "") -> str:
    """A box label: bold title, smaller detail lines."""
    return f"<b>{title}</b>" + (f"<br><font style='font-size:10px'>{small}</font>" if small else "")


class Page:
    def __init__(self, name: str, width: int = 1700, height: int = 1000):
        self.name, self.width, self.height = name, width, height
        self.cells: list[str] = []
        self.n = 0

    def _id(self) -> str:
        self.n += 1
        return f"{self.name[:3].lower()}{self.n}"

    def box(self, label, x, y, w, h, kind="service", extra=""):
        cid = self._id()
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(label)} style="{STYLE[kind]}{extra}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return cid

    def edge(self, src, dst, label="", style=EDGE, extra="", *, exit=None, entry=None, points=(), both=False):
        """exit/entry: (x, y) as fractions of the source/target box, so the attachment point is fixed."""
        cid = self._id()
        if exit:
            extra += f"exitX={exit[0]:.4f};exitY={exit[1]:.4f};exitDx=0;exitDy=0;exitPerimeter=0;"
        if entry:
            extra += f"entryX={entry[0]:.4f};entryY={entry[1]:.4f};entryDx=0;entryDy=0;entryPerimeter=0;"
        if both:
            extra += "startArrow=block;startFill=1;"
        pts = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points)
        geometry = (
            f'<mxGeometry relative="1" as="geometry"><Array as="points">{pts}</Array></mxGeometry>'
            if pts else '<mxGeometry relative="1" as="geometry"/>'
        )
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(label)} style="{style}{extra}" edge="1" parent="1" source="{src}" target="{dst}">'
            f"{geometry}</mxCell>"
        )
        return cid

    def arrow(self, x1, y1, x2, y2, label="", style=STRAIGHT):
        cid = self._id()
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(label)} style="{style}" edge="1" parent="1">'
            f'<mxGeometry relative="1" as="geometry"><mxPoint x="{x1}" y="{y1}" as="sourcePoint"/>'
            f'<mxPoint x="{x2}" y="{y2}" as="targetPoint"/></mxGeometry></mxCell>'
        )

    def xml(self) -> str:
        body = "".join(self.cells)
        return (
            f'<diagram id="{self.name.replace(" ", "-").lower()}" name={quoteattr(self.name)}>'
            f'<mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
            f'fold="1" page="1" pageScale="1" pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram>'
        )


def fx(x: float, left: float, width: float) -> float:
    """Where absolute x falls along a box, as a fraction (for exit/entry points)."""
    return (x - left) / width


LEGEND = [
    ("gateway", "Gateway"), ("service", "Public service (via Kong)"), ("worker", "Private worker"),
    ("bus", "Pub/Sub topic"), ("data", "Data store"), ("external", "Outside service"),
]


def legend(p: Page, x: int, y: int, cols: int = 1) -> None:
    rows = -(-len(LEGEND) // cols)
    item_w = 190
    p.box("<b>Legend</b>", x, y, 20 + cols * (item_w + 10), 40 + rows * 32, "group")
    for i, (kind, text) in enumerate(LEGEND):
        col, row = divmod(i, rows)
        p.box(text, x + 10 + col * (item_w + 10), y + 32 + row * 32, item_w, 26, kind, "fontSize=10;")


# ------------------------------------------------------------------ 1. System architecture
# Public services sit above the topic, workers below it, so every topic arrow is a short vertical.
p1 = Page("1 System architecture", 1680, 1000)
p1.box("Six Sevens: event-driven services (Stage 1, Cloud Run)", 40, 20, 900, 40, "title")
officer = p1.box("Loan officer<br><font style='font-size:10px'>browser</font>", 40, 140, 120, 70, "user")
fe = p1.box(S("Frontend", "React, served by nginx<br>static build"), 240, 125, 190, 100, "ui")
kong = p1.box(S("Kong API Gateway", "single entry point · CORS<br>rate limits · request IDs<br>blocks /push and /metrics"), 530, 115, 200, 120, "gateway")
p1.box("Public, behind Kong (each checks the app's token)", 790, 100, 560, 170, "group")
api = p1.box(S("api", "reads, login, officer commands<br>uploads · WebSocket live updates<br>case match"), 810, 140, 240, 110)
audit = p1.box(S("audit-metrics", "stores every event in audit_events<br>/audit/events · /audit/cases/{id}/cost<br>runs the sweeper (stuck work)"), 1090, 140, 250, 110)
sched = p1.box(S("Cloud Scheduler", "sweep.requested<br>every 5 minutes"), 1410, 140, 190, 90, "external")

BUS_X, BUS_W = 480, 1120
bus = p1.box(S("Pub/Sub topic: domain-events", "one topic · a filtered push subscription per role · at-least-once delivery · OIDC-signed pushes"), BUS_X, 330, BUS_W, 70, "bus")

WG_Y, WG_H = 470, 180
workers_group = p1.box("Private workers", 580, WG_Y, 760, WG_H, "group")
WORKERS = [  # (label, detail, centre x, in/out events)
    ("doc-worker", "OCR · classify · extract<br>LangGraph · Sonnet/Haiku", 710, "in: uploaded, retyped,<br>retry, cancel<br>out: document.analyzed"),
    ("detection-worker", "lead agent + 3 specialists<br>10 fraud checks · flags", 960, "in: approved,<br>retry, cancel<br>out: document.detected"),
    ("case-worker", "cross-document analysis<br>deterministic risk · summary", 1210, "in: analysis requested<br>out: analysis completed"),
]
worker_ids = []
for name, detail, cx, events in WORKERS:
    wid = p1.box(S(name, detail), cx - 110, 510, 220, 120, "worker")
    worker_ids.append(wid)
    p1.edge(bus, wid, events, exit=(fx(cx, BUS_X, BUS_W), 1), entry=(0.5, 0), both=True)

dlq = p1.box(S("domain-events-dead-letter", "an event lands here after<br>5 failed deliveries"), 1420, 480, 210, 70, "bus")
regs = p1.box(S("Public registries", "data.gov.sg · URA · OneMap"), 1420, 590, 210, 70, "external")
claude = p1.box(S("Anthropic Claude", "Sonnet and Haiku, called by every worker's agents"), 600, 710, 720, 50, "external")
supa = p1.box(S("Supabase", "Postgres: cases, documents, fields,<br>flags, runs, risk, audit_events<br><br>Storage: uploaded files (private)<br><br>Realtime: row changes"), 40, 330, 340, 320, "data")

p1.edge(officer, fe, "uses", exit=(1, 0.5), entry=(0, 0.5))
p1.edge(fe, kong, "HTTPS +<br>WebSocket", exit=(1, 0.5), entry=(0, 0.5))
p1.edge(kong, api, "API calls", exit=(1, 0.5), entry=(0, fx(175, 140, 110)))
p1.edge(kong, audit, "/audit/*", exit=(0.5, 0), entry=(0.5, 0), points=[(630, 80), (1215, 80)])
p1.edge(api, bus, "commands<br>(uploaded, approved,<br>analysis requested)", exit=(0.5, 1), entry=(fx(930, BUS_X, BUS_W), 0))
p1.edge(bus, audit, "in: every event<br>out: recovery.requested", exit=(fx(1215, BUS_X, BUS_W), 0), entry=(0.5, 1), both=True)
p1.edge(sched, bus, "tick", exit=(0.5, 1), entry=(fx(1505, BUS_X, BUS_W), 0))
p1.edge(bus, dlq, "", DASHED, exit=(fx(1525, BUS_X, BUS_W), 1), entry=(0.5, 0))
for wid, (_, _, cx, _) in zip(worker_ids, WORKERS):
    p1.edge(wid, claude, "", DASHED, exit=(0.5, 1), entry=(fx(cx, 600, 720), 0))
# From the group's edge, not detection-worker's, so the arrow doesn't cross case-worker.
p1.edge(workers_group, regs, "detection<br>lookups", DASHED, exit=(1, fx(625, WG_Y, WG_H)), entry=(0, 0.5))
p1.edge(workers_group, supa, "reads and writes: status,<br>fields, flags, runs, risk", DASHED, exit=(0, fx(560, WG_Y, WG_H)), entry=(1, fx(560, 330, 320)), both=True)
p1.edge(api, supa, "reads and writes ·<br>Realtime → live updates", DASHED, exit=(0, 0.85), entry=(0.5, 0),
        points=[(770, 233.5), (770, 290), (210, 290)], both=True)
p1.box(
    "<b>How the services talk</b><br>• Only through events on one topic, and through Supabase state. No service calls another directly.<br>"
    "• Each worker reads and writes Supabase (status, fields, flags, runs, risk) and publishes its result back to the topic.<br>"
    "• Every event is also an audit row, stored once by audit-metrics (redeliveries ignored by event id).<br>"
    "• Status changes are compare-and-swap, so a repeated event can't apply twice. Only Pub/Sub's identity can invoke a worker.<br>"
    "• One codebase, one Dockerfile with a build target per role (shared layers); ROLE picks what each service runs.",
    40, 810, 900, 140, "note",
)
legend(p1, 1000, 810, cols=2)

# ------------------------------------------------------------------ 2. GCP infrastructure
# Services in one row, Pub/Sub as a bar under them: each service's own arrow to the topic is a short vertical.
p2 = Page("2 GCP infrastructure", 1840, 1040)
p2.box("Infrastructure on Google Cloud (all created by Terraform, deploy/terraform)", 40, 20, 1100, 40, "title")
p2.box("Management", 300, 70, 1180, 120, "group")
for i, label in enumerate([
    S("Terraform", "everything below, state in GCS"),
    S("Grafana + Managed Prometheus", "4 dashboards, alerts"),
    S("Cloud IAM", "a service account per service"),
    S("Billing", "budget alert · BigQuery export"),
    S("GitHub Actions", "CI + deploy via Workload<br>Identity Federation (no keys)"),
]):
    p2.box(label, 320 + i * 230, 100, 210, 70, "ops")

USERS = (40, 330, 130, 210)
users = p2.box("Loan officers<br><font style='font-size:10px'>browser</font>", *USERS, "user")
p2.box("Google Cloud Platform · project fyp-six-sevens", 200, 220, 1360, 790, "region", "fillColor=#ffffff;")
p2.box("asia-southeast1 (Singapore)", 230, 260, 1300, 730, "region")
p2.box("Cloud Run (scale to zero; Managed Prometheus sidecar on backend services and Kong)", 260, 300, 1240, 290, "group")
fe2 = p2.box(S("frontend", "nginx, public"), 290, 330, 160, 60, "gcp")
p2.box(S("grafana", "+ Prometheus query proxy<br>reads Monitoring and BigQuery"), 1280, 320, 200, 80, "gcp")
ROW, ROW_H = 480, 60
kong2 = p2.box(S("kong", "public entry point"), 290, ROW, 160, ROW_H, "gcp")
api2 = p2.box(S("api", "max 3 instances"), 500, ROW, 160, ROW_H, "gcp")
aud2 = p2.box(S("audit-metrics", "+ sweeper"), 690, ROW, 160, ROW_H, "gcp")
WSUB = (885, 440, 570, 120)
wsub = p2.box("Private workers", *WSUB, "group")
workers2 = []
for x, (name, detail) in zip((900, 1090, 1280), [("doc-worker", "2 vCPU · 4 GiB"), ("detection-worker", "2 vCPU · 4 GiB"), ("case-worker", "OIDC push only")]):
    workers2.append((p2.box(S(name, detail), x, ROW, 160, ROW_H, "gcp"), x + 80))

BUS2 = (500, 650, 940, 60)
ps2 = p2.box(S("Pub/Sub", "domain-events topic, a filtered push subscription per role · dead-letter topic"), *BUS2, "bus")
sch2 = p2.box(S("Cloud Scheduler", "sweep every 2 min"), 290, 650, 170, 60, "gcp")
ROW3 = 780
p2.box(S("Artifact Registry", "an image per role (one Dockerfile,<br>shared layers), pushed by CI"), 260, ROW3, 190, 70, "gcp")
p2.box(S("Secret Manager", "each key readable only by<br>the services that need it"), 470, ROW3, 190, 70, "gcp")
p2.box(S("Cloud Monitoring", "Managed Prometheus<br>alert policies"), 680, ROW3, 190, 70, "gcp")
p2.box(S("BigQuery", "billing_export.costs"), 890, ROW3, 190, 70, "data")
p2.box(S("Cloud Trace", "OpenTelemetry spans"), 1100, ROW3, 190, 70, "gcp")
p2.box(S("IAM + Workload Identity Federation", "github-deployer · pubsub-push · run-*"), 1310, ROW3, 190, 70, "gcp")

regs2 = p2.box(S("Gov registries", "data.gov.sg · URA · OneMap"), 1640, 330, 180, 60, "external")
supa2 = p2.box(S("Supabase", "Postgres · Storage · Realtime"), 1640, 460, 180, 60, "data")
anth2 = p2.box(S("Anthropic", "Claude API"), 1640, 560, 180, 60, "external")

p2.edge(users, fe2, "HTTPS", exit=(1, fx(360, USERS[1], USERS[3])), entry=(0, 0.5))
p2.edge(users, kong2, "API + WebSocket", exit=(1, fx(510, USERS[1], USERS[3])), entry=(0, 0.5))
p2.edge(kong2, api2, "", exit=(1, 0.5), entry=(0, 0.5))
p2.edge(kong2, aud2, "/audit", exit=(0.5, 0), entry=(0.5, 0), points=[(370, 450), (770, 450)])
p2.edge(sch2, ps2, "", exit=(1, 0.5), entry=(0, 0.5))
p2.edge(api2, ps2, "publish", exit=(0.5, 1), entry=(fx(580, BUS2[0], BUS2[2]), 0))
p2.edge(ps2, aud2, "in: every event<br>out: recovery.requested", exit=(fx(770, BUS2[0], BUS2[2]), 0), entry=(0.5, 1), both=True)
for wid, cx in workers2:
    p2.edge(ps2, wid, "push in,<br>result out", exit=(fx(cx, BUS2[0], BUS2[2]), 0), entry=(0.5, 1), both=True)
p2.edge(workers2[1][0], regs2, "lookups", DASHED, exit=(0.75, 0), entry=(0, 0.5), points=[(1210, 425), (1600, 425), (1600, 360)])
p2.edge(wsub, supa2, "reads and writes", DASHED, exit=(1, fx(490, WSUB[1], WSUB[3])), entry=(0, 0.5), both=True)
p2.edge(wsub, anth2, "Claude calls", DASHED, exit=(1, fx(540, WSUB[1], WSUB[3])), entry=(0, 0.5), points=[(1600, 540), (1600, 590)])
p2.box(
    "<b>Security</b><br>• Workers can be invoked only by the pubsub-push identity (OIDC); audit-metrics checks it in code.<br>"
    "• No long-lived keys: GitHub deploys through Workload Identity Federation.<br>"
    "• Secrets come from Secret Manager per service; Grafana has its own login. Containers run as non-root.",
    260, 880, 1000, 90, "note",
)
legend(p2, 1640, 700)

# ------------------------------------------------------------------ 3. Life of a document (sequence)
p3 = Page("3 Life of a document", 1800, 1420)
p3.box("Life of a document: upload → analyze → approve → detect → case analysis", 40, 20, 1200, 40, "title")
lanes = ["Officer (UI)", "Kong + api", "Pub/Sub", "doc-worker", "detection-worker", "case-worker", "audit-metrics", "Supabase"]
xs = {name: 60 + i * 210 for i, name in enumerate(lanes)}
for name in lanes:
    p3.box(f"<b>{name}</b>", xs[name], 80, 170, 1240, "lifeline")
mid = {name: xs[name] + 85 for name in lanes}
steps = [  # (from, to, label, live: a live update rather than a request or event)
    ("Officer (UI)", "Kong + api", "1. upload file(s)", False),
    ("Kong + api", "Supabase", "2. store file, document row = queued", False),
    ("Kong + api", "Pub/Sub", "3. publish document.uploaded", False),
    ("Pub/Sub", "doc-worker", "4. push (filtered subscription)", False),
    ("doc-worker", "Supabase", "5. claim queued→processing (compare-and-swap); classify, extract; = analyzed", False),
    ("doc-worker", "Pub/Sub", "6. publish document.analyzed (+ Claude cost)", False),
    ("Pub/Sub", "audit-metrics", "7. every event stored once in audit_events", False),
    ("Supabase", "Kong + api", "8. Realtime: the document row changed", True),
    ("Kong + api", "Officer (UI)", "9. fresh snapshot over the WebSocket", True),
    ("Officer (UI)", "Kong + api", "10. review fields, approve", False),
    ("Kong + api", "Supabase", "11. status = approved (compare-and-swap)", False),
    ("Kong + api", "Pub/Sub", "12. publish document.approved (if it fails, step 11 is undone)", False),
    ("Pub/Sub", "detection-worker", "13. push", False),
    ("detection-worker", "Supabase", "14. = detecting; lead + specialists + checks; flags; = detected", False),
    ("detection-worker", "Pub/Sub", "15. publish document.detected (+ Claude cost)", False),
    ("Officer (UI)", "Kong + api", "16. run case analysis", False),
    ("Kong + api", "Pub/Sub", "17. publish case.analysis.requested", False),
    ("Pub/Sub", "case-worker", "18. push", False),
    ("case-worker", "Supabase", "19. cross-document findings, risk score, summary", False),
    ("case-worker", "Pub/Sub", "20. publish case.analysis.completed (+ Claude cost)", False),
    ("Supabase", "Kong + api", "21. Realtime: the analysis run finished", True),
    ("Kong + api", "Officer (UI)", "22. live update: score and findings appear", True),
]
for i, (a, b, label, live) in enumerate(steps):
    y = 170 + i * 52
    p3.arrow(mid[a], y, mid[b], y, label, STRAIGHT + ("dashed=1;" if live else ""))
p3.box(
    "One trace in Cloud Trace covers steps 1-6 and 10-15: the traceparent travels on each Pub/Sub message. Dashed arrows are live updates. "
    "A worker that dies mid-push is redelivered by Pub/Sub; work stuck past its limit is recovered by the sweeper (page 4).",
    60, 1340, 1500, 50, "note",
)

# ------------------------------------------------------------------ 4. Stuck work recovery
p4 = Page("4 Stuck work recovery", 1680, 920)
p4.box("Recovering stuck work (no database changes; the audit trail is the memory)", 40, 20, 1200, 40, "title")
s_sched = p4.box(S("Cloud Scheduler", "every 5 minutes"), 40, 225, 170, 80, "external")
s_topic = p4.box(S("domain-events", "type = sweep.requested"), 270, 215, 170, 100, "bus")
s_sweep = p4.box(S("Sweeper (in audit-metrics)", "finds work past its limit:<br>queued/uploaded &gt; 5 min (event lost)<br>processing/extracting/detecting &gt; 15 min<br>case analysis running &gt; 15 min"), 540, 185, 280, 160, "service")
s_db = p4.box(S("Supabase", "documents, runs<br>audit_events (earlier attempts)"), 580, 430, 200, 90, "data")
T2_Y, T2_H = 100, 330
s_topic2 = p4.box(S("domain-events", "the same topic, not a new one<br><br>type = recovery.requested<br><br>subscriptions filter on<br>attribute owner = the worker<br><br>attempt n of 3,<br>a full limit apart"), 920, T2_Y, 180, T2_H, "bus")
s_group = p4.box("Owning workers", 1280, 60, 320, 390, "group")
owners = [
    ("doc-worker", "reset to queued (CAS), rerun<br>after 3 tries: failed → officer can Retry", 145),
    ("detection-worker", "reset to approved (CAS), rerun<br>after 3 tries: approved with error", 265),
    ("case-worker", "mark the run failed (no rerun:<br>costs Claude calls, officer decides)", 385),
]
for name, detail, cy in owners:
    oid = p4.box(S(name, detail), 1300, cy - 45, 280, 90, "worker")
    p4.edge(s_topic2, oid, f"owner = {name}", exit=(1, fx(cy, T2_Y, T2_H)), entry=(0, 0.5))
s_out = p4.box(S("Outcome events", "document.recovered · recovery.exhausted"), 1300, 520, 280, 70, "bus")
s_obs = p4.box(S("Seen in", "audit trail · Pipeline dashboard (stuck now,<br>recovered, gave up) · email alert on give-up"), 1300, 650, 280, 80, "ops")
p4.edge(s_sched, s_topic, "publish", exit=(1, 0.5), entry=(0, 0.5))
p4.edge(s_topic, s_sweep, "push", exit=(1, 0.5), entry=(0, 0.5))
p4.edge(s_sweep, s_db, "read only", exit=(0.5, 1), entry=(0.5, 0))
p4.edge(s_sweep, s_topic2, "publish one<br>per stuck item", exit=(1, 0.5), entry=(0, 0.5))
p4.edge(s_group, s_out, "each publishes its outcome", exit=(0.5, 1), entry=(0.5, 0))
p4.edge(s_out, s_obs, "", exit=(0.5, 1), entry=(0.5, 0))
p4.box(
    "<b>Also handled</b><br>• Worker dies mid-push → Pub/Sub redelivers (ack deadline 600 s); after 5 attempts → dead-letter + email alert.<br>"
    "• Officer cancels a run on another instance → the run polls the audit trail / its status every 5 s and stops.<br>"
    "• Publishing an approval fails → the approval is undone and the officer sees an error (nothing left half-done).",
    40, 780, 1000, 110, "note",
)

# ------------------------------------------------------------------ 5. Observability
p5 = Page("5 Observability and cost", 1360, 640)
p5.box("Observability: metrics, traces, audit trail and cost", 40, 20, 1000, 40, "title")
o_kong = p5.box(S("Kong", "status codes, latency"), 40, 90, 280, 60, "gateway")
o_svc = p5.box(S("Every backend service", "GET /metrics (prometheus_client)<br>requests · events · stage timings<br>Claude calls/tokens/cost/latency per agent<br>cost per document · tool outcomes · stuck work"), 40, 190, 280, 150, "service")
SIDE_Y, SIDE_H = 90, 250
o_side = p5.box(S("Managed Prometheus sidecar", "beside each service<br>scrapes /metrics every 30 s"), 410, SIDE_Y, 210, SIDE_H, "gcp")
o_gmp = p5.box(S("Cloud Monitoring", "Managed Prometheus +<br>Cloud Run and Pub/Sub metrics"), 690, 170, 220, 90, "gcp")
o_alert = p5.box(S("Alert policies → email", "dead letters · 5xx &gt; 5%<br>Claude spend/day · recovery gave up"), 1080, 60, 240, 80, "ops")
o_gra = p5.box(S("Grafana", "Services · Pipeline · Agents · Cost"), 1080, 170, 240, 90, "ops")
o_bill = p5.box(S("BigQuery billing export", "actual invoice lines"), 690, 310, 220, 80, "data")
o_trace = p5.box(S("Cloud Trace", "one trace per upload, across services"), 40, 430, 280, 70, "gcp")
o_audit = p5.box(S("audit_events", "every action;<br>each run's Claude cost"), 410, 450, 210, 80, "data")
o_audsvc = p5.box(S("audit-metrics", "sums each run's cost<br>per document and case"), 690, 450, 220, 80, "service")
o_ui = p5.box(S("App: Claude Cost tab", "per case and per document<br>GET /audit/cases/{id}/cost"), 1080, 450, 240, 80, "ui")
p5.edge(o_kong, o_side, "scraped<br>(pull)", exit=(1, 0.5), entry=(0, fx(120, SIDE_Y, SIDE_H)))
p5.edge(o_svc, o_side, "scraped<br>(pull)", exit=(1, 0.5), entry=(0, fx(265, SIDE_Y, SIDE_H)))
p5.edge(o_side, o_gmp, "write", exit=(1, fx(215, SIDE_Y, SIDE_H)), entry=(0, 0.5))
p5.edge(o_gmp, o_alert, "PromQL conditions", exit=(0.5, 0), entry=(0, 0.5), points=[(800, 100)])
p5.edge(o_gmp, o_gra, "PromQL (query proxy)", exit=(1, 0.5), entry=(0, 0.5))
p5.edge(o_bill, o_gra, "SQL (BigQuery plugin)", exit=(1, 0.5), entry=(0.5, 1), points=[(1200, 350)])
p5.edge(o_svc, o_trace, "OpenTelemetry spans", DASHED, exit=(0.5, 1), entry=(0.5, 0))
p5.edge(o_audit, o_audsvc, "read", exit=(1, 0.5), entry=(0, 0.5))
p5.edge(o_audsvc, o_ui, "GET /audit/cases/<br>{id}/cost", exit=(1, 0.5), entry=(0, 0.5))

# ------------------------------------------------------------------ 6. CI/CD
# CI's four jobs run in parallel in a column; the gate below them hands a green push to main to deploy.yml.
p6 = Page("6 CI and deploy", 1800, 900)
p6.box("Delivery: CI on every push and PR; a green push to main deploys (GitHub Actions)", 40, 20, 1200, 40, "title")
c_push = p6.box(S("git push / PR", "ci-cd.yml<br>a docs-only push is skipped"), 40, 250, 160, 70, "user")
JOB_X, JOB_W, JOB_H = 270, 380, 80
JOBS = [
    ("Backend", "ruff · pyright (no suppressions)<br>pytest + coverage · pip and exiftool cached"),
    ("Frontend", "lint · build<br>Playwright: 116 tests, 2 workers, production build"),
    ("Security", "Checkov: Terraform, Dockerfiles, workflows<br>Snyk: Python and npm (blocking)"),
    ("Terraform", "fmt · validate (1.16.6)<br>apply stays a manual step"),
]
c_gate = p6.box(S("All four green?", "only a push to main deploys;<br>a PR or branch stops here"), 740, 250, 190, 70, "gateway")
for i, (title, detail) in enumerate(JOBS):
    y = 110 + i * (JOB_H + 20)
    job = p6.box(S(title, detail), JOB_X, y, JOB_W, JOB_H, "service")
    p6.edge(c_push, job, exit=(1, 0.5), entry=(0, 0.5), points=[(235, 285), (235, y + JOB_H / 2)])
    p6.edge(job, c_gate, exit=(1, 0.5), entry=(0, 0.5), points=[(695, y + JOB_H / 2), (695, 285)])

DEPLOY_Y, DEPLOY_H = 600, 100
p6.box("deploy.yml · one deploy at a time, never cancelled halfway (that would skip the rollback)", 220, 540, 1560, 250, "group")
c_run = p6.box(S("Run workflow", "manual: Deploy to Cloud Run"), 40, 615, 160, 70, "user")
c_auth = p6.box(S("Authenticate", "Workload Identity Federation<br>one short-lived token per job"), 240, DEPLOY_Y, 200, DEPLOY_H, "gcp")
c_build = p6.box(S("Build 8 images", "5 role targets of one Dockerfile + Kong,<br>frontend, Grafana (one list in deploy.yml)<br>skipped if this commit's image exists"), 500, DEPLOY_Y, 240, DEPLOY_H, "worker")
c_ar = p6.box(S("Artifact Registry"), 800, 620, 160, 60, "gcp")
c_roll = p6.box(S("Roll out", "workers first, then api, Kong,<br>frontend and Grafana<br>traffic → newest revision"), 1020, DEPLOY_Y, 220, DEPLOY_H, "worker")
c_check = p6.box(S("Gateway check", "/health ok · /cases 401 · /push 404<br>worker 403 · CORS · frontend · Grafana"), 1300, DEPLOY_Y, 230, DEPLOY_H, "service")
c_ok = p6.box(S("Done", "summary with URLs"), 1590, 570, 160, 60, "data")
c_back = p6.box(S("Roll back every service", "to the revision serving before"), 1590, 700, 170, 70, "ops")
p6.edge(c_gate, c_auth, "green push to main", exit=(0.5, 1), entry=(0.5, 0), points=[(835, 510), (340, 510)])
p6.edge(c_run, c_auth, exit=(1, 0.5), entry=(0, 0.5))
p6.edge(c_auth, c_build, exit=(1, 0.5), entry=(0, 0.5))
p6.edge(c_build, c_ar, exit=(1, 0.5), entry=(0, 0.5))
p6.edge(c_ar, c_roll, exit=(1, 0.5), entry=(0, 0.5))
p6.edge(c_roll, c_check, exit=(1, 0.5), entry=(0, 0.5))
p6.edge(c_check, c_ok, "pass", exit=(1, 0.3), entry=(0, 0.5), points=[(1560, 630), (1560, 600)])
p6.edge(c_check, c_back, "fail", exit=(1, 0.7), entry=(0, 0.5), points=[(1560, 670), (1560, 735)])
p6.box("Terraform owns configuration (sizes, scaling, env, secrets, IAM) and is applied by hand before a push that needs it; "
       "the deploy only changes images.", 240, 820, 1000, 40, "note")

pages = [p1, p2, p3, p4, p5, p6]
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    '<mxfile host="app.diagrams.net" type="device">' + "".join(p.xml() for p in pages) + "</mxfile>\n", encoding="utf-8"
)
print(f"wrote {OUT} with {len(pages)} pages")
