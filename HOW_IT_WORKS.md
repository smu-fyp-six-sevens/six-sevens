# How the App Works (Current State, October 2026)

A plain-language walkthrough of the whole system as it exists on the `agentic-experiments` branch today. It covers what happens from the moment a loan officer logs in to the moment a case gets a risk score. It also says what's built, what isn't, and where it's fragile.

---

## 1. The one-paragraph version

A loan officer creates a **case** for a company applying for a loan and uploads that company's documents (bank statements, NRICs, certificates and so on). Each document goes through a background pipeline. It is **read** (text pulled out of the PDF, or OCR for scans), **classified** ("this is a payslip"), and **extracted** ("net pay = 8,027.85"). The officer reviews and **approves** it. Approval kicks off the **Detection Agent**, a lead AI investigator plus three specialist AIs. They run forensic checks (fonts, layout, images, metadata, spelling, arithmetic, government registries) and write a fraud report for that document. Once every approved document has a report, the officer can run **Case Analysis**. An AI proposes ways the documents might contradict *each other*, code tests those ideas, and only proven contradictions are kept. A **deterministic risk score** (Low / Medium / High / Critical, 0–100) is calculated from all of the findings. Everything shows up live in the browser over a WebSocket.

---

## 2. The pieces

| Piece | What it is | Where |
|---|---|---|
| **Frontend** | React + Tailwind web app the loan officer uses | `frontend/src` |
| **Backend** | One FastAPI (Python) server that does *everything*: API, queue, AI pipelines, scoring | `backend/app` |
| **Database** | Supabase (hosted Postgres): cases, documents, extracted fields, traces, risk scores | Supabase project |
| **File storage** | Supabase Storage, private `documents` bucket. Files are only reachable through short-lived signed URLs | Supabase project |
| **AI** | Claude via the Anthropic API. **Sonnet** for the hard thinking (classifying, extracting, leading detection, case analysis), **Haiku** for cheaper helper calls (second-opinion classifier, specialists) | `backend/app/langgraph`, `backend/app/case_pipeline` |
| **Agent framework** | LangGraph: each AI workflow is a graph of steps ("nodes") with rules for which step comes next | same as above |
| **Live updates** | Supabase Realtime tells the backend when a row changes; the backend pushes a fresh "case snapshot" to the browser over a WebSocket | `realtime_listener.py`, `routers/ws.py` |
| **Deployment** | GitHub Actions builds Docker images and deploys both services to Google Cloud Run. It's **manual only** (you click "Run workflow") | `.github/workflows/deploy.yml` |

**Important:** despite the 5-service design in `CLAUDE.md`, **today it's all one process.** Upload, queue, pipelines, scoring and live updates all run inside the same FastAPI server, and the "queue" is just a list in memory. The Pub/Sub split hasn't been built yet.

---

## 3. End-to-end: following one case through the system

### Step 0: Logging in
- There's one shared username/password (from env vars `AUTH_USERNAME` / `AUTH_PASSWORD`).
- `POST /auth/login` checks it and hands back a signed token. Every other API call sends that token.
- The WebSocket can't send headers from a browser, so the frontend first asks for a **one-time ticket** (`POST /ws/tickets`) and puts that in the WebSocket URL instead.

### Step 1: Creating a case
- The officer clicks "Create case" and types the company name.
- A row goes into the `cases` table. A database trigger gives it a human-friendly ID (the "GreenLink case ID").
- Cases created from your laptop are marked `dev = true`, so they don't show up in the cloud demo's case list.

### Step 2: Uploading documents
- The officer drags in files (PDF, PNG or JPEG, up to 25 MB each, up to 20 at once).
- For each file the backend:
  1. checks the type and size,
  2. saves the file to Supabase Storage,
  3. creates a `documents` row with status **`uploaded`**,
  4. puts the document's ID on the in-memory queue and sets status **`queued`**.
- The upload request returns immediately. All the slow work happens in the background.

### Step 3: The per-document pipeline (read → classify → extract)
Four background **workers** pull documents off the queue (`queue_service.py`). Each one runs the LangGraph **document graph** (`langgraph/graph.py`):

1. **Ingest (status `processing`, shown as "Classifying").**
   - The file is downloaded from storage.
   - If the PDF has real text, it's read directly (PyMuPDF). If it's a scan or an image, it's OCR'd (RapidOCR).
   - `exiftool` pulls the file's metadata (author, software, creation and modification dates) and saves it on the document row.
     - ⚠️ exiftool isn't installed on your machine, so the metadata check currently can't run.

