"""
Generates the five Grafana dashboards in deploy/grafana/dashboards/. Edit the panels here, not the JSON, then run
from the repo root:  python deploy/grafana/build_dashboards.py deploy/grafana/dashboards
"""

import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
DS = {"type": "prometheus", "uid": "prometheus"}
# GKE's system metrics, limited to the app's namespaces (deploy/helm).
POD = ('monitored_resource="k8s_container",'
       'namespace_name=~"api|doc-worker|detection-worker|case-worker|audit-metrics|gateway|frontend|observability"')
LB = 'monitored_resource="https_lb_rule"'
GIB = "1073741824"
NOT_PUSH = 'route!~"/push.*"'
LINKS = [
    {"title": "Overview", "url": "/d/six-overview", "type": "link", "icon": "dashboard"},
    {"title": "Services", "url": "/d/six-services", "type": "link", "icon": "dashboard"},
    {"title": "Pipeline", "url": "/d/six-pipeline", "type": "link", "icon": "dashboard"},
    {"title": "Agents", "url": "/d/six-agents", "type": "link", "icon": "dashboard"},
    {"title": "Cost", "url": "/d/six-cost", "type": "link", "icon": "dashboard"},
    {"title": "Traces (Cloud Trace)", "url": "https://console.cloud.google.com/traces/explorer", "type": "link",
     "icon": "external link", "targetBlank": True},
]


class Layout:
    """
    Panels in rows. With fold=True (the detail dashboards) every row after the first starts collapsed: the page opens
    on its summary, and each further row is one click away, which keeps a dashboard readable at a glance.
    """

    def __init__(self, fold=False):
        self.x = self.y = self.row_h = 0
        self.panels = []
        self.next_id = 1
        self.fold = fold
        self.rows = 0
        self.folded_row = None  # the collapsed row new panels go inside

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        panel["id"] = self.next_id
        self.next_id += 1
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        (self.folded_row["panels"] if self.folded_row is not None else self.panels).append(panel)

    def row(self, title):
        self.x, self.y = 0, self.y + self.row_h
        self.row_h = 0
        collapsed = self.fold and self.rows > 0
        self.rows += 1
        self.folded_row = None
        row = {"type": "row", "title": title, "collapsed": collapsed, "panels": []}
        self.add(row, 24, 1)
        self.folded_row = row if collapsed else None
        self.x, self.y, self.row_h = 0, self.y + 1, 0


def target(expr, legend="", instant=False, ref="A"):
    t = {"refId": ref, "datasource": DS, "expr": expr, "legendFormat": legend or "__auto"}
    if instant:
        t.update(instant=True, range=False)
    return t


def targets(queries, instant=False):
    return [target(expr, legend, instant, chr(65 + i)) for i, (expr, legend) in enumerate(queries)]


def stat(title, expr, unit, desc="", decimals=None, thresholds=None):
    defaults = {"unit": unit, "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": desc, "datasource": DS,
            "targets": [target(expr, instant=True)],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False, "fields": ""},
                        "colorMode": "value", "graphMode": "none", "textMode": "auto", "justifyMode": "auto"}}


def series(title, queries, unit, desc="", stack=False, bars=False, interval=None):
    custom = {"drawStyle": "bars" if bars else "line", "fillOpacity": 30 if (stack or bars) else 10,
              "lineWidth": 1, "showPoints": "never", "spanNulls": True,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    panel = {"type": "timeseries", "title": title, "description": desc, "datasource": DS,
             "targets": targets(queries),
             "fieldConfig": {"defaults": {"unit": unit, "custom": custom, "min": 0}, "overrides": []},
             "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max"]},
                         "tooltip": {"mode": "multi", "sort": "desc"}}}
    if interval:
        panel["interval"] = interval
    return panel


def bars(title, queries, unit, desc="", decimals=None):
    defaults = {"unit": unit, "min": 0, "color": {"mode": "palette-classic"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "bargauge", "title": title, "description": desc, "datasource": DS,
            "targets": targets(queries, instant=True),
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "valueMode": "color", "namePlacement": "left",
                        "reduceOptions": {"calcs": ["lastNotNull"], "values": False, "fields": ""}}}


def pie(title, expr, legend, desc=""):
    return {"type": "piechart", "title": title, "description": desc, "datasource": DS,
            "targets": [target(expr, legend, instant=True)],
            "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
            "options": {"pieType": "donut", "legend": {"displayMode": "table", "placement": "right", "values": ["value"]},
                        "reduceOptions": {"calcs": ["lastNotNull"], "values": False, "fields": ""}}}


def text(title, content):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}}


