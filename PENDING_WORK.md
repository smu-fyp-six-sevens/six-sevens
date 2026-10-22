# Pending work

Open product and platform work only. Code smells and optimisations are in `REFACTORING_AUDIT.md`; test work is in `backend_tests.md`; detection bugs are in `FRAUD_DETECTION_MAP.md` §6. The roadmap (automatic case analysis, the cloud split) is in `CLAUDE.md`'s "Future Direction"; the cloud design is `CLOUD_ARCHITECTURE_PLAN.md`. Updated 2026-10-21.

## Decided, not started

- **Case-level flags.** Cross-document and identity findings are deliberately not in the per-document `flags` table. Today they exist only inside each analysis run's JSON (`case_analysis_runs.result.findings`, with the deterministic `xd-det-N` ones), so there is no row per finding, no status and no officer dismissal. A case-level flags table and module would give them that.
- **Automatic case analysis.** The officer clicks it. The gate on every document completion, supersede with a run-id fence, debounce and re-run cap (`CROSS_DOCUMENT_AND_RISK_PLAN.md`, Step 0) are not built.
- **Guard against repeated runs.** Each "Run again" spends about 12k tokens even when the documents are unchanged. Skipping a run whose input fingerprint matches the last complete run would stop that, but changes what the button does.
- **Officer review of flags.** Detection writes each approved finding to `flags` with status `open`. The `status`, `reviewed_by` and `reviewed_at` columns exist so an officer can confirm or dismiss a flag, but no endpoint reads or changes them and the frontend never shows the table (it shows the detection report and the analysis findings instead). Building it needs an endpoint, a UI, and a decision on how a dismissal affects the score.
- **Flags for older documents.** Flags are written when a detection run finishes. Documents detected before the table was wired have a report but no flag rows, and approved documents can't re-run detection.
- **Payslip 7 pt shift not detected.** The layout check caught the same 7 pt shift on the balance sheet and the credit report but not on the payslip. Likely there is no reference layout for the payslip template (not verified); read `layout_anomalies` and the payslip generator, or run the check on the payslip.
- **Re-scan a case to confirm the latest detection changes.** The row and foreign-font font checks, the hard-breach rule, subject and title in metadata, the cross-document leads (prompt `cross-doc-v7`) and the file-contents guidance are tested on synthetic inputs only. After a live run, check that image tools mostly stop returning "nothing to check" (before: 15 of 19 calls) and that the leads produce a tested hypothesis.
- **Agent-raised findings.** A specialist can only assess the findings a tool returns and write a summary, so something it notices on its own (a subject of "modified copy") appears in its summary but never scores or becomes a flag. Adding it would need a way to raise a finding that cites the field it read.
- **Install `exiftool` locally.** Without it the metadata check and the AI-provenance part of the AI-generation check report "could not run", so they never contribute to a score. Optional local fixes: a startup warning when it is missing, a PyMuPDF fallback for basic metadata, and re-reading the file when the stored record is a failure.

## Verification debt

