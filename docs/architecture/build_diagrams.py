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
    ("gateway", "Front door"), ("service", "Internal service (via Kong)"), ("worker", "Private worker"),
    ("bus", "Pub/Sub topic"), ("data", "Data store"), ("external", "Outside service"),
]


def legend(p: Page, x: int, y: int, cols: int = 1) -> None:
    rows = -(-len(LEGEND) // cols)
    item_w = 190
    p.box("<b>Legend</b>", x, y, 20 + cols * (item_w + 10), 40 + rows * 32, "group")
    for i, (kind, text) in enumerate(LEGEND):
        col, row = divmod(i, rows)
        p.box(text, x + 10 + col * (item_w + 10), y + 32 + row * 32, item_w, 26, kind, "fontSize=10;")


def legend_small(p: Page, x: int, y: int) -> None:
    """The colour key as bare swatches and labels in a corner: three columns, no frame."""
    for i, (kind, text) in enumerate(LEGEND):
        left, top = x + (i % 3) * 150, y + (i // 3) * 24
        p.box("", left, top, 16, 16, kind)
        p.box(text, left + 22, top - 2, 125, 20, "text", "fontSize=10;verticalAlign=middle;")


# ------------------------------------------------------------------ 1. System architecture
# Front door and the public services on top, the topic across the middle, workers under it, Supabase as a bar at the
# bottom: roughly square, and every topic arrow is a short vertical.
p1 = Page("1 System architecture", 1170, 1040)
p1.box("Six Sevens: event-driven services on GKE Autopilot", 40, 20, 600, 40, "title")
legend_small(p1, 680, 20)
officer = p1.box("Loan officer<br><font style='font-size:10px'>browser</font>", 40, 140, 100, 60, "user")
lb = p1.box(S("Load balancer + Cloud Armor", "sixsevens.me · managed TLS<br>OWASP rules · per-address limits<br>(Gateway API)"), 170, 115, 180, 110, "gateway")
kong = p1.box(S("Kong API Gateway", "CORS · rate limits · request IDs<br>blocks /push and /metrics"), 390, 100, 180, 80, "gateway")
fe = p1.box(S("frontend", "React, served by nginx"), 390, 195, 180, 50, "ui")
p1.box("Internal services behind Kong (ClusterIP; each checks the app's token)", 610, 90, 480, 140, "group")
api = p1.box(S("api", "reads, login, officer commands<br>uploads · WebSocket live updates<br>case match"), 630, 130, 200, 90)
audit = p1.box(S("audit-metrics", "stores every event in audit_events<br>/audit/events · /audit/cases/{id}/cost<br>runs the sweeper (stuck work)"), 850, 130, 220, 90)
sched = p1.box(S("Cloud Scheduler", "sweep.requested<br>every 5 minutes"), 1000, 290, 130, 70, "external")

BUS_X, BUS_W = 160, 970
bus = p1.box(S("Pub/Sub topic: domain-events", "one topic · a filtered pull subscription per role · at-least-once delivery · dead letters after 5 tries"), BUS_X, 420, BUS_W, 60, "bus")

WG_X, WG_Y, WG_W, WG_H = 160, 580, 680, 170
workers_group = p1.box("Private workers (no inbound traffic; each pulls its own events)", WG_X, WG_Y, WG_W, WG_H, "group")
WORKERS = [  # (label, detail, centre x, in/out events)
    ("doc-worker", "OCR · classify · extract<br>LangGraph · Sonnet/Haiku", 280, "pull: uploaded, retyped,<br>retry, cancel<br>out: document.analyzed"),
    ("detection-worker", "lead agent + 3 specialists<br>13 checks · flags", 500, "pull: approved,<br>retry, cancel<br>out: document.detected"),
    ("case-worker", "cross-document analysis<br>deterministic risk · summary", 720, "pull: analysis requested<br>out: analysis completed"),
]
worker_ids = []
for name, detail, cx, events in WORKERS:
    wid = p1.box(S(name, detail), cx - 100, 620, 200, 100, "worker")
    worker_ids.append(wid)
    p1.edge(bus, wid, events, exit=(fx(cx, BUS_X, BUS_W), 1), entry=(0.5, 0), both=True)

dlq = p1.box(S("domain-events-dead-letter", "an event lands here after<br>5 failed deliveries"), 900, 540, 230, 70, "bus")
regs = p1.box(S("Public registries", "data.gov.sg · URA · OneMap"), 900, 630, 230, 70, "external")
keda = p1.box(S("KEDA", "scales each worker on<br>its undelivered messages"), 900, 720, 230, 60, "gcp")
claude = p1.box(S("Claude (Anthropic API)", "Sonnet and Haiku, called by every worker's agents; the API key is mounted from Secret Manager"), 260, 810, 800, 50, "external")
supa = p1.box(S("Supabase", "Postgres: cases, documents, fields, flags, runs, risk, audit_events<br>Storage: uploaded files (private) · Realtime: row changes"), 40, 900, 1090, 90, "data")

p1.edge(officer, lb, "HTTPS +<br>WebSocket", exit=(1, 0.5), entry=(0, 0.5))
p1.edge(lb, kong, "api.", exit=(1, fx(140, 115, 110)), entry=(0, 0.5))
p1.edge(lb, fe, "sixsevens.me", exit=(1, fx(220, 115, 110)), entry=(0, 0.5))
p1.edge(kong, api, "API calls", exit=(1, 0.5), entry=(0, fx(140, 130, 90)))
p1.edge(kong, audit, "/audit/*", exit=(0.5, 0), entry=(0.5, 0), points=[(480, 70), (960, 70)])
p1.edge(api, bus, "commands<br>(uploaded, approved,<br>analysis requested)", exit=(0.5, 1), entry=(fx(730, BUS_X, BUS_W), 0))
p1.edge(bus, audit, "pull: every event<br>out: recovery.requested", exit=(fx(880, BUS_X, BUS_W), 0), entry=(fx(880, 850, 220), 1), both=True)
p1.edge(sched, bus, "tick", exit=(fx(1065, 1000, 130), 1), entry=(fx(1065, BUS_X, BUS_W), 0))
p1.edge(bus, dlq, "", DASHED, exit=(fx(1015, BUS_X, BUS_W), 1), entry=(0.5, 0))
p1.edge(keda, workers_group, "scales", DASHED, exit=(0, fx(740, 720, 60)), entry=(1, fx(740, WG_Y, WG_H)))
p1.edge(workers_group, regs, "detection<br>lookups", DASHED, exit=(1, fx(665, WG_Y, WG_H)), entry=(0, 0.5))
for wid, (_, _, cx, _) in zip(worker_ids, WORKERS):
    p1.edge(wid, claude, "", DASHED, exit=(0.5, 1), entry=(fx(cx, 260, 800), 0))
p1.edge(workers_group, supa, "reads and writes<br>status, fields, flags", DASHED, exit=(fx(200, WG_X, WG_W), 1), entry=(fx(200, 40, 1090), 0), both=True)
p1.edge(api, supa, "reads and writes ·<br>Realtime → live updates", DASHED, exit=(fx(645, 630, 200), 1), entry=(fx(90, 40, 1090), 0),
        points=[(645, 290), (90, 290)], both=True)

# ------------------------------------------------------------------ 2. GCP infrastructure
# The cluster's pods in two rows, Pub/Sub as a bar under them: each pod's arrow to the topic is a short vertical.
p2 = Page("2 GCP infrastructure", 1880, 960)
p2.box("Infrastructure on Google Cloud (Terraform for the cloud, a Helm chart inside the cluster)", 40, 20, 1000, 40, "title")
legend_small(p2, 1420, 16)
p2.box("Management", 300, 70, 1180, 120, "group")
for i, label in enumerate([
    S("Terraform", "the cloud pieces, state in GCS"),
    S("Grafana + Managed Prometheus", "5 dashboards (Overview first)<br>alerts · SLOs"),
    S("Git (main)", "the chart + image digests:<br>what the cluster should run"),
    S("Billing", "budget alert · BigQuery export"),
    S("GitHub Actions", "CI + deploy via Workload<br>Identity Federation (no keys)"),
]):
    p2.box(label, 320 + i * 230, 100, 210, 70, "ops")

LB2 = (260, 240, 1240, 50)
users = p2.box("Loan officers<br><font style='font-size:10px'>browser</font>", 40, 235, 130, 60, "user")
p2.box("Google Cloud Platform · project fyp-six-sevens", 200, 220, 1360, 690, "region", "fillColor=#ffffff;")
lb2 = p2.box(S("Load balancer (Gateway API, global) + Cloud Armor", "sixsevens.me · api. · grafana. · Google-managed certificate · OWASP rules · per-address rate limits"), *LB2, "gateway")
p2.box("asia-southeast1 (Singapore)", 230, 310, 1300, 580, "region")
CLUSTER = (260, 340, 1240, 300)
cluster = p2.box("GKE Autopilot · regional · private nodes · a namespace per role · default-deny network policies · Workload Identity", *CLUSTER, "group")
fe2 = p2.box(S("frontend", "nginx"), 290, 380, 160, 60, "gcp")
kong2 = p2.box(S("kong", "namespace gateway"), 480, 380, 160, 60, "gcp")
graf2 = p2.box(S("grafana", "namespace observability"), 1300, 380, 170, 60, "gcp")
p2.box(S("Argo CD", "namespace argocd · GitOps:<br>syncs the chart from git"), 1090, 380, 190, 60, "ops")
api2 = p2.box(S("api", "1–2 pods"), 290, 500, 160, 60, "gcp")
aud2 = p2.box(S("audit-metrics", "+ sweeper · 1 pod"), 480, 500, 160, 60, "gcp")
WSUB = (685, 470, 575, 110)
wsub = p2.box("Private workers (scaled by KEDA)", *WSUB, "group")
workers2 = []
for x, (name, detail) in zip((700, 890, 1080), [("doc-worker", "1 vCPU · 4 GiB"), ("detection-worker", "1 vCPU · 4 GiB"), ("case-worker", "0.5 vCPU · 2 GiB")]):
    workers2.append((p2.box(S(name, detail), x, 500, 160, 60, "gcp"), x + 80))
keda2 = p2.box(S("KEDA", "scales on backlog"), 1300, 500, 170, 60, "gcp")

BUS2 = (290, 670, 990, 60)
ps2 = p2.box(S("Pub/Sub", "domain-events · a filtered pull subscription per role · dead-letter topic"), *BUS2, "bus")
nat2 = p2.box(S("Cloud NAT", "one static egress IP<br>(Supabase, registries)"), 1310, 665, 190, 70, "gcp")
ROW3 = 790
sch2 = None
for i, (label, kind) in enumerate([
    (S("Cloud Scheduler", "sweep every 5 min"), "gcp"),
    (S("Artifact Registry", "images by digest<br>Trivy-scanned in CI"), "gcp"),
    (S("Binary Authorization", "admits only signed,<br>attested images (KMS)"), "gcp"),
    (S("Secret Manager", "mounted as files<br>(GKE add-on)"), "gcp"),
    (S("Cloud Monitoring", "Managed Prometheus · alerts<br>uptime checks · 2 SLOs"), "gcp"),
    (S("BigQuery", "billing_export.costs"), "data"),
    (S("Cloud Trace + Logging", "OpenTelemetry spans"), "gcp"),
]):
    cid = p2.box(label, 260 + i * 177, ROW3, 165, 70, kind)
    sch2 = sch2 or cid

vtx2 = p2.box(S("Anthropic API", "Claude Sonnet and Haiku<br>key from Secret Manager"), 1640, 245, 180, 70, "external")
regs2 = p2.box(S("Gov registries", "data.gov.sg · URA · OneMap"), 1640, 360, 180, 60, "external")
supa2 = p2.box(S("Supabase", "Postgres · Storage · Realtime"), 1640, 470, 180, 60, "data")

p2.edge(users, lb2, "HTTPS", exit=(1, 0.5), entry=(0, 0.5))
p2.edge(lb2, fe2, "sixsevens.me", exit=(fx(370, LB2[0], LB2[2]), 1), entry=(0.5, 0))
p2.edge(lb2, kong2, "api.", exit=(fx(560, LB2[0], LB2[2]), 1), entry=(0.5, 0))
p2.edge(lb2, graf2, "grafana.", exit=(fx(1385, LB2[0], LB2[2]), 1), entry=(0.5, 0))
p2.edge(kong2, api2, "", exit=(0.25, 1), entry=(0.5, 0), points=[(520, 465), (370, 465)])
p2.edge(kong2, aud2, "/audit", exit=(0.5, 1), entry=(0.5, 0))
p2.edge(keda2, wsub, "scales", DASHED, exit=(0, 0.5), entry=(1, fx(530, WSUB[1], WSUB[3])))
p2.edge(api2, ps2, "publish", exit=(0.5, 1), entry=(fx(370, BUS2[0], BUS2[2]), 0))
p2.edge(ps2, aud2, "pull every event", exit=(fx(560, BUS2[0], BUS2[2]), 0), entry=(0.5, 1), both=True)
for wid, cx in workers2:
    p2.edge(ps2, wid, "pull in,<br>result out", exit=(fx(cx, BUS2[0], BUS2[2]), 0), entry=(0.5, 1), both=True)
p2.edge(sch2, ps2, "sweep", exit=(0.5, 0), entry=(fx(342, BUS2[0], BUS2[2]), 1))
p2.edge(cluster, nat2, "egress", DASHED, exit=(fx(1405, CLUSTER[0], CLUSTER[2]), 1), entry=(0.5, 0))
p2.edge(nat2, regs2, "", DASHED, exit=(1, 0.5), entry=(0, 0.5), points=[(1605, 700), (1605, 390)])
p2.edge(nat2, supa2, "HTTPS", DASHED, exit=(1, 0.5), entry=(0, 0.5), points=[(1605, 700), (1605, 500)])
p2.edge(wsub, vtx2, "Claude calls", DASHED, exit=(1, fx(490, WSUB[1], WSUB[3])), entry=(0, 0.5), points=[(1580, 490), (1580, 280)])

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
    ("Pub/Sub", "doc-worker", "4. pulled (its own filtered subscription)", False),
    ("doc-worker", "Supabase", "5. claim queued→processing (compare-and-swap); classify, extract; = analyzed", False),
    ("doc-worker", "Pub/Sub", "6. publish document.analyzed (+ Claude cost)", False),
    ("Pub/Sub", "audit-metrics", "7. every event stored once in audit_events", False),
    ("Supabase", "Kong + api", "8. Realtime: the document row changed", True),
    ("Kong + api", "Officer (UI)", "9. fresh snapshot over the WebSocket", True),
    ("Officer (UI)", "Kong + api", "10. review fields, approve", False),
    ("Kong + api", "Supabase", "11. status = approved (compare-and-swap)", False),
    ("Kong + api", "Pub/Sub", "12. publish document.approved (if it fails, step 11 is undone)", False),
    ("Pub/Sub", "detection-worker", "13. pulled", False),
    ("detection-worker", "Supabase", "14. = detecting; lead + specialists + checks; flags; = detected", False),
    ("detection-worker", "Pub/Sub", "15. publish document.detected (+ Claude cost)", False),
    ("detection-worker", "Supabase", "16. last approved document scanned: record a running analysis run", False),
    ("detection-worker", "Pub/Sub", "17. publish case.analysis.requested (by the system)", False),
    ("Pub/Sub", "case-worker", "18. pulled", False),
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
    "A worker that dies mid-run never acknowledges its message, so Pub/Sub redelivers it; work stuck past its limit is recovered by the sweeper (page 4).",
    60, 1340, 1500, 50, "note",
)

# ------------------------------------------------------------------ 4. Stuck work recovery
p4 = Page("4 Stuck work recovery", 1680, 920)
p4.box("Recovering stuck work (no database changes; the audit trail is the memory)", 40, 20, 1200, 40, "title")
s_sched = p4.box(S("Cloud Scheduler", "every 5 minutes"), 40, 225, 170, 80, "external")
s_topic = p4.box(S("domain-events", "type = sweep.requested"), 270, 215, 170, 100, "bus")
s_sweep = p4.box(S("Sweeper (in audit-metrics)", "finds work past its limit:<br>queued/uploaded &gt; 5 min (event lost)<br>processing/extracting/detecting &gt; 10 min<br>case analysis running &gt; 15 min"), 540, 185, 280, 160, "service")
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
p4.edge(s_topic, s_sweep, "pulled", exit=(1, 0.5), entry=(0, 0.5))
p4.edge(s_sweep, s_db, "read only", exit=(0.5, 1), entry=(0.5, 0))
p4.edge(s_sweep, s_topic2, "publish one<br>per stuck item", exit=(1, 0.5), entry=(0, 0.5))
p4.edge(s_group, s_out, "each publishes its outcome", exit=(0.5, 1), entry=(0.5, 0))
p4.edge(s_out, s_obs, "", exit=(0.5, 1), entry=(0.5, 0))
p4.box(
    "<b>Also handled</b><br>• Worker dies mid-run → its message is never acknowledged, so Pub/Sub redelivers it; after 5 attempts → dead-letter + email alert.<br>"
    "• A pod being replaced finishes its current run first (up to 10 minutes' grace) and takes no new events.<br>"
    "• Officer cancels a run on another instance → the run polls the audit trail / its status every 5 s and stops.<br>"
    "• Publishing an approval fails → the approval is undone and the officer sees an error (nothing left half-done).",
    40, 770, 1000, 130, "note",
)

# ------------------------------------------------------------------ 5. Observability
p5 = Page("5 Observability and cost", 1360, 640)
p5.box("Observability: metrics, traces, audit trail and cost", 40, 20, 1000, 40, "title")
o_kong = p5.box(S("Kong", "status codes, latency"), 40, 90, 280, 60, "gateway")
o_svc = p5.box(S("Every backend service", "GET /metrics (prometheus_client)<br>requests · events · stage timings<br>Claude calls/tokens/cost/latency per agent<br>cost per document · tool outcomes · stuck work"), 40, 190, 280, 150, "service")
SIDE_Y, SIDE_H = 90, 250
o_side = p5.box(S("Managed Prometheus", "a PodMonitoring per namespace<br>scrapes /metrics every 30 s"), 410, SIDE_Y, 210, SIDE_H, "gcp")
o_gmp = p5.box(S("Cloud Monitoring", "Managed Prometheus +<br>GKE, load balancer and Pub/Sub metrics<br>uptime checks from 3 regions<br>SLOs: 99% API · 95% detections &lt; 3 min"), 690, 160, 220, 110, "gcp")
o_alert = p5.box(S("Alert policies → email", "site down · SLO budget burning<br>dead letters · 5xx &gt; 5%<br>Claude spend/day · recovery gave up"), 1080, 50, 240, 90, "ops")
o_gra = p5.box(S("Grafana", "Status · Services · Pipeline<br>Agents · Cost (incl. per case)"), 1080, 170, 240, 90, "ops")
o_bill = p5.box(S("BigQuery billing export", "invoice lines, by namespace<br>(cloud cost per case)"), 690, 310, 220, 80, "data")
o_trace = p5.box(S("Cloud Trace", "one trace per upload, across services"), 40, 430, 280, 70, "gcp")
o_audit = p5.box(S("audit_events", "every action;<br>each run's Claude cost"), 410, 450, 210, 80, "data")
o_audsvc = p5.box(S("audit-metrics", "sums each run's cost<br>per document and case<br>(the $5 per-case cap reads it)"), 690, 450, 220, 80, "service")
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
# Icon-based, like a typical delivery overview: the repo triggers Actions, Actions fans out to the checks and the
# registry, and Argo CD pulls from git into the cluster. Icons are the public brand SVGs in docs/architecture/icons/,
# embedded in the file so it opens anywhere.
import base64

ICON_DIR = Path(__file__).parent / "icons"
ICON_BELOW = "verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
ICON_RIGHT = "labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=10;"


def icon(page: Page, name: str, label: str, x: int, y: int, size: int = 80, caption: str = ICON_BELOW) -> str:
    data = base64.b64encode((ICON_DIR / f"{name}.svg").read_bytes()).decode()
    cid = page._id()
    style = f"shape=image;html=1;aspect=fixed;imageAspect=0;fontSize=12;{caption}image=data:image/svg+xml,{data};"
    page.cells.append(
        f'<mxCell id="{cid}" value={quoteattr(label)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{size}" height="{size}" as="geometry"/></mxCell>'
    )
    return cid


p6 = Page("6 CI and deploy", 1300, 960)
p6.box("", 10, 10, 1280, 940, "group")
p6.box("<b>Delivery:</b> CI on every push and PR; a green push to main deploys (GitHub Actions → git → Argo CD)", 30, 20, 1000, 30, "title", "fontSize=16;")
dev = p6.box("Developers", 60, 459, 100, 90, "user", "shape=actor;verticalLabelPosition=bottom;verticalAlign=top;fillColor=#ffffff;strokeColor=#999999;")
repo = icon(p6, "github", "<b>Application repository</b><br>main branch", 380, 480)
act = icon(p6, "githubactions", "<b>GitHub Actions</b><br>ci-cd.yml, deploy.yml", 680, 480)
snyk = icon(p6, "snyk", "<b>Snyk</b><br>dependency scan, on lock-file changes<br>(non-blocking for now)", 910, 110, caption=ICON_RIGHT)
checkov = p6.box("<b style='font-size:28px;color:#2b3a8c'>checkov</b>", 880, 290, 140, 40, "text", "align=center;verticalAlign=middle;")
tf = icon(p6, "terraform", "<b>Terraform</b>", 1170, 270)
checks = p6.box("<b>Backend:</b> ruff · pyright · pytest + coverage<br><b>Frontend:</b> eslint · tsc · Playwright<br>"
                "<b>Security:</b> pip-audit · npm audit · gitleaks<br><b>Chart changes:</b> helm.yml, render + kind test", 870, 430, 400, 90, "service", "align=left;spacingLeft=10;fontSize=12;")
ar = icon(p6, "googlecloud", "<b>Artifact Registry</b><br>images by digest, Trivy-scanned,<br>signed (cosign, SBOM, SLSA)", 910, 610, caption=ICON_RIGHT)
gke = icon(p6, "kubernetes", "<b>Google Kubernetes Engine</b><br>Autopilot behind the load balancer;<br>Binary Authorization admits only signed images", 910, 820, caption=ICON_RIGHT)
argo = icon(p6, "argo", "<b>Argo CD</b>", 680, 820)

p6.edge(dev, repo, "Commits and<br>pushes code", exit=(1, 0.5), entry=(0, fx(504, 480, 80)))
p6.edge(repo, act, "Triggers CI<br>(push and PR)", exit=(1, 0.5), entry=(0, 0.5))
TRUNK = 830
p6.edge(act, snyk, "", exit=(1, 0.5), entry=(0, 0.5), points=[(TRUNK, 520), (TRUNK, 150)])
p6.edge(act, checkov, "", exit=(1, 0.5), entry=(0, 0.5), points=[(TRUNK, 520), (TRUNK, 310)])
p6.edge(act, checks, "", exit=(1, 0.5), entry=(0, 0.5), points=[(TRUNK, 520), (TRUNK, 475)])
p6.edge(act, ar, "Build · scan · sign · push<br>(green push to main)", exit=(1, 0.5), entry=(0, 0.5), points=[(TRUNK, 520), (TRUNK, 650)])
p6.edge(tf, checkov, "scans Terraform,<br>Dockerfiles, workflows<br>(blocking)", exit=(0, 0.5), entry=(1, 0.5))
p6.edge(act, repo, "Commits the image digests<br>(green push to main)", exit=(0.5, 0), entry=(0.5, 0), points=[(720, 70), (420, 70)])
p6.edge(ar, gke, "Pulls signed images", exit=(0.5, 1), entry=(0.5, 0))
p6.edge(argo, repo, "Polls git for<br>changes", exit=(0, 0.5), entry=(0, fx(544, 480, 80)), points=[(300, 860), (300, 544)])
p6.edge(argo, gke, "Syncs the chart · rolling update<br>rolls back if the check fails", exit=(1, 0.5), entry=(0, 0.5))

pages = [p1, p2, p3, p4, p5, p6]
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    '<mxfile host="app.diagrams.net" type="device">' + "".join(p.xml() for p in pages) + "</mxfile>\n", encoding="utf-8"
)
print(f"wrote {OUT} with {len(pages)} pages")