def table(title, expr, desc=""):
    return {"type": "table", "title": title, "description": desc, "datasource": DS,
            "targets": [{**target(expr, instant=True), "format": "table"}],
            "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True, "__name__": True}}}],
            "fieldConfig": {"defaults": {}, "overrides": []}, "options": {"showHeader": True}}


def dashboard(uid, title, layout, time_from="now-6h", variables=None, desc=""):
    return {"uid": uid, "title": title, "description": desc, "tags": ["six-sevens"], "timezone": "browser",
            "editable": True, "graphTooltip": 1, "schemaVersion": 39, "version": 1, "refresh": "1m",
            "time": {"from": time_from, "to": "now"}, "links": LINKS,
            "templating": {"list": variables or []}, "annotations": {"list": []}, "panels": layout.panels}


def textbox(name, label, value, desc):
    return {"type": "textbox", "name": name, "label": label, "description": desc, "query": value,
            "current": {"text": value, "value": value}, "options": [{"selected": True, "text": value, "value": value}],
            "hide": 0}


BQ = {"type": "grafana-bigquery-datasource", "uid": "bigquery"}
COSTS = "billing_export.costs"


def bq_target(sql, table=True):
    return {"refId": "A", "datasource": BQ, "rawSql": sql, "format": 1 if table else 0,
            "rawQuery": True, "editorMode": "code"}


def bq_stat(title, sql, desc="", decimals=2):
    panel = stat(title, "", "none", desc, decimals)
    panel.update(datasource=BQ, targets=[bq_target(sql)])
    return panel


def bq_bars(title, sql, label, value, desc=""):
    return {"type": "bargauge", "title": title, "description": desc, "datasource": BQ, "targets": [bq_target(sql)],
            "fieldConfig": {"defaults": {"unit": "none", "decimals": 2, "min": 0, "color": {"mode": "palette-classic"},
                                         "displayName": "${__data.fields." + label + "}"}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "valueMode": "color",
                        "namePlacement": "left",
                        "reduceOptions": {"calcs": ["lastNotNull"], "values": True, "fields": f"/^{value}$/"}}}


def bq_series(title, sql, desc=""):
    panel = series(title, [], "none", desc, bars=True, stack=True)
    panel.update(datasource=BQ, targets=[bq_target(sql, table=False)])
    return panel


def bq_table(title, sql, desc=""):
    return {"type": "table", "title": title, "description": desc, "datasource": BQ, "targets": [bq_target(sql)],
            "fieldConfig": {"defaults": {"decimals": 4}, "overrides": []}, "options": {"showHeader": True}}


def per_case(title, sql, count_expr, desc=""):
    """A BigQuery sum over a Prometheus count, divided in Grafana (a mixed panel and a math expression)."""
    panel = stat(title, "", "none", desc, 3)
    panel.update(datasource={"type": "datasource", "uid": "-- Mixed --"}, targets=[
        {**bq_target(sql), "hide": True},
        {**target(f"clamp_min({count_expr}, 1)", instant=True, ref="B"), "hide": True},
        {"refId": "C", "datasource": {"type": "__expr__", "uid": "__expr__"}, "type": "math", "expression": "$A / $B"},
    ])
    return panel


def or0(expr):
    return f"(({expr}) or vector(0))"


# ---------------------------------------------------------------- Services
L = Layout(fold=True)
L.row("At a glance")
L.add(stat("API requests / s", f"sum(rate(http_requests_total{{{NOT_PUSH}}}[5m]))", "reqps",
           "Requests to the app's own routes (Pub/Sub pushes excluded)."), 4, 4)
L.add(stat("5xx share", f"sum(rate(http_requests_total{{status=~\"5..\",{NOT_PUSH}}}[5m])) / clamp_min(sum(rate(http_requests_total{{{NOT_PUSH}}}[5m])), 1e-9)",
           "percentunit", thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.01}, {"color": "red", "value": 0.05}]), 4, 4)
L.add(stat("API p95 latency", f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{NOT_PUSH}}}[5m])))", "s",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 3}]), 4, 4)
L.add(stat("Pods running", f"count(count by (pod_name) (kubernetes_io:container_uptime{{{POD}}}))", "short",
           "Pods across the app's namespaces on GKE (Kong, the frontend and Grafana included).", decimals=0), 4, 4)
L.add(stat("Dead-lettered (range)", "sum(increase(pubsub_googleapis_com:subscription_dead_letter_message_count[$__range]))", "short",
           "Events that failed every delivery attempt. Should be 0; read them from domain-events-dead-letter-pull.",
           decimals=0, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]), 4, 4)
L.add(stat("Claude spend (range)", "sum(increase(llm_cost_usd_total[$__range]))", "currencyUSD", decimals=2), 4, 4)

