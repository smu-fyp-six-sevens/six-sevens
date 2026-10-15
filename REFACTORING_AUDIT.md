# Refactoring audit

Code smells found in `backend/app` and `frontend/src`, from a static scan plus reading the hot spots.
Tests are ignored, as asked. Nothing here has been changed yet.

**How to read it.** Each item says where it is, what is wrong, and what to do.
Effort is S (minutes), M (an hour or two), L (a day or more).
"Verified" means I searched the app for other uses and found none.

Last reviewed 2026-10-15.

---

## 1. Dead code (safe to delete)

| Where | What | Notes | 
|---|---|---|
| `backend/app/case_pipeline/state.py` `usable_findings` | Function nobody calls | Left over from before the risk score came only from the analysis run. S | 
| `backend/app/fraud_detection/external_validation/registry_lookups/http.py` `clear_cache` | Never called, not even by tests | S | 
| `backend/app/fraud_detection/layout_anomalies/text_overlay.py` `_is_expected_overlay` | Only tests call it | The real logic uses something else. S |
| `backend/app/fraud_detection/numerical_validation/runner.py` `SCAN_ALL` | Constant defined, never read | S |
| `backend/app/injection/schemas.py` `ManipulationIndicator` | Enum nobody uses | S |
| `backend/app/injection/pdf.py` `extract_pdf_text`, `media_type_for`, `read_pdf_metadata` | Only tests use them | S |
| `backend/app/injection/store.py` `get_document_file_path` | Only tests use it | S |
| `backend/app/injection/field_text.py` `is_label_only`, `require_value` | Only tests use them | S |
| `backend/app/case_service.py` imports `RoutingStatus`, `is_classification_confirmed` | Unused imports | S |
| `backend/app/case_pipeline/cross_document/tools/reading.py` imports `Optional` | Unused import | S |
| `backend/app/langgraph/detection/tools.py` `_NOTHING_TO_CHECK_REASONS` | The pattern `^Nothing to check:` is listed twice | S |
| `frontend/src/pages/upload/uploadHelpers.ts` `REQUIREMENT_LABELS`, `isExtractionNotConfiguredFailure`, `structuredFieldEntries` | Exported, never imported | S |
| `frontend/src/pages/UploadPage.tsx` `savingFields`, `saveFieldErrors`, `setFieldDraftValue`, `saveFieldDraft` | State and functions that are defined and never used | Already listed in `PENDING_WORK.md`. S |
| `frontend/src/services/uploadService/cases.ts` `getCaseRequirementCoverage` | Exported, never used | S |
| `frontend/src/services/uploadService/documents.ts` `listDocuments`, `moveDocument` | Exported, never used (the page uses the bulk versions) | S |

Where the only user is a test, delete the test with it.

- ignore these for now. will work on it seperately. 

---

## 2. Duplicated code

### Backend

1. **"Read the trace, fall back to memory" written four times.**
   `routers/detection.py` (three places: `_require_detection_has_run`, `get_document_detection_report`, the retry endpoint) and `routers/documents.py` `get_document_langgraph_trace`.
   Each does `get_persisted` in a try/except, then `langgraph_trace.get`.
   One `trace.load(document_id)` helper would replace all four. S

2. **Two copies of "fetch a document's extracted fields, return [] on failure".**
   `routers/detection.py` `_fetch_extracted_fields` and `langgraph/detection/tools.py` `_load_fields` have identical bodies. S

3. **Four ways to download a document's file.**
   `routers/detection.py` `_download_document_bytes`, `langgraph/detection/tools.py` `_load_bytes`, `langgraph/nodes.py`, `langgraph/vision_tool.py`. Each wraps `storage_service.download_file` in `to_thread` with its own error handling.
   Give `storage_service` an async `download(document)`. S

4. **`_normalise` is copied word for word** in `langgraph/llm_classify.py` and `langgraph/rule_classify.py`
   (turns match strengths into confidences). Move it to `classification_confidence.py`, which already exists. S

