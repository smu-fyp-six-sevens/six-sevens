"""
Generates the four Grafana dashboards in deploy/grafana/dashboards/. Edit the panels here, not the JSON, then run
from the repo root:  python deploy/grafana/build_dashboards.py deploy/grafana/dashboards
"""

import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
DS = {"type": "prometheus", "uid": "prometheus"}
RUN = 'monitored_resource="cloud_run_revision"'
APP = 'container_name!~"collector|prometheus"'
NOT_PUSH = 'route!~"/push.*"'
LINKS = [
    {"title": "Services", "url": "/d/six-services", "type": "link", "icon": "dashboard"},
    {"title": "Pipeline", "url": "/d/six-pipeline", "type": "link", "icon": "dashboard"},
    {"title": "Agents", "url": "/d/six-agents", "type": "link", "icon": "dashboard"},
    {"title": "Cost", "url": "/d/six-cost", "type": "link", "icon": "dashboard"},
    {"title": "Traces (Cloud Trace)", "url": "https://console.cloud.google.com/traces/explorer", "type": "link",
     "icon": "external link", "targetBlank": True},
]


class Layout:
    def __init__(self):
        self.x = self.y = self.row_h = 0
        self.panels = []
        self.next_id = 1

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        panel["id"] = self.next_id
        self.next_id += 1
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        self.panels.append(panel)

    def row(self, title):
        self.x, self.y = 0, self.y + self.row_h
        self.row_h = 0
        self.add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)
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


def or0(expr):
    return f"(({expr}) or vector(0))"


# ---------------------------------------------------------------- Services
L = Layout()
L.row("At a glance")
L.add(stat("API requests / s", f"sum(rate(http_requests_total{{{NOT_PUSH}}}[5m]))", "reqps",
           "Requests to the app's own routes (Pub/Sub pushes excluded)."), 4, 4)
L.add(stat("5xx share", f"sum(rate(http_requests_total{{status=~\"5..\",{NOT_PUSH}}}[5m])) / clamp_min(sum(rate(http_requests_total{{{NOT_PUSH}}}[5m])), 1e-9)",
           "percentunit", thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.01}, {"color": "red", "value": 0.05}]), 4, 4)
L.add(stat("API p95 latency", f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{NOT_PUSH}}}[5m])))", "s",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 3}]), 4, 4)
L.add(stat("Instances running", f"sum(run_googleapis_com:container_instance_count{{{RUN},state=\"active\"}})", "short",
           "Active Cloud Run instances across every service (0 when everything has scaled to zero)."), 4, 4)
L.add(stat("Dead-lettered (range)", "sum(increase(pubsub_googleapis_com:subscription_dead_letter_message_count[$__range]))", "short",
           "Events that failed every delivery attempt. Should be 0; read them from domain-events-dead-letter-pull.",
           decimals=0, thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]), 4, 4)
L.add(stat("Claude spend (range)", "sum(increase(llm_cost_usd_total[$__range]))", "currencyUSD", decimals=2), 4, 4)

L.row("Application (from /metrics)")
L.add(series("Requests / s by service", [(f"sum by (namespace) (rate(http_requests_total{{{NOT_PUSH}}}[5m]))", "{{namespace}}")], "reqps"), 12, 8)
L.add(series("5xx / s by route", [("sum by (namespace, route) (rate(http_requests_total{status=~\"5..\"}[5m]))", "{{namespace}} {{route}}")], "reqps"), 12, 8)
L.add(series("p95 latency by route", [(f"histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{{{NOT_PUSH}}}[5m])))", "{{route}}")], "s"), 12, 8)
L.add(table("Running revisions", "count by (namespace, roles, revision) (app_info)",
            "Each process's roles and Cloud Run revision; a new row after every deploy."), 12, 8)

L.row("Gateway (Kong)")
L.add(series("Kong requests / s by status", [("sum by (code) (rate(kong_http_requests_total[5m]))", "{{code}}")], "reqps", stack=True), 12, 8)
L.add(series("Kong p95 latency", [
    ("histogram_quantile(0.95, sum by (le) (rate(kong_request_latency_ms_bucket[5m])))", "total"),
    ("histogram_quantile(0.95, sum by (le) (rate(kong_upstream_latency_ms_bucket[5m])))", "upstream"),
    ("histogram_quantile(0.95, sum by (le) (rate(kong_kong_latency_ms_bucket[5m])))", "kong itself"),
], "ms"), 12, 8)

