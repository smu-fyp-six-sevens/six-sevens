# How the App Works

A plain-language walkthrough of the whole system as it is now (updated 2026-10-15): from a loan officer logging in to a case getting a risk score. It also says what is built, what isn't, and where it is rough. Design rationale lives in the other documents; this one describes behaviour.

---

## 1. The one-paragraph version

A loan officer creates a **case** for a company applying for a loan and uploads its documents (bank statements, NRICs, certificates and so on). Each document goes through a background pipeline: it is **read** (text from the PDF, or OCR for scans), **classified** ("this is a payslip") and **extracted** ("net pay = 8,027.85"). The officer reviews and **approves** it. Approval starts the **Detection Agent**: a lead AI investigator plus three specialist AIs that run forensic checks (fonts, layout, images, metadata, spelling, arithmetic, government registries) and write a fraud report for that document. The findings a specialist judged genuine are also stored one by one as **flags**. Once every approved document has a report, the officer can run **Case Analysis**. It looks for contradictions *between* documents, scores the case deterministically, suggests what further evidence to request, and writes a plain summary. **The risk score and the recommended supporting documents exist only as the output of that run**; nothing else computes them. Everything updates live in the browser over a WebSocket.

---

## 2. The pieces

| Piece | What it is | Where |
|---|---|---|
| **Frontend** | React + Tailwind app for the loan officer | `frontend/src` |
| **Backend** | One FastAPI (Python) server: API, in-memory queue, AI pipelines, scoring, live updates | `backend/app` |
| **Database** | Supabase (hosted Postgres) | Supabase project |
| **File storage** | Supabase Storage, private `documents` bucket | Supabase project |
| **AI** | Claude via the Anthropic API. Sonnet for classifying, extracting, leading detection and the case analysis; Haiku for the second-opinion classifier, detection specialists, evidence requests and the summary | `app/langgraph`, `app/case_pipeline` |
| **Agent framework** | LangGraph: each AI workflow is a graph of steps | same |
| **Live updates** | Supabase Realtime tells the backend when a row changes; the backend pushes a case snapshot to the browser over a WebSocket | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py` |
| **Cost logging** | One `perf` log line per unit of work: Supabase calls, Claude calls, tokens, cache hits | `query_stats.py` |

Everything runs in **one process** today. The 5-service Pub/Sub split described in `CLAUDE.md` is not built; the queue is a list in memory, so queued work is lost on restart.

---

## 3. End to end: following one case

### Step 0: Login
One shared username and password (`AUTH_USERNAME` / `AUTH_PASSWORD`). `POST /auth/login` returns a signed token that every other call sends. A browser WebSocket can't send headers, so the frontend first asks for a one-time ticket (`POST /ws/tickets`) and puts that in the WebSocket URL.

### Step 1: Creating a case
A row in `cases`. A database trigger assigns the readable ID (`GLDB-<n>`), skipping any ID already taken. Cases made locally are `dev = true` so they stay out of the cloud demo's list.

### Step 2: Uploading
PDF, PNG or JPEG, up to 25 MB each, up to 20 per batch. The batch checks the case once, then for each file: stores it, creates a `documents` row (`uploaded`), and queues it (`queued`). The request returns immediately.

### Step 3: Per-document pipeline (read, classify, extract)
Four workers pull documents off the queue and run the LangGraph document graph:

1. **Ingest (`processing`, shown as "Classifying").** Download the file, read native PDF text or OCR it, and read file metadata with `exiftool` (not installed on every machine; the metadata check then reports it couldn't run). PDF and OCR work runs on a dedicated single-thread pool (`cpu_pool.py`) so it never starves database calls and PyMuPDF is never used on two threads at once.
2. **Classify.** A rule-based classifier first (free); then a Sonnet agent that may ask a Haiku classifier for a second opinion, and only on a later turn call vision. Confidence below **0.85** routes the document to manual review.
3. **Extract (`extracting`).** One Sonnet call fills the type's schema, with a confidence per field. Fields are stored one row each in `extracted_fields`, **before** the document row is updated, so a live snapshot never sees the document without its fields.
4. **Disagreement loop.** If extraction says the type is wrong, the document returns to the classifier once; if still unresolved it is marked `failed`.
5. Done: `analyzed`.

### Step 4: Officer review
On the case and document pages the officer can edit or delete fields, correct the document type (which re-runs extraction), move, delete, cancel or retry a document, and **approve** or **reject** it. Approval locks the document. A **case-match** check (plain code) compares each document's company and people with the certificate of incumbency, to catch a document in the wrong case; it is not a fraud signal.

The case page also shows the **requirements checklist** (required document types, proof of address per person, both sides of each ID). When everything required is covered, `submission_finalized` is set; it is written only when its value changes.

Coverage depends on a document's routing, confirmed type, whether it was rejected, and its fields. So only classification, field saves, rejection, moves and deletes resync the case. Approving, or a status change to `analyzed`, does not.

### Step 5: The Detection Agent (`approved` → `detecting` → `detected`)
Approval starts detection in the background, **two runs at a time** (`DETECTION_CONCURRENCY`); the rest wait, still shown as "Approved".

1. **Load context.** The document and its fields are fetched once and shared with every tool for the run (concurrent tool calls wait for one fetch rather than each fetching).
2. **Lead plans (Sonnet).** One brief per specialist.
3. **Three specialists in parallel (Haiku).** Each picks which of its tools to run, at most 3 tool rounds:

| Specialist | Tools (plain code) | Looks for |
|---|---|---|
| **Layout & Rendering** | font consistency, layout anomalies, image manipulation, image tampering | odd fonts and sizes, misalignment, text pasted over text, spliced or edited images |
| **Content Validation** | spelling, date consistency, numerical checks, external validation | typos, impossible dates, sums and balances that don't add up, broken NRIC/UEN/MRZ checksums, a company missing from ACRA, a postal code missing from OneMap, a home address at an office or industrial unit, wrong CPF/GST rates |
| **Digital Authenticity** | metadata, AI generation | editing software in the file metadata, modified-after-created dates, AI-generated images |

   A tool returns *candidate* findings. The specialist gives each a severity or dismisses it as a false positive; it cannot invent findings or change a tool's facts.
4. **Lead reviews**, and may request one second look from another specialist.
5. **Merge (code).** One report with a verdict: `flagged`, `inconclusive` or `clean`.
6. **Save.** The report is written to the document's trace, then the findings a specialist **approved** are written to the `flags` table, then the document becomes `detected`.

**Flags.** Written by code, never by the model. A finding becomes a flag only if a specialist assessed it and did not dismiss it, and it is a per-document finding. Unassessed findings stay in the trace and still count towards the risk score as "unreviewed", but are not flags. Cross-document findings (a name or address that differs between documents, an identity mismatch) are not flags; that belongs to the case-level module. A retry replaces the document's open flags. Each flag's UUID is also stamped on the finding in the report, so the report, the `flags` row and `risk_scores.flag_ids` name the same id.

If detection crashes it is recorded, the document returns to `approved`, and the officer can retry. Single checks can also be run on demand once a report exists.

### Step 6: Case Analysis (on demand, the only source of the score)
When every approved document is `detected`, the officer clicks **Run case analysis** (`POST /cases/{id}/analysis/run`). One run does all of this, in order:

1. **Dossier (code).** A digest of each approved document's fields, derived transaction facts, and its detection findings. A person's protected attributes (sex, nationality, date of birth, MRZ, race, religion, marital status, age and similar) are **removed**, and the dossier is capped at 24,000 characters.
2. **Plan (one Sonnet call, forced structured output).** At most 3 hypotheses, each with the checks to run and one refutation probe. A hypothesis that touches a protected attribute, or that is about one document with nothing computed behind it, is dropped before any check runs.
3. **Run the checks (code, no model).** Read-only evidence tools. Bank-statement rows printed as `20 JUN` take their year from the statement period.
4. **Judge (one Sonnet call, forced structured output).**
5. **Verify (code).** A verdict stands only if:
   - every citation is a ref a tool issued in this run;
   - a refutation probe ran that looked at something the claim does not already rest on (re-reading the same fields doesn't count);
   - it cites at least two documents, or at least one fact computed over the data (transaction stats, monthly flow, counterparties, period comparison);
   - nothing in its text touches a protected attribute;
   - severity is capped at medium unless a tool returned a typed breach flag or a registry mismatch backs it.

   Everything else is discarded and never shown or scored.
6. **Score (code).** The deterministic scorer runs over the document findings plus the surviving cross-document findings.
7. **Outlook (one Haiku call plus code).** The model suggests evidence to request; code re-scores "if this supports the client". Tampering, registry mismatches and conflicting identity can't be reduced by more evidence.
8. **Supporting documents (code).** Rule-based recommendations from the documents (large supplier or customer payments, balance-sheet breakdowns).
9. **Summary (one Haiku call).** Grounded in the findings. It is told which checks ran clean and which **could not run**, so it cannot call an unrun check clear.

The run stores its result, **including the risk and the supporting documents**, and writes one row to `risk_scores`. Cost limits: the plan and judge are a hard cap of 2 model calls per run, with output capped at 3,000 and 2,500 tokens. Plan and judge share one cached system prompt. Bias is enforced at four points (input, prompt, proposals, verdicts); see `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