5. **The same "try → 502" block appears 37 times in the routers**, and `asyncio.to_thread(...)` is written by hand 68 times.
   Most endpoints are `try: await to_thread(repo.x) except Exception: log; raise HTTPException(502, "...")`.
   One helper (or an exception handler for a `StorageError`) would remove roughly 300 lines. M

6. **The 12 on-demand check endpoints in `routers/detection.py` all have the same shape**:
   gate → download → maybe fetch fields → `run_cpu(check)` → `_trace_check` → return.
   A small table of `(path, check function, needs_fields, response model)` would replace 12 near-identical functions. M

7. **The list of document types exists in four places.**
   `models.DocumentType`, `langgraph/classify_support.VALID_TYPES`, `langgraph/extraction_prompts.KNOWN_DOCUMENT_TYPES`, and the frontend list.
   Adding a type means editing all of them, and they are already slightly different (`unrecognised` is in some, not others).
   Derive the others from `DocumentType`. M

8. **Name and address normalisers: seven versions with different rules.**
   `case_match.py` (`_normalize_id`, `normalize_uen`, `company_names_match`, `_company_key`, `_strings`),
   `mapper.py` (`_normalize_name`, `_normalize_id_number`),
   `cross_document/address_matching.py` (`_normalize_person_name`, `_company_key`, `_strings`),
   `cross_document/company_identity.py` (`normalize_company_name`, `_normalize_text`, `company_names_match`),
   `risk/supporting_documents.py` (`_normalise`).
   `company_names_match` is defined in two modules with different behaviour, so the same two names can match in one check and not in another.
   Already noted in `PENDING_WORK.md`; the risk is that consolidating changes results, so do it with the rules written down first. 

-feedback: for this one, leave it. they are uspposed to be 2 different implementations. one is to make sure that the document matches the case. the other one is to flag whether a document is fraudulently manipulated and does not match.

9. **Small helpers repeated across the detection checks.**
   `_finding` (5 modules), `_empty_result` (3), `_unable` (2), `_decode` (2), `_page` / `_pages` (2), `_x_overlap` (2), `_content_type_from_filename` (`spelling_anomaly.py` and `nodes.py`).
   And the result models in ten modules each declare their own `unable_to_assess` and `unable_to_assess_reason`.
   A shared base model plus a small `fraud_detection/_common.py` would cover these. M

10. **`_row` cast helper copied into four repositories** (`case_repository`, `document_repository`, `extracted_fields_repository`, `case_pipeline/repository`). S

### Frontend

1. **Two `displayableFieldEntries` and two sets of field-value formatting**:
   `pages/upload/uploadHelpers.ts` (plain values) and `pages/document-details/fieldDisplay.ts` (values wrapped as `{value, confidence}`).
   Same name, same job, different shapes. Pick one shape at the service boundary. M

2. **Detection stage computed in two places**:
   `DocumentDetailsPage.tsx` and `upload/DocumentListPanel.tsx` both call `documentDetectionStage(...)` with the same four arguments, and the list panel repeats the "in progress" logic around it. Make it one `useDocumentStage(document)` or pass the computed stage in. S

3. **Delete-case logic written twice**: `CaseQueuePage.tsx` and `pages/upload/useCaseSettings.ts` (`confirmDeleteCase`, the same error message, similar state). S

4. **Two hooks named `useDocumentActions`** (`pages/upload/` and `pages/document-details/`), both with `handleApproveDocument`, `handleCancelDocument`, `handleRetryDocument`. One shared hook with the page-specific bits injected. M

5. **CSS: same colours under several names.**
   `.case-id--ready` = `.case-id--green`, `--in-progress` = `--blue`, `--failed` = `--red`, `--pending` = `--grey`.
   `.document-status--failed` is defined twice, `.status-dot--failed` and `--rejected` are identical.
   About 18 selectors are defined in more than one place (`.summary-card`, `.summary-card-header`, `.upload-btn`, `.risk-severity--high`, `.section-label`, `.documents-table-row` and others), partly because `case-analysis.css` overrides `case-summary.css`. Merge the overrides into the originals. M

6. **CSS: 410 hard-coded hex colours against 192 `var(--…)` uses.** The palette in `base.css` is only partly used, and several near-identical greys and blues differ by a digit. Define a small set of tokens and replace. L