L.row("Application (from /metrics)")
L.add(series("Requests / s by service", [(f"sum by (namespace) (rate(http_requests_total{{{NOT_PUSH}}}[5m]))", "{{namespace}}")], "reqps"), 12, 8)
L.add(series("5xx / s by route", [("sum by (namespace, route) (rate(http_requests_total{status=~\"5..\"}[5m]))", "{{namespace}} {{route}}")], "reqps"), 12, 8)
L.add(series("p95 latency by route", [(f"histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{{{NOT_PUSH}}}[5m])))", "{{route}}")], "s"), 12, 8)
L.add(table("Running revisions", "count by (namespace, roles, revision) (app_info)",
            "Each process's roles and revision; a new row after every deploy."), 12, 8)

L.row("Gateway (Kong)")
L.add(series("Kong requests / s by status", [("sum by (code) (rate(kong_http_requests_total[5m]))", "{{code}}")], "reqps", stack=True), 12, 8)
L.add(series("Kong p95 latency", [
    ("histogram_quantile(0.95, sum by (le) (rate(kong_request_latency_ms_bucket[5m])))", "total"),
    ("histogram_quantile(0.95, sum by (le) (rate(kong_upstream_latency_ms_bucket[5m])))", "upstream"),
    ("histogram_quantile(0.95, sum by (le) (rate(kong_kong_latency_ms_bucket[5m])))", "kong itself"),
], "ms"), 12, 8)

L.row("GKE (platform metrics)")
L.add(series("Load balancer requests / s by backend and class", [(f"sum by (backend_target_name, response_code_class) (rate(loadbalancing_googleapis_com:https_request_count{{{LB}}}[5m]))", "{{backend_target_name}} {{response_code_class}}")], "reqps",
             "Everything reaching sixsevens.me, api. and grafana., before Kong."), 12, 8)
L.add(series("Load balancer p95 latency by backend", [(f"histogram_quantile(0.95, sum by (le, backend_target_name) (rate(loadbalancing_googleapis_com:https_total_latencies_bucket{{{LB}}}[5m])))", "{{backend_target_name}}")], "ms"), 12, 8)
L.add(series("Pods by namespace", [(f"count by (namespace_name) (kubernetes_io:container_uptime{{{POD}}})", "{{namespace_name}}")], "short", stack=True,
             desc="A second detection-worker pod appears here when KEDA scales on the backlog."), 12, 8)
L.add(series("Container restarts per 15 min", [(f"sum by (namespace_name) (increase(kubernetes_io:container_restart_count{{{POD}}}[15m]))", "{{namespace_name}}")], "short",
             "A crash or an out-of-memory kill; should stay at 0."), 12, 8)
L.add(series("CPU used / requested, max per namespace", [(f"max by (namespace_name) (kubernetes_io:container_cpu_request_utilization{{{POD}}})", "{{namespace_name}}")], "percentunit",
             "Autopilot bills the request, so a pod well under it is oversized."), 12, 8)
L.add(series("Memory used / requested, max per namespace", [(f"max by (namespace_name) (kubernetes_io:container_memory_request_utilization{{{POD},memory_type=\"non-evictable\"}})", "{{namespace_name}}")], "percentunit",
             "Near 100% risks an out-of-memory kill; raise the role's memory in the gke block of deploy/terraform/roles.json."), 12, 8)
services = dashboard("six-services", "Six Sevens · Services", L, desc="Request rate, errors, latency and capacity per service.")

# ---------------------------------------------------------------- Pipeline
L = Layout(fold=True)
L.row("Events")
L.add(series("Events published / s by type", [("sum by (type) (rate(events_published_total{result=\"ok\"}[5m]))", "{{type}}")], "short", stack=True), 12, 8)
L.add(series("Publish failures", [("sum by (type) (increase(events_published_total{result=\"error\"}[5m]))", "{{type}}")], "short",
             "A failed publish fails the officer's action (or, for audit-only events, is logged)."), 12, 8)
L.add(series("Events handled / s by role", [("sum by (role, result) (rate(events_handled_total[5m]))", "{{role}} {{result}}")], "short"), 12, 8)
L.add(series("Handler failures by role and type", [("sum by (role, type) (increase(events_handled_total{result=\"error\"}[5m]))", "{{role}} {{type}}")], "short",
             "A failed event is redelivered, and dead-lettered after 5 attempts."), 12, 8)