If a case has never been analysed it shows "Not scored yet". If documents change after a run, the run is marked out of date and the previous result stays visible with a notice; re-run to update it. `CASE_ANALYSIS_ENABLED=false` switches the module off.

### How the score is calculated (no AI)
`case_pipeline/risk/case_risk.py`:
- Points per finding: high 8, medium 3, low 1, info 0.
- A check that fires repeatedly on one document counts in full once, then at 25%.
- Low and medium points are capped at 6 per document; high findings are never capped.
- Total ≥ 8 → High, ≥ 3 → Medium, otherwise Low. **Critical** needs high findings on 3 or more different documents.
- The 0–100 number is anchored to the level, so a Critical case always outscores a High one.

---

## 4. How live updates work

1. A change to a `documents` row fires a Supabase Realtime event; one subscription serves the whole server.
2. The backend waits **0.5 s** to merge a burst of changes, then builds a **case snapshot**: case, documents with fields, detection states, the latest analysis (risk, supporting documents, overview) and case-match.
3. It pushes the snapshot to every browser with that case open (`/ws/cases/{case_id}`).
4. **No snapshot is built for a case nobody has open.**
5. **One build runs per case at a time.** A request that arrives mid-build waits for a single follow-up build, which everyone who arrived meanwhile shares. It is never handed the in-flight result, which may predate its change.
6. On (re)connect the browser always gets a fresh snapshot, so it can't miss anything. It reconnects with backoff (1 s, 2 s, 4 s … capped at 30 s, with jitter).

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
| `cases` | company name, GreenLink ID, `submission_finalized`, dev flag. `recommended_additional_documents` is always empty; the API reports the missing checklist types under that name instead |
| `documents` | one row per file: status, type, confidences, routing, requirements fulfilled, file metadata |
| `extracted_fields` | one row per field per document |
| `langgraph_trace` | one JSON trace per document: pipeline steps and the **full detection report** |
| `flags` | the per-document findings a specialist approved, one row each, with severity, check, description and evidence |
| `case_analysis_runs` | each run: findings, outlook, summary, **risk**, **supporting documents**, audit, step trace |
| `risk_scores` (+ `latest_risk_scores` view) | one row per completed run: score, level, reasoning, and `flag_ids` (real `flags` UUIDs) |
| Storage bucket `documents` | the uploaded files |