2. **Classify: "what kind of document is this?"**
   - First a **rule-based classifier** (keyword and pattern matching, no AI, free).
   - Then a **Sonnet agent** decides what else it needs. On its first turn it may ask a **Haiku classifier** for a second opinion. Only if they still disagree or it's unsure can it, on a later turn, call **vision** (sending Claude an image of the page, the most expensive option).
   - The result is one of the 12 known types or **"unrecognised" (Other)**, plus a confidence score.
   - Confidence below **0.85** means the document is flagged for **manual review** (`routing_status = manual_review`). Otherwise it's `accepted`.
   - The document's type also determines which **checklist requirements** it fulfils (e.g. an NRIC can count as proof of address).

3. **Extract (status `extracting`).** One Sonnet call reads the text and fills in a schema for that document type (names, amounts, dates, IDs and so on), with a confidence for each field. The fields are stored one row per field in the `extracted_fields` table.

4. **Disagreement loop.** If Extraction says "this isn't really a payslip, it looks like a bank statement", the document goes back to the classifier agent **once** (`MAX_RECLASSIFICATION_ATTEMPTS = 1`). If they still can't agree, the document is marked **`failed`** for a human to sort out.

5. Done: status **`analyzed`**.

**Cost-saving rule:** most work is plain code (text extraction, rules). AI is used where judgement is needed, and vision only as a last resort.

### Step 4: The officer reviews the document
On the case page and the document page the officer can:
- see the extracted fields, with low-confidence ones (< 0.9) highlighted as editable,
- **fix fields** by hand (these then count as confidence 1.0),
- **correct the document type** if the classifier was unsure. This re-runs Extraction only,
- **move** a document to a different case, **delete** it, **cancel** it while it's still processing, or **retry** a failed or stuck one,
- **approve** or **reject** it.

Alongside this, a **case-match check** (`case_match.py`, plain code) checks that each document actually belongs to this company. The **Certificate of Incumbency** is the reference: other documents' UEN, company name and people are compared against it. This isn't a fraud signal. It catches a document uploaded to the wrong case.

The case page also shows a **requirements checklist** (which of the required document types are covered, proof of address per person, and both sides of each ID). When everything required is covered, the case's `submission_finalized` flag flips on automatically. It flips off again if coverage drops.

**Approval locks the document.** After that, no more edits, retries or moves.

### Step 5: The Detection Agent (status `detecting`, shown as "Scanning")
Approving a document starts detection automatically in the background (`langgraph/detection/graph.py`). At most **2 run at once** (`DETECTION_CONCURRENCY`). Any others wait their turn while still showing "Approved".

How a detection run works:

1. **Load context:** document type, filename, whether fields were extracted.
2. **Lead investigator plans (Sonnet):** writes a short brief for each of the three specialists, saying what to focus on for *this* document type.
3. **Three specialists work in parallel (Haiku).** Each picks which of its tools to run, at most 3 rounds of tool calls:

| Specialist | Tools (all plain code, no AI inside) | Looks for |
|---|---|---|
| **Layout & Rendering** | font consistency, layout anomalies, image manipulation, image tampering | odd fonts or sizes, misaligned text, text pasted over other text, spliced or edited images |
| **Content Validation** | spelling, date consistency, numerical checks, external validation | typos in formal documents, impossible dates, sums and balances that don't add up, broken NRIC/UEN/MRZ checksums, a company that's not in ACRA, a postal code that doesn't exist in OneMap, wrong CPF/GST rates |
| **Digital Authenticity** | metadata, AI-generation | editing software in the metadata, modified-after-created dates, AI-generated photos |

   Each tool returns **candidate** findings. The specialist then judges each one: it gives it a severity (high / medium / low / info) or dismisses it as a false positive. It can't invent findings or change a tool's facts.

4. **Lead reviews:** reads all three reports and may ask **one** specialist for a "second look" at another's finding (allowed once per run).
5. **Synthesise:** code (not AI) merges everything into one report with a verdict:
   - **flagged**: something worth a human look,
   - **inconclusive**: nothing found, but some checks couldn't run,
   - **clean**: nothing found.

   It also adds any identity mismatches from the case-match check.
6. The report is saved and the document becomes **`detected`**.

