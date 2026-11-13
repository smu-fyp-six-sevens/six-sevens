# Codebase audit

Open items only: delete a row once it's fixed. Bugs, stability, performance, structure and security in one place. Read from the code, 2026-11-13; nothing was run against live services. Sev: High, Medium, Low. Size: S (minutes), M (hours), L (a day+). Paths are under `backend/app/` unless they say otherwise. "(unconfirmed)" means read from code but not proven. Tests: `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md`. Product work: `PENDING_WORK.md`.

## 1. Most important

| Items | In plain words |
|---|---|
| 7.3 | The audit log keeps personal data forever, even after a case is deleted (needs a decision, §9) |

## 2. Bugs

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 2.1 | Low | One unassessed finding alone makes a case Medium (3 points = the Medium threshold) | Decide (§9): lower its points or raise the threshold | `case_pipeline/risk/case_risk.py:58`, `:62` | S |

## 3. Stability

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1 | Medium | A document the content guard wrongly blocks can never be processed: retry blocks it again and there's no override | An officer "process anyway" action, logged in the audit trail | `langgraph/nodes.py` (guard step) | M |
| 3.2 | Low | The leak check never forgets prompts of cancelled calls: a slow memory leak | Drop entries older than the LLM timeout | `guardrails/leak_check.py` | S |
| 3.3 | Low | Two Supabase clients on the same key: an extra connection pool for nothing | Keep one client | `supabase_client.py` | S |
| 3.4 | Low | Some router calls turn a Supabase error into a bare 500 with no log | Use `to_thread_or_502` in `cases.py` (`update_case` re-read), `detection.py` (`langgraph_trace.resume`, `list_documents`), `upload.py` (case lookup) | `routers/` | S |

## 4. Performance

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 4.1 | Low | Dashboard cost totals load every costed event and add them in Python | Sum in SQL | `audit.py` (`cost_usd_many`) | M |
| 4.2 | Medium | Agent loops resend their whole history without caching, each turn at full price (size of bill unmeasured) | Add a cache breakpoint on the last tool result each turn | `langgraph/detection/specialists.py`, `langgraph/classify_graph.py`, `case_pipeline/cross_document/agents.py` | M |
| 4.3 | Low | The extraction retry resends the whole document | Decide (§9): accept, or cache the document block (costs more on every first call) | `langgraph/extract.py:107-115` | S |
| 4.4 | Medium | The sweeper is the biggest database load: 25% of requests and 41% of database time. Its document scan is the slowest query | Check with `EXPLAIN`; a partial index on the in-flight statuses; read only the columns it needs; or sweep less often | `sweeper.py` | S |
| 4.5 | Low | A case is re-read hundreds of times a day by live snapshot builds | Recheck once a few cases are worked in a day | `snapshots.py` | — |
| 4.6 | Low | Case search scans every case | A trigram index once there are thousands of cases | `case_repository.py` | S |
| 4.7 | Low | Precision/recall figures read every review (`list_reviews`) | Aggregate in SQL once reviews pass the thousands (or store evaluation rows: `CLOUD_ARCHITECTURE_PLAN.md` §13) | `findings_repository.py` | M |
| 4.8 | Low | Cancel checks poll the database every 5 s per run | Fine for now; later, push cancels over Realtime | `workers/recovery.py` | S |

Measured 2026-11-07 (24 hours, 8 cases, 80 documents): 2,343 requests, 745 s of database time; an idle hour is ~24 requests, all the sweeper (4.4).

## 5. Code structure and hygiene

| # | In plain words | Fix | Where | Size |
|---|---|---|---|---|
| 5.1 | Raw colours outside the tokens in 8 frontend files | Move to tokens | `frontend/src/styles/*.css`, `DashboardPage.tsx` | S |
| 5.2 | Risk colours mostly use hue tokens, not the risk tokens | Move them when the risk cards are restyled | `frontend/src/styles/` | S |
| 5.3 | Four Cloud Run definitions repeat the same blocks | A local module, only if Cloud Run stays long-term | `deploy/terraform/` | M |
| 5.4 | "Private" helpers (`_as_number`, `_rows`, `_parse_field_date`, …) are imported all over | Make them public in `fraud_detection/shared/field_values.py`; same for `_result`, `_with_ids` in `detection/tools.py` | `fraud_detection/` | M |
| 5.5 | `case_service.py` (674 lines) does three jobs; required-document rules are split over three files | Split | `case_service.py` | M |
| 5.6 | `models.py` (1,084 lines) mixes enums, rules, API schemas and risk models | Split | `models.py` | M |
| 5.7 | `DetectionReport` and `LangGraphTrace` types are hand-written in the frontend | A response model for the report | `frontend/src/services/uploadService/types.ts` | M |
| 5.8 | The schema is a hand-kept snapshot, not migrations | Migrations, when staging needs them | `backend/sql/schema.sql` | M |