---

## 3. Things that don't make sense / are in the wrong place

### Backend

1. **Business rules live inside the routers.**
   `routers/documents.py`: `update_extracted_fields` (100 lines deciding which fields count as human-verified), `approve_document` (98 lines of eligibility rules), `update_document_type` (82 lines).
   They belong in a service next to `case_service`, so the rules can be tested and reused without HTTP. M

2. **One router reaches into another's private names.**
   `routers/detection.py` imports `documents_router._detection_tasks`, `documents_router._launch_detection` and `documents_router.skips_detection`.
   Detection task management should be its own module (`detection_runner.py`) that both routers use. This is also why splitting `documents.py` is hard. M

3. **`case_service.py` mixes a rollup with pass-throughs.**
   `get_case`, `create_case`, `update_case` just call `case_repository`. The real logic (checklist, `submission_finalized`, snapshot) is mixed in with them.
   The required-documents rules are split across `models.compute_fulfilled_requirements`, `case_service.missing_required_fields` and `mapper.py`. Put them in one module. M

4. **`models.py` (620 lines) is three things**: enums and business rules (`counts_as_address_proof`, `compute_fulfilled_requirements`), API request/response schemas, and case-risk models that also have a twin in `case_pipeline/models.py`. Split into `models/` (or at least move the functions out). M

5. **`case_match.py` (908 lines) sits at the app root** but is a detection feature with its own normalisers, next to `case_pipeline/cross_document/`. It also has the longest functions outside the forensic checks (`_verify_incumbency` 95 lines, `_match_document` 85). Move and split. M

6. **The `injection` package ships in the production API.**
   About 4,000 lines (`injection/pdf.py` alone is 3,216) plus `/injection/*` routes behind the same auth. It is a tool for making tampered test documents, not part of fraud detection.
   Move it to a separate package or behind a setting so the production image doesn't carry it. M

