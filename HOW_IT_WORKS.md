# How the App Works

A plain-language walkthrough of the system as it is now (updated 2026-11-11): from a loan officer logging in to a case getting a risk score, what is built, and where it is rough. Design rationale is in the other documents; this one describes behaviour.

---

## 1. The one-paragraph version

A loan officer creates a **case** for a company applying for a loan and uploads its documents (bank statements, NRICs, certificates and so on). Each document goes through a background pipeline: it is **read** (text from the PDF, or OCR for scans), **classified** ("this is a payslip") and **extracted** ("net pay = 8,027.85"). The officer reviews and **approves** it. Approval starts the **Detection Agent**: a lead AI investigator plus three specialist AIs that run forensic checks (fonts, layout, images, metadata, spelling, arithmetic, government registries) and write a fraud report for that document. The findings a specialist judged genuine are also stored one by one as **flags**. Once every document is decided and every approved one has a report, **Case Analysis** starts on its own. Code first compares the documents with each other (company name, address, UEN, a person's ID and details); an AI then looks for deeper contradictions; the case is scored deterministically; the system suggests what further evidence to request, searches the web for recent adverse media about the company (a note for the officer, never scored) and writes a plain summary. **The risk score and the recommended supporting documents exist only as the output of that run.** Everything updates live in the browser over a WebSocket.

---

## 2. The pieces

| Piece | What it is | Where |
|---|---|---|
| **Frontend** | React + Tailwind app for the loan officer | `frontend/src` |
| **Backend** | One FastAPI codebase run as five roles: `api` (reads, officer commands, uploads, live updates), `doc-worker` (read, classify, extract), `detection-worker`, `case-worker` (case analysis) and `audit-metrics` (the audit trail) | `backend/app` |
| **Events** | The roles trigger each other only through domain events: in one process during development, Pub/Sub in the cloud | `app/events/`, `app/workers/` |
| **Gateway** | Kong in front of api and audit-metrics (compose and the cloud only) | `deploy/kong/` |
| **Metrics** | `GET /metrics` on every role (Prometheus) | `metrics.py` |
| **Database / storage** | Supabase: hosted Postgres and a private `documents` bucket | Supabase project |
| **AI** | Claude via the Anthropic API. Sonnet for classifying, extracting (and its second look), leading detection and the case analysis; Haiku for the second-opinion classifier, detection specialists, evidence requests, the adverse-media search and the summary | `app/langgraph`, `app/case_pipeline` |
| **Agent framework** | LangGraph: each AI workflow is a graph of steps | same |
| **Live updates** | Supabase Realtime tells the backend a row changed; the backend pushes a case snapshot over a WebSocket | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py` |
| **Cost logging** | One `perf` log line per unit of work | `query_stats.py` |

`ROLE` decides what a process runs. `uvicorn app.main:app` keeps the default `ROLE=all` and `EVENT_TRANSPORT=inprocess`: every role in one process, each event handled by a background task, as before the split (work in flight is lost on restart). In compose and on Cloud Run each role is its own container, events go through Pub/Sub (`EVENT_TRANSPORT=pubsub`), and each worker receives them on `POST /push/<role>`. The layout, the deployment and how to run it are in `CLOUD_ARCHITECTURE_PLAN.md`.

---

## 3. End to end: following one case

### Step 0: Login
A few named accounts, all in one setting: `AUTH_USERS=alice:password1;bob:password2` (no roles; a password can't contain `;`, `,`, `$` or `#`). `POST /auth/login` returns a signed token carrying the username, which every other call sends; a handler that declares `user: CurrentUser` learns who is acting. A browser WebSocket can't send headers, so the frontend first asks for a one-time ticket (`POST /ws/tickets`) and puts it in the WebSocket URL.

### Step 1: Creating a case
A row in `cases`. A database trigger assigns the readable ID (`GLDB-<n>`), skipping any already taken. Local runs and the cloud share one Supabase project and one case list.

### Step 2: Uploading
PDF, PNG or JPEG, up to 25 MB each, up to 20 per batch. For each file: store it, create a `documents` row (`uploaded`), mark it `queued` and publish `document.uploaded`. The request returns immediately; a failed publish fails that file with a 502.

### Step 3: Per-document pipeline (read, classify, extract)
The doc-worker takes each `document.uploaded` (or an officer's `document.retry_requested`), claims the document with a compare-and-swap from `queued` to `processing` (so a repeated event is skipped), and runs the LangGraph document graph, `QUEUE_WORKER_CONCURRENCY` at once per process. When it ends it publishes `document.analyzed` or `document.failed`:

1. **Ingest (`processing`, shown as "Classifying").** Download the file; read native PDF text or OCR it (a scanned PDF over `OCR_MAX_PAGES`, 50, or with a page over `OCR_MAX_PAGE_PIXELS` once rendered, fails with the reason before any page is read), and read file metadata with `exiftool` at the same time (it is a subprocess, so it runs in its own thread while the text extraction uses the PDF/OCR pool, `cpu_pool.py`: one thread locally, which keeps PyMuPDF off two threads at once, and two processes on the deployed workers, each with its own PyMuPDF). A failed metadata read is stored as an error record, never as "no metadata". The **content guard** (`guardrails/content_guard.py`) then scans the file for hidden text and wording addressed to an AI: flagged hidden text is taken out of what the models read, the result is saved for detection to report, and a document worded as an instruction to the AI fails here (`GUARD_BLOCK_INJECTION`).
2. **Classify.** A rule-based classifier first (free); then a Sonnet agent that may ask a Haiku classifier for a second opinion and, on a later turn, vision. Every turn offers both tools (a thinking block is only valid with the tools list it was made with); the order is enforced in code (`classify_graph.tools_node`): a call out of turn gets an error result instead of running. Confidence below **0.85** routes the document to manual review.
3. **Extract (`extracting`).** A Sonnet text pass fills the type's schema, with a confidence and, when confidence is low, a reason for each field. On a native-text PDF bank statement, code first reads the transaction table from word positions (`statement_table.py`, columns taken from each page's own header row) and the model reads everything else, naming each line's counterparty in the same call. The table is accepted on parse quality only, never on whether its balances add up; a scan, an image or a table that can't be read cleanly takes the usual text pass. The trace's `transaction_table` says which it was. Code selects only low-confidence fields, fields returned without a confidence, missing required fields, and figures removed because they aren't printed in the text; an optional field that simply isn't on the document doesn't count, and one-period totals and sample values are never looked at again. If none qualify, the text pass is the whole extraction. Otherwise, a field-scoped extraction agent may use a free layout lookup first (the cached text lines with their positions, OCR for scans, and each relevant page's tables); it may then make at most one visual read, cropped to the field's lines where the layout placed them, if the layout does not settle the selected fields. The whole second look has its own time limit (`EXTRACTION_SECOND_LOOK_TIMEOUT_SECONDS`, 90s); past it, or on any failure, the text pass stands. Image uploads can go straight to the visual read. The second look can fill a missing value but never replaces an existing one, and it raises a field's confidence only when code can see the value in the layout lines returned for that field. If it reads a different value from the text, on any file and from either tool, the text value stays, the field's confidence drops below the review threshold, and its reason gives both readings: the document goes to Manual Review and approval waits until the officer saves the field, so the officer decides. The disagreement is shown under the field and in the agent trace, and detection doesn't see it. A native PDF's visual read cannot introduce a number absent from its text layer, and neither can the layout lookup for a figure the unprinted-number guard removed. Each field's source (text, layout, or vision) is recorded in the document trace. The same second-look path runs when an officer changes the document type and re-runs extraction. Fields are stored one row each in `extracted_fields` **before** the document row is updated, so a snapshot never sees a document without its fields. For a document that can belong to a company or a person (a bank statement, a source-of-wealth document) the model also says which it is (`entity_type`).
4. **Disagreement loop.** If extraction says the type is wrong, the document returns to the classifier once; if still unresolved it is marked `failed`. An unreadable reply fails the document (retryable) without reclassifying it.
5. Done: `analyzed`.

### Step 4: Officer review
The officer can edit or delete fields, correct the document type (which re-runs extraction), move, delete, cancel or retry a document, and **approve** or **reject** it. A company name shows a character count from 80 characters, in red at the 100-character limit. The case menu (⋮) holds Download report, Edit case details and Delete case; a report that fails to download shows one plain sentence and logs the raw error. Approval locks the document, and waits while any field is below the confidence threshold until the officer saves the fields. Under such a field the page shows why extraction was unsure, read from the latest extraction step of the document's trace; if that trace write failed the reason is missing, but Manual Review still applies. A **case-match** check (plain code, `case_match.py`) compares each document's company and people with the certificate of incumbency to catch a document in the wrong case; it is not a fraud signal.

The case page also shows the **requirements checklist** (required types, proof of address per person, both sides of each ID).

### Step 5: The Detection Agent (`approved` → `detecting` → `detected`)
Approval publishes `document.approved`; the detection-worker runs it, **two runs at a time** per process (`DETECTION_CONCURRENCY`); the rest wait, shown as "Approved". A cancel or retry is an event too. The run ends with `document.detected` or `detection.failed`. Before a run starts, the worker claims the document with a compare-and-swap to `detecting` (from `approved`; a retry may also start from `detected`), so a redelivered approval finds it taken and does nothing; a repeat while the run is still going in the same process is ignored without touching the database. The case-worker likewise claims the analysis run (`case_analysis_runs.worker_started_at`), and an approval only goes through from `analyzed`, so two at once publish one event.

1. **Load context.** The document and its fields are fetched once and shared by every tool in the run. The file's contents are read too (`fraud_detection/shared/file_facts.py`): its kind, page count, whether a PDF has selectable text, and the size of each distinct embedded image.
2. **Lead plans (Sonnet).** One brief per specialist. The lead sees those file facts and says which checks do not apply.
3. **Three specialists in parallel (Haiku).** Each picks which of its tools to run, at most 3 tool rounds. Each is shown the file's contents and told what its tools need (image manipulation compares images with each other, so it needs at least two; image tampering needs embedded images; AI generation scores photographs and scans of at least 128 px, not logos; the font check needs selectable text). It is guidance, not a fixed list: the specialist still decides, and is told to run a tool anyway when the contents are unclear. Two checks run on every document before the specialist's first turn, so they don't depend on its choice: hidden text and prompt injection:

| Specialist | Tools (plain code) | Looks for |
|---|---|---|
| **Layout & Rendering** | hidden text, font consistency, layout anomalies, image manipulation, image tampering | text a reader can't see, odd fonts and sizes, misalignment, text over text, spliced or edited images |
| **Content Validation** | spelling, date consistency, page consistency, numerical checks, external validation | typos, impossible dates, a page missing, swapped in or copied in a multi-page statement, sums and balances that don't add up, broken NRIC/UEN/MRZ checksums, a company missing from ACRA, a postal code missing from OneMap, a home address at an office or industrial unit, wrong CPF/GST rates |
| **Digital Authenticity** | prompt injection, metadata, AI generation | wording written to steer the AI reviewing the file, editing software in the metadata, modified-after-created dates, AI-generated images |

   A tool returns *candidate* findings. The specialist gives each a severity or dismisses it as a false positive; it cannot invent findings or change a tool's facts.

   - **A hard breach cannot be dismissed or rated below high.** A finding a check marked `impossible` (an arithmetic that cannot hold, an identifier absent from its register) with confident extraction stays on the report at high whatever the specialist rated it; if the specialist dismissed it, it is marked not approved, with the specialist's note. A finding that needs review, or is only a signal, can still be dismissed.
   - **Income and balance checks stay at least medium.** On a payslip, notice of assessment or bank statement, a CPF, net pay, running balance, open/close or UEN finding (`PAY.CPF_*`, `PAY.NET`, `BS.RUNNING_BALANCE`, `BS.OPEN_CLOSE`, `ID.UEN_FORMAT`, `REG.UEN_EXISTS`) can't be dismissed or rated below medium; whenever a floor overrules the specialist, the finding keeps its own rating (`agent_severity`, with `severity_floor_reason`) and the review screen shows "raised from … by rule".
   - **Font check.** Besides comparing a piece of text with the neighbours in its column, it compares a number with the other cells in its row (a lone table value has no column) and flags a number in a typeface no other number uses. Both fire only for a style that at most two runs in the document use, so a legitimate repeated style is not flagged.
   - **Metadata.** Subject and title are read out for the specialist to judge. No rule inspects them, and they do not affect whether the check can assess a document. A specialist can mention them in its summary but cannot create a scored finding.
   - **Tool time limits** count the time a tool works, not the time its job waits for a free CPU worker; a wait has its own cap (`CPU_QUEUE_TIMEOUT_SECONDS`).
4. **Lead reviews**, and may request one second look from another specialist.
5. **Merge (code).** One report with a verdict: `flagged`, `inconclusive` or `clean`. When a second look dismisses a finding the first pass kept, the later view stands: the finding is removed from the report, the flags and the score (the dismissal stays visible under the specialist). A check that failed and then ran in a retry or the second look is not listed as unable to assess.
6. **Save.** The report is written to `detection_results`, then every finding a tool returned, kept or dismissed, is written to `findings`, then the document becomes `detected`.

**Findings** are written by code, never by the model (`findings_repository.py`, through the `sync_findings` function, in one transaction). Each row records what the specialists decided (`agent_decision`: accepted, not_assessed, overridden or dismissed) and, once an officer reviews it, the verdict. A finished run replaces the document's findings outright, with any verdicts on them (missed issues officers reported stay); a completed case analysis does the same for the case's cross-document findings, linked to the case only. Officers review at the end, so nothing is carried over. A finding is named within its run by a fingerprint (`finding_keys.py`), which the report and the case risk carry too. The Risk Summary lists the scored findings and, per document, the ones the specialists set aside, dismissed or rated info (`CaseRisk.dismissed_findings`): an officer ticks any that is real, and a set-aside finding marked real counts as a miss for recall (`detection_metrics.py`). A group with no findings moves to Reviewed only once an officer saves it (`review_sign_offs`); this doesn't hold up the case's stage.

If detection crashes it is recorded, the document returns to `approved`, and the officer can retry. Single checks can be run on demand once a report exists.

### Step 6: Case Analysis (automatic, the only source of the score)
**It starts on its own** (`case_pipeline/service.start_if_due`) once every document is decided (none processing, failed or waiting for the officer, required checklist documents approved) and every approved one is `detected`. The check runs after each detection run (detection-worker) and after an officer rejects, cancels, moves or deletes a document (api). It records a `running` run and publishes `case.analysis.requested` as the system (`detail.trigger = "automatic"`); the case-worker runs it and publishes `case.analysis.completed`. While it is due or running, the case stays at **Fraud Detection**.
- **No repeat:** nothing starts when the newest run already covers the same inputs (its fingerprint matches), whether complete, failed (the officer reruns a failed one) or still running.
- **Replaced:** a run over outdated inputs is not stopped; a new one starts, and the old one's result is recorded as `superseded` when it ends (`repository.finish_run`).
- **Cap:** at most `CASE_ANALYSIS_AUTO_RUNS_PER_HOUR` (4) runs per case per hour start on their own; past it, the officer starts one.
- **By hand:** "Run again" (`POST /cases/{id}/analysis/run`) stays for a failed run or one that didn't start. It is refused while the latest run is still `running`; one still `running` after `CASE_ANALYSIS_TIMEOUT_SECONDS` (15 minutes) reads as interrupted.

One run, in order:

1. **Deterministic comparisons (code).** Company name, address, UEN, and a person's ID number, name and details (date of birth, race, sex, nationality) across documents. A person is compared against every document that names them, not just the NRIC. Each difference is its own finding (`xd-det-N`), written as one plain sentence naming the documents by type, and merged by value so one wrong ID on three documents is one row. Differences in ID, UEN or date of birth are high; the rest are medium. Checks that agreed are listed as "checked and consistent". The protected details are compared only here, in code, and never shown to any model.
2. **Dossier (code).** A digest of each approved document's fields, derived transaction facts and detection findings. A person's protected attributes are **removed**. The code-found differences are added as established facts, grouped by value (at most 5,000 characters), with the instruction not to restate them. The planner's whole input is capped at 30,000 characters.
3. **Plan (one Sonnet call, forced structured output).** Where a detection finding's figure should also appear on another document in the case, the dossier adds a `lead:` line (`cross_document/leads.py`): a balance-sheet tax finding leads to the notice of assessment's tax payable and ECI, a payslip net pay finding to a salary credit in the bank statement. The planner takes the leads first and tests the figure against that document rather than restating the finding. At most 3 hypotheses, each with up to 4 checks and one refutation probe. A hypothesis that touches a protected attribute, or concerns one document with nothing computed behind it, is dropped before any check runs.
4. **Run the checks (code, no model).** Read-only evidence tools. Statement rows printed as `20 JUN` take their year from the statement period.
5. **Judge (one Sonnet call, forced structured output).** Writes for an officer: no tool names, flags or ref ids in its wording.
6. **Verify (code).** A verdict stands only if every citation is a ref a tool issued in this run; a refutation probe ran that looked at something the claim does not already rest on (unless a comparison tool's own mismatch flag is cited); it cites two documents or one computed fact; nothing in it touches a protected attribute; and severity is capped at medium unless a tool returned a typed breach flag or a registry mismatch backs it. A verdict that only restates a code finding is dropped. Everything else is discarded and never shown or scored.
7. **Score (code).** The deterministic scorer runs over the document findings plus the code and agent cross-document findings.
8. **Outlook (one Haiku call plus code).** The model suggests evidence to request; code re-scores "if this supports the client". Tampering, registry mismatches and conflicting identity can't be reduced by more evidence.
9. **Additional documents (code).** The rule-based recommendations (large supplier or customer payments, balance-sheet breakdowns) and the model's requests are merged into one list, each marked as score-changing or not.
10. **Summary (one Haiku call).** Grounded in the findings. It is told which checks ran clean and which **could not run**, so it cannot call an unrun check clear.

**Adverse media (one Haiku call with Claude's web search, `adverse_media.py`).** It starts alongside the cross-document check, so it adds no time unless it outlasts the run, and is limited to 45 s. A due-diligence note for the officer: it never creates a finding or touches the score.
- **What it searches:** at most 3 searches for sanctions, regulatory action, fraud, insolvency or other serious negative news from the last 21 days, only on the 22 sites in `TRUSTED_DOMAINS` (many large news sites block the search crawler, so they can't be listed).
- **Which name:** the company named on the documents (business profile, then certificate of incorporation), or the case's name if neither is there. One search covers up to 3 former names.
- **Results:** `clean` only when every search ran and the reply says so. `concerns` needs at least one cited page the search really returned. Anything else is `failed`, or `skipped` with no company name; neither reads as clean.
- **Cache:** a `clean` or `concerns` result is reused for 24 hours while the company and former names are the same, with no search and no cost.

The run stores its result, including the risk and additional documents, and writes one `risk_scores` row. Cost: 5 model calls (plan, judge, requests, summary, adverse media) against a ceiling of 7, with output capped at 3,000 and 2,500 tokens for plan and judge; a real run before the media check logged about 13k tokens in and 2.4k out. Each web search is billed on top of the tokens (`pricing.py`). Plan and judge share one cached system prompt. Bias is enforced at four points (input, prompt, proposals, verdicts); see `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

A case that was never analysed shows "Not scored yet". If documents change after a run, the run is marked out of date and the previous result stays visible with a notice. `CASE_ANALYSIS_ENABLED=false` switches the module off.

### How the score is calculated (no AI)
`case_pipeline/risk/case_risk.py`:
- Points per finding: high 8, medium 3, low 1, info 0.
- The same check with the same stated value on several documents scores once; the copies show at 0 points. A check that fires repeatedly on one document counts in full once, then at 25%.
- Low and medium points are capped at 6 per document; high findings are never capped.
- A cross-document finding is multiplied by up to 1.5 as more documents corroborate it.
- Total ≥ 8 → High, ≥ 3 → Medium, otherwise Low. **Critical** needs scoring high findings on 3 or more different documents.
- The 0–100 number is anchored to the level, so a Critical case always outscores a High one.

### Learning from officers' verdicts
Every verdict an officer saves on a finding, and every missed issue they report, is copied to `finding_feedback` (`feedback_repository.py`) with names and identifiers scrubbed (`feedback_scrub.py`). An item whose summary or note touches a protected attribute (`bias.has_protected_text`) is not kept; the verdict on the finding stands as normal. Drafts never reach the server: the review is sent when the officer saves the group. Feedback goes when the officer clears the verdict or deletes the missed issue, or with its case; deleting a document only clears its `document_id`.
- **Counted, not single verdicts.** The agents are shown lessons from other cases in the last 6 months, counted by `feedback_counts` (`schema.sql`): per check and lesson (false alarm, confirmed, set aside wrongly, missed), how many cases (each once), the reasons given, and up to 2 example notes, newest lesson first. A false alarm is shown only once 2 cases agree; the rest from the first case. Notes are screened for protected attributes again when read.
- **Detection (`langgraph/detection/feedback.py`).** A specialist reads it once per run, for all its tools. Each tool result then carries, appended after it, the lessons for exactly the check codes it fired (a finding without a code, and missed issues, go under the tool's name), each code once a run. It sits after the cache breakpoint, as do all tool results.
- **Codes name the rule, not the tool**, so two false alarms count as agreeing only when they are the same thing: the numeric, date, page and registry checks have their own; metadata has one per rule (`META.PDF_EDITOR`, `META.AI_TOOL`, ...); fonts, layout and the two image checks take theirs from the finding's type (`FONT.SIZE`, `LAYOUT.TEXT_OVERLAY`, `TAMPER.VISIBLE_BOUNDARY`); AI generation tells a scored image from a declared AI source. Spelling's is the word (`SPELLING.<word>`), so a word dismissed in 2 cases is shown as such, like a dictionary the agent is told about rather than one that skips the word; one that names a party to the case or a confidential detail isn't kept. Hidden text and prompt injection have none: their findings can't be dismissed.
- **Case analysis (`cross_document/feedback.py`).** Read once per analysis for the two model-decided categories, and shown to the planner and the judge. Confirmations, findings set aside wrongly and missed issues only, never false alarms: a category holds free-form hypotheses with no code to tell them apart.
- **Recorded:** each finding stores the ids of what it was shown for its code (`feedback_ids`), and the case page lists them as "Feedback the agent was shown". Being shown is not the same as acting on it.
- **Limits:** feedback only informs the model. Severity floors, impossible values, the citation ledger, `bias.py` and the score are all enforced in code, whatever it says. A lookup that fails means no feedback, never a failed run.
- **No on/off setting** (decided: too many edge cases).
- **Reset for a test:** `truncate public.finding_feedback;` in Supabase's SQL editor. To seed a known set, run `backend/sql/dev/seed_feedback_test.sql`, and afterwards `delete from public.cases where id like 'test-case-%';` (its feedback goes with the cases). `backend/scripts/preview_feedback.py` prints what an agent would be shown, with no model call.

The classifier and the extractor learn from what officers change, read from `audit_events` (`corrections_repository.py`, through `type_corrections` and `field_edit_counts` in `schema.sql`). Labels and field names only, never values, and no table of their own. A lesson counts once 2 cases agree, and only corrections after `LEARNING_SINCE` count (a constant in code, so a test can move it). A failed read gives no hint. Both hints sit after the cache breakpoint (`langgraph/correction_hints.py`). `audit_events` is never deleted, so these can't be reset like the feedback table: a test moves `LEARNING_SINCE` instead.
- **Classifier:** a retype from one type to another (`document.retyped`; confirming the same type isn't a correction) teaches "officers corrected X to Y in N cases". When the agent's guess is X it is told this, with what tells Y apart (`SIGNALS`, written by the team; without one, it just names the confusion). The rule classifier's guess gets it on the first turn, and a classify tool's guess gets it appended to that tool's result, each guess once.
- **Extractor:** `document.field_edited` carries `document_type`. A field added (a change from nothing) or deleted (a change to nothing) on a type teaches the extractor of that type to look for it, or to extract it only when the document shows one. A field that is added and deleted equally often teaches nothing. Field names that aren't plain snake_case, or that name a protected attribute, are never taught. Edits from before `document_type` was recorded can't be grouped, so they don't count.

### Checking what the models return
Every model reply and tool result is checked against an expected shape before anything uses it. A bad one is repaired, replaced by a safe default, or recorded as "could not assess": it never crashes a run, and a check that couldn't run never counts as clean.
- **`app/llm_io.py`** reads replies: `message_text` (ignores thinking blocks), `thinking_summaries` (the readable summaries every request asks for, `_anthropic._with_thinking_summaries`, recorded with each Claude call on the run's trace and shown as "Reasoning" in the trace view), `parse_json_object` (tolerates fences and prose around the object), `forced_args` for tool calls, `was_cut_off` and `salvage_*` for replies cut off at the token limit, and `validate(Model, data)`, which never raises and returns `ok`, `degraded` (unusable fields dropped, defaults apply) or `failed` (the caller's fallback). Lenient on shape, strict on meaning: a test fails if a contract gains a numeric field, so coercion can't make up a number.
- **`app/llm_contracts.py`** has one model per reply: `ClassifierReply`, `ExtractionReply`, `LeadPlan`, `LeadReview`, `SpecialistAssessment`, `EvidenceRequests`, `SummaryReply`. Hypotheses and verdicts are checked item by item, then in `verify.py`.
- **Tool calls:** Haiku can be forced to call a tool (the evidence requests; each specialist's first turn). Sonnet 5.5 can't, so the cross-document planner and judge use `tool_choice: auto`, a `strict` tool, a prompt line naming it, and one retry if the reply didn't call it. The detection lead and extraction answer in JSON text.
- **Refusals:** a `stop_reason` of `refusal` fails the step with its own reason (`ModelRefused`): the document fails with a plain message for the officer, and a specialist marks its findings unassessed.
- **Detection tools** all return `ToolResult` (`unable_to_assess`, `nothing_to_check`, `findings`, …). A finding without a summary gets a placeholder rather than being dropped, so a flagged document can't read as clean. Stored reports carry a `schema_version` and are read through `read_report()`.

| Reply | When it's unusable |
|---|---|
| Classifier agent | One cheap re-ask, then `unrecognised` and manual review; an unreadable `confidence` alone takes 0 |
| LLM classifier, vision | Empty candidates; the rule classifier decides |
| Extraction | Retried once with its own reply shown (not when cut off); then the document fails, retryable. A cut-off reply keeps its complete fields. Nothing is written without usable output, so a failed rerun doesn't wipe stored fields. Confidence is clamped to 0–1, anything else is unknown |
| Lead plan / review | Default briefs / no summary and no second look |
| Specialist | Findings stay unassessed, `uncertain=True` |
| Hypotheses, verdicts, evidence requests | Empty list |
| Summary | The deterministic summary |

**Bounds:** a run may make at most `DOCUMENT_MAX_MODEL_CALLS` (24) or `DETECTION_MAX_MODEL_CALLS` (30) calls, and a case analysis 7 (`run_context.py`). Past the detection limit the verdict can't be clean. Every tool has a time limit (`TOOL_TIMEOUT_SECONDS` 60, `SLOW_TOOL_TIMEOUT_SECONDS` 120 for model and registry tools); a timeout is `unable_to_assess`. Transient API errors are retried by the client (`LLM_MAX_RETRIES`, 1). Every repaired reply is written to the trace (`run_audit`), with the raw reply capped at 4,000 characters.

---

## 4. How live updates work

1. A change to a `documents`, `cases` or `case_analysis_runs` row fires a Supabase Realtime event; one subscription serves the whole api process. This is how a result written by a worker in another service reaches the browser (`case_analysis_runs` must be in the `supabase_realtime` publication).
2. The backend waits **0.5 s** to merge a burst, then builds a **case snapshot**: case, documents with fields, detection states, the latest analysis (risk, additional documents, overview) and case-match. For a case with no analysis it reads only each report's verdict, not the full report. When only the case row or its analysis run changed, it reuses the documents, fields and detection states of the case's last full build (neither can change them) and reads the rest; the payload is the same full snapshot either way.
3. It pushes the snapshot to every browser with that case open (`/ws/cases/{case_id}`). **No snapshot is built for a case nobody has open.**
4. **One build runs per case at a time.** A request that arrives mid-build waits for one follow-up build shared by everyone who arrived meanwhile; it is never handed the in-flight result, which may predate its change.
5. On (re)connect the browser always gets a fresh snapshot. It reconnects with backoff (1 s, 2 s, 4 s … capped at 30 s, with jitter).
6. The detection worker's and the case analysis's own snapshot pushes are skipped when the Realtime listener runs in the same process (locally), since it already turns the same write into a snapshot.

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
| `cases` | company name, GreenLink ID, the stage (`status`), and `checklist_enforced` and `reviews_required`, set at creation. `recommended_additional_documents` (jsonb) holds `[]` until a case is analysed, then the whole `additional_documents` result of its latest complete run; the case summary returns it as `additional_documents`. The summary's `missing_requirements` (the checklist types no accepted document covers yet) is computed on every read, not stored |
| `documents` | one row per file: status, type, confidences, routing, requirements fulfilled, file metadata (read only by the metadata checks) |
| `extracted_fields` | one row per field per document |
| `langgraph_trace` | one JSON trace per document: every run's steps and spans (classification, extraction, detection, single checks) and the **full detection report**. For debugging and for showing field reasons on the review screen; never read to decide anything |
| `detection_results` | each document's latest detection report, or why its last run failed |
| `findings` | every finding (per document, kept and dismissed, and per case), the agents' decision, and the officer's verdict; missed issues |
| `review_sign_offs` | a group of findings an officer saved as reviewed |
| `finding_feedback` | scrubbed officer verdicts and missed issues, read counted through `feedback_counts` |
| `case_notes` | officers' notes on a case or a document, and their replies |
| `case_analysis_runs` | each run: findings (including `xd-det-N`), `consistent_checks`, outlook, summary, **risk**, **additional documents**, adverse media, audit, step trace |
| `risk_scores` (+ `latest_risk_scores` view) | one row per completed run: score, level, reasoning |
| `audit_events` | every domain event, stored by audit-metrics (id = the event's id, so a redelivery is ignored): who (officer, agent or system), what, which case or document, outcome, timing, and Kong's request id; read at `GET /audit/events` |
| Storage bucket `documents` | the uploaded files |

Deleting a document removes its fields, findings, trace and file; deleting a case removes its documents, then its runs, scores and findings. `backend/sql/schema.sql` mirrors the live schema by hand; `README.md` lists the objects the backend expects.

---

## 7. Configuration

Required: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `ANTHROPIC_API_KEY`, `AUTH_USERS`, `AUTH_SECRET_KEY`. With `EVENT_TRANSPORT=pubsub` (the cloud and the compose stack), `api` and `audit-metrics` refuse to start while `AUTH_SECRET_KEY` is empty.

| Variable | Default | Effect |
|---|---|---|
| `ROLE` | `all` | `all`, one role, or a comma list (`api`, `doc-worker`, `detection-worker`, `case-worker`, `audit-metrics`) |
| `EVENT_TRANSPORT` | `inprocess` | `pubsub` publishes to Pub/Sub (needs `GOOGLE_CLOUD_PROJECT`; the emulator when `PUBSUB_EMULATOR_HOST` is set) |
| `PUBSUB_TOPIC` | `domain-events` | the one topic |
| `PUSH_AUDIENCE`, `PUSH_SERVICE_ACCOUNT` | — | when set, `/push` requires Pub/Sub's OIDC token for that audience from that account |
| `CORS_IN_APP` | true | false behind Kong, which answers CORS itself |
| `CASE_ANALYSIS_TIMEOUT_SECONDS` | 900 | a run still `running` after this reads as interrupted |
| `QUEUE_WORKER_CONCURRENCY` | 4 | documents one doc-worker process runs at once |
| `DETECTION_CONCURRENCY` | 2 | detection runs at once |
| `CPU_WORKER_CONCURRENCY` | 1 | threads for PDF, OCR and forensic work; PyMuPDF is not thread-safe |
| `CPU_WORKER_PROCESSES` | 0 | processes for that work instead, each with its own PyMuPDF (2 on the doc-worker and detection-worker); a bound method or closure still uses the thread |
| `DOCUMENT_MAX_MODEL_CALLS` | 24 | model calls one document's classify and extract run may make |
| `DETECTION_MAX_MODEL_CALLS` | 30 | model calls one detection run may make; past it the verdict can't be clean |
| `LLM_MAX_RETRIES`, `LLM_TIMEOUT_SECONDS` | 1, 300 | the Anthropic client's own retries (each is billed) and one request's time limit |
| `TOOL_TIMEOUT_SECONDS`, `SLOW_TOOL_TIMEOUT_SECONDS` | 60, 120 | time limit per detection tool, counted while it works (not while its job waits for the CPU worker); the slow ones call a model or a registry |
| `CPU_QUEUE_TIMEOUT_SECONDS` | 300 | the most a tool waits for the CPU worker before it starts |
| `CASE_ANALYSIS_ENABLED` | true | kill switch for the case analysis |
| `CASE_ANALYSIS_AUTO_RUNS_PER_HOUR` | 4 | runs per case per hour that start on their own; past it, the officer starts one |
| `REQUIRE_COMPLETE_CHECKLIST` | false | whether new cases need every required checklist document approved before analysis (stored on the case at creation; on only in production) |
| `GUARD_BLOCK_INJECTION` | true | a document worded as an instruction to the AI fails at ingest; false lets it through, flagged. An officer can still let one blocked document through with "Process Anyway" (`POST /documents/{id}/process-anyway`): saved on its guard result, logged as `document.guard_overridden`, re-queued like a retry, warning kept |
| `SWEEPER_ENABLED` | false | runs the stuck-work sweeper (Terraform sets it on audit-metrics) |
| `STUCK_WAITING_SECONDS`, `STUCK_RUNNING_SECONDS`, `RECOVERY_MAX_ATTEMPTS` | 300, 900, 3 | when the sweeper counts work as stuck, and how often it retries |
| `LOG_HTTP_REQUESTS` | false | httpx's one line per Supabase call |
| `LANGGRAPH_SONNET_MODEL`, `LANGGRAPH_HAIKU_MODEL` | `claude-sonnet-5-5`, `claude-haiku-5-5` | model overrides. Every call sets its effort in code (`low`, or `medium` for the detection lead and the cross-document planner and judge) and sends no `temperature`; a refusal fails the step with its own reason |
| `SUPABASE_STORAGE_BUCKET` | `documents` | bucket name |
| `DATA_GOV_SG_API_KEY`, `ONEMAP_API_TOKEN` | — | optional: without them the anonymous limits apply to data.gov.sg (lookups and the zoning download) and OneMap |
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

The same counts are also on `GET /metrics` for Prometheus (`metrics.py`), together with:
- request rates and latencies, events published and handled, and stage durations;
- Claude calls, tokens, cost and latency per agent and model, with list prices from `pricing.py`;
- cost and calls per unit of work, and detection tool outcomes.

Labels are routes, event types, stages, agents and models, never ids. Kong blocks `/metrics` from outside. Grafana's four dashboards (services, pipeline, agents, cost) read these metrics (`deploy/grafana`, `CLOUD_ARCHITECTURE_PLAN.md` section 7).

**Traces.** OpenTelemetry (`telemetry.py`) is off unless `OTEL_ENABLED=true`, which Terraform sets in the cloud. Spans go to:
1. OTLP, when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (Jaeger locally);
2. otherwise Cloud Trace, when `GOOGLE_CLOUD_PROJECT` names a real project;
3. otherwise the console.

Each event's Pub/Sub message carries `traceparent`, so one upload is one trace across services. `OTEL_TRACES_SAMPLE_RATIO` (default 1.0) samples fewer.

**Agent trace in the app** (SCRUM-59). The same spans are also written to the run's own trace, whether or not `OTEL_ENABLED` is set: a document's `langgraph_trace` row, or a case run's `trace` column (`telemetry.steps_into`). Each span is one `"span"` step, written when it ends: its `id`, `parent`, `duration_s`, its Claude calls (`llm_calls`: model, tokens, cost, seconds, stop reason, reasoning summaries, from the usage callback in `query_stats.py`), any reply that failed validation (`validation_failures`: site, agent, time, issues, the raw payload and what was used), and what `telemetry.annotate` added. Every other step names the span it happened in (`in_span`). These never go to Cloud Trace.
- **Spans:** a root per run (`langgraph.process_document`, `langgraph.rerun_extraction`, `detection.run`, `case_pipeline.run`); each agent's model turns; each specialist's whole review (`detection.review.<name>`, with its ordered `tool_calls`, `[]` when it called none); each tool call (`tool.<name>`: input, outcome; `langgraph/tool_spans.py`).
- **Saving:** a run saves its trace after its root span closes, so the root's own step is kept.
- **The view:** `components/agentTrace/` draws the tree beside the selected step's details, with a run's steps under folded headings (Classifier, Extractor, Lead agent and so on, in `buildTrace.ts`) and each step's time since the step before it in the same span. The document's own trace opens from the document page's actions menu ("Agent trace"), while the case analysis trace opens from the case actions menu when the case is on `section=analysis` ("Case analysis trace", with its earlier runs). A trace from before spans were recorded shows as a flat list of steps.

**Stuck work** (`sweeper.py`, `CLOUD_ARCHITECTURE_PLAN.md` section 5). In the cloud, Cloud Scheduler publishes `sweep.requested` every 5 minutes and audit-metrics runs the sweeper (`SWEEPER_ENABLED`; off locally, because the database is shared).
- **What counts as stuck:** a document uploaded or queued for over 5 minutes (its event was lost), or processing, extracting or detecting for over 15 minutes (its run died); or a case analysis still running past `CASE_ANALYSIS_TIMEOUT_SECONDS`.
- **What happens:** the sweeper publishes `recovery.requested` to the worker that owns the stage. That worker resets the status (compare-and-swap) and reruns it. After 3 attempts it fails the document (detection: back to approved with an error) and publishes `recovery.exhausted`. A stuck case analysis is marked failed, not rerun.
- **Locally:** `python -m app.sweeper --once --case <id>` runs one pass for one case and does the recovery in the same process.
- **Cancels:** a run checks every 5 seconds for a cancel made through another instance (Pub/Sub mode only).
- **Approvals:** an approval whose event can't be published is undone.

Every 60 seconds a summary of Realtime and WebSocket traffic (events received, ignored, merged, connects, pushes) and of calls made outside any unit of work is also logged. Rough budgets: case list 3 queries; a snapshot 5; approve 2; delete or add a field 6–7; detection about 12.

---

## 9. What is built and what isn't

**Built:** upload and queue; the content guard; classify and extract with the reclassify loop and the second look; officer review tools; requirements checklist; case-match; the Detection Agent with 13 checks and on-demand single checks, cancel and rerun; every finding stored with the agents' decision and the officer's verdict (dismissed ones included); learning from officers; the case analysis (deterministic and agent cross-document findings, score, outlook, additional documents, adverse media, summary); live WebSocket updates; case notes; cost logging and per-case Claude cost (every run: retries, reruns, cancelled and failed runs, earlier analyses, deleted documents); the service split on Cloud Run (roles, domain events, Pub/Sub push, audit trail, stuck-work recovery, `/metrics`, Grafana, traces, alerts), Kong, the compose stack, Terraform, and CI/CD that deploys every green push to `main` (`CLOUD_ARCHITECTURE_PLAN.md`).

**Not built:** open items are in `PENDING_WORK.md` and the cloud plan's section 9.

## 10. Known rough edges

- `exiftool` is not installed on the dev machine, so the metadata check and the AI-provenance part of the AI-generation check report "could not run". The summary says so.
- Detection specialists run on Haiku, whose minimum cacheable prompt is longer than their prompts, so caching there does nothing. It works for classification, extraction and the case analysis.
- "Run again" on unchanged documents spends the tokens again; only an automatic start is skipped when the fingerprint matches.
- The first requester of a burst of snapshot requests can wait for a follow-up build.
- Known limits of the split:
  - On-demand single checks run in the api process.
  - A WebSocket ticket replayed within its 10 s on a second api instance would pass.
  - There's no shared Anthropic rate budget across instances.

  Stuck work, cancels across instances, unpublished approvals and signed tickets are handled (see "Stuck work" above).

---

## 11. Where to start reading

| To understand… | Read |
|---|---|
| Startup, roles, routers, auth | `backend/app/main.py`, `auth.py`, `config.py` |
| Events and their transport | `events/` (`types.py`, `bus.py`, `push.py`, `registry.py`) |
| Upload and the doc-worker | `routers/upload.py`, `workers/documents.py` |
| Classify and extract | `langgraph/pipeline.py`, `graph.py`, `classify_graph.py`, `extract.py`, `extract_graph.py`, `extraction_tools.py`, `statement_table.py` |
| Approve and detection | `document_service.py` (`approve`), `workers/detection.py`, `langgraph/detection/graph.py`, `specialists.py`, `tools.py` |
| The forensic checks | `fraud_detection/` |
| Findings and their reviews | `findings_repository.py`, `finding_keys.py`, `routers/finding_reviews.py` |
| Case snapshot, checklist, case list | `case_service.py` |
| Comparable keys for IDs, names, companies | `identifiers.py` |
| Risk score | `case_pipeline/risk/case_risk.py`, `store.py` |
| Case analysis | `case_pipeline/service.py`, `cross_document/deterministic.py`, `agents.py`, `verify.py`, `bias.py`, `risk/outlook.py`, `adverse_media.py` |
| Learning from officers | `feedback_repository.py`, `corrections_repository.py`, `langgraph/detection/feedback.py`, `langgraph/correction_hints.py` |
| Live updates | `realtime_listener.py`, `case_broadcaster.py`, `routers/ws.py`, `frontend/src/services/caseSocket.ts` |
| Cost logging and metrics | `query_stats.py`, `metrics.py` |
| Audit trail | `audit.py`, `routers/audit.py` |