## 6. Bigger reorganisations (optional, in order)

1. **One status-transition function**: every status write already checks the current status; one function would make "one writer per transition" checkable in one place. M.
2. **Start case analysis from an event** in the case-worker: untangles imports. M.
3. **Load only each role's code** in `main.py`: faster cold starts. M.
4. **Split `document_service.py`** (710 lines). M.

## 7. Security

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 7.1 | Low | Any service can publish any event type, including ones that look like an officer's | Handlers check which role may send each type | `deploy/terraform/iam.tf:37-42` | M |
| 7.2 | Medium | The public `api` holds the database key that bypasses every rule | Later: a database role per service. Sooner: restrict the key to the services' network | `deploy/terraform/main.tf:12` | L |
| 7.3 | High | The audit log keeps old/new field values, file and company names forever, even after a case is deleted | Decide (§9): store "changed" only, or a retention period | `document_service.py`, `routers/upload.py`, `routers/cases.py` | M |
| 7.4 | Low | The stored trace has file names and quoted values, and any officer can read it | Decide (§9): accept, or strip values unless debugging | `langgraph/nodes.py:259` | S |
| 7.5 | Low | Grafana's admin password is made by Terraform, so its value also sits in the Terraform state file (the Secret Manager copy is the one Grafana reads) | Decide (§9): accept, or create the password outside Terraform | `deploy/terraform/observability.tf:7-22` | S |
| 7.6 | Low | Actions and the base image are pinned by tag, not hash; pip doesn't check hashes | Pin by digest; `--require-hashes` (needs the digests looked up and a hashed lock) | `.github/workflows/ci-cd.yml:63`; `backend/Dockerfile` | S |

Password hashing and the login token's place in `localStorage` are in `PENDING_WORK.md` ("Security").

## 8. Not read yet, and leftovers

- **Not read yet (flagged by size only):** `DocumentDetailsPage.tsx` (716 lines) and `UploadPage.tsx` (530); `routers/detection.py` (33 imports); `_finding` defined in 7 files.
- **Deletes** rely on cascades (`backend/sql/schema.sql`); `audit_events` is never deleted. Known limits: a delete's audit event is best effort, and a failed file delete leaves the file in storage (logged).
- **Unused columns:** `risk_scores.scored_at`, `extracted_fields.field_category`, `extracted_fields.extracted_at`.
- **Probably dead** since the 2026-10-31 wipe: the "older extracted-field data" branch in `frontend/src/pages/document-details/fieldDisplay.ts:40`. Confirm against `GET /documents/{id}`, then delete.
- **Rule:** a new stored field gets a model default that old rows read correctly.

## 9. Decisions needed

- **What should the audit log keep, and for how long? (7.3)** It's permanent on purpose, but it keeps the values officers corrected (names, IDs, amounts). Options: keep values forever; keep only "field X changed, by whom, when"; or delete entries after a set period. *Recommended: "changed" only, and ask GLDB how long audit records must be kept.*
- **Should one unassessed finding make a case Medium? (2.1)** *Recommended: no; give it 2 points so it takes two to reach Medium.*
- **Extraction retry cost (4.3):** *Recommended: accept; retries are rare and caching every first call costs more.*
- **Trace values (7.4):** *Recommended: accept for now; the review screen reads the reasons.*
- **Grafana password in state (7.5):** the state file is in a private bucket that only the team can read, so the exposure is small. *Recommended: accept.*
- **Who approves a release to production?** Only `main` can deploy now (the Google sign-in checks the branch). Option still open: a GitHub environment with a named approver on the deploy job. *Recommended: once staging exists.*

## 10. Order

3.4 (quick) and `FRAUD_DETECTION_MAP.md` §8 bug 4, 3.3, 5.4, the `fieldDisplay` leftover, 3.1 when officer work is next, 5.2 with the risk-card restyle.