The schema is changed directly in Supabase; the repo keeps no SQL files. `README.md` lists the objects the backend expects (tables, the `latest_risk_scores` view, the case id sequence and trigger, and the grants on `risk_scores` and `flags`).

---

## 7. Configuration

Required: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `ANTHROPIC_API_KEY`, `AUTH_USERNAME`, `AUTH_PASSWORD`, `AUTH_SECRET_KEY`.

Optional, with defaults:

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
| `LOG_HTTP_REQUESTS` | false | httpx's one line per Supabase call (the `perf` summaries replace it) |
| `LANGGRAPH_SONNET_MODEL`, `LANGGRAPH_HAIKU_MODEL` | `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` | model overrides |
| `SUPABASE_STORAGE_BUCKET` | `documents` | bucket name |
| `DATA_GOV_SG_API_KEY` | — | data.gov.sg lookups and the zoning download (anonymous limits without) |
| `URA_API_KEY` | — | URA approved residential use; skipped without it |
| `LAND_USE_RUNTIME_REBUILD` | true | whether a server may build the URA zoning index itself; the Docker image sets false |

The backend image builds the zoning index and fails if it can't. Pass `DATA_GOV_SG_API_KEY` as a BuildKit secret
(`docker build --secret id=data_gov_sg_api_key,env=DATA_GOV_SG_API_KEY -f backend/Dockerfile --target base .`);
`--build-arg ALLOW_MISSING_LAND_USE=true` builds without the index, and zoning is then skipped.

