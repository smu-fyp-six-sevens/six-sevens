# How the App Works

A plain-language walkthrough of the system as it is now (updated 2026-10-19): from a loan officer logging in to a case getting a risk score, what is built, and where it is rough. Design rationale is in the other documents; this one describes behaviour.

---

## 1. The one-paragraph version

A loan officer creates a **case** for a company applying for a loan and uploads its documents (bank statements, NRICs, certificates and so on). Each document goes through a background pipeline: it is **read** (text from the PDF, or OCR for scans), **classified** ("this is a payslip") and **extracted** ("net pay = 8,027.85"). The officer reviews and **approves** it. Approval starts the **Detection Agent**: a lead AI investigator plus three specialist AIs that run forensic checks (fonts, layout, images, metadata, spelling, arithmetic, government registries) and write a fraud report for that document. The findings a specialist judged genuine are also stored one by one as **flags**. Once every approved document has a report, the officer can run **Case Analysis**. Code first compares the documents with each other (company name, address, UEN, a person's ID and details); an AI then looks for deeper contradictions; the case is scored deterministically; the system suggests what further evidence to request and writes a plain summary. **The risk score and the recommended supporting documents exist only as the output of that run.** Everything updates live in the browser over a WebSocket.

---

## 2. The pieces

| Piece | What it is | Where |
|---|---|---|
| **Frontend** | React + Tailwind app for the loan officer | `frontend/src` |
| **Backend** | One FastAPI server: API, in-memory queue, AI pipelines, scoring, live updates | `backend/app` |
| **Database / storage** | Supabase: hosted Postgres and a private `documents` bucket | Supabase project |
| **AI** | Claude via the Anthropic API. Sonnet for classifying, extracting, leading detection and the case analysis; Haiku for the second-opinion classifier, detection specialists, evidence requests and the summary | `app/langgraph`, `app/case_pipeline` |
| **Agent framework** | LangGraph: each AI workflow is a graph of steps | same |
| **Live updates** | Supabase Realtime tells the backend a row changed; the backend pushes a case snapshot over a WebSocket | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py` |
| **Cost logging** | One `perf` log line per unit of work | `query_stats.py` |

Everything runs in **one process**. The 5-service Pub/Sub split in `CLAUDE.md` is not built, and the queue is a list in memory, so queued work is lost on restart.

---

## 3. End to end: following one case

### Step 0: Login
A few named accounts, all in one setting: `AUTH_USERS=alice:password1;bob:password2` (no roles; a password can't contain `;`, `,`, `$` or `#`). `POST /auth/login` returns a signed token carrying the username, which every other call sends; a handler that declares `user: CurrentUser` learns who is acting. A browser WebSocket can't send headers, so the frontend first asks for a one-time ticket (`POST /ws/tickets`) and puts it in the WebSocket URL.

### Step 1: Creating a case
A row in `cases`. A database trigger assigns the readable ID (`GLDB-<n>`), skipping any already taken. Cases made locally are `dev = true` so they stay out of the cloud demo's list.

### Step 2: Uploading
PDF, PNG or JPEG, up to 25 MB each, up to 20 per batch. For each file: store it, create a `documents` row (`uploaded`), queue it (`queued`). The request returns immediately.

### Step 3: Per-document pipeline (read, classify, extract)
Four workers pull documents off the queue and run the LangGraph document graph:

1. **Ingest (`processing`, shown as "Classifying").** Download the file; read native PDF text or OCR it, and read file metadata with `exiftool` at the same time (it is a subprocess, so it runs in its own thread while the text extraction uses the single PDF/OCR worker, `cpu_pool.py`, which keeps PyMuPDF off two threads at once). A failed metadata read is stored as an error record, never as "no metadata".
2. **Classify.** A rule-based classifier first (free); then a Sonnet agent that may ask a Haiku classifier for a second opinion and, on a later turn, vision. Confidence below **0.85** routes the document to manual review.
3. **Extract (`extracting`).** One Sonnet call fills the type's schema, with a confidence per field. Fields are stored one row each in `extracted_fields` **before** the document row is updated, so a snapshot never sees a document without its fields. For a document that can belong to a company or a person (a bank statement, a source-of-wealth document) the model also says which it is (`entity_type`).
4. **Disagreement loop.** If extraction says the type is wrong, the document returns to the classifier once; if still unresolved it is marked `failed`. An unreadable reply fails the document (retryable) without reclassifying it.
5. Done: `analyzed`.

### Step 4: Officer review
The officer can edit or delete fields, correct the document type (which re-runs extraction), move, delete, cancel or retry a document, and **approve** or **reject** it. Approval locks the document. A **case-match** check (plain code, `case_match.py`) compares each document's company and people with the certificate of incumbency to catch a document in the wrong case; it is not a fraud signal.

The case page also shows the **requirements checklist** (required types, proof of address per person, both sides of each ID). When everything required is covered, `submission_finalized` is set. It is recomputed after classification, field saves, rejection, moves and deletes, batched per case over 1.5 seconds so a burst of classifications costs one recompute, and written only when its value changes. Nothing reads it yet.

### Step 5: The Detection Agent (`approved` → `detecting` → `detected`)
Approval starts detection in the background, **two runs at a time** (`DETECTION_CONCURRENCY`); the rest wait, shown as "Approved".

1. **Load context.** The document and its fields are fetched once and shared by every tool in the run.
2. **Lead plans (Sonnet).** One brief per specialist.
3. **Three specialists in parallel (Haiku).** Each picks which of its tools to run, at most 3 tool rounds:

| Specialist | Tools (plain code) | Looks for |
|---|---|---|
| **Layout & Rendering** | font consistency, layout anomalies, image manipulation, image tampering | odd fonts and sizes, misalignment, text over text, spliced or edited images |
| **Content Validation** | spelling, date consistency, numerical checks, external validation | typos, impossible dates, sums and balances that don't add up, broken NRIC/UEN/MRZ checksums, a company missing from ACRA, a postal code missing from OneMap, a home address at an office or industrial unit, wrong CPF/GST rates |
| **Digital Authenticity** | metadata, AI generation | editing software in the metadata, modified-after-created dates, AI-generated images |

   A tool returns *candidate* findings. The specialist gives each a severity or dismisses it as a false positive; it cannot invent findings or change a tool's facts.
4. **Lead reviews**, and may request one second look from another specialist.
5. **Merge (code).** One report with a verdict: `flagged`, `inconclusive` or `clean`.
6. **Save.** The report is written to the document's trace, then the findings a specialist **approved** are written to `flags`, then the document becomes `detected`.

**Flags** are written by code, never by the model. A finding becomes a flag only if a specialist assessed it and did not dismiss it, and it is a per-document finding. Unassessed findings stay in the trace and still count towards the score as "unreviewed". Case-level findings are not flags. A retry replaces the document's open flags. Each flag's UUID is stamped on the finding, so the report, the `flags` row and `risk_scores.flag_ids` name the same id.

If detection crashes it is recorded, the document returns to `approved`, and the officer can retry. Single checks can be run on demand once a report exists.

### Step 6: Case Analysis (on demand, the only source of the score)
When every approved document is `detected`, the officer clicks **Run case analysis** (`POST /cases/{id}/analysis/run`). One run, in order:

1. **Deterministic comparisons (code).** Company name, address, UEN, and a person's ID number, name and details (date of birth, race, sex, nationality) across documents. A person is compared against every document that names them, not just the NRIC. Each difference is its own finding (`xd-det-N`), written as one plain sentence naming the documents by type, and merged by value so one wrong ID on three documents is one row. Differences in ID, UEN or date of birth are high; the rest are medium. Checks that agreed are listed as "checked and consistent". The protected details are compared only here, in code, and never shown to any model.
2. **Dossier (code).** A digest of each approved document's fields, derived transaction facts and detection findings. A person's protected attributes are **removed**. The code-found differences are added as established facts, grouped by value (at most 5,000 characters), with the instruction not to restate them. The planner's whole input is capped at 30,000 characters.
3. **Plan (one Sonnet call, forced structured output).** At most 3 hypotheses, each with up to 4 checks and one refutation probe. A hypothesis that touches a protected attribute, or concerns one document with nothing computed behind it, is dropped before any check runs.
4. **Run the checks (code, no model).** Read-only evidence tools. Statement rows printed as `20 JUN` take their year from the statement period.
5. **Judge (one Sonnet call, forced structured output).** Writes for an officer: no tool names, flags or ref ids in its wording.
6. **Verify (code).** A verdict stands only if every citation is a ref a tool issued in this run; a refutation probe ran that looked at something the claim does not already rest on (unless a comparison tool's own mismatch flag is cited); it cites two documents or one computed fact; nothing in it touches a protected attribute; and severity is capped at medium unless a tool returned a typed breach flag or a registry mismatch backs it. A verdict that only restates a code finding is dropped. Everything else is discarded and never shown or scored.
7. **Score (code).** The deterministic scorer runs over the document findings plus the code and agent cross-document findings.
8. **Outlook (one Haiku call plus code).** The model suggests evidence to request; code re-scores "if this supports the client". Tampering, registry mismatches and conflicting identity can't be reduced by more evidence.
9. **Additional documents (code).** The rule-based recommendations (large supplier or customer payments, balance-sheet breakdowns) and the model's requests are merged into one list, each marked as score-changing or not.
10. **Summary (one Haiku call).** Grounded in the findings. It is told which checks ran clean and which **could not run**, so it cannot call an unrun check clear.

The run stores its result, including the risk and additional documents, and writes one `risk_scores` row. Cost: 4 model calls (plan, judge, requests, summary) against a ceiling of 6, with output capped at 3,000 and 2,500 tokens for plan and judge; a real run logged about 13k tokens in and 2.4k out. Plan and judge share one cached system prompt. Bias is enforced at four points (input, prompt, proposals, verdicts); see `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

A case that was never analysed shows "Not scored yet". If documents change after a run, the run is marked out of date and the previous result stays visible with a notice. `CASE_ANALYSIS_ENABLED=false` switches the module off.

### How the score is calculated (no AI)
`case_pipeline/risk/case_risk.py`:
- Points per finding: high 8, medium 3, low 1, info 0.
- The same check with the same stated value on several documents scores once; the copies show at 0 points. A check that fires repeatedly on one document counts in full once, then at 25%.
- Low and medium points are capped at 6 per document; high findings are never capped.
- A cross-document finding is multiplied by up to 1.5 as more documents corroborate it.
- Total ≥ 8 → High, ≥ 3 → Medium, otherwise Low. **Critical** needs scoring high findings on 3 or more different documents.
- The 0–100 number is anchored to the level, so a Critical case always outscores a High one.

---

## 4. How live updates work

1. A change to a `documents` row fires a Supabase Realtime event; one subscription serves the whole server.
2. The backend waits **0.5 s** to merge a burst, then builds a **case snapshot**: case, documents with fields, detection states, the latest analysis (risk, additional documents, overview) and case-match. For a case with no analysis it reads only each report's verdict, not the full report.
3. It pushes the snapshot to every browser with that case open (`/ws/cases/{case_id}`). **No snapshot is built for a case nobody has open.**
4. **One build runs per case at a time.** A request that arrives mid-build waits for one follow-up build shared by everyone who arrived meanwhile; it is never handed the in-flight result, which may predate its change.
5. On (re)connect the browser always gets a fresh snapshot. It reconnects with backoff (1 s, 2 s, 4 s … capped at 30 s, with jitter).

---

## 5. Document statuses

```
uploaded → queued → processing → extracting → analyzed ──approve──→ approved → detecting → detected
              ("Classifying")                    │                    ("Approved")  ("Scanning")   (final)
                                                 └──reject──→ rejected
   any in-flight status ──cancel──→ cancelled
   anything that goes wrong ──────→ failed   (retry possible)
```

---

## 6. Where data lives

| Table | Holds |
|---|---|
| `cases` | company name, GreenLink ID, `submission_finalized`, dev flag. `recommended_additional_documents` (jsonb) holds `[]` until a case is analysed, then the whole `additional_documents` result of its latest complete run; the case summary returns it as `additional_documents`. The summary's `missing_requirements` (the checklist types no accepted document covers yet) is computed on every read, not stored |
| `documents` | one row per file: status, type, confidences, routing, requirements fulfilled, file metadata (read only by the metadata checks) |
| `extracted_fields` | one row per field per document |
| `langgraph_trace` | one JSON trace per document: pipeline steps and the **full detection report** |
| `flags` | the per-document findings a specialist approved, with severity, check, description and evidence |
| `case_analysis_runs` | each run: findings (including `xd-det-N`), `consistent_checks`, outlook, summary, **risk**, **additional documents**, audit, step trace |
| `risk_scores` (+ `latest_risk_scores` view) | one row per completed run: score, level, reasoning, `flag_ids` |
| Storage bucket `documents` | the uploaded files |

Deleting a document removes its fields, flags, trace and file; deleting a case removes its documents, then its runs, scores and flags. The schema is changed directly in Supabase (no SQL in the repo); `README.md` lists the objects the backend expects.

---

## 7. Configuration

Required: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `ANTHROPIC_API_KEY`, `AUTH_USERS`, `AUTH_SECRET_KEY`.

| Variable | Default | Effect |
|---|---|---|
| `QUEUE_WORKER_CONCURRENCY` | 4 | documents processed at once |
| `DETECTION_CONCURRENCY` | 2 | detection runs at once |
| `CPU_WORKER_CONCURRENCY` | 1 | threads for PDF, OCR and forensic work; PyMuPDF is not thread-safe |
| `DOCUMENT_MAX_MODEL_CALLS` | 24 | model calls one document's classify and extract run may make |
| `DETECTION_MAX_MODEL_CALLS` | 30 | model calls one detection run may make; past it the verdict can't be clean |
| `LLM_MAX_RETRIES`, `LLM_TIMEOUT_SECONDS` | 1, 300 | the Anthropic client's own retries (each is billed) and one request's time limit |
| `TOOL_TIMEOUT_SECONDS`, `SLOW_TOOL_TIMEOUT_SECONDS` | 60, 120 | time limit per detection tool, including its wait for the CPU worker; the slow ones call a model or a registry |
| `CASE_ANALYSIS_ENABLED` | true | kill switch for the case analysis |
| `LOG_HTTP_REQUESTS` | false | httpx's one line per Supabase call |
| `LANGGRAPH_SONNET_MODEL`, `LANGGRAPH_HAIKU_MODEL` | `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` | model overrides |
| `SUPABASE_STORAGE_BUCKET` | `documents` | bucket name |
| `DATA_GOV_SG_API_KEY` | — | data.gov.sg lookups and the zoning download |
| `URA_API_KEY` | — | URA approved residential use; skipped without it |
| `LAND_USE_RUNTIME_REBUILD` | true | whether a server may build the URA zoning index itself; the Docker image sets false |

The backend image builds the zoning index and fails if it can't (`README.md` has the build flags).

---

## 8. Reading the cost logs

Every API request, WebSocket snapshot, Realtime push and background job logs one line:

```
GET /cases -> 3 supabase (GET cases x1, GET documents x1, GET latest_risk_scores x1; 210ms), 260ms total
detection doc=a25f9fc4 -> 12 supabase (...) | 11 claude (26601 in / 3008 out tokens; cache read 0 / write 0), 43813ms total
```

Every 60 seconds a summary of Realtime and WebSocket traffic (events received, ignored, merged, connects, pushes) and of calls made outside any unit of work is also logged. Rough budgets: case list 3 queries; a snapshot 5; approve 2; delete or add a field 6–7; detection about 12.

---

## 9. What is built and what isn't

**Built:** upload and queue; classify and extract with the reclassify loop; officer review tools; requirements checklist; case-match; the Detection Agent with 10 checks and on-demand single checks; per-document flags; the case analysis (deterministic and agent cross-document findings, score, outlook, additional documents, summary); live WebSocket updates; cost logging; a manual Cloud Run deploy workflow.

**Not built:** the Output Aggregator; automatic case analysis when all documents are scanned; the 5-service Pub/Sub split; case-level flags; an officer review flow for flags (the `status` and `reviewed_by` columns exist, unused); Prometheus and Grafana. Open items are in `PENDING_WORK.md`.

## 10. Known rough edges

- `exiftool` is not installed on the dev machine, so the metadata check and the AI-provenance part of the AI-generation check report "could not run". The summary says so.
- Detection specialists run on Haiku, whose minimum cacheable prompt is longer than their prompts, so caching there does nothing. It works for classification, extraction and the case analysis.
- Re-running the analysis on unchanged documents spends the tokens again; nothing short-circuits an identical fingerprint.
- The first requester of a burst of snapshot requests can wait for a follow-up build.

---

## 11. Where to start reading

| To understand… | Read |
|---|---|
| Startup, routers, auth | `backend/app/main.py`, `auth.py` |
| Upload and queue | `routers/upload.py`, `queue_service.py` |
| Classify and extract | `langgraph/pipeline.py`, `graph.py`, `classify_graph.py`, `extract.py` |
| Approve and detection | `routers/documents.py` (`_launch_detection`), `langgraph/detection/graph.py`, `specialists.py`, `tools.py` |
| The forensic checks | `fraud_detection/` |
| Flags | `flags_repository.py` |
| Case snapshot, checklist, case list | `case_service.py` |
| Comparable keys for IDs, names, companies | `identifiers.py` |
| Risk score | `case_pipeline/risk/case_risk.py`, `store.py` |
| Case analysis | `case_pipeline/service.py`, `cross_document/deterministic.py`, `agents.py`, `verify.py`, `bias.py`, `risk/outlook.py` |
| Live updates | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py`, `frontend/src/services/caseSocket.ts` |
| Cost logging | `query_stats.py` |
