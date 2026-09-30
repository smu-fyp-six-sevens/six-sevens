# Fraud Detection — Implementation Map

Snapshot of where fraud/anomaly detection logic lives and is tested, as of 2026-09-30.

## 1. Core check modules — `backend/app/fraud_detection/`

| File | Dimension | Tests |
|---|---|---|
| `layout_anomalies.py` | Indentation/alignment/margin/spacing deviations in PDF layout | 108 |
| `numerical_validation.py` | Deterministic arithmetic/identity checks over extracted fields (balances, totals) | 88 |
| `metadata_analysis.py` | File-embedded metadata (author/producer/create-modify dates) tamper signals | 54 |
| `date_anomalies.py` | Date extraction/labeling and logical date consistency (future dates, end-before-start) | 38 |
| `spelling_anomaly.py` | Misspellings via SymSpell, filtered against `domain_terms.py` allow-list | 24 |
| `font_consistency.py` | Font family/size/emphasis (bold/italic/underline) inconsistency | 16 |
| `domain_terms.py` | Not a check — allow-list of legal/financial jargon feeding `spelling_anomaly.py` | — |

No image-manipulation or AI-generated-content check exists yet.

`case_match.py` (696 lines, 46 tests) — cross-document validation: verifies each case
document's company/individuals against the certificate of incumbency (UEN/name/ID fuzzy
matching). Gates fraud detection per its own docstring.

## 2. LangGraph Detection Agent — `backend/app/langgraph/detection/`

- **`tools.py`** — **stubbed, not wired to real logic.** All 5 `@tool` wrappers
  (`font_consistency_tool`, `spelling_tool`, `date_consistency_tool`,
  `transaction_balance_tool`, `metadata_tool`) return `unable_to_assess=True` with reason
  `"Not yet correctly implemented — pending rewire"` (constant `_PENDING_REWIRE`). None of the
  real `fraud_detection/*.py` functions are called from this agent. Shared plumbing helpers
  (`_document`, `_extracted_fields`, `_file_bytes`, etc.) exist but are currently unused.
- **`specialists.py`** — 3 `Specialist` dataclasses (`layout_rendering`, `content_validation`,
  `digital_authenticity`), each a tool-calling subgraph bound to a subset of the (stubbed)
  tools. `run_specialist()` compiles/caches one graph per specialist.
- **`graph.py`** — Lead Investigator graph: `load_context → lead_plan → [specialists via Send]
  → lead_review → optional second look → synthesize`. Lead picks a specialist team per document
  type via an LLM call, dispatches in parallel, deterministically merges findings into a final
  verdict (`flagged`/`inconclusive`/`clean`). `run_detection(document_id)` is the entry point
  wired into `POST /documents/{id}/detection/run`. Orchestration logic works end-to-end, but
  since every tool is stubbed, every specialist currently reports "unable to assess" — no real
  findings are produced by this path today.

## 3. API surface — `backend/app/routers/detection.py`

Real, working checks (bypass the stubbed LangGraph tools entirely, call `fraud_detection/*.py`
and `case_match.py` directly):

- `GET /documents/{id}/detection/font-consistency`
- `GET /documents/{id}/detection/layout-anomalies`
- `GET /documents/{id}/detection/spelling-anomaly`
- `GET /documents/{id}/detection/numerical-validation`
- `GET /documents/{id}/detection/date-anomalies`
- `GET /documents/{id}/detection/metadata-anomaly`
- `GET /documents/{id}/detection/case-match` (wraps `case_service.get_case_match_for_document`)

Agentic path (produces no real findings yet, see §2):

- `POST /documents/{id}/detection/run` → `langgraph.detection.graph.run_detection`
- `GET /documents/{id}/detection/report` → reads persisted langgraph trace's `detection_report`
- `GET /cases/{case_id}/detection/reports` → all stored reports for a case

`backend/app/routers/extraction.py` exposes parallel dev/QA endpoints calling the same real
fraud_detection functions on ad-hoc uploads (no persisted document/case): `POST
/test-spelling-anomaly`, `/test-metadata-anomaly`, `/test-numerical-validation`,
`/test-date-anomalies`, `/test-layout-anomalies`.

## 4. Test coverage — `backend/tests/`

| Test file | Covers | # tests |
|---|---|---|
| `test_layout_anomalies.py` | `fraud_detection/layout_anomalies.py` | 108 |
| `test_numerical_validation.py` | `fraud_detection/numerical_validation.py` | 88 |
| `test_metadata_analysis.py` | `fraud_detection/metadata_analysis.py` | 54 |
| `test_case_match.py` | `case_match.py` | 46 |
| `test_langgraph.py` | LangGraph pipeline generally (not detection-specific) | 37 |
| `test_date_anomalies.py` | `fraud_detection/date_anomalies.py` | 38 |
| `test_spelling_anomaly.py` | `fraud_detection/spelling_anomaly.py` | 24 |
| `test_detection.py` | `routers/detection.py` endpoints | 19 |
| `test_font_consistency.py` | `fraud_detection/font_consistency.py` | 16 |
| `test_fraud_injections.py` | synthetic-fraud injection correctness | 15 |
| `test_injection_router.py` | `routers/injection.py` | 8 |
| `test_injectors.py` | `injection/injectors.py` | 7 |

No dedicated test file exercises `langgraph/detection/tools.py`, `specialists.py`, or
`graph.py` beyond generic coverage in `test_langgraph.py`/`test_detection.py` — consistent with
the tool layer being a known-stubbed work in progress.

## 5. Frontend surface

`frontend/src/pages/TestAnomalyPage.tsx` — dev/QA page ("Test anomaly detection") that hits the
ad-hoc `/test-*` extraction-router endpoints. No dedicated "Detection Report" panel exists yet;
`DocumentDetailsPage.tsx` is the likely eventual home for rendering `/detection/report` results.

## 6. Synthetic fraud data — `backend/app/injection/`

Generates the test/training data the checks above are validated against: `injectors.py`
(defect classes injecting layout/font/text/metadata manipulations into PDFs, built on `pdf.py`
and `field_text.py`), `schemas.py`, `service.py`, `store.py`. Exposed via
`backend/app/routers/injection.py` (`/api/dev` prefix).

## Bottom line

The individual fraud-detection checks are real, implemented, and well-tested. The *agentic*
orchestration layer (`langgraph/detection/`) that's supposed to call them is fully wired
end-to-end but its tool layer is stubbed — `POST /detection/run` completes without erroring but
returns no real findings. Rewiring `tools.py` to call the real `fraud_detection/*.py` functions
(reusing `numerical_validation.py`'s existing balance-check logic instead of reimplementing it,
per the earlier cleanup plan) is the concrete next step to make the agentic path functional.
