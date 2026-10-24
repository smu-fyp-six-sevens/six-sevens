# Pending work

Open product and platform work only: remove an item once it's done. Elsewhere: code smells and optimisations in `REFACTORING_AUDIT.md`; test work in `backend_tests.md`; detection bugs in `FRAUD_DETECTION_MAP.md` §6; the cloud to-do list (failure tests, case dashboard, eval job, shared Anthropic rate budget, Kong config test, staging, Stage 2) in `CLOUD_ARCHITECTURE_PLAN.md` section 9. Updated 2026-10-24.

## Product

- **Automatic case analysis** (this sprint). The officer clicks it today. Not built: the gate on every document completion, supersede with a run-id fence, debounce and a re-run cap (`CROSS_DOCUMENT_AND_RISK_PLAN.md` Step 0). Decide `submission_finalized`'s shape here: keep it stored, or compute it inside the gate and delete the debounce machinery (`REFACTORING_AUDIT.md` backend 2.4).
- **Case-level flags.** Cross-document and identity findings live only inside each run's JSON (`case_analysis_runs.result.findings`), so there's no row per finding, no status and no dismissal. A case-level flags table and module would give them that.
- **Officer review of flags.** `flags` has `status`, `reviewed_by` and `reviewed_at`, but no endpoint or UI uses them. Needs an endpoint, a UI, and a decision on how a dismissal affects the score.
- **Guard against repeated runs.** Each "Run again" spends about 12k tokens even when nothing changed; skipping a run whose fingerprint matches the last complete one would stop that, but changes what the button does.
- **Flags for older documents.** Documents detected before `flags` was wired have a report but no flag rows, and approved documents can't re-run detection.
- **Agent-raised findings.** A specialist can only assess findings a tool returns; something it notices itself (a "modified copy" subject) appears in its summary but never scores. Needs a way to raise a finding that cites the field it read.

## Detection and the document pipeline

- **Payslip 7 pt shift not detected** (caught on the balance sheet and credit report). Probably no reference layout for the payslip template; check `layout_anomalies` and the payslip generator.
- **Re-scan a case to confirm the latest detection changes** (the row and foreign-font checks, the hard-breach rule, metadata subject and title, cross-document leads `cross-doc-v7`, file-contents guidance), all tested on synthetic inputs only. After a live run, image tools should mostly stop returning "nothing to check" (before: 15 of 19 calls), and the leads should produce a tested hypothesis.
- **Install `exiftool` locally.** Without it the metadata check and the AI-provenance check report "could not run" and never score. Optional: a startup warning when it's missing, a PyMuPDF fallback for basic metadata.
- **CPU work runs on one thread** (PyMuPDF isn't thread-safe; `CPU_WORKER_CONCURRENCY=1`), so the detection-worker's second vCPU idles. A process pool is the real fix.
- **Very long bank statements** can overflow the extraction cap (`_EXTRACTION_MAX_TOKENS` 16,000); the document fails cleanly with the complete fields kept. Chunked transaction extraction would fix it.
- **Credit report total check** (`CR.AGGREGATE_SUM`) allows 1%, which would pass a 2,000 difference on 1.8M. Tighten if it proves too loose.
- **A cross-document finding's category:** a share-capital conflict is labelled `unsupported_claim`; a `cross_document_variance` label for verdicts needs a schema change.
- **Vertical text:** `text_overlay.py` skips it by an aspect-ratio guess; reading the direction from PyMuPDF would replace the guess.
- **Detection specialist prompts don't cache** (Haiku's minimum cacheable prefix is longer than their prompts). Accept it unless the cost logs show a large Haiku input bill.
- **Postgres checkpointing** isn't built (`checkpointer=False`): a crashed run restarts from scratch, with no replay. Now only a debugging aid. If built: `langgraph-checkpoint-postgres` on the transaction pooler, its own schema, parent graph only, delete a document's thread when it finishes, no PII or text in state.
- **LangGraph Store** (learning from officer corrections): last phase, not started.