L.row("Stage timings")
L.add(series("Stage duration p50 / p95", [
    ("histogram_quantile(0.5, sum by (le, stage) (rate(stage_duration_seconds_bucket{outcome=\"success\"}[1h])))", "p50 {{stage}}"),
    ("histogram_quantile(0.95, sum by (le, stage) (rate(stage_duration_seconds_bucket{outcome=\"success\"}[1h])))", "p95 {{stage}}"),
], "s", "From the duration each end-of-run event carries (document.analyzed, document.detected, case.analysis.completed)."), 12, 8)
L.add(bars("Stage runs by outcome (range)", [("sum by (stage, outcome) (increase(stage_duration_seconds_count[$__range]))", "{{stage}} {{outcome}}")], "short", decimals=0), 12, 8)
L.add(series("Handler p95 by role", [("histogram_quantile(0.95, sum by (le, role) (rate(event_handler_duration_seconds_bucket[15m])))", "{{role}}")], "s"), 12, 8)
L.add(pie("Detection verdicts (range)", "sum by (verdict) (increase(detection_verdicts_total[$__range]))", "{{verdict}}"), 12, 8)

L.row("Pub/Sub")
L.add(series("Backlog by subscription", [("max by (subscription_id) (pubsub_googleapis_com:subscription_num_undelivered_messages)", "{{subscription_id}}")], "short",
             "Messages waiting to be pulled and acked; KEDA scales each worker on its own."), 12, 8)
L.add(series("Oldest unacked message", [("max by (subscription_id) (pubsub_googleapis_com:subscription_oldest_unacked_message_age)", "{{subscription_id}}")], "s",
             "Above ~600 s a message has outlived the ack deadline at least once."), 12, 8)
L.add(series("Acked / s by subscription", [("sum by (subscription_id) (rate(pubsub_googleapis_com:subscription_ack_message_count[5m]))", "{{subscription_id}}")], "short",
             "Events the workers finished with (acked once the handler returns)."), 12, 8)
L.add(series("Dead-lettered per hour", [("sum by (subscription_id) (increase(pubsub_googleapis_com:subscription_dead_letter_message_count[1h]))", "{{subscription_id}}")], "short", bars=True), 12, 8)

L.row("Stuck work and recovery")
L.add(stat("Stuck now", "sum(max by (stage) (stuck_work))", "short",
           "Work past its stage's limit at the last sweep (every 5 minutes).", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}]), 6, 4)
L.add(stat("Recovered (range)", "sum(increase(recoveries_total{result=\"recovered\"}[$__range]))", "short", decimals=0), 6, 4)
L.add(stat("Gave up (range)", "sum(increase(recoveries_total{result=\"exhausted\"}[$__range]))", "short",
           "Marked failed after RECOVERY_MAX_ATTEMPTS (case analyses are marked failed at once, not rerun).", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]), 6, 4)
L.add(stat("Cancels picked up remotely (range)", "sum(increase(cancels_seen_remotely_total[$__range]))", "short",
           "Cancels that reached a run on another instance by polling.", decimals=0), 6, 4)
L.add(series("Stuck by stage", [("max by (stage) (stuck_work)", "{{stage}}")], "short", stack=True), 12, 8)
L.add(series("Recovery outcomes", [("sum by (stage, result) (increase(recoveries_total[15m]))", "{{stage}} {{result}}")], "short", bars=True), 12, 8)

L.row("Supabase")
L.add(series("Supabase calls / s by table", [("topk(10, sum by (target) (rate(supabase_calls_total[5m])))", "{{target}}")], "reqps", stack=True), 24, 8)
pipeline = dashboard("six-pipeline", "Six Sevens · Pipeline and events", L, desc="Events, stage timings, Pub/Sub delivery and Supabase load.")

# ---------------------------------------------------------------- Agents
CACHE_ALL = 'kind=~"input|cache_read|cache_write"'
L = Layout(fold=True)
L.row("At a glance (selected range)")
L.add(stat("Claude calls", "sum(increase(llm_calls_total[$__range]))", "short", decimals=0), 4, 4)
L.add(stat("Spend", "sum(increase(llm_cost_usd_total[$__range]))", "currencyUSD", decimals=2), 4, 4)
L.add(stat("Cache hit ratio", f"sum(increase(llm_tokens_total{{kind=\"cache_read\"}}[$__range])) / sum(increase(llm_tokens_total{{{CACHE_ALL}}}[$__range]))", "percentunit",
           "Share of input tokens served from the prompt cache (billed at a tenth of the input price).",
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.3}, {"color": "green", "value": 0.6}]), 4, 4)
L.add(stat("Saved by caching", "sum(increase(llm_cache_savings_usd_total[$__range]))", "currencyUSD", decimals=2), 4, 4)
L.add(stat("Call error rate", "sum(increase(llm_calls_total{result=\"error\"}[$__range])) / sum(increase(llm_calls_total[$__range]))", "percentunit",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.02}, {"color": "red", "value": 0.1}]), 4, 4)
L.add(stat("Runs out of model calls", "sum(increase(llm_budget_exhausted_total[$__range]))", "short",
           "Runs that hit their model-call ceiling (DOCUMENT_MAX_MODEL_CALLS, the detection and case limits).", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}]), 4, 4)

