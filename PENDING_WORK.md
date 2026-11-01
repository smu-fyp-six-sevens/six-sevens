# Pending work

Open product and platform work only: remove an item once it's done. Elsewhere: code smells and optimisations in `REFACTORING_AUDIT.md`; test work in `backend_tests.md`; detection bugs in `FRAUD_DETECTION_MAP.md` §6; the cloud to-do list (failure tests, case dashboard, shared Anthropic rate budget, Kong config test, staging) in `CLOUD_ARCHITECTURE_PLAN.md` section 9, and the revised Stage 2 in section 10; backwards-compatibility fallbacks in `REFACTORING_AUDIT.md` §5. Updated 2026-11-01.

## Database

- **Keep prod unused until `integration` is deployed:** the database no longer has the tables and columns `main`'s code writes (`flags`, `finding_reviews`, `case_status_history`, `app_settings`, `cases.submission_finalized`).

## Product

- **Automatic case analysis** (this sprint). The officer clicks it today. Not built: the gate on every document completion, supersede with a run-id fence, debounce and a re-run cap (`CROSS_DOCUMENT_AND_RISK_PLAN.md` Step 0).
- **Guard against repeated runs.** Each "Run again" spends about 12k tokens even when nothing changed; skipping a run whose fingerprint matches the last complete one would stop that, but changes what the button does.
- **Agent-raised findings.** A specialist can only assess findings a tool returns; something it notices itself (a "modified copy" subject) appears in its summary but never scores. Needs a way to raise a finding that cites the field it read.

## Claude models

- **Live check of the switch to Sonnet 5.5 and Haiku 5.5** (effort `low` everywhere except the detection lead and the cross-document planner and judge at `medium`). Run a few known cases end to end and compare against earlier runs: types, fields, findings, the case analysis, cost per case and time per stage. `backend/.env` may still name the old models; remove those lines. Watch for: replies cut off at the raised token caps; the specialists' first turn, which starts with a code-made tool call that has no thinking block (accepted in principle, unconfirmed live); and how often the cross-document planner or judge needs its one retry (`attempt` on the span).
- **Billed cost per role.** Per-call cost is exact (token counts × list price, `pricing.py`). To reconcile with the bill, give each worker role its own Claude workspace and API key; the Cost API (`/v1/organizations/cost_report`, Admin API key, daily) then reports billed USD per workspace. Per-request tags don't exist.

## Deployment note (report and commercialisation document)

- **Data residency asterisk.** Put this wherever the report describes the model calls, and carry it into the commercialisation document the supervisor asked for (server, model, RAM, storage requirements): *"This prototype calls the public Claude API. In production, GLDB would reach Claude through an enterprise agreement or its cloud provider (for example Vertex AI), with regional processing and zero data retention, subject to its outsourcing and MAS requirements."* Before relying on it: check whether Vertex AI serves Claude in Singapore (`asia-southeast1`); the Claude API's own residency option is global or US only. Don't name banks that have signed such contracts without a source.

## Extraction agent

Extraction is one Sonnet call over the page text (`langgraph/extract.py`). The text loses table columns, and the model says so in `low_confidence_reason`: a statement's debit and credit "inferred from balance movement", a credit report's "table layout is garbled; balance column alignment unclear". Plan: escalate field by field, the way classification escalates (rules, then the text classifier, then vision).
1. **Text pass:** today's call, unchanged.
2. **Code picks the fields for a second look:** below a confidence threshold, a required field that's missing, or one a guard dropped (`_drop_unprinted_numbers` and the like). None picked means no extra cost, as on most clean native PDFs.
3. **An extraction agent on those fields only,** a subgraph shaped like `classify_graph` (`extract_graph.py`, tools in `extraction_tools.py`), with the order enforced in code like `classify_graph._refusal`:
   - `layout_lookup(fields)`: the words near each field's label with their positions (PyMuPDF), and table cells by column (pdfplumber) for statement and credit-report rows. Free, so it runs first; on a scan it uses the OCR boxes.
   - `vision_read(fields, pages)`: renders only those pages and asks only for those fields. Once at most, only for fields layout didn't settle; straight away on an image-only document.
   - The agent may leave an unimportant optional field alone.