---

## 8. Reading the cost logs

Every API request, WebSocket snapshot, Realtime push and background job logs one line:

```
GET /cases -> 3 supabase (GET cases x1, GET documents x1, GET latest_risk_scores x1; 210ms), 260ms total
detection doc=a25f9fc4 -> 12 supabase (...) | 11 claude (26601 in / 3008 out tokens; cache read 0 / write 0), 43813ms total
```

Every 60 seconds there is also a summary of Realtime and WebSocket traffic (events received, ignored because nobody had the case open, merged by the debounce, connects, pushes) and of calls made outside any unit of work, mostly trace saves.

Rough budgets: case list 3 queries; a snapshot 5; approve 2; delete or add a field 6–7; detection about 12 queries.

---

## 9. What is built and what isn't

**Built:** upload and queue; classify and extract with the reclassify loop; officer review tools; requirements checklist; case-match; the Detection Agent with 10 checks and on-demand single checks; per-document flags; the case analysis (cross-document findings, deterministic score, outlook, supporting documents, summary); live WebSocket updates; cost logging; a manual Cloud Run deploy workflow.

**Not built:** the Output Aggregator; automatic case analysis when all documents are scanned (the officer clicks it); the 5-service Pub/Sub split; case-level flags (a separate module, deliberately later); an officer review flow for flags (the `status`, `reviewed_by` columns exist, unused); persisting recommended documents to `cases.recommended_additional_documents`; Prometheus and Grafana.

## 10. Known rough edges

- `exiftool` is not installed on the dev machine, so the metadata check and the AI-provenance part of the AI-generation check report "could not run". The case analysis summary reports this honestly.
- Detection specialists run on Haiku, whose minimum cacheable prompt is larger than their prompts, so prompt caching there does nothing. It works for classification, extraction and the case analysis.
- Several concurrent snapshot requests are merged, so the first requester of a burst can wait for a follow-up build.
- `GET /cases/{id}/analysis` recomputes staleness from the documents, fields and traces (5 queries) each time the card reloads.
- Re-running the analysis on unchanged documents spends tokens again; nothing short-circuits an identical fingerprint.
- Dev React StrictMode fires some document-page requests twice.

---

## 11. Where to start reading

| To understand… | Read |
|---|---|
| Startup, routers, auth | `backend/app/main.py`, `auth.py` |
| Upload and queue | `routers/upload.py`, `queue_service.py` |
| Classify and extract | `langgraph/pipeline.py`, `graph.py`, `classify_graph.py`, `extract.py` |
| Approve and detection | `routers/documents.py` (`_launch_detection`), `langgraph/detection/graph.py`, `specialists.py`, `tools.py` |
| The forensic checks | `backend/app/fraud_detection/` |
| Flags | `flags_repository.py` |
| Case snapshot, checklist, case list | `case_service.py` |
| Risk score | `case_pipeline/risk/case_risk.py`, `store.py` |
| Case analysis | `case_pipeline/service.py`, `cross_document/agents.py`, `verify.py`, `bias.py`, `risk/outlook.py` |
| Live updates | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py`, `frontend/src/services/caseSocket.ts` |
| Cost logging | `query_stats.py` |
| Other design documents | `CLAUDE.md`, `FRAUD_DETECTION_MAP.md`, `CROSS_DOCUMENT_AND_RISK_PLAN.md`, `PENDING_WORK.md` |