If detection crashes it's recorded, the document goes back to `approved`, and the officer can retry. The officer can also re-run any single check on demand from the document page, but only after the agent's own run has happened.

**Where the report lives:** in the `langgraph_trace` table, inside a JSON "trace" of everything the pipeline did for that document (also used by the debug trace panel in the UI). The `documents` table only holds the status.

### Step 6: The case risk score (always on, no AI)
`case_pipeline/risk/case_risk.py` turns all the document reports into one score per case:
- Points per finding: high = 8, medium = 3, low = 1, info = 0.
- If the same check fires several times on the same document, only the first counts in full. The rest count at 25%, because one edit reported four times is still one edit.
- Low and medium points are capped at 6 per document, so a long document full of typos can't make a case High on its own. High findings are never capped.
- Total ≥ 8 → **High**, ≥ 3 → **Medium**, otherwise **Low**.
- **Critical** requires corroboration: high-severity findings on **3 or more different documents**.
- The 0–100 number is anchored to the level, so any Critical case always outscores any High case.
- The score is saved to `risk_scores` (only when it changes) so the case list doesn't have to recompute every case.

### Step 7: Case Analysis (on demand)
When every approved document is `detected`, the officer can click **Run analysis** (`POST /cases/{id}/analysis/run`). This looks for problems *across* documents (`case_pipeline/`):

1. **Dossier:** code builds a compact summary of every document's fields and findings.
2. **Hypothesise (one Sonnet call):** proposes up to 3 specific, checkable ideas, e.g. "the payslip's net pay should show up as salary credits in the bank statement". Each comes with checks to run and one **refutation test** (a check that could prove the idea wrong).
3. **Gather evidence (code):** runs those checks with read-only evidence tools (`cross_document/tools/`: comparisons, consistency, transactions, reading).
4. **Judge (one Sonnet call):** decides whether each idea is *conclusively* proven.
5. **Verify (code):** a verdict only survives if it cites real evidence references, its refutation test actually ran, its severity fits the evidence, and it doesn't rely on race, religion, gender or similar. Everything else is thrown away and never shown or scored.
6. The surviving **cross-document findings** are added to the risk score.
7. **Outlook (one call plus code):** the AI suggests extra evidence the applicant could provide. **Code** recalculates what the score would become if that evidence cleared up the related findings. Tampering, registry mismatches and conflicting identities can't be reduced this way.
8. **Summary (one call):** a short plain-English paragraph for the officer. The AI never sets the score.

Results are saved in `case_analysis_runs`. If documents change afterwards, the analysis is marked **stale** and stops counting towards the score until it's re-run. There's a kill switch: `CASE_ANALYSIS_ENABLED=false`.

⚠️ **Currently broken in practice:** in the last trace (`trace.md`), the hypotheses call ran out of output tokens because Sonnet wrote its reasoning as prose before the JSON. Nothing parsed, so you got "No cross-document findings". The case still scored correctly from the document findings.

---

## 4. How the live updates work

1. Any change to a `documents` row fires a **Supabase Realtime** event to the backend. There's one subscription for the whole server.
2. The backend waits **0.5 s** to merge bursts of changes, then builds a **case snapshot**: case, documents, fields, detection states, analysis, risk and case-match, all in one payload.
3. It pushes that snapshot to every browser that has that case open over the WebSocket (`/ws/cases/{case_id}`).
4. If **nobody has the case open**, no snapshot is built. The case is just marked "stale", so the case list recomputes its risk the next time it loads.
5. On (re)connect the browser always gets a fresh snapshot, so it can't miss anything that happened while it was disconnected. It reconnects every 2 s if the connection drops.

---

## 5. Document statuses at a glance

```
uploaded → queued → processing → extracting → analyzed ──approve──→ approved → detecting → detected
   ("Classifying")                       │                (waiting)  ("Scanning")   (final)
                                         ├──reject──→ rejected
  any in-flight status ──cancel──→ cancelled
  anything that goes wrong ──────→ failed  (retry possible)
```

---

## 6. Where data lives

| Table | Holds |
|---|---|
| `cases` | company name, GreenLink ID, `submission_finalized`, dev flag |
| `documents` | one row per file: status, type, confidences, routing, requirements fulfilled, file metadata, storage path |
| `extracted_fields` | one row per field per document (value, confidence, required/additional) |
| `langgraph_trace` | one JSON trace per document: pipeline steps, plus the **detection report** |
| `case_analysis_runs` | each Case Analysis run: findings, outlook, summary, audit, step trace |
| `risk_scores` (+ `latest_risk_scores` view) | history of each case's score, written only when it changes |
| Storage bucket `documents` | the actual uploaded files |

