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

- **`tools.py`** — wired to the real checks. All 6 `@tool` wrappers (`font_consistency_tool`,
  `layout_anomalies_tool`, `spelling_tool`, `date_consistency_tool`, `transaction_balance_tool`,
  `metadata_tool`) call the matching `fraud_detection/*.py` function directly, same calling
  convention as `routers/detection.py`'s own endpoints. `transaction_balance_tool` filters
  `check_numerical_validation`'s findings down to `BS.RUNNING_BALANCE` only, since the other
  numerical checks it also runs (ACRA/CR/FATCA/FS/LOAN/NOA/PAY) are out of this tool's scope.
  Each check's own deterministic severity (e.g. `impossible`/`inconsistent`, or `high`/`medium`/
  `low` for metadata) is passed through as `severity_hint`, kept distinct from the specialist's
  own `info/low/medium/high` scale added later.
- **`specialists.py`** — 3 `Specialist` dataclasses (`layout_rendering`, `content_validation`,
  `digital_authenticity`), each a tool-calling subgraph bound to a subset of tools.
  `layout_rendering` now has both `font_consistency_tool` and `layout_anomalies_tool`.
  `run_specialist()` compiles/caches one graph per specialist.
- **`graph.py`** — Lead Investigator graph: `load_context → lead_plan → [specialists via Send]
  → lead_review → optional second look → synthesize`. Lead picks a specialist team per document
  type via an LLM call, dispatches in parallel, deterministically merges findings into a final
  verdict (`flagged`/`inconclusive`/`clean`). `run_detection(document_id)` is the entry point
  wired into `POST /documents/{id}/detection/run`.

## 3. API surface — `backend/app/routers/detection.py`

Real, working checks (same `fraud_detection/*.py` and `case_match.py` functions the LangGraph
tools above now also call):

- `GET /documents/{id}/detection/font-consistency`
- `GET /documents/{id}/detection/layout-anomalies`
- `GET /documents/{id}/detection/spelling-anomaly`
- `GET /documents/{id}/detection/numerical-validation`
- `GET /documents/{id}/detection/date-anomalies`
- `GET /documents/{id}/detection/metadata-anomaly`
- `GET /documents/{id}/detection/case-match` (wraps `case_service.get_case_match_for_document`)

Agentic path (now produces real findings, see §2):

- `POST /documents/{id}/detection/run` → `langgraph.detection.graph.run_detection`
- `GET /documents/{id}/detection/report` → reads persisted langgraph trace's `detection_report`
- `GET /cases/{case_id}/detection/reports` → all stored reports for a case

`backend/app/routers/extraction.py`'s `/test-*` dev/QA endpoints (duplicated the same checks on
ad-hoc uploads, no persisted document/case) have been removed now that the real endpoints above
and the LangGraph tools both cover the same ground — only `/extraction/test` (raw-text
extraction, unrelated to fraud detection) remains. `frontend/src/pages/TestAnomalyPage.tsx`
still calls the removed paths and will 404 until it's updated or retired (frontend intentionally
not touched here).

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
`graph.py` beyond generic coverage in `test_langgraph.py`/`test_detection.py`.

## 5. Frontend surface

`frontend/src/pages/TestAnomalyPage.tsx` — dev/QA page ("Test anomaly detection") that hits the
now-removed `/test-*` extraction-router endpoints (see §3) — currently broken, not yet updated.
No dedicated "Detection Report" panel exists yet;
`DocumentDetailsPage.tsx` is the likely eventual home for rendering `/detection/report` results.

## 6. Synthetic fraud data — `backend/app/injection/`

Generates the test/training data the checks above are validated against: `injectors.py`
(defect classes injecting layout/font/text/metadata manipulations into PDFs, built on `pdf.py`
and `field_text.py`), `schemas.py`, `service.py`, `store.py`. Exposed via
`backend/app/routers/injection.py` (`/api/dev` prefix).

## Bottom line

The individual fraud-detection checks are real, implemented, and well-tested, and the agentic
orchestration layer (`langgraph/detection/`) now calls them directly — `POST /detection/run`
produces real findings. No dedicated unit tests for `tools.py`/`specialists.py`/`graph.py` yet
(see §4) — the main gap left in this area.
