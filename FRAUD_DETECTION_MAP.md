# Fraud Detection — Implementation Map

Snapshot of where fraud/anomaly detection logic lives and is tested, as of 2026-10-01.

## 1. Core check modules — `backend/app/fraud_detection/`

| File | Dimension | Tests |
|---|---|---|
| `layout_anomalies.py` | Indentation/alignment/margin/spacing deviations + text overlay | 112 |
| `numerical_validation.py` | Deterministic arithmetic/identity checks over extracted fields (balances, totals) | 88 |
| `metadata_analysis.py` | File-embedded metadata (author/producer/create-modify dates) tamper signals | 54 |
| `date_anomalies.py` | Date extraction/labeling and logical date consistency (future dates, end-before-start) | 38 |
| `spelling_anomaly.py` | Misspellings via SymSpell, filtered against `domain_terms.py` allow-list | 24 |
| `font_consistency.py` | Font family/size/emphasis (bold/italic/underline) inconsistency | 16 |
| `domain_terms.py` | Not a check — allow-list of legal/financial jargon feeding `spelling_anomaly.py` | — |

Text overlay lives inside `layout_anomalies.py` rather than its own module — it shares that
file's ~650-line PDF→blocks/lines extraction pipeline with every other layout check, and the
overlay-specific logic on top of that is only ~130 lines. Splitting it out would mean duplicating
that extraction pipeline, not avoiding duplication.

`image_manipulation.py` (SCRUM-64) covers resolution and compression mismatches between a file's
images (embedded PDF images or a standalone PNG/JPEG). The same module's `check_image_tampering` (SCRUM-65) covers
images edited in after the fact: painted over and replaced (Image Overlay), stretched or rotated
(Scaling Anomaly), pasted with a seam or halo (Visible Boundary), or with altered brightness,
colour, sharpness or noise (Quality Mismatch). No AI-generated-content check exists yet.

`case_match.py` (696 lines, 46 tests) — cross-document validation: verifies each case document's
company/individuals against the certificate of incumbency (UEN/name/ID fuzzy matching). Gates
fraud detection per its own docstring.

## 2. LangGraph Detection Agent — `backend/app/langgraph/detection/`

