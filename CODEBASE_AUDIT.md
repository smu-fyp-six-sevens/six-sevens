# Codebase audit

Open items only: delete a row once it's fixed. Bugs, performance, structure, backwards-compatibility debt and security in one place (detection bugs included). As built: `HOW_IT_WORKS.md`. Checked against the code 2026-11-16. Sev: High, Medium, Low. Size: S (minutes), M (hours), L (a day+). Paths are under `backend/app/` unless they say otherwise. Tests: `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md`. Product work: `PENDING_WORK.md`. Order: §9.

## 2. Bugs

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 2.1 | Medium | Recovering a document stuck `detecting` can start a second run beside a live one. `on_recovery_requested` reverts to `approved` without checking the run is dead, then calls `run()` with no claim; `mark_detecting` writes `detecting` with no compare-and-swap, so a redelivered recovery event reverts and starts yet another. The older run then saves its report and replaces the document's findings (and the officer's verdicts on them, `sync_findings`) before `mark_detected`'s check, which passes because the newer run set `detecting` again. Needs a run alive past `STUCK_RUNNING_SECONDS` (600 s) | Skip when `_runs.is_running`; claim with `start_from=[APPROVED]` after the revert; make `mark_detecting` a compare-and-swap from `approved` (or drop it: the claim already writes `detecting`); check the status before `save_report` and `sync_document` | `workers/detection.py:157-175`, `langgraph/detection/graph.py:649,680-683`, `document_repository.py:208` | M |
| 2.2 | Low | `finish_run` writes with no status guard and after a separate read: a run the sweeper marked failed (past 900 s) that then finishes is rewritten `complete`, after a `recovery.exhausted` already said failed; a new run inserted between its `latest_run_id` read and its update still lets the old result publish, and sync findings and the score | `.eq("status", "running")` on the update; newest-run check and write in one statement (an RPC), or re-check after writing | `case_pipeline/repository.py:178-206` | S |

### Detection bugs

Found by reading the code; none reproduced. Wiring: `HOW_IT_WORKS.md`, "How a detection run is wired".

| # | Sev | In plain words | Where | Size |
|---|---|---|---|---|
| 2.6 | Medium (local only) | A run lost mid-scan can't be cancelled or retried; in the cloud the sweeper recovers it after 10 min, locally run `python -m app.sweeper --once --case <id>` | `routers/detection.py`, `DetectionPanel.tsx` | M |

**Design question:** a wrongly dismissed finding disappears from the lead's view and the report (the panel still lists it with the reason); check how often real documents get dismissals.

## 3. Performance

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1 | Low | A case is re-read hundreds of times a day by live snapshot builds | Recheck once a few cases are worked in a day | `snapshots.py` | — |
| 3.2 | Low | Precision/recall figures read every review (`list_reviews`) | Aggregate in SQL once reviews pass the thousands (or store evaluation rows: `CLOUD_ARCHITECTURE_PLAN.md`, "Accuracy from officer verdicts") | `findings_repository.py` | M |
| 3.3 | Low | Saving a group's verdicts is 3–4 sequential round trips per finding: a document read per item (the same document each time), `find_current`, `set_verdict`, a feedback upsert. A 15-finding group is about 60 calls, roughly 2–3 s at 30–50 ms each (estimate, unmeasured) | Read each distinct document once, the group's findings in one `in_("fingerprint", …)` read, write the verdicts in one RPC | `routers/finding_reviews.py:197-241` | M |
| 3.4 | Low | Reads that fetch more than they use: `GET /documents` without `case_id` reads every document and all their fields, unpaged (the page always sends `case_id`); `list_runs` pulls each run's whole findings array to count it; `_company_on_file` re-reads fields `list_documents` already attached | Make `case_id` required; count in SQL or store the count; use `document.extracted_fields` | `routers/documents.py:87`, `case_pipeline/repository.py:235`, `case_pipeline/service.py:391` | S |

**Prompt caching:** recheck the cache rates on the case cost card after a few cases (on 2026-11-16: documents 37%, detection 64%, case analysis 9%, before the agent loops cached their conversation).

## 4. Code structure and hygiene