- **Checks that exist only as scratch scripts**, worth turning into repo tests: the verifier rules (two documents or a computed fact; a probe that re-reads the same fields doesn't count), the bias screens (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and snapshot builder, the flag write-on-change memo, and `flags_repository.build_rows`.
- **No SQL in the repo.** The schema lives only in Supabase. A new environment can't be built from the repo; the objects it needs are listed in `README.md`, and their `CREATE` statements would have to be exported from Supabase first.

## LangGraph pipeline

- **Postgres checkpointing, pruning and time travel** are not built (every graph compiles with `checkpointer=False`). A crashed run fails the document and is retried from scratch; there is no replay or fork from an earlier step and no thread cleanup. Time travel is no longer needed for "fix the type and re-extract" (the officer correction already reruns extraction only), so it is now a debugging and audit aid. If built: `langgraph-checkpoint-postgres` on the transaction pooler (`prepare_threshold=None`), its own `langgraph` schema (or a second free Postgres via `LANGGRAPH_DATABASE_URL` if churn hurts), only the parent graph checkpoints, delete a document's thread when it finishes and sweep failed ones after a few days by `thread_id` (= `document_id`; the tables have no `created_at`). Keep PII and text out of state (re-ingest the stored file to replay), measure bytes per document (target well under ~50 MB), consider encrypting the serializer, and add a keep-alive for the Supabase free-tier inactivity pause before the November demo.
- **CPU work runs on one thread.** PyMuPDF is not thread-safe, so all PDF and forensic checks share one worker (`CPU_WORKER_CONCURRENCY=1`). Raising `DETECTION_CONCURRENCY` (now 2) only overlaps the model and registry calls; a process pool would be the real fix. Tune it from the `perf` log lines once the rate limiter below exists.
- **Per-provider rate limiting.** No shared Anthropic limiter across the 4 queue workers. Detection runs are capped at `DETECTION_CONCURRENCY` (2), one at `DETECTION_MAX_MODEL_CALLS` (30) and the case analysis at 6, but nothing bounds the total across the queue, detection and an analysis at once.
- **Prometheus metrics.** Done: token, cache and cost counts per agent and per unit of work are on `/metrics` and the Grafana agents and cost dashboards.
- **Guardrails, evals and CI.** `injection_manifest.json` lists the defects planted in each generated `_F` file, so it can score detection per defect type (a case may upload the clean version, which has no `_F`; check the file names first). Synthetic companies always fail the ACRA check, so that finding fires on every generated case; it scores once per case (8 points) and counts the document as high. Golden-set nightly evals (classification accuracy, per-field extraction, escalation rate, cost per document, cache hit rate) from `documents_generated/` and a mocked-subset-per-PR check are not built. Also not built: specialist precision and recall scored on the forged documents from `injection/pdf.py` (keep the synthetic-to-real caveat).
- **Extraction repair node.** Only the classifier has a repair step (deterministic cleanup, one Haiku re-ask, then manual review); extraction has none. Unchecked whether that is still true in the code.
- **LangGraph Store** (learning from officer corrections): last phase, not started.
- **Very long bank statements** can still overflow the extraction cap (`_EXTRACTION_MAX_TOKENS` 16,000). An overflow now fails the document cleanly with the complete fields kept; chunked transaction extraction would fix it properly.
- **Detection specialist prompts don't cache.** Anthropic caches a prompt prefix only above a minimum length (about 4,096 tokens on Haiku 4.5, 1,024 on Sonnet; check the current docs). The specialists run on Haiku and their prompt plus tool definitions is shorter, so their `cache_control` markers are silently ignored. Padding the prefix would cost more than it saves; accept it unless the `perf` log shows a large Haiku input bill.
- **Credit report total check.** The new `CR.AGGREGATE_SUM` compares open accounts with the secured plus unsecured totals within a 1% tolerance, which would pass a 2,000 difference on a 1.8M total. Tighten it if it proves too loose.
- **A cross-document finding's category.** A share-capital conflict is labelled `unsupported_claim` because the verdict schema offers only two categories (`behavioural_anomaly`, `unsupported_claim`); a `cross_document_variance` label would need a schema change.
- **Vertical text.** `text_overlay.py` skips vertical text by an aspect-ratio guess because `_Line` records no direction. Extracting the direction from PyMuPDF would replace the guess.

## Case pipeline (cross-document analysis and risk outlook)

Built and exercised end to end on generated cases. Design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

- **Plan-then-judge has no follow-up.** The plan fixes its checks before seeing any result, so an investigation can't adapt (for example re-query with a different filter). It is deliberately cheap (2 Sonnet calls). If recall is too low in the evals, add one optional second round for hypotheses the judge marks "needs more evidence".
- **The trace includes dropped hypotheses.** `case_analysis_runs.trace` (shown in the case page's debug panel) lists every hypothesis tried and why it was discarded, while the design says unproven hypotheses are never shown or stored against a case. Decide before this is exposed beyond developers.
- **Evals:** false-positive rate on clean generated cases (including Other documents), recall on planted contradictions, and false-positive parity across subgroups.
- **Guardrails still open:** a dismissal flow for cross-document findings, audit retention, and the three sponsor questions in the plan. The protected-attribute screen over-blocks on purpose (a bank named "Indian Overseas Bank" would drop a hypothesis that names it).
- **Case-level frontend:** no frontend unit tests for `CaseAnalysisCard.tsx` and no Playwright spec.

## Performance

Budgets are in `HOW_IT_WORKS.md` §8; read them off the `perf` log lines. Query-level optimisations (the case list, snapshot reads, and so on) are in `REFACTORING_AUDIT.md` §3. Remaining here:

- **`GET /cases/{id}/analysis` costs 5 queries** each time the card reloads (documents, fields and traces, to recompute staleness).
- **`GET /documents/{id}/detection/case-match` costs 7 queries** and is called after each field edit.
- **A burst of changes can chain snapshot builds.** The first requester waits for the follow-up build; handing follow-ups to a background thread would keep its latency down.
- **The case list has no pagination.** Search, filters and sort run in the browser; server-side paging would need those moved.
- **Dev StrictMode** fires some document-page requests twice.

## Architecture (Kong / Prometheus / Grafana)

Built on the `microservices` branch (2026-10-22): Kong, `/metrics` in every service, Grafana with four dashboards, traces joined across services, and stuck-work recovery with its gauges. See `CLOUD_ARCHITECTURE_PLAN.md` sections 7, 8 and 16. Still open:
- **Alert rules.** There are none yet; the budget alert is the only one.
- **Shared Anthropic rate budget across instances** (plan section 5).
- **WebSocket ticket replay.** A ticket replayed within its 10 s on a *different* api instance would be accepted; each instance remembers only the tickets it has used. Closing that needs a shared store, which isn't worth it for a 10 s window.
- **Recovery of work still in a dead-letter subscription.** The sweeper rescues the document behind it, but the dead-lettered message itself stays in `domain-events-dead-letter-pull` until someone reads it.

### Tear down the old single-service deploy (kept running on purpose, 2026-10-22)

`fyp-backend` and `fyp-frontend` (Cloud Run, `asia-southeast1`) are the deploy from before the split. They are kept up for now. While they run:
- **They share the Supabase project.** A document handled through the old frontend is processed by the old code, which writes no audit events, doesn't appear in the new metrics or dashboards, and still applies the old `dev` case filtering. Don't use both at once, and never for a demo.
- **`main`'s `deploy.yml` can still redeploy them** with the `GCP_SA_KEY` secret until `microservices` is merged. Keep that secret until the teardown below.
- **Cost:** none while idle if they scale to zero. Check that no `minScale` of 1 or more appears in `gcloud run services describe fyp-backend --region asia-southeast1 --format="value(spec.template.metadata.annotations)"` (same for `fyp-frontend`).

**Teardown, step by step** (PowerShell; each `delete` asks for confirmation):

1. **Merge first.** Merge `microservices` into `main`, so `main`'s workflow is the new one and can't recreate the old services.
2. **Check what exists:** `gcloud run services list --region asia-southeast1`. Expect the new services (api, doc-worker, detection-worker, case-worker, audit-metrics, kong, frontend, grafana) plus `fyp-backend` and `fyp-frontend`. Don't touch `frontend`, which is the new one.
3. **Note the old service account** before deleting, for step 6: `gcloud run services describe fyp-backend --region asia-southeast1 --format="value(spec.template.spec.serviceAccountName)"`.
4. **Delete the two services:**
   ```powershell
   gcloud run services delete fyp-backend --region asia-southeast1
   gcloud run services delete fyp-frontend --region asia-southeast1
   ```
5. **Delete the GitHub secrets the new workflow doesn't read** (Settings → Secrets and variables → Actions → Secrets): `GCP_SA_KEY`, `SUPABASE_*`, `ANTHROPIC_API_KEY`, `AUTH_*`, `ONEMAP_API_TOKEN`, `URA_API_KEY`, and any `GCP_PROJECT_ID` or `GCP_REGION` *secrets*.
   - Keep the *variables* `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER` and `GCP_DEPLOYER_SA`.
   - Keep the secret `DATA_GOV_SG_API_KEY`.
6. **Revoke the key behind `GCP_SA_KEY`.** Deleting the GitHub secret doesn't invalidate the key itself.
   - Find its account: the `client_email` in that key's JSON, if you still have it, or the account from step 3.
   - List its keys: `gcloud iam service-accounts keys list --iam-account <email> --managed-by user`.
   - Delete each: `gcloud iam service-accounts keys delete <KEY_ID> --iam-account <email>`.
   - If the account was used only by the old deploy (not `github-deployer` or a `run-*` account, which Terraform owns), delete it too: `gcloud iam service-accounts delete <email>`.
7. **Old images.**
   - Run `gcloud artifacts repositories list`. Keep `app`; delete any repository the old deploy used: `gcloud artifacts repositories delete <name> --location asia-southeast1`.
   - Old GHCR packages: GitHub → the organisation or your profile → Packages → delete them.
8. **Old secrets in Secret Manager:** `gcloud secrets list`. Keep every secret Terraform made (`terraform output secrets_to_fill`, plus `grafana-admin-password` and `kong-gmp-config`); delete any others with `gcloud secrets delete <name>`.
9. **Drop the old `dev` column** (optional; a database change, so decide deliberately). Once the old backend is gone nothing writes or reads `cases.dev`, so it can be dropped in the Supabase dashboard.
10. **Update the docs.** Remove this section, and tick the clean-up item in `deploy/terraform/README.md`.

## Frontend

- **Group card props.** `DocumentDetailsPage.tsx` and `UploadPage.tsx` use hooks, but the cards still take many props (`ExtractedInformationCard`, `DocumentListPanel`). See `REFACTORING_AUDIT.md`. Verify with `tsc`, `npm run build` and the `document-review`, `upload`, `case-management` and `checklist` specs.
- **Loading pages (not urgent).** Three pages in `App.tsx` are a bare line of text with no header: the `Suspense` fallback, `Loading case...` and `Loading document...`. Give them the document-not-found page's design. The case and document routes also wait for the whole case list before loading their own data; loading independently would remove that delay.

## Small known gaps

- **Security job: make it blocking.** Checkov and Snyk findings were fixed on 2026-10-22: the backend runs as `app` (uid 10001), every image has a `HEALTHCHECK`, Pillow 13 and pypdf 6; Terraform skips (customer-managed keys, the view's deletion protection, the GitHub OIDC branch pin) are inline with reasons. Once a run is green (Snyk's npm scan had not run before then), remove `continue-on-error` from the job.
- **Frontend fully non-root.** nginx's workers already run as `nginx`; only the master is root (Checkov skip in `frontend/Dockerfile`). Moving to `nginxinc/nginx-unprivileged` means listening on 8080: change `nginx.conf`, the frontend's `container_port` in `run.tf` and the compose port together, and apply before the deploy.
- **Pillow's `getdata()`** is deprecated (removed in Pillow 14, hence the `<14.0` cap). Only the injection tool uses it (`app/injection/pdf.py`); switch to `get_flattened_data()` before raising the cap.
- **Frontend type errors CI doesn't catch.** `tsc` fails on `frontend/src/pages/upload/CaseOverview.tsx:72`, a teammate's case-summary code from commit `d1529ee`. A finding's severity can be `info`, but the sort table lists only high, medium and low; adding `info: 3` fixes it. CI runs lint and build (Vite doesn't type-check) but not `tsc --noEmit`, so add that step to the frontend job.

- `submission_finalized` is remembered per process (`case_service._synced_finalized`) so an unchanged value isn't rewritten. With more than one backend instance, or a hand edit in the database, the stored value is corrected on the next change in the computed answer, not before.
