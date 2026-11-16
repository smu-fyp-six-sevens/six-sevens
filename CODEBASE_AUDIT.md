# Codebase audit

Open items only: delete a row once it's fixed. Bugs, performance, structure, backwards-compatibility debt and security in one place. Read from the code, 2026-11-13; the backend and its Supabase queries again on 2026-11-16 (live schema, indexes and advisors checked through the Supabase MCP, no rows read). Sev: High, Medium, Low. Size: S (minutes), M (hours), L (a day+). Demo: **before** (small, low-risk, worth doing now) or **after**. Paths are under `backend/app/` unless they say otherwise. "(unconfirmed)" means read from code but not proven. Tests: `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md`. Product work: `PENDING_WORK.md`.

## 1. Most important

Nothing open is urgent. The open bugs need a run alive past the sweeper's 10-minute limit to bite.

## 2. Bugs

| # | Sev | In plain words | Fix | Where | Size | Demo |
|---|---|---|---|---|---|---|
| 2.1 | Medium | Recovering a document stuck `detecting` can start a second run beside a live one. `on_recovery_requested` reverts to `approved` without checking the run is dead, then calls `run()` with no claim; `mark_detecting` writes `detecting` with no compare-and-swap, so a redelivered recovery event reverts and starts yet another. The older run then saves its report and replaces the document's findings (and the officer's verdicts on them, `sync_findings`) before `mark_detected`'s check, which passes because the newer run set `detecting` again. Needs a run alive past `STUCK_RUNNING_SECONDS` (600 s) | Skip when `_runs.is_running`; claim with `start_from=[APPROVED]` after the revert; make `mark_detecting` a compare-and-swap from `approved` (or drop it: the claim already writes `detecting`); check the status before `save_report` and `sync_document` | `workers/detection.py:156-175`, `langgraph/detection/graph.py:649,680-683`, `document_repository.py:208` | M | after |
| 2.2 | Low | `finish_run` writes with no status guard and after a separate read: a run the sweeper marked failed (past 900 s) that then finishes is rewritten `complete`, after a `recovery.exhausted` already said failed; a new run inserted between its `latest_run_id` read and its update still lets the old result publish, and sync findings and the score | `.eq("status", "running")` on the update; newest-run check and write in one statement (an RPC), or re-check after writing | `case_pipeline/repository.py:178-206` | S | after |

## 3. Performance

| # | Sev | In plain words | Fix | Where | Size | Demo |
|---|---|---|---|---|---|---|
| 3.1 | Low | A case is re-read hundreds of times a day by live snapshot builds | Recheck once a few cases are worked in a day | `snapshots.py` | — | after |
| 3.2 | Low | Precision/recall figures read every review (`list_reviews`) | Aggregate in SQL once reviews pass the thousands (or store evaluation rows: `CLOUD_ARCHITECTURE_PLAN.md` §13) | `findings_repository.py` | M | after |
| 3.3 | Low | Saving a group's verdicts is 3–4 sequential round trips per finding: a document read per item (the same document each time), `find_current`, `set_verdict`, a feedback upsert. A 15-finding group is about 60 calls, roughly 2–3 s at 30–50 ms each (estimate, unmeasured) | Read each distinct document once, the group's findings in one `in_("fingerprint", …)` read, write the verdicts in one RPC | `routers/finding_reviews.py:197-241` | M | after |
| 3.4 | Low | Reads that fetch more than they use: `GET /documents` without `case_id` reads every document and all their fields, unpaged (the page always sends `case_id`); `list_runs` pulls each run's whole findings array to count it; `_company_on_file` re-reads fields `list_documents` already attached | Make `case_id` required; count in SQL or store the count; use `document.extracted_fields` | `routers/documents.py:87`, `case_pipeline/repository.py:235`, `case_pipeline/service.py:391` | S | after |

Measured 2026-11-07 (24 hours, 8 cases, 80 documents): 2,343 requests, 745 s of database time; an idle hour is ~24 requests, all the sweeper. Since 2026-11-16 the sweeper reads only the status columns and a cancel check is one small read; not measured since.

Prompt caching, from the audit trail on 2026-11-16 (every costed run since the wipe): document runs 37% of input read from the cache ($2.33 over 80), detection 64% ($0.91 over 69), case analysis 9% ($0.40 over 6), extraction reruns 43%. The four agent loops now cache their growing conversation too (`build_chat_anthropic(cache_history=True)`); recheck the rates on the case cost card after a few cases.

## 4. Code structure and hygiene

| # | In plain words | Fix | Where | Size | Demo |
|---|---|---|---|---|---|
| 4.1 | Raw colours outside the tokens in 8 frontend files | Move to tokens | `frontend/src/styles/*.css`, `DashboardPage.tsx` | S | after |
| 4.2 | Risk colours mostly use hue tokens, not the risk tokens | Move them when the risk cards are restyled | `frontend/src/styles/` | S | after |
| 4.3 | "Private" helpers (`_as_number`, `_rows`, `_parse_field_date`, …) are imported across modules (34 imports in 27 files) | Make them public in `fraud_detection/shared/field_values.py`; same for `_result`, `_with_ids` in `detection/tools.py` | `fraud_detection/` | M | after |
| 4.4 | `case_service.py` (674 lines) does three jobs; required-document rules are split over three files | Split | `case_service.py` | M | after |
| 4.5 | `models.py` (1,084 lines) mixes enums, rules, API schemas and risk models | Split | `models.py` | M | after |
| 4.6 | `DetectionReport` and `LangGraphTrace` types are hand-written in the frontend | A response model for the report | `frontend/src/services/uploadService/types.ts` | M | after |
| 4.7 | The schema is a hand-kept snapshot, not migrations (checked 2026-11-16: foreign keys, indexes, functions and the Realtime publication match it) | Migrations, when staging needs them | `backend/sql/schema.sql` | M | after |
| 4.8 | Detection's cancel and retry rules live in the router, and the queued-cancel path writes `detection_results` from the api, a second writer beside the detection-worker (a run that finished between the read and the write gets "Cancelled" beside its fresh report) | A `detection_service` like `document_service`; the queued cancel left to the worker's own cancel path | `routers/detection.py:397-465` | M | after |
| 4.9 | A company-name key built outside `identifiers.py`: "ACME Private Limited" in the fields against "ACME PTE LTD" in a statement line doesn't match, so an own-account transfer is treated as trade and a purchase order is recommended for it | `identifiers.company_tokens` | `case_pipeline/risk/supporting_documents.py:117-124` | S | after |

