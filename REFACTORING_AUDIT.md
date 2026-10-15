# Refactoring audit

Code smells in `backend/app` and `frontend/src`. Tests are ignored.
Effort: S (minutes), M (an hour or two), L (a day or more). Last reviewed 2026-10-15, after everything was merged to main.

## Where we are

| | |
|---|---|
| Backend | 39,400 lines in `app/`, 47 endpoints, 12 document types plus "Other" |
| Detection | Lead + 3 specialists over 8 tools and 4 field checks; 59 numerical and 4 external check codes |
| Case analysis | Cross-document agent, deterministic risk score, supporting-document recommendations |
| Frontend | 9,000 lines of TS/TSX, 4,300 lines of CSS in 11 modules |
| Tests | 1,544 backend test functions in 71 files, 101 Playwright tests in 5 specs (not run as part of this audit) |
| CI gates | ruff clean, pyright 0 errors, 0 type-ignore comments, guard step against suppressions |
| Frontend gates | `tsc` clean, lint 0 errors / 6 warnings, build passes |

**Done since the first audit:** ruff and blocking pyright in CI; unused imports and the unused `match_by_id` removed; `routers/documents.py` comments trimmed; CSS split into modules; not-found pages; layout reference matching fixed; incumbency and NOA extraction fixes.

**Needs attention first (from the URA address work, not yet fixed):**

1. `backend/Dockerfile` and `scripts/build_land_use_index.py` import `app.fraud_detection.registry_lookups.land_use`, which doesn't exist (real path: `...external_validation.registry_lookups.land_use`). The build step ends in `|| echo WARNING`, so images ship without the zoning index and `LAND_USE_RUNTIME_REBUILD=false` stops it being built later. S
2. With no index, a unit URA has no approval for is reported `implausible` instead of `signal` when the zone is unknown (`addresses.py`). S
3. A company address URA answered "NA" can still be flagged by the zoning rule; the docstring says it shouldn't (`addresses.py`, `elif zone == HOMES_ONLY_ZONE`). S
4. The three CPF checks call `assess_cpf(**locals())`: the assessment runs three times per payslip and breaks silently if a signature changes. S
5. `FRAUD_DETECTION_MAP.md` and `HOW_IT_WORKS.md` don't describe the new CPF and address checks, `URA_API_KEY` or `LAND_USE_RUNTIME_REBUILD`. S

---

## 1. Dead code

| Where | What |
|---|---|
| `case_pipeline/state.py` `usable_findings` | Never called |
| `external_validation/registry_lookups/http.py` `clear_cache` | Never called |
| `layout_anomalies/text_overlay.py` `_is_expected_overlay` | Only tests call it |
| `numerical_validation/runner.py` `SCAN_ALL` | Defined, never read |
| `registry_lookups/addresses.py` `_POSTAL_RE` | Defined, never used (new) |
| `injection/schemas.py` `ManipulationIndicator` | Never used |
| `injection/pdf.py` `extract_pdf_text`, `media_type_for`, `read_pdf_metadata` | Only tests use them |
| `injection/store.py` `get_document_file_path` | Only tests use it |
| `injection/field_text.py` `is_label_only`, `require_value` | Only tests use them |
| `langgraph/detection/tools.py` `_NOTHING_TO_CHECK_REASONS` | `^Nothing to check:` listed twice |
| Frontend `uploadHelpers.ts` | `REQUIREMENT_LABELS`, `isExtractionNotConfiguredFailure`, `structuredFieldEntries` never imported |
| Frontend `UploadPage.tsx` | `savingFields`, `saveFieldErrors`, `setFieldDraftValue`, `saveFieldDraft` never used |
| Frontend services | `getCaseRequirementCoverage`, `listDocuments`, `moveDocument` never used |

Where the only user is a test, delete the test with it.

- Ignore these for now. Will work on it separately.

---

## 2. Duplicated code

Cleaned up 2026-10-15 (tests updated, not run):

- Trace read: `langgraph/trace.load()` replaces five copies; routers call it.
- `_normalise`: one `normalise_candidates` in `classification_confidence.py`.
- `_row`: one `supabase_rows.row` for the four repositories.
- Document types: `VALID_TYPES` and `KNOWN_DOCUMENT_TYPES` are derived from `DocumentType`.
- Routers: `to_thread_or_502` in `_dependencies.py` replaces 13 try/except-502 blocks; `detection.py` records check results through one `_record_result`.
- Shared helpers: `fraud_detection/pdf_pages.py` (`page`, `pages`) and `ingestion_service.content_type_from_filename`.
- Frontend: `documentStatusDisplay` replaces the repeated stage/label/in-progress calls; the approve/reject/cancel/retry handlers in both `useDocumentActions` hooks share one runner each; duplicate and aliased CSS rules merged (`case-analysis.css` into `case-summary.css`, `.case-id--*` aliases grouped).