**Rules code enforces:**
- A second look may fill a field or raise its confidence, never "correct" a value to make the arithmetic work. A statement whose balances don't add up may be forged; a disagreement between the text and the image is kept for detection, not resolved.
- On a native PDF, vision can't add a number the text layer doesn't hold (as `_drop_unprinted_numbers`): a forged overlay is exactly where the image and the text disagree.
- Each field records its source (text, layout or vision) in the trace.

**Also:** `extract_node` and `rerun_extraction` both hand over to the subgraph. The CLAUDE.md design rule ("classifier and extraction are plain pipelines") and `HOW_IT_WORKS.md`'s extraction section change with it. Mocked tests cover the tool order, the field picking and the merge rules. An eval on bank statements and payslips (spends tokens; on demand) decides whether it beats the single call.

## Detection and the document pipeline

- **Watch the new CPU process pool** on the first deployed runs (`CPU_WORKER_PROCESSES=2` on doc-worker and detection-worker): memory per instance (each process loads PyMuPDF and the OCR model), and the "CPU worker process died" log line.
- **A cross-document finding's category:** a share-capital conflict is labelled `unsupported_claim`; a `cross_document_variance` label for verdicts needs a schema change.
- **Vertical text:** `text_overlay.py` skips it by an aspect-ratio guess; reading the direction from PyMuPDF would replace the guess.
- **Detection specialist prompts don't cache** (Haiku's minimum cacheable prefix is longer than their prompts). Accept it unless the cost logs show a large Haiku input bill.
- **Postgres checkpointing** isn't built (`checkpointer=False`): a crashed run restarts from scratch, with no replay. Now only a debugging aid. If built: `langgraph-checkpoint-postgres` on the transaction pooler, its own schema, parent graph only, delete a document's thread when it finishes, no PII or text in state.
- **LangGraph Store** (learning from officer corrections): last phase, not started.
- **Model-output gaps:** nothing checks that an extracted value has its schema's type (text in a number field), and the binder and the classifier don't record a repaired reply in the trace (they don't pass `raw` to `validate`).

## Case analysis

Design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.
- **Plan-then-judge has no follow-up round;** add one optional round if the evals show low recall.
- **The run trace lists dropped hypotheses,** while the design says unproven ones are never shown. Decide before exposing it beyond developers.
- **Guardrails still open:** a dismissal flow for cross-document findings, audit retention, the three sponsor questions in the plan.
- **The evidence requests (`risk/outlook.py`) are one Haiku call, not an agent, and need more ideation.** A document can only lower the score, so "ask for all of them" always looks best. Ideas so far: the smallest set that reaches a target level (code, not a model); scoring a document that goes against the client too, since one that can swing either way tells the officer more; an agent that checks the case's existing documents before asking, and tries sets with a re-scoring tool. The score stays deterministic either way.

## Evals and verification

- **Evals aren't built** (next few days): an evaluation framework run on demand when a prompt or model changes, not nightly, as the reference for quality. Golden-set runs from `documents_generated/` (classification accuracy, per-field extraction, escalation rate, cost per document, cache hit rate); detection precision and recall per defect type from `injection_manifest.json` (a case may upload the clean, non-`_F` file; check names first); for case analysis, false positives on clean cases, recall on planted contradictions, parity across subgroups. Synthetic companies always fail the ACRA check (8 points once per case). Keep the synthetic-to-real caveat.
- **Checks that exist only as scratch scripts,** worth turning into tests: the verifier rules, the bias screens (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and snapshot builder, the finding fingerprints and the kept/dismissed split (`finding_keys.py`), `findings_repository`'s row builders.

## Performance

Budgets are in `HOW_IT_WORKS.md` §8; query-level fixes are in `REFACTORING_AUDIT.md` §3.
- A burst of changes can chain snapshot builds, and the first requester waits for the follow-up.
- **Opening a case still waits in sequence on a direct link or refresh:** the case lookup, then the WebSocket ticket, connection and first snapshot. (From the queue the case is handed over and the page code is prefetched.) Not done yet: fetching the first snapshot over a plain request alongside the socket, and putting the case's own details in the snapshot so a direct load needs one request.
- **The queue's status filter, risk filter and risk sort read every matching case** (and `/cases/counts` reads every document's status) because status and risk are computed, not stored. Fine at today's volume; a stored rollup or index would be needed if the case count grows a lot.

## Cloud operations

- **Artifact Registry clean-up is in dry-run mode** (applied 2026-10-24; `app` held 11.8 GB). The DELETE policies in `main.tf` (older than 14 days; untagged older than 7) only log what they'd delete; KEEP protects the 10 newest versions of each image.
  1. After a day, check Logs Explorer with `protoPayload.methodName="Dry run : Vulnerability or cleanup policy delete"`: no image a serving revision uses should be listed. Images pushed before 2026-10-24 are indexes with untagged children; newer ones are single manifests (`--provenance=false`).
  2. Set `cleanup_policy_dry_run = false` and apply.
- **Billed cost in Grafana:** set `billing_export_table` once the export table appears (`deploy/terraform/README.md`, "Open").
- **Snyk's npm scan has never completed a run.** The security job blocks the deploy: if it finds a high-severity frontend issue, fix or upgrade rather than making the job optional again.
- **Branch protection on `main` is off on purpose** (small changes are pushed directly). Turning it on (require Backend CI, Frontend CI, Security) would stop a failing change from landing at all.
- **Frontend fully non-root:** nginx's workers already run as `nginx`; going all the way means `nginx-unprivileged` on 8080 (change `nginx.conf`, the frontend's `container_port` in `run.tf` and the compose port together, apply before deploying).
- **Dead-lettered messages** stay in `domain-events-dead-letter-pull` until someone reads them (the sweeper rescues the document behind them).
- **WebSocket ticket replay** within 10 s on a different api instance would be accepted; not worth a shared store.