L.row("Calls and latency")
L.add(series("Calls / min by agent", [("sum by (agent) (rate(llm_calls_total{result=\"ok\"}[5m])) * 60", "{{agent}}")], "short", stack=True), 12, 8)
L.add(series("Tokens / min by kind", [("sum by (kind) (rate(llm_tokens_total[5m])) * 60", "{{kind}}")], "short", stack=True,
             desc="input = uncached input; cache_read and cache_write are the cached parts."), 12, 8)
L.add(series("p95 call latency by agent", [("histogram_quantile(0.95, sum by (le, agent) (rate(llm_call_duration_seconds_bucket[15m])))", "{{agent}}")], "s"), 12, 8)
L.add(bars("Calls per unit of work (average, range)", [("sum by (work) (increase(work_llm_calls_sum[$__range])) / sum by (work) (increase(work_llm_calls_count[$__range]))", "{{work}}")], "short",
           "document = classify + extract; detection = lead + specialists; case_analysis = planner, judge and summaries.", decimals=1), 12, 8)

L.row("Prompt cache")
L.add(series("Cache hit ratio over time", [
    (f"sum(rate(llm_tokens_total{{kind=\"cache_read\"}}[30m])) / sum(rate(llm_tokens_total{{{CACHE_ALL}}}[30m]))", "all agents"),
    (f"sum by (model) (rate(llm_tokens_total{{kind=\"cache_read\"}}[30m])) / sum by (model) (rate(llm_tokens_total{{{CACHE_ALL}}}[30m]))", "{{model}}"),
], "percentunit"), 12, 8)
L.add(bars("Cache hit ratio by agent (range)", [(f"sum by (agent) (increase(llm_tokens_total{{kind=\"cache_read\"}}[$__range])) / sum by (agent) (increase(llm_tokens_total{{{CACHE_ALL}}}[$__range]))", "{{agent}}")], "percentunit",
           "Low for an agent that should reuse its system prompt: check its cache_control breakpoint and cache_gate."), 12, 8)
L.add(bars("Cache reads vs writes by agent (range)", [
    ("sum by (agent) (increase(llm_tokens_total{kind=\"cache_read\"}[$__range]))", "{{agent}} read"),
    ("sum by (agent) (increase(llm_tokens_total{kind=\"cache_write\"}[$__range]))", "{{agent}} write"),
], "short", "Writes cost 1.25x input and pay off only when read again within 5 minutes."), 12, 8)
L.add(bars("Cache hit ratio by kind of work (range)", [("sum by (work) (increase(work_llm_cache_read_tokens_total[$__range])) / sum by (work) (increase(work_llm_input_tokens_total[$__range]))", "{{work}}")], "percentunit"), 12, 8)

L.row("Detection tools")
L.add(bars("Tool results by outcome (range)", [("sum by (tool, outcome) (increase(detection_tool_calls_total[$__range]))", "{{tool}} · {{outcome}}")], "short", decimals=0), 12, 10)
L.add(bars("Unable to assess or failed, share per tool (range)", [("sum by (tool) (increase(detection_tool_calls_total{outcome=~\"unable|error\"}[$__range])) / sum by (tool) (increase(detection_tool_calls_total[$__range]))", "{{tool}}")], "percentunit",
           "A tool that often can't assess leaves gaps in the report."), 12, 10)
L.add(series("Call errors by agent", [("sum by (agent) (increase(llm_calls_total{result=\"error\"}[15m]))", "{{agent}}")], "short"), 24, 7)

L.row("Sensibility checks")
EVALS_MARKED = 'result=~"pass|warn|fail"'
L.add(series("Warnings and failures per check (share of runs marked)", [
    (f"sum by (agent, check) (increase(agent_eval_results_total{{result=~\"warn|fail\"}}[1h])) / sum by (agent, check) (increase(agent_eval_results_total{{{EVALS_MARKED}}}[1h]))", "{{agent}} · {{check}}"),
], "percentunit", "Code checks of each agent run: did it take the expected path, is its answer sensible. Not accuracy."), 12, 9)
L.add(bars("Failures by check (range)", [("sum by (agent, check) (increase(agent_eval_results_total{result=\"fail\"}[$__range]))", "{{agent}} · {{check}}")], "short",
           "A failure means a design rule was broken; open the run's agent trace to see why.", decimals=0), 12, 9)
agents = dashboard("six-agents", "Six Sevens · Agentic workflows", L,
                   desc="Claude calls, tokens, latency, prompt cache, detection tools and sensibility checks per agent.")