---

## 7. What's built and what isn't

**Built and working**
- Upload, queue, and classify → extract with the reclassify loop
- Officer review tools (edit fields, fix type, move, delete, cancel, retry, approve, reject)
- Requirements checklist and automatic `submission_finalized`
- Case-match (right-company check)
- Detection Agent (lead + 3 specialists), with ~12 forensic checks and on-demand single checks
- Deterministic case risk scoring with saved history
- Case Analysis pipeline (runs end to end, but see the hypotheses bug above)
- Live WebSocket updates
- Manual Cloud Run deploy

**Not built yet**
- **Output Aggregator** (final compiled case report)
- **Automatic** Case Analysis once all documents are scanned (today the officer clicks the button)
- The **5-service Pub/Sub split** for the Cloud Native award. Today it's one process with an in-memory queue, so anything queued is lost if the server restarts
- Grafana/Prometheus observability (OpenTelemetry hooks exist but are off by default)

---

## 8. Recent performance changes (14 Oct)

The app was slowing down under load. Changes made:
- Snapshots are only built for cases someone has open, and bursts are merged over 0.5 s instead of 0.1 s.
- Case snapshots read extracted fields once, not twice, and only the newest analysis run.
- Document list queries no longer download the large metadata blob.
- The case list saves freshly computed risk scores in one batch, so the next load is cheap.
- **Detection is capped at 2 runs at a time.**
- All PDF, OCR and forensic work runs on its **own single thread** (`app/cpu_pool.py`). Database calls no longer wait behind it, and PyMuPDF (which isn't thread-safe) never runs on two threads at once.
- Trace saves are coalesced (at most one pending write per document), and a detection report is saved before the document is marked `detected`.

**Still to do:**
- Fix the hypotheses JSON bug.
- Make finding IDs unique per document (right now two documents can both have `external_validation_tool-1`).
- Save extracted fields before updating the document row (avoids a brief "missing fields" flash).
- Install exiftool on the server.
- Check that the database has indexes on `documents(case_id)`, `extracted_fields(document_id)`, `langgraph_trace(document_id)` and `risk_scores(case_id, scored_at)`.
- Add reconnect backoff to the frontend socket.
- Reuse one Anthropic client per model instead of creating one per call.
- Paginate the case list. It currently loads every document in the database.

**Cloud Run caveat:** by default Cloud Run only gives a container CPU while it's answering a request, and scales to zero when idle. This app does its real work (queue, detection, analysis) in the *background*, after the request has returned. `deploy.yml` doesn't set `--no-cpu-throttling` or a minimum instance count. So on the cloud deploy, background work can crawl, or be lost when the instance shuts down. Worth fixing before a demo; the Pub/Sub split is the long-term fix.

---

## 9. Where to start reading the code

| If you want to understand… | Read |
|---|---|
| App startup, routers, auth | `backend/app/main.py`, `auth.py` |
| Upload → queue | `routers/upload.py`, `queue_service.py` |
| Classify → extract | `langgraph/pipeline.py`, `langgraph/graph.py`, `langgraph/classify_graph.py`, `langgraph/extract.py` |
| Approve → detection | `routers/documents.py` (`_launch_detection`), `langgraph/detection/graph.py`, `specialists.py`, `tools.py` |
| The forensic checks themselves | `backend/app/fraud_detection/` |
| Case snapshot, checklist, case list | `case_service.py` |
| Risk score | `case_pipeline/risk/case_risk.py`, `store.py` |
| Case Analysis | `case_pipeline/service.py`, `cross_document/agents.py`, `verify.py`, `risk/outlook.py` |
| Live updates | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py`, `frontend/src/services/caseSocket.ts` |
| Frontend pages | `frontend/src/pages/` (`CaseQueuePage`, `UploadPage` = case page, `DocumentDetailsPage`) |
| Deeper design docs | `CLAUDE.md`, `LANGGRAPH_MIGRATION_PLAN.md`, `FRAUD_DETECTION_MAP.md`, `CROSS_DOCUMENT_AND_RISK_PLAN.md`, `WS.MD` |