L.row("Cloud Run (platform metrics)")
L.add(series("Requests / s by service and class", [(f"sum by (service_name, response_code_class) (rate(run_googleapis_com:request_count{{{RUN}}}[5m]))", "{{service_name}} {{response_code_class}}")], "reqps"), 12, 8)
L.add(series("p95 request latency by service", [(f"histogram_quantile(0.95, sum by (le, service_name) (rate(run_googleapis_com:request_latencies_bucket{{{RUN}}}[5m])))", "{{service_name}}")], "ms",
             "Workers' latency is a whole push: a detection run takes 1-2 minutes."), 12, 8)
L.add(series("Instances by service", [(f"sum by (service_name, state) (run_googleapis_com:container_instance_count{{{RUN}}})", "{{service_name}} {{state}}")], "short", stack=True), 12, 8)
L.add(series("Cold starts and p95 startup time", [
    (f"sum by (service_name) (increase(run_googleapis_com:container_startup_latencies_count{{{RUN},{APP}}}[15m]))", "{{service_name}} starts / 15m"),
], "short", "A new instance after scaling from zero waits roughly 10-30 s for its imports."), 12, 8)
L.add(series("CPU utilisation p95", [(f"histogram_quantile(0.95, sum by (le, service_name) (rate(run_googleapis_com:container_cpu_utilizations_bucket{{{RUN},{APP}}}[5m])))", "{{service_name}}")], "percentunit"), 12, 8)
L.add(series("Memory utilisation p95", [(f"histogram_quantile(0.95, sum by (le, service_name) (rate(run_googleapis_com:container_memory_utilizations_bucket{{{RUN},{APP}}}[5m])))", "{{service_name}}")], "percentunit",
             "Near 100% risks an out-of-memory restart; raise the role's memory in deploy/terraform/main.tf."), 12, 8)
services = dashboard("six-services", "Six Sevens · Services", L, desc="Request rate, errors, latency and capacity per service.")

# ---------------------------------------------------------------- Pipeline
L = Layout()
L.row("Events")
L.add(series("Events published / s by type", [("sum by (type) (rate(events_published_total{result=\"ok\"}[5m]))", "{{type}}")], "short", stack=True), 12, 8)
L.add(series("Publish failures", [("sum by (type) (increase(events_published_total{result=\"error\"}[5m]))", "{{type}}")], "short",
             "A failed publish fails the officer's action (or, for audit-only events, is logged)."), 12, 8)
L.add(series("Events handled / s by role", [("sum by (role, result) (rate(events_handled_total[5m]))", "{{role}} {{result}}")], "short"), 12, 8)
L.add(series("Handler failures by role and type", [("sum by (role, type) (increase(events_handled_total{result=\"error\"}[5m]))", "{{role}} {{type}}")], "short",
             "A failed push is redelivered, and dead-lettered after 5 attempts."), 12, 8)

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
             "Messages waiting for a push to succeed. The Stage 2 autoscaling signal."), 12, 8)
L.add(series("Oldest unacked message", [("max by (subscription_id) (pubsub_googleapis_com:subscription_oldest_unacked_message_age)", "{{subscription_id}}")], "s",
             "Above ~600 s a message has outlived the ack deadline at least once."), 12, 8)
L.add(series("Push responses by class", [("sum by (subscription_id, response_class) (rate(pubsub_googleapis_com:subscription_push_request_count[5m]))", "{{subscription_id}} {{response_class}}")], "reqps"), 12, 8)
L.add(series("Dead-lettered per hour", [("sum by (subscription_id) (increase(pubsub_googleapis_com:subscription_dead_letter_message_count[1h]))", "{{subscription_id}}")], "short", bars=True), 12, 8)

