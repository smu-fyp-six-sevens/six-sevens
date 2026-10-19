# Refactoring audit

Code smells and optimisations in `backend/app` and `frontend/src`; tests are ignored (see `backend_tests.md`). Effort: S (minutes), M (an hour or two), L (a day or more). Reviewed 2026-10-19 on `main`.

## Where we are

| | |
|---|---|
| Backend | 41,500 lines in `app/` (4,300 of them in `injection/`), 52 route handlers |
| Frontend | 9,000 lines of TS/TSX, 4,400 lines of CSS in 12 modules |
| Tests | about 1,740 backend test functions in 80 files, 105 Playwright tests in 5 specs |
| Backend gates | ruff clean, 0 type-ignore comments, pyright blocking in CI (locally 1 error: `ijson` isn't in this venv but is in `requirements.txt`) |
| Frontend gates | `tsc` clean, lint 0 errors / 1 warning, build passes |

---

## 1. Duplicated code

### Backend

1. **Detection endpoints:** the 12 on-demand check endpoints repeat gate, download, run; only the trace call is shared. M
2. **`unable_to_assess` and `unable_to_assess_reason`** are declared separately in the result models of ten fraud-check modules. A shared base model. M

### Frontend

1. **Two hooks named `useDocumentActions`** (one per page, different state shapes). Rename for clarity. S
2. **CSS: about 400 hard-coded hex colours against 200 `var(--...)` uses.** Define tokens and replace. L

---

## 2. Wrong place / doesn't make sense

### Backend

1. **Business rules in the routers.** `routers/documents.py` (972 lines): `update_extracted_fields` (100 lines), `approve_document` (98), `update_document_type`. Move to a service. M
2. **The detection router uses another router's private names** (`_detection_tasks`, `_launch_detection`, `skips_detection`). Put task handling in its own module; this also blocks splitting `documents.py`. M
3. **`case_service.py` (513 lines) mixes a rollup with pass-throughs** (`get_case`, `create_case`, `update_case`), and the required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
4. **`models.py` (650 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
5. **`case_match.py` (552 lines) at the app root** has long functions (`_verify_incumbency` 95, `_match_document` 97). Split into a `case_match/` package. S-M
6. **`injection` ships in the production API:** 4,300 lines and `/injection/*` routes, a test-data tool that is not part of detection. Move it out or put it behind a setting. M
7. **Two instrumentation systems** (`telemetry.py` spans and `query_stats.py`) overlap on token usage; seven modules still use `telemetry`. Pick one. M
8. **`field_checks/*` import private helpers** (`_result`, `_with_ids`) from `detection/tools.py`; `document_fields` just wraps `_extracted_fields`. S
9. **`app/langgraph` shares its name with the `langgraph` library.** Works through relative imports; confuses people and tools. M
10. **Very long functions** (checks that are one decision after another, so splitting is optional): `image_manipulation._check_compression` (267), `_check_replacement` (193), `check_layout_anomalies` (188), `tools/transactions.build_transaction_tools` (195, a list of closures), `addresses._building_use` (112), `ingestion_service.ingest_document` (117). M

### Frontend

1. **Too many props:** `UploadPage` passes about 30 to `DocumentListPanel` (465 lines, rows inline). M
2. **Long pages:** `DocumentDetailsPage.tsx` (601), `UploadPage.tsx` (436), `App.tsx` (395: routing, auth and case state), `CaseQueuePage.tsx` (371). M
3. **Inline `style={{...}}`**, 27 uses in `DetectionPanel`, `LangGraphTracePanel`, `CaseAnalysisTracePanel`, `CaseRiskCard`. S
4. **`mock-*` class names** (18 uses) for real features. S
5. **`CaseAnalysisCard.tsx:48` hook warning:** the effect reads `overview` but lists only its fields. Depend on `overview` or a derived flag. S

---

## 3. Optimisations

Found by reading the hot paths and measuring the frontend build; nothing was profiled against production data.

| # | Where | What happens now | Fix | Effort |
|---|---|---|---|---|
| 1 | `case_service._build_case_snapshot` | About five sequential reads per snapshot (case, documents, fields, analysis, traces) | Two parallel rounds: case, documents and analysis; then fields and traces. Saves about three round trips per push | S-M |
| 2 | `case_service.list_case_summaries` | Reads every document in the database (all cases, including the `evidence` arrays) on each queue page load. Waiting on the rework of how case statuses work | Select only the columns `is_stuck` and `compute_documents_status` use, and filter with `.in_("case_id", ids)` | S-M |
| 3 | `analysis_repository.get_latest_many` | Fetches every run of every case it is given, but is only ever called with one case id | Make it a single-case function so the fetch-everything branch can't become live | S |
| 4 | Frontend `CaseQueuePage` | `loadPrefs()` runs three times in the initial state setup (three `localStorage` reads) | One lazy initializer, three values derived from it | S |
| 5 | `langgraph_trace` | `_write_row` is update-then-insert because `document_id` has no unique key. Steady state is one call and `_persist` already serialises writers per document, so this is hygiene | A unique constraint in Supabase, then one upsert (schema change) | S |

**Considered and left**

- **Snapshot payload** (every document on each push): pushes are debounced to 0.5 s and built only for open cases. Measure a real case before designing a projection or deltas.
- **The document page subscribes to the whole case snapshot**, but only while the document is in flight or detecting; a per-document stream adds backend work for almost no gain.
- **`useCaseSnapshot`'s 12 state setters** are batched by React into one render. Cleaner as one object, not faster.
- **`DocumentListPanel` row memoization** would not help until the props cleanup (frontend 2.1) is done; virtualization isn't needed at about a dozen documents per case.
- **Keeping per-page OCR text for the spelling check:** detection runs hours after upload and after restarts, so an in-memory copy would mostly miss; it would need storing in the database.

**Needs measuring first:** whether rule-confident classifications could skip the Sonnet call, or the detection lead could use Haiku. The `perf` log lines (Claude calls, tokens, cache hits per unit of work) are the data. It changes detection quality, so test against the regression set first.

---

## Suggested order

1. **Move rules out of the routers (2.1) and detection task handling (2.2).** Unblocks splitting `routers/documents.py`.
2. **The detection endpoint table (1.1).** The biggest line-count win left.
3. **Snapshot reads in parallel (3.1)** and the small ones (3.3, 3.4).
4. **The case-list query (3.2, after the status rework), the `injection` package (2.6)** and CSS tokens (frontend 1.2), whenever convenient.
