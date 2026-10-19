# Refactoring audit

Code smells in `backend/app` and `frontend/src`. Tests are ignored.
Effort: S (minutes), M (an hour or two), L (a day or more). Last reviewed 2026-10-19 on `main`, after the cross-document, entity-holder and identity-matching work was merged.

## Where we are

| | |
|---|---|
| Backend | 41,500 lines in `app/` (4,300 of them in `injection/`), 52 route handlers, 12 document types plus "Other" |
| Detection | Lead + 3 specialists over per-document tools and field checks; numerical, external and layout checks |
| Case analysis | Deterministic cross-document checks, then a bounded agent, deterministic risk score, one merged additional-documents list |
| Frontend | 9,000 lines of TS/TSX, 4,400 lines of CSS in 12 modules |
| Tests | 1,729 backend test functions in 79 files, 105 Playwright tests in 5 specs (not run as part of this audit) |
| CI gates | ruff clean, 0 type-ignore comments, guard step against suppressions; pyright reports 1 error locally (`ijson` isn't installed in this venv; it is in `requirements.txt`, so CI resolves it) |
| Frontend gates | `tsc` clean, lint 0 errors / 1 warning |

**Done since the last audit (2026-10-16):**
- All five "needs attention first" items. The Dockerfile imports the right `land_use` path and fails the build unless `ALLOW_MISSING_LAND_USE=true`. An unknown zone is a `signal`, not `implausible`. The "NA" company-address rule only fires without URA's answer. The CPF checks share one `assess_cpf(PayslipCpfInputs)`. The docs describe the CPF and address checks and both env vars.
- Dead code: all gone (backend and frontend), including `_POSTAL_RE`, the `propose_requests` wrapper, `best_reference` and `isStructuredValue`. `build_reference` (used by `scripts/build_layout_reference.py`) and `build_index` (the builder five tests use) stay on purpose.
- Payslip classification: a weak-additional pattern separates "performance pay" from real bonuses.
- Land-use and URA: `zones_at` runs through `run_cpu`, and the `ura._answers` cache is bounded.
- Identifier normalisation: one `app/identifiers.py` (leaf module, imports neither `case_match` nor `cross_document`) holds `alnum`, `id_key`, `uen_key`, `name_key`, `company_key`, the company/individual name matchers and the person extraction (`Person`, `linked_people`). `case_match` (829 -> 552 lines), `cross_document`, `mapper`, `acra.py` and `bank.py` use it, and neither `cross_document` nor `acra.py` imports `case_match` any more. The cross-document company-name comparison stays strict on purpose, in `company_identity.py`.
- Router 502 blocks: `to_thread_or_502` for a single blocking call and the new `bad_gateway_on_error` context manager for a block of work (HTTPException passes through) replaced 18 hand-written blocks. Four stay on purpose: the specific-exception handlers in `cases.py` and `upload.py`, and one response status in `detection.py`.
- Optimisations: the spelling check no longer re-runs exiftool (`ingest_document(with_metadata=False)`); the ingest node runs exiftool in a thread beside the text extraction instead of inside the one PDF worker; document reads select named columns, with `metadata` only when the metadata tool or endpoint asks (`include_metadata=True`); `delete_case` skips the field query; case snapshots of a case with no analysis fetch only each report's verdict (93 KB of reports became 110 bytes on live data, same set of documents and failures); and the case's `submission_finalized` refresh is batched per case (`SYNC_DEBOUNCE_SECONDS`, 1.5 s, 0 in tests). Not done: keeping the per-page OCR text for the spelling check. Detection runs hours after upload and after restarts, so an in-memory copy would mostly miss and would grow without bound; it would need storing in the database.
- Cross-document work: deterministic checks always run, one row per difference, the agent sees conflicts grouped by value and is bounded (plan, judge, 6-call ceiling), identity matching moved out of detection, birth date, race, sex and nationality are compared in code, and findings are plain sentences.

---

## 1. Duplicated code

### Backend

1. **Detection endpoints:** the 12 on-demand check endpoints still repeat gate, download, run. Only the trace call is shared. M
2. **`unable_to_assess` and `unable_to_assess_reason`** are declared separately in the result models of ten fraud-check modules. A shared base model. M

### Frontend

1. **Two hooks named `useDocumentActions`** (one per page, different state shapes). Rename for clarity. S
2. **CSS: about 400 hard-coded hex colours against 200 `var(--...)` uses.** Define tokens and replace. L

---

## 2. Wrong place / doesn't make sense

### Backend

1. **Business rules in the routers.** `routers/documents.py` (972 lines): `update_extracted_fields` (100 lines), `approve_document` (98), `update_document_type`. Move to a service. M
2. **Detection router uses another router's private names** (`_detection_tasks`, `_launch_detection`, `skips_detection`, 5 references). Put task handling in its own module. Also what blocks splitting `documents.py`. M
3. **`case_service.py` (513 lines) mixes a rollup with pass-throughs** (`get_case`, `create_case`, `update_case`), and the required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
4. **`models.py` (650 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
5. **`case_match.py` (552 lines) at the app root.** It holds only the upload-to-the-right-case check, but it still has the long functions `_verify_incumbency` (95) and `_match_document` (97), and the shared helpers have moved to `identifiers.py`. Split the rest into a `case_match/` package. S-M
6. **`injection` ships in the production API:** 4,300 lines and `/injection/*` routes (`main.py` includes the router), a test-data tool and not part of detection. Move out or put behind a setting. M
7. **Two instrumentation systems** (`telemetry.py` spans and `query_stats.py`) that overlap on token usage. Seven modules still use `telemetry`. Pick one. M
8. **`field_checks/*` import private helpers** (`_result`, `_with_ids`) from `detection/tools.py`; `document_fields` just wraps `_extracted_fields`. S
9. **`app/langgraph` shares its name with the `langgraph` library.** Works through relative imports, confuses people and tools. M
10. **Very long functions** (all in checks that are one decision after another, so splitting is optional): `image_manipulation._check_compression` (267), `_check_replacement` (193), `check_layout_anomalies` (188), `tools/transactions.build_transaction_tools` (195, a list of closures), `addresses._building_use` (112), `ingestion_service.ingest_document` (117). M
11. **Tracked debug files at the repo root:** `trace.md` (827 lines) and `backend_terminal_trace.md` (248). S

### Frontend

1. **Too many props:** `UploadPage` passes about 30 to `DocumentListPanel` (465 lines, rows inline). M
2. **Long pages:** `DocumentDetailsPage.tsx` (601), `UploadPage.tsx` (436), `App.tsx` (395: routing, auth and case state), `CaseQueuePage.tsx` (371). M
3. **Inline `style={{...}}`**, 27 uses in `DetectionPanel`, `LangGraphTracePanel`, `CaseAnalysisTracePanel`, `CaseRiskCard`. S
4. **`mock-*` class names** (18 uses) for real features. S
5. **`CaseAnalysisCard.tsx:48` hook warning:** the effect reads `overview` but lists only its fields. Depend on `overview` or on a derived flag. S

---

## 3. Optimisations

Found by reading the hot paths (upload, detection run, case snapshot, case list, case analysis) and measuring the frontend build. Nothing was profiled against production data; effects come from reading the code.

### Still open

| # | Where | What happens now | Fix | Effect | Effort |
|---|---|---|---|---|---|
| 5 | `case_service.list_case_summaries` | Reads every document in the database (all cases, including the `evidence` arrays) on each queue page load, then groups them. | Select only the columns `is_stuck` and `compute_documents_status` use, and filter with `.in_("case_id", ids)` for the cases being listed. | Queue load no longer grows with the total document count times the width of a row. | S-M |

Waiting on the rework of how case statuses work.

### Checked, nothing to do

- **Anthropic calls:** one client per (class, model, max tokens, temperature), so connections are reused; prompt caching is on classification, extraction, the detection lead and binder, and the cross-document calls; the extraction cache gate stops the first document of a type racing the cache write.
- **Case analysis cost:** bounded at plan, judge and two Haiku calls (6-call ceiling), with capped input and output; the log shows about 13k tokens in and 2.4k out for a real run.
- **Snapshot build:** one documents query, one fields query (shared by the plain values and the `{value, confidence}` wrappers) and one trace query.
- **List reads** already skip the metadata blob (`_LIST_COLUMNS`); the multi-case analysis lookup is only called for one case, so its fetch-all-runs path never runs.
- **Frontend:** the main bundle is 206 kB (67 kB gzip) with pages and the detection panels already lazy-loaded; the only timers are two 15-second ticks. Nothing worth changing.
- **Regex compiled in a function** (`reference_checks/tax.py`, `company_identity.py`): `re` caches compiled patterns, so the cost is negligible.

### Needs measuring first (not a fix yet)

- **Model choice per step:** classification and extraction use Sonnet. The `perf` log lines (Claude calls, tokens, cache hits per unit of work) are the data to decide whether rule-confident classifications could skip the Sonnet call, or the lead could use Haiku. This changes detection quality, so test against the regression set before changing it.

---

## Suggested order

1. **Move rules out of the routers (2.1) and detection task handling (2.2).** Unblocks splitting `routers/documents.py`.
2. **The detection endpoint table (1.1).** Biggest line-count win left.
3. **The case-list query (3.5, after the status rework), the `injection` package (2.6)**, the two trace files (2.11) and CSS tokens (frontend 2.2), whenever convenient.
