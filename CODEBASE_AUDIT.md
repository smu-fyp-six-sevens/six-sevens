# Codebase audit

Open items only: delete a row once it's fixed. Bugs, performance, structure, backwards-compatibility debt and security in one place (detection bugs included). As built: `HOW_IT_WORKS.md`. Checked against the code 2026-11-16, renumbered 2026-11-17. Sev: High, Medium, Low. Size: S (minutes), M (hours), L (a day+). Paths are under `backend/app/` unless they say otherwise. Tests: `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md`. Product work: `PENDING_WORK.md`. Order: §8.

## 1. Bugs

None open.

**Design question:** a wrongly dismissed finding disappears from the lead's view and the report (the panel still lists it with the reason); check how often real documents get dismissals.

## 2. Performance

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 2.1 | Low | A case is re-read hundreds of times a day by live snapshot builds | Recheck once a few cases are worked in a day | `snapshots.py` | — |
| 2.2 | Low | Precision/recall figures read every review (`list_reviews`) | Aggregate in SQL once reviews pass the thousands (or store evaluation rows: `CLOUD_ARCHITECTURE_PLAN.md`, "Accuracy from officer verdicts") | `findings_repository.py` | M |
| 2.3 | Low | The content guard's OCR of an image embedded in a PDF takes several seconds (about 6 s on a laptop for one 1031×84 px strip, 90% of it detection): RapidOCR's detection scales a thin image up until its shortest side is 736 px (`Det.limit_type: min`), and it reads every image up to 12 per document, short ones twice | Time one pass on a doc-worker pod first (the laptop's timings varied 2–3×). Then try 4 onnxruntime threads (twice as fast on the laptop) or `limit_type: max` for the guard's own reader only, checking every attack in `tests/test_content_guard.py` is still caught; ingestion's page OCR keeps its settings | `guardrails/content_guard.py` `_ocr_lines`, `ingestion/ocr_extractor.py` | S |

**Prompt caching:** recheck the cache rates on the case cost card after a few cases (on 2026-11-16: documents 37%, detection 64%, case analysis 9%). Case analysis stays low by design: its two calls share only the tools and the system prompt, and the dossier is sent once.

## 3. Code structure and hygiene

| # | In plain words | Fix | Where | Size |
|---|---|---|---|---|
| 3.1 | `case_service.py` (674 lines) does three jobs; required-document rules are split over three files. About 100 test patches point at `app.case_service.*`, so they move too | Split | `case_service.py` | M |
| 3.2 | `models.py` (1,084 lines) mixes enums, rules, API schemas and risk models | Split into a package that re-exports every name, so no import changes | `models.py` | M |
| 3.3 | Other "private" helpers still imported across modules: the case report's `_text`, `_notice`, `_data_table` and the like, `_counterparty_from_description`, `_reducible`, `_font_family`, `_is_newest_first`, `_UEN_RE`, `_noa_year`, `_open_document` | Make them public where they're shared | `case_report/`, `fraud_detection/`, `case_pipeline/` | M |
| 3.4 | `DetectionReport` and `LangGraphTrace` types are hand-written in the frontend | A response model for the report | `frontend/src/services/uploadService/types.ts` | M |
| 3.5 | The schema is a hand-kept snapshot, not migrations (checked 2026-11-16: foreign keys, indexes, functions and the Realtime publication match it) | Migrations, if a second environment ever needs them | `backend/sql/schema.sql` | M |
| 3.6 | Detection's cancel and retry rules live in the router, and the queued-cancel path writes `detection_results` from the api, a second writer beside the detection-worker (a run that finished between the read and the write gets "Cancelled" beside its fresh report) | A `detection_service` like `document_service`; the queued cancel left to the worker's own cancel path | `routers/detection.py:397-465` | M |

## 4. Backwards compatibility

Code kept only for old data, old links or the retired platform (Cloud Run's infrastructure and CI deploy were removed 2026-11-18). The database (and the audit trail) was wiped on 2026-11-15, so a shim for data older than that guards nothing.

| # | In plain words | Remove when | Where | Size |
|---|---|---|---|---|
| B1 | Cloud Run's code left behind: push delivery (`EVENT_DELIVERY=push`, the `/push/<role>` routes, `PUSH_AUDIENCE` and its check), the `none` mode and the chart's HTTP-only branch for a consumer not yet moved, `migration.json` (every consumer is listed; only `gen_values.py` reads it now), and comments naming Cloud Run | After the demo. Switch the local compose stack to pull first (`deploy/compose/pubsub.yaml`; `EVENT_DELIVERY` defaults to `push` in `config.py`). The gateway key (`gateway.py`) stays as a second guard | `events/push.py`, `main.py`, `config.py`, `deploy/compose/`, `deploy/helm/six-sevens/templates/backend.yaml`, `deploy/helm/gen_values.py` | M |
| B2 | Two names for one Supabase client: `service_supabase_client()` (50 calls) was kept "for the code that used to have its own" client; `supabase_client` (15 imports) is the same object. Not a rename: 47 test files mock `service_supabase_client` as a function (`client.return_value.table…`), so each test's mock setup changes too | One name, with the tests' mocks rewritten | `supabase_client.py:41-46`, its callers and `backend/tests/` | M |

## 5. Bigger reorganisations (optional, in order)

1. **One status-transition function**: every status write now checks the current status; one function would make "one writer per transition" checkable in one place. M.
2. **Start case analysis from an event** in the case-worker: untangles imports. M.
3. **Load only each role's code** in `main.py`: faster cold starts. M.
4. **Split `document_service.py`** (750 lines). M.

## 6. Security

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 6.1 | Low | Any service can publish any event type, including ones that look like an officer's | Handlers check which role may send each type | `deploy/terraform/iam.tf:37-42` | M |
| 6.2 | Medium | The public `api` holds the database key that bypasses every rule | A database role per service. Restricting the key to the cluster's NAT address doesn't help: Supabase's network restrictions don't cover the HTTPS APIs the backend uses | `deploy/terraform/roles.json` (each role's `secrets`) | L |
| 6.3 | Low | Actions and the base image are pinned by tag, not hash; pip doesn't check hashes | Pin by digest; `--require-hashes` (needs the digests looked up and a hashed lock). Our own images already deploy to GKE by digest, signed and admitted by Binary Authorization | `.github/workflows/ci-cd.yml:63`; `backend/Dockerfile` | S |

## 7. Not read yet

- **Flagged by size only:** `DocumentDetailsPage.tsx` (716 lines) and `UploadPage.tsx` (530); `_finding` defined in 7 files.

## 8. Order

1. 3.6
2. After the demo: B1, 3.1, 3.2, B2, 3.3