7. **Two parallel instrumentation systems**: `telemetry.py` (OpenTelemetry spans) and `query_stats.py` (per-request cost log). They overlap on token usage (`telemetry.record_usage` in `cross_document/agents.py`, query_stats' own LLM callback). Decide which one is the source of truth. M

8. **`field_checks/*` import private helpers** (`_result`, `_with_ids`) from `langgraph/detection/tools.py`. Either make them public or move them to a shared module. Also `document_fields` just wraps `_extracted_fields`. S

9. **`case_pipeline/readiness.py` has the wrong module docstring** (it's the package's, copied). Trivial, but it misleads. S

10. **The package `app/langgraph` has the same name as the library `langgraph`.** It works because imports are relative, but it confuses tooling and people. Rename to `app/pipeline` or similar. M

### Frontend

1. **Components that take too many props.** `UploadPage.tsx` passes about 30 props to `DocumentListPanel`, which is 474 lines with the row rendering inline.
   Move state into hooks or a context, and pull `DocumentRow` out. M

2. **Pages that are too long**: `DocumentDetailsPage.tsx` (602), `UploadPage.tsx` (520), `App.tsx` (389, which mixes routing, auth and case state). M

3. **Inline `style={{…}}`** in `DetectionPanel.tsx` (8), `CaseAnalysisTracePanel.tsx` (12), `LangGraphTracePanel.tsx` (4) and `CaseAnalysisCard.tsx` (1), in an app that otherwise uses classes. S

4. **CSS class names that don't describe their use**: the `mock-*` classes (`mock-badge`, `mock-note`, `mock-classification`) are now used for real features. Rename. S

---

## 4. Stale or misleading comments

These describe things that no longer exist, which sends readers the wrong way.

| Reference | Where | Reality |
|---|---|---|
| `LANGGRAPH_MIGRATION_PLAN.md` | `langgraph/graph.py`, `classify_graph.py`, `classify_support.py`, `cache_gate.py`, `extract.py`, `state.py`, `text_cache.py`, `llm_classify.py`, `rule_classify.py`, `detection/__init__.py`, `config.py` | The file doesn't exist. |
| `WS.MD` | `case_broadcaster.py` | The file doesn't exist. |
| "Dify", `backend/app/dify/...` | `rule_classify.py`, `llm_classify.py`, `vision_tool.py`, `classify_support.py`, `extraction_prompts.py` | Dify is removed. "Port of the Dify node" comments are history, not documentation. |
| "langgraph-test branch" | `config.py`, `langgraph/trace.py` | There is one main code path now. |
| "Phase 1 / Phase 4" | `langgraph/graph.py`, `state.py`, `classify_graph.py` | Phases were a migration plan. |
| "no checkpointer yet" | `langgraph/graph.py` | True, but it points at a missing plan. |

A search-and-trim pass would clean these in one go. S

---

## 5. Size and complexity

Modules over 1,000 lines: `injection/pdf.py` (3,216), `fraud_detection/image_manipulation.py` (2,519), `date_anomalies.py` (1,096), `ai_generation.py` (1,031).

Functions over 100 lines (hardest to read and test):

| Function | Lines |
|---|---|
| `image_manipulation._check_compression` | 267 |
| `image_manipulation._check_replacement` | 193 |
| `layout_anomalies.check_layout_anomalies` | 173 |
| `injection/pdf._replace_field_text_on_image` | 147 |
| `image_manipulation._check_resolution` | 134 |
| `cross_document/tools/transactions.build_transaction_tools` | 132 |
| `layout_anomalies/indentation._check_indentation` | 131 |
| `image_manipulation._check_covered_regions` | 125 |
| `layout_anomalies/reference.compare_with_reference` | 118 |
| `ingestion/ingestion_service.ingest_document` | 117 |
| `image_manipulation.check_image_manipulation` | 116 |
| `layout_anomalies/spacing._check_paragraph_spacing` | 115 |
| `image_manipulation._collect_regions` | 113 |
| `injection/pdf._try_splice_rewritten_text` | 111 |
| `layout_anomalies/alignment._check_row_alignment` | 111 |
| `routers/documents.update_extracted_fields` | 100 |

A long function isn't wrong by itself, but these mix several jobs (find, measure, decide, build the finding). Split them when you next touch them. L

---

## 6. Error handling and style

- **114 `except Exception`** blocks. Most log and degrade on purpose. The ones worth reviewing are those that swallow silently:
  `injection/pdf.py` has five `except …: pass` (lines 390, 398, 1139, 1352, 3071). `routers/ws.py:115` is fine (closing an already-closed socket). S
- **Mixed typing style**: 167 uses of `Optional[X]` and 493 of `X | None`. Pick one (`X | None`) and run a formatter. S
- **Import style**: several modules use `from .. import a, b, c` on one long line (`routers/documents.py`) and others use one per line. A formatter (ruff or black) would settle it. S

---

## 7. Repo hygiene

- `trace.md`, `backend_terminal_trace.md`, `backend_tests.md` at the root are debug output, untracked. Delete or move out of the repo.
- `backend/documents_generated/` is 108 MB and untracked. Keep it out of the repo, or document how to regenerate it with `doc_gen_scripts`.
- `backend/migrations/` is an empty folder.
- `backend/langgraph.json` points at three graph builders for LangGraph Studio. If nobody uses Studio, remove it, otherwise keep it in step with any rename in 3.10.
- No linter or formatter is configured in `pyproject.toml`. Adding ruff would catch most of section 1 and the import style in section 6 automatically.

---

## Suggested order

1. **Section 1 and the stale comments in section 4.** Pure deletion, no behaviour change.
2. **Duplication that has an obvious home**: items 1–4 and 10 in section 2 (backend), and 2–3 on the frontend.
3. **Move the business rules out of the routers** (3.1) and pull detection task handling into its own module (3.2). This unblocks splitting `routers/documents.py`.
4. **The 502 helper and the detection endpoint table** (2.5, 2.6). Biggest line-count win.
5. **One source for document types** (2.7), then **the normalisers** (2.8) with their rules written down first.
6. **The `injection` package** (3.6) and the CSS tokens (2.6 frontend), whenever convenient.