# ---------------------------------------------------------------- Cost
# Autopilot bills each pod's requested vCPU and memory per second; GKE samples them once a minute, so a range's
# sum of samples / 60 is requested vCPU-hours (GiB-hours for memory).
CPU = f"kubernetes_io:container_cpu_request_cores{{{POD}}}"
MEM = f"kubernetes_io:container_memory_request_bytes{{{POD}}}"
TIB = "1099511627776"


def pod_cost(by=""):
    group = f" by ({by})" if by else ""
    return (f"sum{group} (sum_over_time({CPU}[$__range])) / 60 * $cpu_price"
            f" + sum{group} (sum_over_time({MEM}[$__range])) / 60 / {GIB} * $mem_price")


gke = or0(pod_cost())
pubsub = f"{or0('sum(increase(pubsub_googleapis_com:topic_byte_cost[$__range]))')} / {TIB} * $pubsub_price"
claude = or0("sum(increase(llm_cost_usd_total[$__range]))")
L = Layout(fold=True)
L.row("Billed by Google (Cloud Billing export; a few hours behind, in the billing account's currency)")
L.add(bq_stat("Billed this month", f"SELECT SUM(net_cost) AS billed FROM {COSTS} WHERE invoice_month = FORMAT_DATE('%Y%m', CURRENT_DATE())",
              "This project's invoice so far this month, after credits (free tier, promotions)."), 6, 4)
L.add(bq_stat("Billed in range", f"SELECT SUM(net_cost) AS billed FROM {COSTS} WHERE $__timeFilter(time)"), 6, 4)
L.add(bq_stat("Credits in range", f"SELECT SUM(credits) AS credits FROM {COSTS} WHERE $__timeFilter(time)",
              "Negative: what the free tier and promotions took off."), 6, 4)
L.add(bq_stat("Currency", f"SELECT ANY_VALUE(currency) AS currency FROM {COSTS}", decimals=None), 6, 4)
L.add(bq_bars("Billed by service (range)",
              f"SELECT service, ROUND(SUM(net_cost), 4) AS cost FROM {COSTS} WHERE $__timeFilter(time) GROUP BY service ORDER BY cost DESC",
              "service", "cost"), 12, 8)
L.add(bq_series("Billed per day by service",
                f"SELECT TIMESTAMP_TRUNC(time, DAY) AS time, service, SUM(net_cost) AS cost FROM {COSTS} WHERE $__timeFilter(time) GROUP BY 1, 2 ORDER BY 1"), 12, 8)
L.add(bq_table("Top SKUs (range)",
               f"SELECT service, sku, ROUND(SUM(cost), 4) AS cost, ROUND(SUM(credits), 4) AS credits, ROUND(SUM(net_cost), 4) AS net FROM {COSTS} WHERE $__timeFilter(time) GROUP BY service, sku ORDER BY net DESC LIMIT 15"), 24, 8)

L.row("Live estimates (selected range, defaults to this month)")
L.add(stat("Total (estimate)", f"{claude} + {gke} + {pubsub}", "currencyUSD",
           "Claude + GKE pods + Pub/Sub at list price, before credits. Leaves out the load balancer and the cluster fee; the billed row above has them.", decimals=2), 6, 4)
L.add(stat("Claude", claude, "currencyUSD", decimals=2), 6, 4)
L.add(stat("GKE pods (estimate)", gke, "currencyUSD", "vCPU-hours and GiB-hours the app's pods requested, times the prices above.", decimals=2), 6, 4)
L.add(stat("Pub/Sub (estimate)", pubsub, "currencyUSD", "Billable bytes published and delivered; the first 10 GiB a month are free.", decimals=4), 6, 4)
L.add(stat("Claude, projected month", "sum(increase(llm_cost_usd_total[24h])) * 30", "currencyUSD", "The last 24 hours times 30.", decimals=2), 6, 4)
L.add(stat("Avg per document", "sum(increase(work_llm_cost_usd_sum{work=\"document\"}[$__range])) / sum(increase(work_llm_cost_usd_count{work=\"document\"}[$__range]))", "currencyUSD", "Classify and extract.", decimals=3), 6, 4)
L.add(stat("Avg per detection run", "sum(increase(work_llm_cost_usd_sum{work=\"detection\"}[$__range])) / sum(increase(work_llm_cost_usd_count{work=\"detection\"}[$__range]))", "currencyUSD", decimals=3), 6, 4)
L.add(stat("Avg per case analysis", "sum(increase(work_llm_cost_usd_sum{work=\"case_analysis\"}[$__range])) / sum(increase(work_llm_cost_usd_count{work=\"case_analysis\"}[$__range]))", "currencyUSD", decimals=3), 6, 4)

