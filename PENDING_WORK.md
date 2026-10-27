# Pending work

Open product and platform work only: remove an item once it's done. Elsewhere: code smells and optimisations in `REFACTORING_AUDIT.md`; test work in `backend_tests.md`; detection bugs in `FRAUD_DETECTION_MAP.md` §6; the cloud to-do list (failure tests, case dashboard, shared Anthropic rate budget, Kong config test, staging) in `CLOUD_ARCHITECTURE_PLAN.md` section 9, and the revised Stage 2 in section 10. Updated 2026-10-26.

## my notes

check if audit trail watches for cancel/retry etc.

## Product

- **Automatic case analysis** (this sprint). The officer clicks it today. Not built: the gate on every document completion, supersede with a run-id fence, debounce and a re-run cap (`CROSS_DOCUMENT_AND_RISK_PLAN.md` Step 0). Decide `submission_finalized`'s shape here: keep it stored, or compute it inside the gate and delete the debounce machinery (`REFACTORING_AUDIT.md` backend 2.4).
- **Case-level flags.** Cross-document and identity findings live only inside each run's JSON (`case_analysis_runs.result.findings`), so there's no row per finding, no status and no dismissal. A case-level flags table and module would give them that.
- **Officer review of flags.** `flags` has `status`, `reviewed_by` and `reviewed_at`, but no endpoint or UI uses them. Needs an endpoint, a UI, and a decision on how a dismissal affects the score.
- **Guard against repeated runs.** Each "Run again" spends about 12k tokens even when nothing changed; skipping a run whose fingerprint matches the last complete one would stop that, but changes what the button does.
- **Flags for older documents.** Documents detected before `flags` was wired have a report but no flag rows, and approved documents can't re-run detection.
- **Agent-raised findings.** A specialist can only assess findings a tool returns; something it notices itself (a "modified copy" subject) appears in its summary but never scores. Needs a way to raise a finding that cites the field it read.

## Claude models

- **Newer models are cheaper; we still default to the older ones.** `config.py` defaults to `claude-sonnet-4-6` ($3 / $15 per million input / output tokens) and `claude-haiku-4-5-20251001` ($1 / $5); `backend/.env` may override them. The current generation lists `claude-sonnet-5-5` at $2 / $10 (about a third cheaper) and `claude-haiku-5-5` at $0.10 / $0.50 (about 10× cheaper, for prompts up to 100K tokens). Switching isn't a one-line change:
  - `temperature=0` in `langgraph/_anthropic.py`: the new models accept only the default sampling settings, so those requests are rejected.
  - Forced tool calls (`with_structured_output` or `tool_choice` in `langgraph/_anthropic.py`, `langgraph/detection/specialists.py`, `case_pipeline/cross_document/agents.py`, `case_pipeline/risk/outlook.py`): Sonnet 5.5 rejects them; move to a non-forced call or structured outputs.
  - Thinking is always on in the new models, so a call can produce more tokens: compare cost per case on real runs, not the price table.
  - `pricing.py` lists the Sonnet 5 and Haiku 5 families at $3 / $15 and $1 / $5, so the cost logs would be wrong after a switch until it's corrected.
  - Decide per agent (classifier, extraction, detection lead and specialists, cross-document planner and judge) whether to switch, checking a few known cases by hand before and after.

## Deployment note (report and commercialisation document)

- **Data residency asterisk.** Put this wherever the report describes the model calls, and carry it into the commercialisation document the supervisor asked for (server, model, RAM, storage requirements): *"This prototype calls the public Claude API. In production, GLDB would reach Claude through an enterprise agreement or its cloud provider (for example Vertex AI), with regional processing and zero data retention, subject to its outsourcing and MAS requirements."* Before relying on it: check whether Vertex AI serves Claude in Singapore (`asia-southeast1`); the Claude API's own residency option is global or US only. Don't name banks that have signed such contracts without a source.

## Detection and the document pipeline

- **Watch the new CPU process pool** on the first deployed runs (`CPU_WORKER_PROCESSES=2` on doc-worker and detection-worker): memory per instance (each process loads PyMuPDF and the OCR model), and the "CPU worker process died" log line.
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

- **Evals aren't built** (next few days): an evaluation framework run on demand when a prompt or model changes, not nightly, as the reference for quality. Golden-set runs from `documents_generated/` (classification accuracy, per-field extraction, escalation rate, cost per document, cache hit rate); detection precision and recall per defect type from `injection_manifest.json` (a case may upload the clean, non-`_F` file; check names first); for case analysis, false positives on clean cases, recall on planted contradictions, parity across subgroups. Synthetic companies always fail the ACRA check (8 points once per case). Keep the synthetic-to-real caveat.
- **Checks that exist only as scratch scripts,** worth turning into tests: the verifier rules, the bias screens (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and snapshot builder, the flag write-on-change memo, `flags_repository.build_rows`.

## Performance

Budgets are in `HOW_IT_WORKS.md` §8; query-level fixes are in `REFACTORING_AUDIT.md` §3.
- `GET /cases/{id}/analysis` and `GET /documents/{id}/detection/case-match` read more than they need: fixes in `REFACTORING_AUDIT.md` §3 (1 and 2).
- A burst of changes can chain snapshot builds, and the first requester waits for the follow-up.
- **Opening a case still waits in sequence on a direct link or refresh:** the case lookup, then the WebSocket ticket, connection and first snapshot. (From the queue the case is handed over and the page code is prefetched.) Not done yet: fetching the first snapshot over a plain request alongside the socket, and putting the case's own details in the snapshot so a direct load needs one request.
- **The queue's status filter, risk filter and risk sort read every matching case** (and `/cases/counts` reads every document's status) because status and risk are computed, not stored. Fine at today's volume; a stored rollup or index would be needed if the case count grows a lot.

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

- **Dev StrictMode** fires some document-page requests twice.
- **`submission_finalized`** is remembered per process (`case_service._synced_finalized`), so with several instances or a hand edit the stored value is only corrected on the next change.
