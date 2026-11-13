# Codebase audit

Open items only: delete a row once it's fixed. Bugs, performance, structure and security in one place. Read from the code, 2026-11-13; nothing was run against live services. Sev: High, Medium, Low. Size: S (minutes), M (hours), L (a day+). Paths are under `backend/app/` unless they say otherwise. "(unconfirmed)" means read from code but not proven. Tests: `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md`. Product work: `PENDING_WORK.md`.

## 1. Most important

Nothing open is urgent. The biggest costs are 3.1 and 3.2.

## 2. Bugs

None open.

## 3. Performance

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1 | Medium | Agent loops resend their whole history without caching, each turn at full price (size of bill unmeasured) | Add a cache breakpoint on the last tool result each turn | `langgraph/detection/specialists.py`, `langgraph/classify_graph.py`, `case_pipeline/cross_document/agents.py` | M |
| 3.2 | Medium | The sweeper is the biggest database load: 25% of requests and 41% of database time. Its document scan is the slowest query | Check with `EXPLAIN`; a partial index on the in-flight statuses; read only the columns it needs; or sweep less often | `sweeper.py` | S |
| 3.3 | Low | A case is re-read hundreds of times a day by live snapshot builds | Recheck once a few cases are worked in a day | `snapshots.py` | — |
| 3.4 | Low | Precision/recall figures read every review (`list_reviews`) | Aggregate in SQL once reviews pass the thousands (or store evaluation rows: `CLOUD_ARCHITECTURE_PLAN.md` §13) | `findings_repository.py` | M |
| 3.5 | Low | Cancel checks poll the database every 5 s per run | Push cancels over Realtime instead (needs a decision on how: below) | `workers/recovery.py` | M |

Measured 2026-11-07 (24 hours, 8 cases, 80 documents): 2,343 requests, 745 s of database time; an idle hour is ~24 requests, all the sweeper (3.2).

## 4. Code structure and hygiene

| # | In plain words | Fix | Where | Size |
|---|---|---|---|---|
| 4.1 | Raw colours outside the tokens in 8 frontend files | Move to tokens | `frontend/src/styles/*.css`, `DashboardPage.tsx` | S |
| 4.2 | Risk colours mostly use hue tokens, not the risk tokens | Move them when the risk cards are restyled | `frontend/src/styles/` | S |
| 4.3 | Four Cloud Run definitions repeat the same blocks | A local module, only if Cloud Run stays long-term | `deploy/terraform/` | M |
| 4.4 | "Private" helpers (`_as_number`, `_rows`, `_parse_field_date`, …) are imported all over | Make them public in `fraud_detection/shared/field_values.py`; same for `_result`, `_with_ids` in `detection/tools.py` | `fraud_detection/` | M |
| 4.5 | `case_service.py` (674 lines) does three jobs; required-document rules are split over three files | Split | `case_service.py` | M |
| 4.6 | `models.py` (1,084 lines) mixes enums, rules, API schemas and risk models | Split | `models.py` | M |
| 4.7 | `DetectionReport` and `LangGraphTrace` types are hand-written in the frontend | A response model for the report | `frontend/src/services/uploadService/types.ts` | M |
| 4.8 | The schema is a hand-kept snapshot, not migrations | Migrations, when staging needs them | `backend/sql/schema.sql` | M |

## 5. Bigger reorganisations (optional, in order)

1. **One status-transition function**: every status write already checks the current status; one function would make "one writer per transition" checkable in one place. M.
2. **Start case analysis from an event** in the case-worker: untangles imports. M.
3. **Load only each role's code** in `main.py`: faster cold starts. M.
4. **Split `document_service.py`** (737 lines). M.

## 6. Security

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 6.1 | Low | Any service can publish any event type, including ones that look like an officer's | Handlers check which role may send each type | `deploy/terraform/iam.tf:37-42` | M |
| 6.2 | Medium | The public `api` holds the database key that bypasses every rule | Later: a database role per service. Sooner: restrict the key to the services' network | `deploy/terraform/main.tf:12` | L |
| 6.6 | Low | Actions and the base image are pinned by tag, not hash; pip doesn't check hashes | Pin by digest; `--require-hashes` (needs the digests looked up and a hashed lock) | `.github/workflows/ci-cd.yml:63`; `backend/Dockerfile` | S |

Password hashing and the login token's place in `localStorage` are in `PENDING_WORK.md` ("Security").

## 7. Not read yet, and leftovers

- **Not read yet (flagged by size only):** `DocumentDetailsPage.tsx` (716 lines) and `UploadPage.tsx` (530); `routers/detection.py` (33 imports); `_finding` defined in 7 files.
- **Deletes** rely on cascades (`backend/sql/schema.sql`); `audit_events` is never deleted. Known limits: a delete's audit event is best effort, and a failed file delete leaves the file in storage (logged).
- **Unused columns:** `risk_scores.scored_at`, `extracted_fields.field_category`, `extracted_fields.extracted_at`.
- **Probably dead** since the 2026-10-31 wipe: the "older extracted-field data" branch in `frontend/src/pages/document-details/fieldDisplay.ts:40`. Confirm against `GET /documents/{id}`, then delete.
- **Rule:** a new stored field gets a model default that old rows read correctly.

## 8. Decisions needed

- **How should a cancel reach a run on another instance? (3.5)** Today the detection run polls the audit trail every 5 s and the document pipeline polls its own status. Options: leave it; a `cancel_requested_at` column on `documents` that the run polls (works with audit-metrics down, still polls); or a Supabase Realtime subscription in each worker (no polling, but each worker then holds a socket and needs a reconnect path). *Recommended: the column, then Realtime only if the polling shows in the database load.*
- **Who approves a release to production?** Only `main` can deploy now (the Google sign-in checks the branch). Option still open: a GitHub environment with a named approver on the deploy job. *Recommended: once staging exists.*

## 9. Order

`FRAUD_DETECTION_MAP.md` §8 bug 4 (quick), 4.4, the `fieldDisplay` leftover, 4.2 with the risk-card restyle.