Still open:

### Backend

1. **24 more try/except -> 502 blocks** in the routers that don't fit the helper (they do extra work in the `try`, or catch `HTTPException` first). M
2. **Detection endpoints:** the 12 on-demand check endpoints still repeat gate, download, run. Only the trace call is shared. M
3. **`unable_to_assess` and `unable_to_assess_reason`** are declared separately in the result models of ten fraud-check modules. A shared base model. M

### Frontend

1. **Two hooks named `useDocumentActions`** (one per page, different state shapes). Rename for clarity. S
2. **CSS: about 410 hard-coded hex colours against 192 `var(--...)` uses.** Define tokens and replace. L

---

## 3. Wrong place / doesn't make sense

### Backend

1. **Business rules in the routers.** `routers/documents.py`: `update_extracted_fields` (100 lines), `approve_document` (98), `update_document_type` (82). Move to a service. M
2. **Detection router uses another router's private names** (`_detection_tasks`, `_launch_detection`, `skips_detection`, 5 references). Put task handling in its own module. Also what blocks splitting `documents.py`. M
3. **`case_service.py` mixes a rollup with pass-throughs** (`get_case`, `create_case`, `update_case`), and the required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
4. **`models.py` (620 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
5. **`case_match.py` (917 lines) at the app root**, with long functions (`_verify_incumbency` 95, `_match_document` 85). Move and split. M
6. **`injection` ships in the production API:** about 4,000 lines and `/injection/*` routes, a test-data tool and not part of detection. Move out or put behind a setting. M
7. **Two instrumentation systems** (`telemetry.py` spans and `query_stats.py`) that overlap on token usage. Pick one. M
8. **`field_checks/*` import private helpers** (`_result`, `_with_ids`) from `detection/tools.py`; `document_fields` just wraps `_extracted_fields`. S
9. **`case_pipeline/readiness.py` has the wrong module docstring.** S
10. **`app/langgraph` shares its name with the `langgraph` library.** Works through relative imports, confuses people and tools. M
11. **`registry_lookups/land_use.py` and `addresses.py` (new):** `zones_at` runs numpy on the event loop (use `run_cpu`); the freshness check compares timestamp strings of different formats; `ura._answers` is unbounded; `addresses._building_use` is 105 lines. S-M
12. **Payslip classification (new):** `split_earnings` takes `abs()` of amounts, so a negative adjustment counts as wages; `_ADDITIONAL_RE` treats "performance allowance" and "variable allowance" as additional wages. S

### Frontend

1. **Too many props:** `UploadPage` passes about 30 to `DocumentListPanel` (474 lines, rows inline). M
2. **Long pages:** `DocumentDetailsPage.tsx` (602), `UploadPage.tsx` (520), `App.tsx` (389: routing, auth and case state). M
3. **Inline `style={{...}}`** in `DetectionPanel`, `CaseAnalysisTracePanel`, `LangGraphTracePanel`, `CaseAnalysisCard`. S
4. **`mock-*` class names** used for real features. S

---

## 4. Style and hygiene

Done 2026-10-16, as one-off ruff autofixes (the CI ruff rules are unchanged); ruff, pyright and `import app.main` pass, and no tests were run.

| # | What | Result |
|---|---|---|
| 1 | **`Optional[X]` to `X \| None`** | 166 uses converted, with the now-unused imports dropped. The one `Optional[...]` left is inside a docstring. |
| 2 | **Dead `# noqa` comments** | 25 `BLE001` comments removed (that rule isn't enabled, so they did nothing); the reason text stays as a plain comment. The 14 `F401` ones are needed (they mark deliberate re-exports in `__init__.py` files) and stay. |

The rest of the old list (the silent `except` in `injection/pdf.py`, the tracked debug files at the root, `cast`, broad `except Exception`) is not being done.

---

## Suggested order

1. **The five "needs attention first" items** above. They affect what ships.
2. **Section 1 dead code** when you pick it up.
3. **Move rules out of the routers (3.1) and detection task handling (3.2).** Unblocks splitting `routers/documents.py`.
4. **The remaining 502 blocks and the detection endpoint table (2.1, 2.2).** Biggest line-count win left.
5. **The `injection` package (3.6)** and CSS tokens (frontend 2.2), whenever convenient.