L.row("Claude")
L.add(series("Spend per hour by model", [("sum by (model) (increase(llm_cost_usd_total[1h]))", "{{model}}")], "currencyUSD", bars=True, stack=True, interval="1h"), 12, 8)
L.add(bars("Spend by agent (range)", [("sort_desc(sum by (agent) (increase(llm_cost_usd_total[$__range])))", "{{agent}}")], "currencyUSD", decimals=3), 12, 8)
L.add(series("Cost per unit of work, p50 / p95", [
    ("histogram_quantile(0.5, sum by (le, work) (rate(work_llm_cost_usd_bucket[6h])))", "p50 {{work}}"),
    ("histogram_quantile(0.95, sum by (le, work) (rate(work_llm_cost_usd_bucket[6h])))", "p95 {{work}}"),
], "currencyUSD"), 12, 8)
L.add(bars("Tokens by kind (range)", [
    ("sum by (kind) (increase(llm_tokens_total[$__range]))", "{{kind}} tokens"),
], "short", "Where the tokens go; the cache_read share is cheap, input and output are not.", decimals=0), 12, 8)

L.row("GKE")
L.add(bars("Estimated pod cost by namespace (range)", [(pod_cost("namespace_name"), "{{namespace_name}}")], "currencyUSD", decimals=3), 12, 8)
L.add(series("vCPU requested by namespace", [(f"sum by (namespace_name) ({CPU})", "{{namespace_name}}")], "short", stack=True,
             desc="What Autopilot bills for, minute by minute."), 12, 8)
L.add(stat("vCPU requested now", f"sum({CPU})", "short", decimals=2,
           desc="The free trial allows 8 vCPUs at once, across everything in the project.",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 6}, {"color": "red", "value": 8}]), 6, 4)
L.add(stat("Memory requested now", f"sum({MEM}) / {GIB}", "short", "GiB.", decimals=1), 6, 4)
L.add(text("About these numbers", (
    "Estimates at list price, before credits and taxes, live within minutes. **Billed cost** is the top row, from "
    "Cloud Billing's export (hours behind; empty until the export is turned on, see deploy/terraform/README.md). "
    "Claude isn't billed by Google, so its cost is always the app's own count, from each call's tokens and "
    "`backend/app/pricing.py`. The GKE prices are Autopilot's **asia-southeast1** list prices, approximate: "
    "check Google's pricing page and edit the boxes at the top if they differ.")), 12, 4)
CASES = 'sum(increase(stage_duration_seconds_count{stage="case.analysis.completed"}[$__range]))'
L.row("Per case (range): GKE from the billing export, by namespace (cost allocation, gke.tf)")
L.add(per_case("Cloud cost per case analysed",
               f"SELECT SUM(net_cost) AS billed FROM {COSTS} WHERE $__timeFilter(time)", CASES,
               "Everything Google billed in the range (the cluster, load balancer, Pub/Sub...) over the case analyses "
               "that finished in it. The cluster costs money idle, so few cases make each one look expensive."), 6, 4)
L.add(stat("Claude cost per case analysed", f"{claude} / clamp_min({CASES}, 1)", "currencyUSD",
           "Every Claude call in the range (documents, detection and analyses) over the case analyses that finished in it.",
           decimals=3), 6, 4)
L.add(stat("Case analyses finished (range)", or0(CASES), "short", decimals=0), 6, 4)
L.add(bq_stat("Cluster billed in range", f"SELECT SUM(net_cost) AS billed FROM {COSTS} WHERE $__timeFilter(time) AND namespace IS NOT NULL",
              "Lines GKE's cost allocation labelled with a namespace; the cluster's own fee and idle capacity have none."), 6, 4)
L.add(bq_bars("Billed by namespace (range)",
              f"SELECT namespace, ROUND(SUM(net_cost), 4) AS cost FROM {COSTS} WHERE $__timeFilter(time) AND namespace IS NOT NULL GROUP BY namespace ORDER BY cost DESC",
              "namespace", "cost", "Each service's share of the cluster: the api, each worker, the gateway, Grafana."), 24, 8)
cost_vars = [
    textbox("cpu_price", "USD / vCPU-hour", "0.0554", "GKE Autopilot pod vCPU price, asia-southeast1 (approximate)."),
    textbox("mem_price", "USD / GiB-hour", "0.0061", "GKE Autopilot pod memory price, asia-southeast1 (approximate)."),
    textbox("pubsub_price", "USD / TiB", "40", "Pub/Sub throughput price."),
]
cost = dashboard("six-cost", "Six Sevens · Cost", L, time_from="now/M", variables=cost_vars,
                 desc="Claude and cloud spend, per service, agent and unit of work.")

# ---------------------------------------------------------------- Status (uptime checks and SLOs, slo.tf)
UPTIME = 'monitoring_googleapis_com:uptime_check_check_passed{monitored_resource="uptime_url"}'
API_OK = 'namespace="api",route!="/health"'
API_5XX_28D = (f'sum(increase(http_requests_total{{{API_OK},status=~"5.."}}[28d])) '
               f'/ clamp_min(sum(increase(http_requests_total{{{API_OK}}}[28d])), 1)')