- **`tools.py`** — 8 `@tool` wrappers (`font_consistency_tool`, `layout_anomalies_tool`,
  `spelling_tool`, `date_consistency_tool`, `transaction_balance_tool`, `metadata_tool`,
  `image_manipulation_tool`, `image_tampering_tool`), each
  calling the matching `fraud_detection/*.py` function directly — same convention
  `routers/detection.py`'s own endpoints use. `transaction_balance_tool` filters
  `check_numerical_validation`'s findings down to `BS.RUNNING_BALANCE` only (the other numerical
  checks it runs — ACRA/CR/FATCA/FS/LOAN/NOA/PAY — are out of this tool's scope). Each check's own
  deterministic severity is passed through as `severity_hint`, kept distinct from the specialist's
  own `info/low/medium/high` scale.
  - **Fixed bug**: every finding dict is contractually required to carry `"summary"` (module
    docstring; the lead's review prompt and the frontend both index it unconditionally), but
    `font_consistency_tool`/`spelling_tool`/`metadata_tool` never set it — crashed real detection
    runs with `KeyError: 'summary'` in `graph.py::_compact_reports`. Fixed by adding a real summary
    to all three, plus an assertion in `_with_ids` so a future tool forgetting it fails loudly
    instead of crashing mid-run. Covered by `tests/test_detection_tools.py` (new).
- **`specialists.py`** — 3 `Specialist`s (`layout_rendering`, `content_validation`,
  `digital_authenticity`), each a tool-calling subgraph bound to a subset of tools.
  `run_specialist()` compiles/caches one graph per specialist.
- **`graph.py`** — Lead Investigator graph: `load_context → lead_plan → [specialists via Send]
  → lead_review → optional second look → synthesize`. Lead picks a specialist team per document
  type via an LLM call, dispatches specialists in parallel, deterministically merges findings
  into a final verdict (`flagged`/`inconclusive`/`clean`). `run_detection(document_id)` is the
  entry point wired into `POST /documents/{id}/detection/run`.
  - **Fixed bug**: `DetectionState.reports` (the fan-in key every parallel specialist writes a
    finding list into) was declared `Required[Annotated[list, operator.add]]` — LangGraph only
    recognizes a reducer when `Annotated` is the *outer* type, so `Required[Annotated[...]]`
    silently fell back to a no-reducer `LastValue` channel and crashed
    (`InvalidUpdateError: At key 'reports'...`) the moment 2+ specialists ran in the same step.
    Fixed by swapping the nesting to `Annotated[Required[list], operator.add]`.
  - **Error handling**: a crash inside `run_detection` is now recorded on the document's trace
    (`detection_error` step, see `langgraph/trace.py`) before re-raising, so a failed run is
    distinguishable from one that never ran. `GET .../detection/report` returns 502 with the
    failure reason when a `detection_error` exists and no report does, 404 only when neither
    exists. The frontend's Detection panel surfaces this and offers a retry button either way.

## 3. API surface — `backend/app/routers/detection.py`

- `GET /documents/{id}/detection/{font-consistency,layout-anomalies,spelling-anomaly,
  numerical-validation,date-anomalies,metadata-anomaly}` — individual on-demand checks, gated by
  `_require_detection_has_run`: a loan officer can trigger one specific check, but only after
  `POST /detection/run` has produced a `detection_report` for that document at least once (409
  otherwise) — a manual single-check run can refine an existing finding, it can't substitute for
  the agent's own pass.
- `GET /documents/{id}/detection/case-match` — ungated (wraps
  `case_service.get_case_match_for_document`).
- `POST /documents/{id}/detection/run` → `langgraph.detection.graph.run_detection`. Also fires
  automatically as a background task right after a document is approved
  (`routers/documents.py::_start_detection_after_approval`).
- `GET /documents/{id}/detection/report` → the persisted `detection_report` (see §2 for its
  404-vs-502 split) · `GET /cases/{case_id}/detection/reports` → all reports for a case.

No dev/QA duplicate of any of this remains — the old `/extraction/test-*` endpoints and the
`/extraction/test` router itself (plus their only frontend callers, `TestAnomalyPage.tsx` and
`ExtractionTestPage.tsx`, and all their dedicated service files) have been deleted outright.

## 4. Frontend — `DocumentDetailsPage.tsx`'s Detection section

Unstyled/debug-styled, covers the full workflow:
- Renders `detectionReport` (`getDetectionReport`), auto-fetched on load; shows the failure reason
  and a "Run detection"/"Retry detection" button when the last run crashed or never ran (`runDetection`).
- "Run an individual check" — six buttons (§3's gated list, via `runIndividualDetectionCheck`),
  disabled with an explanatory message until `detectionReport` exists; each result renders in its
  own collapsible `<details>`.

## 5. Test coverage — `backend/tests/`

Counts match §1's table for the check modules themselves. Beyond those: `test_case_match.py` (46),
`test_langgraph.py` (37, pipeline-wide not detection-specific), `test_detection.py` (19,
`routers/detection.py` endpoints), `test_detection_tools.py` (4, the `tools.py` "summary" contract
fix above only — not general coverage), `test_fraud_injections.py`/`test_injection_router.py`/
`test_injectors.py` (15/8/7, synthetic-fraud generation — see §6).

No dedicated test file exercises `specialists.py` or `graph.py` beyond the generic coverage
above — the main gap left in this area, along with no test covering the `reports`-reducer fix
in §2 specifically.

## 6. Synthetic fraud data — `backend/app/injection/`

Generates the test/training data the checks above are validated against: `injectors.py` (defect
classes injecting layout/font/text/metadata manipulations into PDFs, built on `pdf.py` and
`field_text.py`), `schemas.py`, `service.py`, `store.py`. Exposed via
`backend/app/routers/injection.py` (`/api/dev` prefix).