## 4b. Backwards compatibility

Code kept only for old data, old links or the platform being retired. The database (and the audit trail) was wiped on 2026-11-15, so a shim for data older than that guards nothing.

| # | In plain words | Remove when | Where | Size |
|---|---|---|---|---|
| B1 | Cloud Run's half of the app: push delivery (`EVENT_DELIVERY=push`, the `/push/<role>` routes, `PUSH_AUDIENCE` and its check), the `none` mode and the chart's HTTP-only branch for a consumer not yet moved, the second frontend image, the Cloud Run Terraform and the Cloud Run deploy in CI | Phase 6 (`CLOUD_ARCHITECTURE_PLAN.md`). The local compose stack also uses push: switch it to pull first, or keep push for local runs only. The gateway key (`gateway.py`) is a separate call: keep it as a second guard or drop it | `events/push.py`, `main.py`, `config.py`, `deploy/helm/six-sevens/templates/backend.yaml`, `deploy/terraform/run.tf`, `.github/workflows/deploy.yml` | M |
| B2 | A login token with no `purpose` still counts: tokens issued before 2026-11-13 had none | 2026-11-21, once the last such token has expired (7 days) and nobody needs it for the demo: require `purpose == login` | `auth.py:96-112` | S |
| B3 | Two names for one Supabase client: `service_supabase_client()` (50 calls) was kept "for the code that used to have its own" client; `supabase_client` (15 imports) is the same object. Not a rename: 47 test files mock `service_supabase_client` as a function (`client.return_value.table…`), so each test's mock setup changes too | After the demo: one name, with the tests' mocks rewritten | `supabase_client.py:41-46`, its callers and `backend/tests/` | M |

## 5. Bigger reorganisations (optional, in order)

1. **One status-transition function**: most status writes already check the current status; one function would make "one writer per transition" checkable in one place. The one write that doesn't check today is `mark_detecting` (2.1). M.
2. **Start case analysis from an event** in the case-worker: untangles imports. M.
3. **Load only each role's code** in `main.py`: faster cold starts. M.
4. **Split `document_service.py`** (750 lines). M.

## 6. Security

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 6.1 | Low | Any service can publish any event type, including ones that look like an officer's | Handlers check which role may send each type | `deploy/terraform/iam.tf:37-42` | M |
| 6.2 | Medium | The public `api` holds the database key that bypasses every rule | A database role per service. Restricting the key to the cluster's NAT address doesn't help: Supabase's network restrictions don't cover the HTTPS APIs the backend uses | `deploy/terraform/roles.json` (each role's `secrets`) | L |
| 6.3 | Low | Actions and the base image are pinned by tag, not hash; pip doesn't check hashes | Pin by digest; `--require-hashes` (needs the digests looked up and a hashed lock). Our own images already deploy to GKE by digest, signed and admitted by Binary Authorization | `.github/workflows/ci-cd.yml:63`; `backend/Dockerfile` | S |

Password hashing and the login token's place in `localStorage` are in `PENDING_WORK.md` ("Security"). Supabase's advisors (2026-11-16): security lists only "RLS enabled, no policy" on all 13 tables, which is the design (secret key only, no anon grants); performance lists 4 unused indexes, all backing a cascade or the reply lookup, so they stay.

## 7. Not read yet, and leftovers

- **Not read yet (flagged by size only):** `DocumentDetailsPage.tsx` (716 lines) and `UploadPage.tsx` (530); `_finding` defined in 7 files.
- **Deletes** rely on cascades (`backend/sql/schema.sql`; checked live 2026-11-16: every document- or case-keyed table cascades, `finding_feedback.document_id` is set null); `audit_events` is never deleted. Known limits: a delete's audit event is best effort, and a failed file delete leaves the file in storage (logged). Signed URLs are made only in `routers/documents.py` (server side); a moved document keeps its storage path, which is fine (the path is opaque and the file goes with the row).
- **Field writes** go through the `replace_extracted_fields`, `add_extracted_field` and `delete_extracted_field` functions, each refusing unless the document is still in the status its caller read (`backend/sql/schema.sql`; live since 2026-11-16).
- **Rule:** a new stored field gets a model default that old rows read correctly.

## 8. Decisions needed

1. **4.9:** whether a company name in a statement line should match the case's company with its legal suffix ignored (waiting on the team).

## 9. Order

After the demo: 2.1, 2.2; 3.3, 3.4; 4.8; `FRAUD_DETECTION_MAP.md` §8 bug 4 (quick), 4.3, 4.9 once decided, 4.2 with the risk-card restyle; B1 with phase 6; B2 on 2026-11-21; B3.