L.row("Stuck work and recovery")
L.add(stat("Stuck now", "sum(max by (stage) (stuck_work))", "short",
           "Work past its stage's limit at the last sweep (every 2 minutes).", decimals=0,
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
L = Layout()
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
agents = dashboard("six-agents", "Six Sevens · Agentic workflows", L, desc="Claude calls, tokens, latency, prompt cache and detection tools per agent.")

# ---------------------------------------------------------------- Cost
CPU = f"run_googleapis_com:container_cpu_allocation_time{{{RUN}}}"
MEM = f"run_googleapis_com:container_memory_allocation_time{{{RUN}}}"
REQ = f"run_googleapis_com:request_count{{{RUN}}}"
TIB = "1099511627776"
cloud_run = (f"{or0(f'sum(increase({CPU}[$__range]))')} * $cpu_price + {or0(f'sum(increase({MEM}[$__range]))')} * $mem_price"
             f" + {or0(f'sum(increase({REQ}[$__range]))')} * $req_price")
pubsub = f"{or0('sum(increase(pubsub_googleapis_com:topic_byte_cost[$__range]))')} / {TIB} * $pubsub_price"
claude = or0("sum(increase(llm_cost_usd_total[$__range]))")
L = Layout()
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
L.add(stat("Total (estimate)", f"{claude} + {cloud_run} + {pubsub}", "currencyUSD", "Claude + Cloud Run + Pub/Sub at list price, before any free tier.", decimals=2), 6, 4)
L.add(stat("Claude", claude, "currencyUSD", decimals=2), 6, 4)
L.add(stat("Cloud Run (estimate)", cloud_run, "currencyUSD", "vCPU-seconds and GiB-seconds allocated (sidecars included) and requests, times the prices above.", decimals=2), 6, 4)
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

L.row("Cloud Run")
L.add(bars("Estimated cost by service (range)", [(
    f"sum by (service_name) (increase({CPU}[$__range])) * $cpu_price + sum by (service_name) (increase({MEM}[$__range])) * $mem_price", "{{service_name}}")],
    "currencyUSD", decimals=3), 12, 8)
L.add(series("vCPU-seconds per hour by service", [(f"sum by (service_name) (increase({CPU}[1h]))", "{{service_name}}")], "short", bars=True, stack=True, interval="1h"), 12, 8)
L.add({**stat("vCPU-seconds used (range)", f"sum(increase({CPU}[$__range]))", "short", decimals=0,
              desc="The free tier covers 180,000 vCPU-seconds a month per billing account.",
              thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 140000}, {"color": "red", "value": 180000}])}, 6, 4)
L.add({**stat("GiB-seconds used (range)", f"sum(increase({MEM}[$__range]))", "short", decimals=0,
              desc="The free tier covers 360,000 GiB-seconds a month.",
              thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 280000}, {"color": "red", "value": 360000}])}, 6, 4)
L.add(text("About these numbers", (
    "Estimates at list price, before free tiers and taxes, live within minutes. **Billed cost** is the top row, from "
    "Cloud Billing's export (hours behind; empty until the export is turned on, see deploy/terraform/README.md). "
    "Claude isn't billed by Google, so its cost is always the app's own count. Cloud Run prices default to **asia-southeast1 (tier 2), request-based billing**; "
    "edit the boxes at the top if they change. Claude cost is computed in the app from each call's tokens and "
    "`backend/app/pricing.py`. Counters from an instance that stopped before its next scrape are slightly under-counted.")), 12, 4)
cost_vars = [
    textbox("cpu_price", "USD / vCPU-s", "0.0000336", "Cloud Run CPU price (request-based, tier 2)."),
    textbox("mem_price", "USD / GiB-s", "0.0000035", "Cloud Run memory price (request-based, tier 2)."),
    textbox("req_price", "USD / request", "0.0000004", "Cloud Run request price ($0.40 per million)."),
    textbox("pubsub_price", "USD / TiB", "40", "Pub/Sub throughput price."),
]
cost = dashboard("six-cost", "Six Sevens · Cost", L, time_from="now/M", variables=cost_vars,
                 desc="Claude and cloud spend, per service, agent and unit of work.")

OUT.mkdir(parents=True, exist_ok=True)
for name, board in (("services", services), ("pipeline", pipeline), ("agents", agents), ("cost", cost)):
    (OUT / f"{name}.json").write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
    print(name, len(board["panels"]), "panels")
