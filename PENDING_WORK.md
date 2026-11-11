# Pending work

Open product and platform work only: remove an item once it's done. Elsewhere: stability findings, code smells, optimisations and database load in `STABILITY_AND_REFACTORING_AUDIT.md`; test work in `backend_tests.md`; detection bugs in `FRAUD_DETECTION_MAP.md` §6; the cloud to-do list (failure tests, shared Anthropic rate budget, Kong config test, staging) in `CLOUD_ARCHITECTURE_PLAN.md` section 9, and the revised Stage 2 in section 10; backwards-compatibility fallbacks in `STABILITY_AND_REFACTORING_AUDIT.md` §7. Updated 2026-11-10.

## Product

- **Skip repeated runs** (decided 2026-11-01): "Run again" on a case whose fingerprint matches the last complete run shows that run instead of starting another (each spends about 12k tokens).
- **Agent-raised findings.** A specialist can only assess findings a tool returns; something it notices itself (a "modified copy" subject) appears in its summary but never scores. Needs a way to raise a finding that cites the field it read.
- **Evaluate extraction second-look accuracy.** Compare text-only and selective second-look results against labelled bank statements and payslips. It should also compare code-read bank-statement transactions (`statement_table.py`) with the model's. The evaluator should track field accuracy and text/image disagreements; run it on demand because it uses model calls.

## Claude models

- **Billed cost per role.** Per-call cost is exact (token counts × list price, `pricing.py`). To reconcile with the bill, give each worker role its own Claude workspace and API key; the Cost API (`/v1/organizations/cost_report`, Admin API key, daily) then reports billed USD per workspace. Per-request tags don't exist.

## Deployment note (report and commercialisation document)

- **Data residency asterisk.** Put this wherever the report describes the model calls, and carry it into the commercialisation document the supervisor asked for (server, model, RAM, storage requirements): *"This prototype calls the public Claude API. In production, GLDB would reach Claude through an enterprise agreement or its cloud provider (for example Vertex AI), with regional processing and zero data retention, subject to its outsourcing and MAS requirements."* Google's data-residency page lists Singapore (`asia-southeast1`) for some Claude models, but which ones varies, so the sentence names no model or region; the Claude API's own residency option is global or US only. Don't name banks that have signed such contracts without a source.

## Detection and the document pipeline

- **Watch the new CPU process pool** on the first deployed runs (`CPU_WORKER_PROCESSES=2` on doc-worker and detection-worker): memory per instance (each process loads PyMuPDF and the OCR model), and the "CPU worker process died" log line.
- **A cross-document finding's category:** a share-capital conflict is labelled `unsupported_claim`; a `cross_document_variance` label for verdicts needs a schema change.
- **Vertical text:** `text_overlay.py` skips it by an aspect-ratio guess; reading the direction from PyMuPDF would replace the guess.
- **Detection specialist prompts don't cache** (Haiku's minimum cacheable prefix is longer than their prompts). Accept it unless the cost logs show a large Haiku input bill.
- **Postgres checkpointing** isn't built (`checkpointer=False`): a crashed run restarts from scratch, with no replay. Now only a debugging aid. If built: `langgraph-checkpoint-postgres` on the transaction pooler, its own schema, parent graph only, delete a document's thread when it finishes, no PII or text in state.
- **Learning from officer corrections** is built as a `finding_feedback` table, not a LangGraph Store: every verdict and missed issue is saved with personal details removed, and the lessons from other cases, counted per check code, are shown to the detection specialists (on the tool result that fired the code) and to the case analysis's planner and judge (`app/feedback_repository.py`, `langgraph/detection/feedback.py`, `case_pipeline/cross_document/feedback.py`). Officers' type corrections and field edits teach the classifier and the extractor the same way, from `audit_events` (`corrections_repository.py`, `langgraph/correction_hints.py`). Not built: feedback for the code-decided finding categories (nothing a model could change), and any automatic judgement of whether a lesson helped.
- **Model-output gaps:** nothing checks that an extracted value has its schema's type (text in a number field), and the binder and the classifier don't record a repaired reply in the trace (they don't pass `raw` to `validate`).

## Case analysis

Design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.
- **Plan-then-judge has no follow-up round;** add one optional round if the evals show low recall.
- **Clean up the run trace** (later). It lists dropped hypotheses, which the design says are never shown; that goes with the clean-up.
- **Guardrails still open:** a dismissal flow for cross-document findings, audit retention, the three sponsor questions in the plan.
- **The evidence requests (`risk/outlook.py`) are one Haiku call, not an agent, and need more ideation.** A document can only lower the score, so "ask for all of them" always looks best. Ideas so far: the smallest set that reaches a target level (code, not a model); scoring a document that goes against the client too, since one that can swing either way tells the officer more; an agent that checks the case's existing documents before asking, and tries sets with a re-scoring tool. The score stays deterministic either way.

## Evals and verification

- **Evals aren't built** (next few days): an evaluation framework run on demand when a prompt or model changes, not nightly, as the reference for quality. Golden-set runs from `documents_generated/` (classification accuracy, per-field extraction, escalation rate, cost per document, cache hit rate); detection precision and recall per defect type from `injection_manifest.json` (a case may upload the clean, non-`_F` file; check names first); for case analysis, false positives on clean cases, recall on planted contradictions, parity across subgroups. Synthetic companies always fail the ACRA check (8 points once per case). Keep the synthetic-to-real caveat.
- **Checks that exist only as scratch scripts,** worth turning into tests: the bias screens (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and snapshot builder, the finding fingerprints and the kept/dismissed split (`finding_keys.py`)

## Performance

Budgets are in `HOW_IT_WORKS.md` §8; database load is in `STABILITY_AND_REFACTORING_AUDIT.md` §2.
- A burst of changes can chain snapshot builds, and the first requester waits for the follow-up.
- **Opening a case still waits in sequence on a direct link or refresh:** the case lookup, then the WebSocket ticket, connection and first snapshot. (From the queue the case is handed over and the page code is prefetched.) Not done yet: fetching the first snapshot over a plain request alongside the socket, and putting the case's own details in the snapshot so a direct load needs one request.

## Cloud operations

- **Drop the unused publishable Supabase key.** The backend no longer reads `SUPABASE_PUBLISHABLE_KEY` and the database grants `anon` nothing. Remove it from `local.supabase` in `deploy/terraform/main.tf`, `terraform apply`, then delete the secret in Secret Manager; disabling the legacy keys in the Supabase dashboard is the last step. Not urgent: the logs show no call with it after 08:10 UTC on 2026-11-07, when the new revision took over.
- **Make Snyk blocking again once its monthly quota resets** (made non-blocking 2026-11-04): delete the two `continue-on-error: true` lines under the Snyk steps in `.github/workflows/ci-cd.yml`, and drop "temporarily non-blocking" from CLAUDE.md, README and CLOUD_ARCHITECTURE_PLAN §6.
- **Frontend fully non-root:** nginx's workers already run as `nginx`; going all the way means `nginx-unprivileged` on 8080 (change `nginx.conf`, the frontend's `container_port` in `run.tf` and the compose port together, apply before deploying).
- **Dead-lettered messages** stay in `domain-events-dead-letter-pull` until someone reads them (the sweeper rescues the document behind them).
- **WebSocket ticket replay** within 10 s on a different api instance would be accepted; not worth a shared store.

## Frontend

- **Dev StrictMode** fires some document-page requests twice.