FAST_28D = ('sum(increase(stage_duration_seconds_bucket{stage="document.detected",le="180.0"}[28d])) '
            '/ clamp_min(sum(increase(stage_duration_seconds_count{stage="document.detected"}[28d])), 1)')
GOOD = [{"color": "red", "value": None}, {"color": "green", "value": 0.99}]
WORKERS = 'namespace_name=~"doc-worker|detection-worker|case-worker"'
# The home page: one screen that answers, in order, is it up, is work flowing, how fast, what it costs, what's wrong.
# Twelve panels; everything else is a click away on the four detail dashboards.
L = Layout()
L.row("Is it up and keeping its promises")
L.add(stat("Site reachable", f"min(avg by (host) ({UPTIME}))", "percentunit",
           "Share of the uptime checks (Asia, Europe, US, every 5 minutes) that reached sixsevens.me and the API.",
           decimals=0, thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 1}]), 4, 5)
L.add(stat("API success, 28 days", f"1 - ({API_5XX_28D})", "percentunit", "Objective 99% (Cloud Monitoring SLO).",
           decimals=2, thresholds=GOOD), 4, 5)
L.add(stat("Detection within 3 min, 28 days", FAST_28D, "percentunit", "Objective 95% (Cloud Monitoring SLO).",
           decimals=1, thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 0.95}]), 4, 5)
L.add(stat("Cases analysed", or0(CASES), "short", "Case analyses finished in the selected range.", decimals=0), 4, 5)
L.add(stat("Claude spend", claude, "currencyUSD", "Every Claude call in the selected range, at list price.", decimals=2), 4, 5)
L.add(stat("Failed events", "sum(increase(pubsub_googleapis_com:subscription_dead_letter_message_count[$__range]))", "short",
           "Events that failed every delivery attempt in the range. Should be 0.", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]), 4, 5)
L.row("Is work flowing (under load, a second worker appears)")
L.add(series("Waiting events by worker", [("max by (subscription_id) (pubsub_googleapis_com:subscription_num_undelivered_messages)", "{{subscription_id}}")], "short",
             "Events not yet picked up. KEDA adds a worker when this grows and removes it 5 minutes after it empties."), 12, 8)
L.add(series("Pods by worker", [(f"count by (namespace_name) (kubernetes_io:container_uptime{{monitored_resource=\"k8s_container\",{WORKERS}}})", "{{namespace_name}}")], "short", stack=True), 12, 8)
L.row("How fast, and what it costs")
L.add(series("Time per stage (median and slowest 5%)", [
    ("histogram_quantile(0.5, sum by (le, stage) (rate(stage_duration_seconds_bucket{outcome=\"success\"}[1h])))", "median {{stage}}"),
    ("histogram_quantile(0.95, sum by (le, stage) (rate(stage_duration_seconds_bucket{outcome=\"success\"}[1h])))", "p95 {{stage}}"),
], "s", "document.analyzed = classify and extract; document.detected = detection; case.analysis.completed = case analysis."), 12, 8)
L.add(series("Claude spend per hour by agent", [("sum by (agent) (increase(llm_cost_usd_total[1h]))", "{{agent}}")], "currencyUSD",
             bars=True, stack=True, interval="1h"), 12, 8)
L.row("Anything wrong")
L.add(series("Errors", [
    (f"sum(rate(http_requests_total{{status=~\"5..\",{NOT_PUSH}}}[5m]))", "API 5xx / s"),
    ("sum(rate(events_handled_total{result=\"error\"}[5m]))", "failed event handlers / s"),
], "short", "Both should sit at 0; a failed event is retried, then dead-lettered."), 12, 7)
L.add(series("Stuck work and recoveries", [
    ("sum(max by (stage) (stuck_work))", "stuck now"),
    ("sum(increase(recoveries_total{result=\"recovered\"}[15m]))", "recovered / 15 min"),
    ("sum(increase(recoveries_total{result=\"exhausted\"}[15m]))", "gave up / 15 min"),
], "short", "The sweeper checks every 5 minutes and re-sends stuck work."), 12, 7)
overview = dashboard("six-overview", "Six Sevens · Overview", L, time_from="now-6h",
                     desc="Is it up, is work flowing, how fast, what it costs, and anything wrong. Detail on the other four dashboards.")

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "status.json").unlink(missing_ok=True)  # replaced by the Overview
for name, board in (("overview", overview), ("services", services), ("pipeline", pipeline), ("agents", agents), ("cost", cost)):
    (OUT / f"{name}.json").write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
    print(name, len(board["panels"]), "panels")