## Case analysis

Design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.
- **Plan-then-judge has no follow-up round;** add one optional round if the evals show low recall.
- **The run trace lists dropped hypotheses,** while the design says unproven ones are never shown. Decide before exposing it beyond developers.
- **Guardrails still open:** a dismissal flow for cross-document findings, audit retention, the three sponsor questions in the plan.

## Evals and verification

- **Evals aren't built:** golden-set nightly runs from `documents_generated/` (classification accuracy, per-field extraction, escalation rate, cost per document, cache hit rate); detection precision and recall per defect type from `injection_manifest.json` (a case may upload the clean, non-`_F` file; check names first); for case analysis, false positives on clean cases, recall on planted contradictions, parity across subgroups. Synthetic companies always fail the ACRA check (8 points once per case). Keep the synthetic-to-real caveat.
- **Checks that exist only as scratch scripts,** worth turning into tests: the verifier rules, the bias screens (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and snapshot builder, the flag write-on-change memo, `flags_repository.build_rows`.

## Performance

Budgets are in `HOW_IT_WORKS.md` §8; query-level fixes are in `REFACTORING_AUDIT.md` §3.
- `GET /cases/{id}/analysis` costs 5 queries per card reload; `GET /documents/{id}/detection/case-match` costs 7 and runs after each field edit.
- A burst of changes can chain snapshot builds, and the first requester waits for the follow-up.
- The case list has no pagination (search, filters and sort run in the browser).

## Cloud operations

- **Artifact Registry clean-up is in dry-run mode** (applied 2026-10-24; `app` held 11.8 GB). The DELETE policies in `main.tf` (older than 14 days; untagged older than 7) only log what they'd delete; KEEP protects the 10 newest versions of each image.
  1. After a day, check Logs Explorer with `protoPayload.methodName="Dry run : Vulnerability or cleanup policy delete"`: no image a serving revision uses should be listed. Images pushed before 2026-10-24 are indexes with untagged children; newer ones are single manifests (`--provenance=false`).
  2. Set `cleanup_policy_dry_run = false` and apply.
- **Billed cost in Grafana:** set `billing_export_table` once the export table appears (`deploy/terraform/README.md`, "Open").
- **Snyk's npm scan has never completed a run.** The security job blocks the deploy: if it finds a high-severity frontend issue, fix or upgrade rather than making the job optional again.
- **Branch protection on `main` is off on purpose** (small changes are pushed directly). Turning it on (require Backend CI, Frontend CI, Security, Terraform) would stop a failing change from landing at all.
- **Frontend fully non-root:** nginx's workers already run as `nginx`; going all the way means `nginx-unprivileged` on 8080 (change `nginx.conf`, the frontend's `container_port` in `run.tf` and the compose port together, apply before deploying).
- **Dead-lettered messages** stay in `domain-events-dead-letter-pull` until someone reads them (the sweeper rescues the document behind them).
- **WebSocket ticket replay** within 10 s on a different api instance would be accepted; not worth a shared store.

## Frontend

- **`tsc` fails on `CaseOverview.tsx:72`** (a teammate's code: a finding's severity can be `info`, but the sort table lists only high, medium and low; add `info: 3`). CI doesn't run `tsc --noEmit`; add it to the frontend job.
- **Group card props:** `ExtractedInformationCard` on `DocumentDetailsPage.tsx` still takes many props (the case page's list now takes its hooks as two objects).
- **Loading pages** (not urgent): the `Suspense` fallback, `Loading case...` and `Loading document...` are bare text; give them the not-found page's design. The case and document routes also wait for the whole case list first.
- **Dev StrictMode** fires some document-page requests twice.
- **`submission_finalized`** is remembered per process (`case_service._synced_finalized`), so with several instances or a hand edit the stored value is only corrected on the next change.