| # | In plain words | Fix | Where | Size |
|---|---|---|---|---|
| 4.1 | Raw colours outside the tokens in 8 frontend files | Move to tokens | `frontend/src/styles/*.css`, `DashboardPage.tsx` | S |
| 4.2 | Risk colours mostly use hue tokens, not the risk tokens | Move them when the risk cards are restyled | `frontend/src/styles/` | S |
| 4.3 | "Private" helpers (`_as_number`, `_rows`, `_parse_field_date`, …) are imported across modules (34 imports in 27 files) | Make them public in `fraud_detection/shared/field_values.py`; same for `_result`, `_with_ids` in `detection/tools.py` | `fraud_detection/` | M |
| 4.4 | `case_service.py` (674 lines) does three jobs; required-document rules are split over three files | Split | `case_service.py` | M |
| 4.5 | `models.py` (1,084 lines) mixes enums, rules, API schemas and risk models | Split | `models.py` | M |
| 4.6 | `DetectionReport` and `LangGraphTrace` types are hand-written in the frontend | A response model for the report | `frontend/src/services/uploadService/types.ts` | M |
| 4.7 | The schema is a hand-kept snapshot, not migrations (checked 2026-11-16: foreign keys, indexes, functions and the Realtime publication match it) | Migrations, if a second environment ever needs them | `backend/sql/schema.sql` | M |
| 4.8 | Detection's cancel and retry rules live in the router, and the queued-cancel path writes `detection_results` from the api, a second writer beside the detection-worker (a run that finished between the read and the write gets "Cancelled" beside its fresh report) | A `detection_service` like `document_service`; the queued cancel left to the worker's own cancel path | `routers/detection.py:397-465` | M |
| 4.9 | A company-name key built outside `identifiers.py`: "ACME Private Limited" in the fields against "ACME PTE LTD" in a statement line doesn't match, so an own-account transfer is treated as trade and a purchase order is recommended for it | `identifiers.company_tokens` in `_is_trade` | `case_pipeline/risk/supporting_documents.py:117-124` | S |

## 4b. Backwards compatibility

Code kept only for old data, old links or the platform being retired. The database (and the audit trail) was wiped on 2026-11-15, so a shim for data older than that guards nothing.

| # | In plain words | Remove when | Where | Size |
|---|---|---|---|---|
| B1 | Cloud Run's half of the app: push delivery (`EVENT_DELIVERY=push`, the `/push/<role>` routes, `PUSH_AUDIENCE` and its check), the `none` mode and the chart's HTTP-only branch for a consumer not yet moved, the second frontend image, the Cloud Run Terraform and the Cloud Run deploy in CI | Phase 6 (`CLOUD_ARCHITECTURE_PLAN.md`). Switch the local compose stack to pull first (`deploy/compose/pubsub.yaml`; `EVENT_DELIVERY` defaults to `push` in `config.py`). Keep or drop the gateway key (`gateway.py`): §8 | `events/push.py`, `main.py`, `config.py`, `deploy/compose/`, `deploy/helm/six-sevens/templates/backend.yaml`, `deploy/terraform/run.tf`, `.github/workflows/deploy.yml` | M |
| B2 | A login token with no `purpose` still counts: tokens issued before 2026-11-13 had none | 2026-11-21, once the last such token has expired (7 days): require `purpose == login` | `auth.py:96-112` | S |
| B3 | Two names for one Supabase client: `service_supabase_client()` (50 calls) was kept "for the code that used to have its own" client; `supabase_client` (15 imports) is the same object. Not a rename: 47 test files mock `service_supabase_client` as a function (`client.return_value.table…`), so each test's mock setup changes too | One name, with the tests' mocks rewritten | `supabase_client.py:41-46`, its callers and `backend/tests/` | M |

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

## 7. Not read yet

- **Not read yet (flagged by size only):** `DocumentDetailsPage.tsx` (716 lines) and `UploadPage.tsx` (530); `_finding` defined in 7 files.

## 8. Decisions needed

1. **Gateway key after phase 6:** keep it as a second guard (api is already internal on GKE) or drop it (`gateway.py`, one secret, Kong's header plugin).

## 9. Order

1. 4.9
2. 2.2, then 2.1
3. B1 with phase 6
4. 3.4, 3.3, 4.8
5. 4.3, 4.1, 4.2, B3; B2 on 2026-11-21