## Frontend

- **Raw error messages are shown on the page.** Move the raw text to `console.error` and show an informative, user-friendly message instead (what failed, and what the officer can do: try again, cancel first, refresh, sign in again).
  - **The root:** `services/uploadService/assertOk` (`request.ts`) throws `"<message> (<status>): <response body>"`, so the UI shows the status code and FastAPI's JSON, e.g. `Failed to save case (502): {"detail":"Failed to save the case. Check Supabase table access."}`. Every service function uses it (`cases.ts`, `documents.ts`, `detection.ts`, `notes.ts`, `caseMatch.ts`, `audit.ts`, about 35 calls), and about 30 places print `err.message` as is: `useCaseQueue`, `CaseQueuePage` (delete), `CaseRow`, `useCaseSettings`, `useCaseByNumber` → `App.tsx` `LoadError`, `useDocumentRowActions`, `useDocumentSelection` (delete, move), `useUploadSelection`, `useDocumentActions`, `useDocumentRecord`, `useDocumentTypeEditing`, `useFieldEditing`, `ExtractedInformationCard`, `useDetection` (five errors), `CaseAnalysisCard` (start), `NotesCard`, `AuditTrailCard`, `useCaseCost`, `Breadcrumbs`, `useDocumentPreviewUrl`, `DocumentPreviewModal`.
  - **The fix:** one error class in `request.ts` that keeps the status and the parsed `detail`, logs the raw body to the console, and picks the message:
    - 409 and 400: the backend's `detail` (already written for officers, e.g. "cancel it first");
    - 401: "Your session has expired. Sign in again.";
    - 404: "This case or document no longer exists.";
    - 5xx or no response: "<what failed>. Something went wrong on our side; try again, and tell the team if it keeps happening."
  - **Same pattern outside it:** `CreateCasePage.tsx` builds its own `(<status>): <body>` message; `detection.ts` `runIndividualDetectionCheck` shows a 409's raw body.
  - **Backend errors stored and shown as is:**
    - `CaseAnalysisCard` prints `analysis.error`, which `case_pipeline/service.py` stores as `"<ExceptionType>: <message>"`;
    - `DocumentSummaryCard` shows `failure_reason` under a friendly title (`formatters.describeFailureReason`) but also prints the raw reason (e.g. `Unhandled processing error: ...`);
    - `AuditTrailCard` shows `failure_reason` and stringifies object values in field changes;
    - a crashed detection report comes back as a 502 `"Detection failed: <ExceptionType>: ..."`.

    Show a plain sentence, and keep the raw text in the console (or behind a "Details" toggle).
  - **Leave alone:** the developer panels that print JSON on purpose (`LangGraphTracePanel`, `CaseAnalysisTracePanel`, the individual check results in `DetectionPanel`, all `debug-*`).
  - The mock backend (`e2e/support/mockBackend.ts`) and any spec that matches error text change with it.
- **Dev StrictMode** fires some document-page requests twice.
