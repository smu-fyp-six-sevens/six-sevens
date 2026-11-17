# How the App Works

A plain-language walkthrough of the system as it is now (updated 2026-11-16): from a loan officer logging in to a case getting a risk score, how each part is built and why, the cloud, how it's tested, and where it is rough. Everything built is described here; the other documents at the root list only open work, and go away as it's done. Setup and running: `README.md`; cloud operations: `deploy/terraform/README.md`.

---

## 1. The one-paragraph version

A loan officer creates a **case** for a company applying for a loan and uploads its documents (bank statements, NRICs, certificates and so on). Each document goes through a background pipeline: it is **read** (text from the PDF, or OCR for scans), **classified** ("this is a payslip") and **extracted** ("net pay = 8,027.85"). The officer reviews and **approves** it. Approval starts the **Detection Agent**: a lead AI investigator plus three specialist AIs that run forensic checks (fonts, layout, images, metadata, spelling, arithmetic, government registries) and write a fraud report for that document. Every finding, kept or dismissed, is also stored one by one (`findings`) with the specialist's decision; the kept ones are the case list's **flags**. Once every document is decided and every approved one has a report, **Case Analysis** starts on its own. Code first compares the documents with each other (company name, address, UEN, a person's ID and details); an AI then looks for deeper contradictions; the case is scored deterministically; the system suggests what further evidence to request, searches the web for recent adverse media about the company (a note for the officer, never scored) and writes a plain summary. **The risk score and the recommended supporting documents exist only as the output of that run.** Everything updates live in the browser over a WebSocket.

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

`ROLE` decides what a process runs. `uvicorn app.main:app` keeps the default `ROLE=all` and `EVENT_TRANSPORT=inprocess`: every role in one process, each event handled by a background task, as before the split (work in flight is lost on restart). In compose, on Cloud Run and on GKE each role is its own container and events go through Pub/Sub (`EVENT_TRANSPORT=pubsub`): on Cloud Run and in compose each worker receives them on `POST /push/<role>`; on GKE each pulls its own subscription (`EVENT_DELIVERY=pull`). The layout and how to run it are in §9 below and `deploy/terraform/README.md`.

---

## 3. End to end: following one case

### Step 0: Login
A few named accounts, all in one setting: `AUTH_USERS=alice:<hash>;bob:<hash>` (no roles). Each password is stored only as a salted scrypt hash (`passwords.py`; about 0.4 s to check, run off the event loop); `python -m scripts.hash_password alice` in `backend/` prompts for one and prints the entry. A plain-text password stops the app at start-up. A login runs one hash whether or not the name exists, so timing doesn't reveal accounts. `POST /auth/login` returns a signed token carrying the username, which every other call sends; a handler that declares `user: CurrentUser` learns who is acting. A browser WebSocket can't send headers, so the frontend first asks for a one-time ticket (`POST /ws/tickets`) and puts it in the WebSocket URL.

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
Approval publishes `document.approved`; the detection-worker runs it, **two runs at a time** per process (`DETECTION_CONCURRENCY`); the rest wait, shown as "Approved". A cancel or retry is an event too. The run ends with `document.detected` or `detection.failed`. Before a run starts, the worker claims the document with a compare-and-swap to `detecting` (from `approved`; a retry may also start from `detected`), so a redelivered approval finds it taken and does nothing; a repeat while the run is still going in the same process is ignored without touching the database. The claim stamps `processing_started_at` with the run's start, and the run saves its report, findings and final status only while the document still carries that stamp: a run a recovery replaced, or one ended as lost, saves nothing (`detection_superseded` in its trace) and leaves the newer run's report and the officer's verdicts standing. The case-worker likewise claims the analysis run (`case_analysis_runs.worker_started_at`), and an approval only goes through from `analyzed`, so two at once publish one event.

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

If detection crashes it is recorded, the document returns to `approved`, and the officer can retry. Single checks can be run on demand once a report exists. "Other" documents are scanned too: type-specific checks report "nothing to check", and layout runs in generic mode.

#### How a detection run is wired (`backend/app/langgraph/detection/`)

```
load_context ─► lead_plan ─(×3)─► specialist ─► lead_review ─► synthesize ─► END
                                                  │  ▲
                                                  ▼  │
                                             run_second_look   (at most once)
```

The graph runs with `recursion_limit=40`. On an error the run saves it and puts the document back (`approved`, or `detected` if an earlier report stands); on success it saves the report, stores the findings, then marks `detected`, in that order.

- **Lead** (Sonnet, effort medium, 4,000 tokens, cached prompt): an unusable plan gives every specialist a generic brief (the trace's `fallback_reason` says why); an unusable review gives no summary and no second look. It sees summaries and kept findings, not dismissed ones. A second-look request is kept only if the finding exists and goes to a different, known specialist.
- **Specialist** (Haiku, 6,144 tokens): up to 3 tool rounds, then a wrap-up turn with no tools. Its reply is JSON (`summary`, `assessments`, `uncertain`); if it can't be read, or no tool ran, the report is marked uncertain.
  - Layout & Rendering: hidden text (run first), font, layout, image manipulation, image tampering.
  - Content Validation: spelling, dates, pages, numerical, external (its prompt lists the fields and check menus).
  - Digital Authenticity: prompt injection (run first), metadata, AI generation.
  - **Floors:** a tool with a floor (`_MIN_SEVERITY_BY_TOOL`, or `severity_floor` on the finding) can't be dismissed or rated below it. An unassessed finding starts at `low` (a floor may raise it), `ai_accepted: false`, still flags the document, and scores at most 2 points in the case risk, so one alone doesn't make a case Medium.
  - **Repeat calls:** a call identical to one that already succeeded (same tool, same arguments, or the same call twice in one turn) is answered "already run" and not run again (`_refuse_repeats`, ToolNode's `awrap_tool_call`); a failed call may be redone. A field tool called again with new bindings runs, and its findings get ids of their own (`<tool>-<n>-call2`), so they don't replace the first call's.
- **Tools** (`tools.py`): 13 (hidden text, font, layout, spelling, page consistency, date, metadata, prompt injection, image manipulation, image tampering, AI generation, plus `numerical_checks_tool` and `external_validation_tool` in `field_checks/`). Each returns `{tool, unable_to_assess, reason, findings, truncated}`: up to 15 findings, ids `<tool>-<n>`, the tool's `severity_hint` kept apart from the specialist's rating. The field tools take `bindings` the specialist fills; the document type comes from the run state and decides which checks run. The document, its fields and its file are fetched once per run and shared.
- **Merge (code):** findings keyed by `(tool, id)`; duplicates take the higher severity. `flagged` = anything above `info`; `inconclusive` = nothing flagged but a check couldn't run; else `clean`.
- **Trace events:** `detection_start`, `detection_plan`, `detection_specialist`, `detection_second_look`, `detection_done`, `detection_error`, `findings_error`, `run_audit`. Spans: `detection.run`, `.lead_plan`, `.lead_review`, `.review.<name>`, `.specialist.<name>`, `tool.<name>`. A failed findings write is noted on the trace (`findings_error`) and doesn't fail the run.
- **Other files:** `graph.py` (the lead, the merge, `run_detection()`), `specialists.py`, `feedback.py` (officer feedback appended to tool results).

**API** (`routers/detection.py`): `GET /documents/{id}/detection/<check>` runs one check (409 until a report exists; numerical and external use one Haiku call to bind inputs). `GET …/detection/report` (200; 502 if the last run failed with none; 404 if never run), `POST …/detection/cancel`, `POST …/detection/retry` (409 while running or queued), `GET …/detection/case-match`. There is no "run detection" route: approval starts it.

**Frontend:** `DetectionPanel.tsx` shows the verdict, each specialist's brief, tools and dismissals, the second look, the findings and what couldn't be assessed; Cancel while scanning (a run lost mid-scan, its worker restarted, is ended by the cancel too: at once locally, after `STUCK_RUNNING_SECONDS` in the cloud, where it may still be live on another pod); "Run detection again" after a failed or cancelled run, or over a report (confirmed first).

#### The forensic checks (`backend/app/fraud_detection/`)

| Folder / file | Checks |
|---|---|
| `layout_anomalies/` | Indentation, alignment, margins, spacing. `profiles.py` has per-type rules (only `nric`, and only above the classification threshold); `reference.py` compares standard forms with references in `references/<type>/` (built by `scripts/build_layout_reference.py`) |
| `layout_anomalies/text_overlay.py` | Overlapping text (skips repeated watermarks and vertical text) |
| `font_consistency.py` | Font family, size, emphasis |
| `numerical_validation/` | Arithmetic and identity over extracted fields (`@check` in `registry.py`; `statements.py`, `identity_company.py`, `records.py`, `casts.py`, `payslip_inputs.py`, `runner.py`). `common._as_number` reads DR/CR and comma decimals |
| `authenticity/` | `metadata_analysis.py` (metadata tamper signs), `ai_generation.py` |
| `dates/` | `date_anomalies.py` (future dates, end before start), `date_parsing.py` (any printed date form). "Today" is Singapore's (`app/sg_time.py`) |
| `spelling/` | SymSpell, one finding per word; `domain_terms.py` allow-list |
| `pages/` | Multi-page consistency, no model: `PAGE.NUMBERING`, `PAGE.CARRY_FORWARD`, `PAGE.HEADER_IDENTITY`, `PAGE.SOURCE` (signal), `PAGE.DUPLICATE` |
| `images/` | Resolution/compression mismatches, overlays, scaling, boundaries, quality (`image_manipulation.py`, `image_integrity.py`) |
| `external_validation/` | Facts against outside sources. `registry_lookups/` (live ACRA, OneMap, HDB, URA, MOM), `sg_reference/` (CPF, GST, IRAS, bank ids, holidays), `reference_checks/` (`PAY.CPF_*`, `POA.GST`, `NOA.PAYABLE`, `BS.BANK_IDENTIFIERS`, `BS.ACCOUNT_NUMBER_FORMAT`), `giro.py` |
| `shared/` | Not checks: `check_result.py` (base class), `field_values.py`, `field_catalogue.py`, `file_facts.py`, `pdf_pages.py` |
| `app/guardrails/` | `content_guard.py` (hidden text, prompt injection; scanned at ingest), `envelope.py` (fencing), `leak_check.py` |

**CPF:** the three `PAY.CPF_*` checks share one assessment, so each wrong contribution is reported once: `CPF_CEILING` (ceiling not applied), `CPF_BASE` (basic pay only), `CPF_RATE` (anything else). A pay date in a year after the newest rate table is skipped as unable to assess.

**Addresses** (`registry_lookups/addresses.py`, each checked once): HDB blocks against HDB's register; private buildings against URA approved use, then Master Plan zoning (a ~12 MB local index built into the image; without it zoning is skipped). Addresses compare after `address_matching.normalize_address` ("Blk 50 … #01-30 S639107" = "50 TUAS AVENUE 11, Unit 01-30, Singapore 639107").

| Code | Meaning | Severity |
|---|---|---|
| `REG.POSTAL_CODE_EXISTS` | Postal code not in OneMap | impossible |
| `REG.ADDRESS_MATCH` | Block and street not at that postal code | inconsistent |
| `REG.RESIDENTIAL_ADDRESS` | A home that can't be lived in | inconsistent / implausible / signal |
| `REG.COMPANY_ADDRESS_RESIDENTIAL` | Company registered at a home | signal |
| `REG.UNIT_STOREY` | HDB unit above the top storey | impossible |
| `REG.UNIT_MISSING` | No or unreadable unit number | skipped, not a finding |

### Step 6: Case Analysis (automatic, the only source of the score)
**It starts on its own** (`case_pipeline/service.start_if_due`) once every document is decided (none processing, failed or waiting for the officer, required checklist documents approved) and every approved one is `detected`. The check runs after each detection run (detection-worker) and after an officer rejects, cancels, moves or deletes a document (api). It records a `running` run and publishes `case.analysis.requested` as the system (`detail.trigger = "automatic"`); the case-worker runs it and publishes `case.analysis.completed`. While it is due or running, the case stays at **Fraud Detection**.
- **No repeat:** nothing starts when the newest run already covers the same inputs (its fingerprint matches), whether complete, failed (the officer reruns a failed one) or still running.
- **Replaced:** a run over outdated inputs is not stopped; a new one starts, and the old one's result is recorded as `superseded` when it ends (`repository.finish_run`). The write goes through only while the run is still `running` (a run the sweeper failed stays failed), and the newest run is checked again after it: a run inserted in between still makes this one superseded, so its score and findings are never published.
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

The run stores its result, including the risk and additional documents, and writes one `risk_scores` row. Cost: 5 model calls (plan, judge, requests, summary, adverse media) against a ceiling of 7, with output capped at 3,000 and 2,500 tokens for plan and judge; a real run before the media check logged about 13k tokens in and 2.4k out. Each web search is billed on top of the tokens (`pricing.py`). Plan and judge share one cached system prompt (and the tools); nothing else is cached, since the dossier and the hypotheses are each sent once, so the case analysis's cache rate stays low (about 10%). Bias is enforced at four points (input, prompt, proposals, verdicts); see "Design rules" below.

A case that was never analysed shows "Not scored yet". If documents change after a run, the run is marked out of date and the previous result stays visible with a notice. `CASE_ANALYSIS_ENABLED=false` switches the module off. Every run writes a step-by-step `trace` to its row.

#### Design rules of the case analysis

- **Only conclusive hypotheses exist:** unproven ones aren't shown, stored or scored (the developer trace does list them).
- **Bounds:** 3 hypotheses × 4 checks; two Sonnet calls plus three Haiku calls (summary, evidence requests, adverse media), ceiling 7; capped input and output sizes. No tool loop. The two Sonnet calls (effort medium) are each retried once if they call no tool.
- **Severity cap:** model judgement alone tops out at Medium. High needs a tool result with a typed breach flag (from a fixed list) or a registry mismatch. Name, address and person mismatches stop at medium; identifier conflicts may be high.
- **Evidence tools** (read-only, one case; every fact carries a ref id the model copies, never invents; missing data says so):
  - Reading: `list_documents`, `get_fields`, `get_business_profile`
  - Behaviour: `transaction_stats`, `query_transactions`, `top_counterparties`, `monthly_flow`, `compare_periods`, `find_repeated_transactions`, `card_usage_tool`, `recurring_payment_amounts` (patterns to test, never findings alone)
  - Agreement: `compare_identifiers`, `compare_people`, `compare_linked_people`, `company_name_consistency_tool`, `address_consistency_tool`
- **Storage:** results keep their input fingerprint. A completed run writes one `risk_scores` row and, if newest, its findings to `findings`. One running run per case and fingerprint (a unique index, `case_analysis_runs_one_running_idx`).
- **The model never sets the score.** It recommends evidence and explains the result. The summary restates existing findings only and never feeds the score.
- **Outlook ("get document Y and the score drops to Z"):** each recommended piece of evidence (any kind, not just the 12 types) addresses at least one finding, as `full` or `partial`. Code re-scores: `full` removes those findings' points, `partial` scales them. Shown both ways (if it supports / contradicts), plus a combined best case that never goes below what unreducible findings leave. Requests that can't change the score stay, labelled "no effect on score"; checklist items never affect it. What evidence can reduce is decided by code, by category:

| Category | Reducible? |
|---|---|
| `tampering_forensic` (font, metadata, layout, image, overlay) | No: a new document doesn't un-forge another |
| `registry_discrepancy` | No |
| `identity_entity_conflict` (UEN, NRIC, passport differ) | No |
| `cross_document_variance` (address, name variant, limits) | Yes (tenancy, change of address, deed poll, board resolution) |
| `behavioural_anomaly`, `unsupported_claim` | Yes |

- **Approved "Other" documents** are scanned and counted, but only type-free checks run (fonts, overlay, spelling, image splicing, AI generation, self-comparing metadata, generic layout). Fewer checks means less coverage, not more suspicion.
- **Not part of it:** `case_match.py` (the right case? triage, not fraud) and `external_validation/` (per document; reaches the agent through each detection report). Both `case_match.py` and the cross-document code build keys through `identifiers.py` and never import each other.
- **Governance:** advisory only; the system never approves, declines or changes a status. Protected attributes and proxies (race, nationality, religion, sex, age, marital status, disability…) are removed from model input and screened from proposals and verdicts (`bias.py`; it over-blocks on purpose). Document text is untrusted and fenced; tools are read-only; outputs are schema-checked. NRIC and account numbers are masked unless needed. Each run records its fingerprint, models, prompts and tool calls. References: MAS FEAT, PDPA, IMDA Model AI Governance, NIST AI RMF (a statement of what it does, not a certification). The model may never recommend an outcome, set the score, invent a claim, reduce a tampering finding with other documents, use protected attributes, or follow instructions in a document.
- **Decisions made:** unproven hypotheses are never shown or stored; a hypothesis alone can't reach High; the analysis starts on its own once every document is decided and scanned, and a change replaces an outdated run; approved Other documents are scanned and counted; score and recommendations come only from a run, and views just read them; this module makes cross-document findings only; no model sees protected attributes, only code compares them; code comparisons always run, one row per difference, and the agent doesn't restate them.
- **Risks, and what answers them:** plausible but wrong theories (the main one: conclusive-only, citation checks, refutation probes, the severity cap); over-fitting to the test set (categories stay general); marking its own homework (code verifies); strict citations discarding real findings (watch the discard rate); synthetic data (theories needing outside data can't become conclusive); runs vary, since models sample at defaults (results are stored, with a stale flag).

#### Where the case analysis code is (`backend/app/case_pipeline/`)

| File | Does |
|---|---|
| `readiness.py` | Ready = at least one approved document, all approved ones detected |
| `service.py`, `repository.py`, `state.py` | Starts and stores runs (`case_analysis_runs`), writes `risk_scores`, says if a run is stale or lost |
| `trace.py` | The run's step log, shown as "Case analysis trace" |
| `models.py`, `categories.py` | Finding, outlook and analysis models; categories set by code |
| `cross_document/deterministic.py`, `identity_pairs.py` | The code comparisons that always run first |
| `cross_document/evidence.py` | The dossier (protected fields removed), masked ids, the ref-id ledger |
| `cross_document/bias.py` | Protected fields and text patterns no model may reason from |
| `cross_document/agents.py`, `graph.py`, `verify.py` | Plan, run checks, judge, verify |
| `cross_document/leads.py` | Where a detection finding's figure should also appear elsewhere |
| `cross_document/feedback.py` | Officer feedback from other cases |
| `cross_document/tools/` | Read-only evidence tools |
| `adverse_media.py` | Web search for bad news; never scored |
| `risk/case_risk.py` | The score |
| `risk/store.py`, `risk/outlook.py`, `risk/additional_documents.py`, `risk/summary.py`, `risk/supporting_documents.py` | Score storage, what-if re-scoring, the merged evidence list, the summary, rule-based recommendations (stored on the case when the newest run completes) |

API: `POST /cases/{id}/analysis/run` (409 unless ready, or if already running), `GET /cases/{id}/analysis`. Frontend: `CaseAnalysisCard.tsx` (progress, "Run again" for a failed or missing run, summary, findings, "Checked and consistent", the what-if list, the "Out of date" notice). `app/identifiers.py` is a leaf module (key functions `alnum`, `id_key`, `uen_key`, `name_key`, `company_key`, name matchers, person extraction). `llm_io.py` reads and `llm_contracts.py` checks every model reply.

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
- **Detection (`langgraph/detection/feedback.py`).** A specialist reads it once per run, for all its tools. Each tool result then carries, appended after it, the lessons for exactly the check codes it fired (a finding without a code, and missed issues, go under the tool's name), each code once a run. It sits after the instructions' cache breakpoint; from the next turn it is cached with the rest of the conversation.
- **Codes name the rule, not the tool**, so two false alarms count as agreeing only when they are the same thing: the numeric, date, page and registry checks have their own; metadata has one per rule (`META.PDF_EDITOR`, `META.AI_TOOL`, ...); fonts, layout and the two image checks take theirs from the finding's type (`FONT.SIZE`, `LAYOUT.TEXT_OVERLAY`, `TAMPER.VISIBLE_BOUNDARY`); AI generation tells a scored image from a declared AI source. Spelling's is the word (`SPELLING.<word>`), so a word dismissed in 2 cases is shown as such, like a dictionary the agent is told about rather than one that skips the word; one that names a party to the case or a confidential detail isn't kept. Hidden text and prompt injection have none: their findings can't be dismissed.
- **Case analysis (`cross_document/feedback.py`).** Read once per analysis for the two model-decided categories, and shown to the planner and the judge. Confirmations, findings set aside wrongly and missed issues only, never false alarms: a category holds free-form hypotheses with no code to tell them apart.
- **Recorded:** each finding stores the ids of what it was shown for its code (`feedback_ids`), and the case page lists them as "Feedback the agent was shown". Being shown is not the same as acting on it.
- **Limits:** feedback only informs the model. Severity floors, impossible values, the citation ledger, `bias.py` and the score are all enforced in code, whatever it says. A lookup that fails means no feedback, never a failed run.
- **No on/off setting** (decided: too many edge cases).
- **Reset for a test:** `truncate public.finding_feedback;` in Supabase's SQL editor. To seed a known set, run `backend/sql/dev/seed_feedback_test.sql`, and afterwards `delete from public.cases where id like 'test-case-%';` (its feedback goes with the cases). `backend/scripts/preview_feedback.py` prints what an agent would be shown, with no model call.

The classifier and the extractor learn from what officers change, read from `audit_events` (`corrections_repository.py`, through `type_corrections` and `field_edit_counts` in `schema.sql`). Labels and field names only, never values, and no table of their own. A lesson counts once 2 cases agree, and only corrections after `LEARNING_SINCE` count (a constant in code, so a test can move it). A failed read gives no hint. Both hints sit after the cache breakpoint (`langgraph/correction_hints.py`). `audit_events` is never deleted, so these can't be reset like the feedback table: a test moves `LEARNING_SINCE` instead.
- **Classifier:** a retype from one type to another (`document.retyped`; confirming the same type isn't a correction) teaches "officers corrected X to Y in N cases". When the agent's guess is X it is told this, with what tells Y apart (`SIGNALS`, written by the team; without one, it just names the confusion). The rule classifier's guess gets it on the first turn, and a classify tool's guess gets it appended to that tool's result, each guess once.
- **Extractor:** `document.field_edited` carries `document_type`. A field added (a change from nothing) or deleted (a change to nothing) on a type teaches the extractor of that type to look for it, or to extract it only when the document shows one. A field that is added and deleted equally often teaches nothing. Field names that aren't plain snake_case, or that name a protected attribute, are never taught.

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

### Sensibility checks on each agent run (`app/evals/`)
At the end of each run (classification and extraction, an extraction rerun, detection, case analysis), code checks every agent's work on two things: did it take the expected path (**trajectory**), and is its answer sensible (**output**). No model call and no table: the results are one `evals` step on the run's own trace (`evals_version` on it). The trace view puts a chip on each agent's row (`✓ 5 checks`, `⚠ 1 check`, `✕ 1 check`), lists them under "Sensibility checks", and adds a "Checks" figure to the header. A **failure** means a design rule was broken; a **warning** is worth a look. The checks never change what the run decided, and an error in them is logged, never fails the run. Counter `agent_eval_results_total{agent, check, result}`; the Agents dashboard shows warning and failure rates per check. They don't measure accuracy: a run can pass every check and still miss fraud.

- **Classifier:** read before deciding; looked at the pages when there was no text (fail); vision only when needed; tool order; agrees with a certain rule match (fail); not overconfident (claimed over 0.3 above the computed confidence); answered an extraction objection.
- **Extractor:** required fields present; values fit their field (dates, amounts, pay not negative; judged by field name, names only on the trace). Second look: tool order, turns used up, changes the code rejected, failed (fail). An extraction rerun has no `extraction` step, so only its second look is checked.
- **Lead:** a specific brief for each specialist; second-look requests that name a real finding and specialist; a summary of its own.
- **Each specialist:** the checks expected for the type (Content Validation runs the numerical checks on payslips, notices of assessment and bank statements; Digital Authenticity runs the metadata check) (fail); a check of its own beyond the one the code runs for it; no repeated calls; within its tool rounds; every finding assessed; floors respected; dismissals explained.
- **Hypothesis planner:** answered through its tool; planned its own refutation probes; real tools only; nothing resting on a protected attribute (fail).
- **Hypothesis judge:** answered through its tool; conclusive verdicts the code could verify (fail when none could); kept findings span two documents (fail).
- **Summary writer** (only when the model wrote it): kept its limits; no advice to approve or decline (fail); no protected attribute (fail).
- **Evidence requests:** say what they would show; cite findings that exist. **Adverse media:** a result backed by sources.

**Rules for evals.** They only watch: they never change a score, severity, verdict, status or finding, never reach a prompt, and never block a run. No model marks another model (cost, and "code verifies" is the design rule). No golden set, no nightly evals, no scheduled token spend: evals run inside normal use and cost nothing extra per run. No personal data in metrics or the trace's check results (field names, never values). Accuracy is meant to come from officers' own verdicts on finalised cases, not from a golden set (not built yet: `CLOUD_ARCHITECTURE_PLAN.md`, "Accuracy from officer verdicts").

**What the checks can't tell you.** A run can follow the expected path, give a sensible answer and still miss fraud: the checks don't measure accuracy. Officers mostly confirm or dismiss what the system flagged and rarely see what it missed, so misses are under-measured; this is a stated limitation, along with all test data being synthetic. With no golden set, a prompt change can't be rerun on fixed cases and compared; changes are watched through the checks and the officer-verdict trend instead.

### Which agent version ran (`app/agent_versions.py`)
Each agent has a **fingerprint**: a hash of what the agent directory (`/agents`, `agent_registry.py` and `agent_prompts.py`) shows for it, meaning its prompt templates (every document type's, for the extractor, plus its second look), model, tools and call budget. Change any of them and the fingerprint changes, so a version can't be forgotten. It's computed once per process. Every document, extraction-rerun, detection and case-analysis run's end event carries the fingerprints of the agents it used (`detail.agent_versions`, about 20 characters each), so the audit trail answers "which version produced this finding?". The `agent_versions` table has one row per fingerprint, with its model, tools, prompt hash and the commit it first appeared in (`GIT_COMMIT`, set by the deploy). Each Claude-calling role adds its rows at start-up, skips any already stored, and never fails start-up over it. The agent directory shows each agent's fingerprint. Nothing reads them to decide anything. The summary writer, evidence requests and adverse media aren't in the registry, so they have no fingerprint yet.

What the sensibility checks read that the trace didn't hold before: refused tool calls (`classify_tool_refused`, `extraction_tool_refused`), the lead's `detection_review` and `fallback_briefs`, each specialist's `tool_rounds`, `wrapped_up`, `unassessed` and `floor_overruled`, the planner's `unknown_tools` and `probes_added_by_code`, and the extraction's `field_shape`.

---

## 4. How live updates work

1. A change to a `documents`, `cases` or `case_analysis_runs` row fires a Supabase Realtime event; one subscription serves the whole api process. This is how a result written by a worker in another service reaches the browser (`case_analysis_runs` must be in the `supabase_realtime` publication).
2. The backend waits **0.5 s** to merge a burst, then builds a **case snapshot**: case, documents with fields, detection states, the latest analysis (risk, additional documents, overview) and case-match. For a case with no analysis it reads only each report's verdict and its findings counted by severity (`severity_counts`, stored with the report), not the full report. Each detected document carries its counts (`detection_counts`), so the Documents list shows its anomaly numbers with the list itself; only a report stored before the counts were is read separately. When only the case row or its analysis run changed, it reuses the documents, fields and detection states of the case's last full build (neither can change them) and reads the rest; the payload is the same full snapshot either way.
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
| `langgraph_trace` | one JSON trace per document: every run's steps and spans (classification, extraction, detection, single checks); the detection report itself is in `detection_results`. For debugging and for showing field reasons on the review screen; never read to decide anything |
| `detection_results` | each document's latest detection report, or why its last run failed |
| `findings` | every finding (per document, kept and dismissed, and per case), the agents' decision, and the officer's verdict; missed issues |
| `review_sign_offs` | a group of findings an officer saved as reviewed |
| `finding_feedback` | scrubbed officer verdicts and missed issues, read counted through `feedback_counts` |
| `case_notes` | officers' notes on a case or a document, and their replies |
| `case_analysis_runs` | each run: findings (including `xd-det-N`), `consistent_checks`, outlook, summary, **risk**, **additional documents**, adverse media, audit, step trace |
| `risk_scores` (+ `latest_risk_scores` view) | one row per completed run: score, level, reasoning |
| `audit_events` | every domain event, stored by audit-metrics (id = the event's id, so a redelivery is ignored): who (officer, agent or system), what, which case or document, outcome, timing, and Kong's request id; read at `GET /audit/events` |
| Storage bucket `documents` | the uploaded files |

Deleting a document removes its fields, findings, trace and file; deleting a case removes its documents, then its runs, scores and findings. `backend/sql/schema.sql` mirrors the live schema by hand.

- **Deletes rely on cascades:** every document- or case-keyed table cascades (checked live 2026-11-16), except `finding_feedback.document_id`, which is set null. The code removes only stored files, cross-document findings on a deleted document and in-memory traces. `audit_events` is never deleted. A delete's audit event is best effort, and a failed file delete leaves the file in storage (logged). Signed URLs are made only in `routers/documents.py` (server side); a moved document keeps its storage path (it is opaque, and the file goes with the row).
- **Field writes** go only through the `replace_extracted_fields`, `add_extracted_field` and `delete_extracted_field` functions. Each locks the document row and writes only while the document is still in the status its caller read, so a field write can't interleave with an approval, a cancel or a recovery. An approval then re-checks the fields after its own write and undoes itself if they changed in between.
- **A new stored field** gets a model default that old rows read correctly.
- Supabase's advisors (2026-11-16): security lists only "RLS enabled, no policy" on every table, which is the design (secret key only, no anon grants); performance lists 4 unused indexes, all backing a cascade or the reply lookup, so they stay.

---

## 7. Configuration

Required: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `ANTHROPIC_API_KEY`, `AUTH_USERS`, `AUTH_SECRET_KEY`. With `EVENT_TRANSPORT=pubsub` (the cloud and the compose stack), `api` and `audit-metrics` refuse to start while `AUTH_SECRET_KEY` is empty.

| Variable | Default | Effect |
|---|---|---|
| `ROLE` | `all` | `all`, one role, or a comma list (`api`, `doc-worker`, `detection-worker`, `case-worker`, `audit-metrics`) |
| `EVENT_TRANSPORT` | `inprocess` | `pubsub` publishes to Pub/Sub (needs `GOOGLE_CLOUD_PROJECT`; the emulator when `PUBSUB_EMULATOR_HOST` is set) |
| `PUBSUB_TOPIC` | `domain-events` | the one topic |
| `PUSH_AUDIENCE`, `PUSH_SERVICE_ACCOUNT` | — | when set, `/push` requires Pub/Sub's OIDC token for that audience from that account |
| `EVENT_DELIVERY` | `push` | `push`: Pub/Sub posts to `/push/<role>` (Cloud Run). `pull`: each installed role streams its own subscription (`events/pull.py`, for GKE), with no `/push` route; acked when the handler returns, nacked when it raises. `none`: no events, HTTP only (GKE's audit-metrics serving `/audit` before its events move) |
| `METRICS_PORT` | 0 | when set (9100 on GKE), `/metrics` is also served on this port, apart from the app's, so the cluster's scraper needs no gateway key; the network policies let only the scraper reach it |
| `PULL_MAX_MESSAGES`, `PULL_MAX_LEASE_SECONDS`, `PULL_DRAIN_SECONDS` | 2, 3600, 20 | pull only: messages per role at once; the longest a run may hold its message (the client keeps extending it); at shutdown, how long runs in flight get before they're stopped and redelivered |
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
| `CASE_SPEND_CAP_USD` | 5 | Claude spend per case (list price, every run in the audit trail) past which no detection run or case analysis starts on it: detection saves the reason as the document's error, an officer's analysis start gets a 409, an automatic one is skipped. 0 turns it off; a spend that can't be read never stops a run (`spend_cap.py`) |
| `REQUIRE_COMPLETE_CHECKLIST` | false | whether new cases need every required checklist document approved before analysis (stored on the case at creation; on only in production) |
| `GUARD_BLOCK_INJECTION` | true | a document worded as an instruction to the AI fails at ingest; false lets it through, flagged. An officer can still let one blocked document through with "Process Anyway" (`POST /documents/{id}/process-anyway`): saved on its guard result, logged as `document.guard_overridden`, re-queued like a retry, warning kept |
| `SWEEPER_ENABLED` | false | runs the stuck-work sweeper (Terraform sets it on audit-metrics) |
| `STUCK_WAITING_SECONDS`, `STUCK_RUNNING_SECONDS`, `RECOVERY_MAX_ATTEMPTS` | 300, 600, 3 | when work counts as stuck (the sweeper, the case queue's Stuck badge and an officer's retry alike), and how often the sweeper retries |
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

Labels are routes, event types, stages, agents and models, never ids. Kong blocks `/metrics` from outside. Grafana's five dashboards (status, services, pipeline, agents, cost) read these metrics (`deploy/grafana`).

**Traces.** OpenTelemetry (`telemetry.py`) is off unless `OTEL_ENABLED=true`, which Terraform sets in the cloud. Spans go to:
1. OTLP, when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (Jaeger locally);
2. otherwise Cloud Trace, when `GOOGLE_CLOUD_PROJECT` names a real project;
3. otherwise the console.

Each event's Pub/Sub message carries `traceparent`, so one upload is one trace across services. `OTEL_TRACES_SAMPLE_RATIO` (default 1.0) samples fewer.

**Agent trace in the app** (SCRUM-59). The same spans are also written to the run's own trace, whether or not `OTEL_ENABLED` is set: a document's `langgraph_trace` row, or a case run's `trace` column (`telemetry.steps_into`). Each span is one `"span"` step, written when it ends: its `id`, `parent`, `duration_s`, its Claude calls (`llm_calls`: model, tokens, cost, seconds, stop reason, reasoning summaries, from the usage callback in `query_stats.py`), any reply that failed validation (`validation_failures`: site, agent, time, issues, the raw payload and what was used), and what `telemetry.annotate` added. Every other step names the span it happened in (`in_span`). These never go to Cloud Trace.
- **Spans:** a root per run (`langgraph.process_document`, `langgraph.rerun_extraction`, `detection.run`, `case_pipeline.run`); each agent's model turns; each specialist's whole review (`detection.review.<name>`, with its ordered `tool_calls`, `[]` when it called none); each tool call (`tool.<name>`: input, outcome; `langgraph/tool_spans.py`).
- **Saving:** a run saves its trace after its root span closes, so the root's own step is kept.
- **The view:** `components/agentTrace/` draws the tree beside the selected step's details, with a run's steps under folded headings (Classifier, Extractor, Lead agent and so on, in `buildTrace.ts`) and each step's time since the step before it in the same span. The document's own trace opens from the document page's actions menu ("Agent trace"), while the case analysis trace opens from the case actions menu when the case is on `section=analysis` ("Case analysis trace", with its earlier runs). A trace from before spans were recorded shows as a flat list of steps.

**Stuck work** (`sweeper.py`, summarised in §9 below). In the cloud, Cloud Scheduler publishes `sweep.requested` every 5 minutes and audit-metrics runs the sweeper (`SWEEPER_ENABLED`; off locally, because the database is shared).
- **What counts as stuck:** a document uploaded or queued for over 5 minutes (its event was lost), or processing, extracting or detecting for over 10 minutes (its run died); or a case analysis still running past `CASE_ANALYSIS_TIMEOUT_SECONDS`. The case queue's "Stuck" badge and an officer's retry of a `processing` document use the same limits (`monitoring.py`).
- **What happens:** the sweeper publishes `recovery.requested` to the worker that owns the stage. That worker resets the status (compare-and-swap) and reruns it. After 3 attempts it fails the document (detection: back to approved with an error) and publishes `recovery.exhausted`. A stuck case analysis is marked failed, not rerun.
- **Locally:** `python -m app.sweeper --once --case <id>` runs one pass for one case and does the recovery in the same process.
- **Cancels:** a run checks every 5 seconds for a cancel made through another instance (Pub/Sub mode only), with one small read: a document run reads its status (`cancelled`); a detection run reads the document's `cancel_requested_at`, which the cancel route must save before it publishes (a cancel whose flag can't be saved fails with a 502). The audit trail is read only when that flag can't be.
- **Approvals:** an approval whose event can't be published is undone.

Every 60 seconds a summary of Realtime and WebSocket traffic (events received, ignored, merged, connects, pushes) and of calls made outside any unit of work is also logged. Rough budgets: case list 3 queries; a snapshot 5; approve 2; delete or add a field 6–7; detection about 12.

---

## 9. What is built and what isn't

**Built:** upload and queue; the content guard; classify and extract with the reclassify loop and the second look; officer review tools; requirements checklist; case-match; the Detection Agent with 13 checks and on-demand single checks, cancel and rerun; every finding stored with the agents' decision and the officer's verdict (dismissed ones included); learning from officers; the case analysis (deterministic and agent cross-document findings, score, outlook, additional documents, adverse media, summary); live WebSocket updates; case notes; cost logging and per-case Claude cost (every run: retries, reruns, cancelled and failed runs, earlier analyses, deleted documents); the service split on Cloud Run and then GKE Autopilot (`sixsevens.me`), Kong, the compose stack, Terraform, and CI/CD that deploys every green push to `main` (summary below).

**Not built:** open product work is in `PENDING_WORK.md`, open cloud work in `CLOUD_ARCHITECTURE_PLAN.md`.

### The cloud, as built

Live in GCP project `fyp-six-sevens` (`asia-southeast1`): on Cloud Run since 2026-10-22, and on GKE Autopilot at `sixsevens.me` since 2026-11-14 (the GKE bullet below), where every consumer now takes its events. Until phase 6 the rest of this section also describes Cloud Run, which runs alongside taking none. One GCP project and one Supabase project, shared by local runs and the cloud. Operations and every problem met: `deploy/terraform/README.md`. Adding a service: `docs/ADDING_A_SERVICE.md`.

- **Roles:** one image per role, `ROLE` picks what runs. api (HTTP, uploads, WebSocket), doc-worker (OCR, classify, extract), detection-worker (lead and specialists, checks), case-worker (case analysis, score, summary, adverse media), audit-metrics (stores every event, serves the audit trail and costs, runs the sweeper). Plus Kong, the frontend and Grafana. Everything scales to zero by default. Sizes are in `deploy/terraform/roles.json`, the one role table Terraform and the Helm chart both read.
- **Services talk only through events and Supabase.** One topic, `domain-events`, with a dead-letter topic; each role has a filtered subscription (filters from `backend/app/events/types.py`), pushed to Cloud Run or pulled on GKE (`migration.json`). Messages carry ids; Supabase holds the state. Delivery is at least once, so handlers are idempotent, and each state change has one writer. An officer action that starts work fails with a 502 if its publish fails. Kong's request id and a W3C `traceparent` travel with each message, so one upload is one trace.
- **Kong** is the only entry point (`deploy/kong/`). It rate-limits login, uploads and officer actions, and adds a gateway key that api and audit-metrics require. Workers accept calls only from Pub/Sub's push identity.
- **Stuck work:** Cloud Scheduler sends a sweep every 5 minutes (`backend/app/sweeper.py`). Limits: 5 min for uploads, 10 min for processing and detection, 15 min for the case analysis. At most 3 recovery attempts in 24 h. Case analysis is never rerun automatically, because its Claude calls are the officer's call.
- **Delivery:** CI runs ruff, pyright, pytest, Playwright, Checkov, pip-audit, npm audit, gitleaks (secrets in the pushed commits only, `.gitleaks.toml` allow-lists test fixtures; blocking) and Terraform checks. The deploy rolls out the workers first, then api, Kong, the frontend and Grafana, then checks through Kong; any failure rolls every service back. Snyk runs when lock files change or by hand, and is non-blocking until its quota resets.
- **Security:** one service account per service, each secret readable only by the services that need it, keyless GitHub sign-in, non-root containers (except the frontend's nginx: `PENDING_WORK.md`).
- **Observability:** `/metrics` on every service with no document or case ids in labels; Managed Prometheus; Grafana (Status, Services, Pipeline, Agents, Cost); Cloud Trace; uptime checks and two SLOs with burn-rate alerts (below); alerts for dead letters, API 5xx over 5%, daily Claude spend and recovery given up.
- **Fixed design** (changing one touches every service): the event envelope and names, one topic with filtered subscriptions, the ownership rules, a Dockerfile target per role, Kong as the only entry, handler plus adapter.
- **Assessment criteria it addresses:** loose coupling, independent scaling, resilience, observability, secure delivery, least privilege, cost control.
- **GKE (live since 2026-11-14, `sixsevens.me`):** the same images on GKE Autopilot, behind `gke_enabled` in Terraform and `GKE_ENABLED` in the repository variables. Terraform makes the cluster (regional, private nodes), its network with Cloud NAT on one static address, `sixsevens.me` in Cloud DNS with a Google-managed certificate, Cloud Armor (enforcing), Workload Identity (each pod acts as its role's own Google account), the signing key, Binary Authorization (enforced: only images the deploy signed run) and the Cloud Deploy pipeline. The Helm chart (`deploy/helm/six-sevens`) holds everything inside the cluster: a namespace per role, Kong, the frontend and Grafana; default-deny network policies; secrets from Secret Manager as files; KEDA scaling consumers on undelivered messages, an HPA for api and Kong. Sizes are a lean profile for a team of 5-6 on free-trial credits: one replica each always on (1.6 vCPU); workers normally scale from 0 to 1 (4.85 vCPU at peak), but until the demo on 2026-11-20 they stay warm at 1, with doc- and detection-worker allowed 2 (4.1 vCPU always on, about 6.9 at peak). Set in `roles.json`'s `gke` blocks and the chart's `values.yaml`. KEDA scales a worker up on its undelivered messages, polled every 15 s, about 2–4 minutes from zero (the backlog metric lags 1–2 minutes), and back to its minimum 5 minutes after the backlog empties. Its role values are generated from `roles.json` (`deploy/helm/gen_values.py`). Consumers pull their subscription (`EVENT_DELIVERY=pull`); `deploy/terraform/migration.json` says which roles GKE runs, and Terraform switches exactly those subscriptions from push to pull in place, so only one platform takes a role's events: all four consumers are on GKE (phases 2–4), and Cloud Run runs alongside taking none until phase 6 removes it. Kong runs one nginx worker (one per node CPU would outgrow its memory), and no pod is marked `safe-to-evict: "false"` (Autopilot would give each its own node, past the trial's vCPU quota); an evicted run is nacked and redelivered. The deploy signs every image (cosign with an SBOM, plus the attestation), renders the chart by digest and releases it through Cloud Deploy, whose verify step runs the gateway checks; a failed rollout is rolled back. CI runs the chart on kind, whose network plugin enforces network policies (`.github/workflows/helm.yml`): must-pass and must-block calls, one pulled event per worker, Checkov on the rendered chart. Operations: `deploy/terraform/README.md`.
- **Running locally:** one `uvicorn` process with in-memory events (`README.md`), or the compose split. Debugging workers in the IDE and the checks before a risky change: `deploy/terraform/README.md`.

### Uptime, SLOs, recovery, load and cost per case

- **Uptime checks** (`deploy/terraform/slo.tf`, GKE only): `sixsevens.me` and `api.sixsevens.me/health` (which must answer `"status":"ok"`) from Asia-Pacific, Europe and Oregon every 5 minutes. An email when more than one region fails for 10 minutes.
- **SLOs** (Cloud Monitoring, over 28 days, on the app's own Prometheus metrics):
  - *API availability* 99%: requests to api that aren't a 5xx (`http_requests_total`, `/health` left out).
  - *Detection latency* 95%: detection runs finishing within 180 s (`stage_duration_seconds`, stage `document.detected`).
  - *Burn-rate alerts*, by email: the error budget burning 14.4× too fast over 1 hour (gone in 2 days) or 6× over 6 hours.
  - Grafana's **Status** dashboard shows the uptime checks, each SLO against its target, the budget left and the API's burn rate.
- **Disaster recovery** (`backend/scripts/dr_drill.py`, run by hand): dumps Supabase's `public` schema, restores it into a throwaway local Postgres 17 (Docker) and compares every table's row count, printing the dump, restore and total time. It reads production and writes nothing to it, and needs `SUPABASE_DB_URL`. Targets: RTO 1 hour (restore the database, then `terraform apply` and a push rebuild everything else, since the cluster holds no state); RPO as Supabase's backups give it (the plan decides: `PENDING_WORK.md`, "Database recovery target"). Stored files (Storage) aren't in the dump. *Drill (2026-11-17):* 13 tables, 985 rows, all back; dump 9.3 s (360 KB), restore 1.1 s, 10.4 s in all (pg_restore's one error, `schema "public" already exists`, is expected on an empty Postgres).
- **Load tests** (`deploy/loadtest/`): the real API, events and workers in one process, with Supabase replaced by the in-memory test database and Claude by waits as long as real runs (`stand_ins.py`); `run.py` plays officers creating cases, uploading, approving and waiting for detection and the case analysis. It costs nothing and touches no real data. KEDA's scaling isn't in it: on GKE it is shown live (Workloads page and the Pipeline dashboard).
  - *Result (2026-11-17, one process, a classify-and-extract run 20 s, detection 40 s, a case analysis 30 s):* 10 officers starting within a minute, 3 documents each (30 uploads a minute), all done in 11 minutes, no failures, every request under 0.1 s. Seconds, p50 / p95: upload → analysed 57 / 107; approve → detected 257 / 460; upload → case analysed 384 / 597.
  - *What it shows:* detection queues first, as expected. It runs 2 at a time (`DETECTION_CONCURRENCY`), so one detection-worker clears about 3 documents a minute, against 12 for one doc-worker (4 at a time). The API itself never slowed. On GKE a second detection-worker (its maximum until the demo) doubles that; a burst beyond about 6 documents a minute waits in its subscription, which is safe, since nothing times out while it waits.
- **Cost per case:** GKE cost allocation labels billing rows by namespace (`k8s-namespace`); the billing view in BigQuery carries it as `namespace`. Grafana's Cost dashboard has a "Per case" row: cloud cost per case (the billed cluster cost over analyses finished, from Prometheus), Claude cost per case, and each namespace's share. A Claude spend cap per case (`CASE_SPEND_CAP_USD`, §7) stops new runs on a case past it.

### The GKE design

| Concern | Choice | Why |
|---|---|---|
| Cluster | GKE Autopilot, regional, `asia-southeast1`, one cluster (prod) | No nodes to manage; the control plane survives a zone loss. The lean profile (one replica each) doesn't: raising `kong.replicas` and api's `gke.min` to 2 spreads them across zones and adds their disruption budgets |
| Namespaces | One per role, plus `gateway`, `frontend`, `observability` | A boundary each; dashboards key on `namespace` |
| Front door | `sixsevens.me` (the frontend), `api.sixsevens.me` (Kong), `grafana.sixsevens.me` on Cloud Load Balancing (Gateway API, global external), one Google-managed certificate (Certificate Manager, DNS authorisation) that re-encrypts to the cluster; services internal (ClusterIP) | One front door, no plain-HTTP hop; the gateway key (backend `gateway.py`) stays until phase 6 as a second guard. The API's URL is fixed at build time, so until phase 6 CI builds a second frontend image for GKE (`VITE_API_URL=https://api.sixsevens.me`) |
| WebSockets | A `GCPBackendPolicy` with `timeoutSec: 3600` on Kong's backend (the default 30 s would cut every case WebSocket); a `HealthCheckPolicy` on `/health` | Live updates through the load balancer |
| Edge protection | Cloud Armor on the load balancer: OWASP rules and per-address rate limits before traffic reaches Kong | A web application firewall; Kong keeps the per-officer limits |
| Events | Same topic; each role's subscription switched from push to pull in place (no second subscription: it would collect every message and process each twice) | Private services can't take push; removes Cloud Run's 600 s push limit |
| Scaling | KEDA on undelivered messages, 0 to max; Kong, api, frontend and audit-metrics at least 1. From zero, expect 2–4 minutes (the backlog metric lags 1–2 min, and Autopilot may add a node), close to the sweeper's 5-minute limit for queued work. Workers get a termination grace longer than a detection run but stay evictable | Idle workers cost nothing |
| Database | Supabase outside; egress through Cloud NAT on a static IP | Private nodes need NAT to reach Supabase and Anthropic. Supabase's network restrictions cover direct Postgres and pooler connections only, and the backend uses only the HTTPS APIs, so an allow-list doesn't make a leaked key useless |
| Secrets | Secret Manager through the GKE add-on, mounted as files; the chart's start command exports them as the variables the app (`config._str`) and Kong (`render.sh`) read. A rotated secret needs a restart | No secrets in Helm values |
| Identity | Workload Identity, one account per role | No keys |
| Claude | The Anthropic API, its key from Secret Manager (Vertex AI dropped 2026-11-16: it appeared not to serve the Claude 5 models in `asia-southeast1`) | One outside key, mounted only where it's used |
| Images | Artifact Registry; Trivy scans each image in the deploy (high and critical with a fix, report only); cosign signs each image and attaches its SBOM and a signed SLSA v1 provenance (which commit, workflow and run built it; a failed attestation only warns), and a Binary Authorization attestation (same Cloud KMS key) admits it: the enforcing policy checks attestors, not cosign signatures. KEDA's images are allow-listed | Only our software runs, with a parts list |
| Releases | Cloud Deploy renders the Helm chart, records each rollout, rolls back in one step; no approval step (decided: the merge to `main` is the checkpoint); its verify step runs the gateway checks | Every production change recorded and reversible |
| Monitoring | Managed Prometheus (PodMonitoring), the same Grafana (in `observability`), Cloud Logging, Cloud Trace, the GKE security posture dashboard | App metrics keep working because namespaces are named after the roles |
| Backups | The cluster holds no state; Supabase point-in-time recovery matters | No cluster backup tool |
| Environments | dev (laptop, compose or kind) and prod | No staging (decided) |

**Not used, on purpose:** Cloud SQL, Firestore, Cloud Storage and Memorystore (Supabase Postgres and Storage, and no Redis, are settled); a service mesh (network policies suffice); GKE Enterprise (one cluster); Kyverno or Gatekeeper (Autopilot covers it); Velero (no state to back up); Falco (needs privileged pods; the posture dashboard covers basics); Litmus (scripted failure tests instead).

**The Helm chart** (`deploy/helm/six-sevens`): one chart, a template per kind; per-role values generated from `deploy/terraform/roles.json`; values files for default, kind and prod.
- **Network:** default-deny per namespace; gateway → api and audit-metrics only; Kong takes traffic only from Google's front ends and health checks (`35.191.0.0/16`, `130.211.0.0/22`, also Kong's trusted IPs for the real client address); workers take no inbound traffic except the metrics scrape; egress only to DNS, Supabase, Anthropic, Pub/Sub and the registries.
- **Labels** are chosen once: Deployment selectors are immutable, so a changed label means deleting the Deployment.
- **KEDA** is installed once per cluster (a pinned `helm install`, in `deploy/terraform/README.md`), not by the chart.
- **Pods:** non-root (uid 10001; not yet the frontend's nginx), read-only root, no privilege escalation, CPU and memory limits; a disruption budget for Kong and api once they run 2 or more (with one, it would block every node drain).

**Releases on GKE:** Actions builds and pushes to Artifact Registry, signs each digest with cosign and attaches its SBOM (syft), creates the Binary Authorization attestation with the same KMS key, then creates a Cloud Deploy release that renders the chart with the image digests. Terraform owns the cloud pieces; the chart owns everything inside the cluster. Binary Authorization enforces (since 2026-11-14, after a clean dry run). Until phase 6 the same images also roll out to Cloud Run (`deploy.yml`), with the GKE job behind a repository variable.

**How the move went:** each consumer's subscription switched from push to pull in place (`migration.json`), so only one platform gets a role's messages, and rollback is switching back. Phase 1 (the cluster and front door, which also covered the planned phase 5) went live on 2026-11-14; phases 2–4 (audit-metrics, case-worker, detection-worker, doc-worker) were applied together the same day, at the team's call. Phase 6 (removing Cloud Run) is open.

### Why the cloud is built this way

**Why GKE.** Load doesn't force it (detection's p95 is 92 s against Cloud Run's 600 s push limit), so it rests on what Kubernetes shows and what a bank would run, for the Cloud Native award:

| Reason | What it shows |
|---|---|
| Portability | The same Helm chart runs on GLDB's own cluster, on-premises or in any cloud; Cloud Run is Google-only |
| Network isolation | Services are internal; only the load balancer and Kong face the internet |
| No request-time ceiling | Pull workers have no 600 s push limit, so a long case analysis can't be cut off |
| Event-driven scaling | Workers scale on undelivered messages (KEDA), down to zero |
| Supply-chain control | Only signed images from our registry are admitted |
| Operations a bank recognises | Recorded releases, namespaces per service, network policies, pod security |

**Each piece, in the bank's terms:**

| Feature | Problem it solves | Expectation it meets |
|---|---|---|
| Sensibility checks; accuracy from officer verdicts | Catches agents going off track | MAS FEAT (monitoring); TRM change management |
| Helm chart | GLDB can run it on its own platform | MAS outsourcing (exit, portability) |
| Pull workers + KEDA | No 10-minute ceiling; free when idle | TRM capacity |
| Load balancer + Cloud Armor, internal services | Nobody reaches a service directly; common web attacks stopped at the edge | TRM access control; OWASP |
| NetworkPolicies | A hijacked part can't reach the others | TRM segmentation |
| Workload Identity | No keys to leak | TRM least privilege |
| Secret Manager add-on | Secrets in one audited place | TRM; PDPA |
| Cloud NAT, static IP | All egress from one known address the bank's firewalls can allow-list | TRM network security |
| Cloud Deploy | Every release recorded and reversible; the merge to `main` is the approval | TRM change management |
| Signing, Binary Authorization, Trivy | Only our software runs, with known parts | TRM supply chain; OWASP A08 |
| Regional cluster in Singapore | Survives a zone outage; the cluster and registry stay in Singapore (the Supabase database and files are in Seoul, `ap-northeast-2`: a stated limitation) | TRM; PDPA; MAS outsourcing |

**Decisions:**
- **No staging environment** (decided 2026-11-14). Designed but not built, about 3–5 days of work: a second GCP and Supabase project with the same Terraform; schema as migrations; a frontend runtime config so one image serves both; no Anthropic key, with `langgraph/_anthropic.py` returning canned replies; `main` → staging → smoke test → the same images to production. Load tests run against the local compose stack with Claude stubbed; failure tests run on production on synthetic cases.
- **No release approval in Cloud Deploy:** a green push to `main` deploys; every release is still recorded and one step to roll back.- **The supervisor's feedback (2026-10-26):** the architecture is fine, and a handful of users doesn't justify heavier infrastructure without a measured need; let load testing drive decisions. The GKE move goes beyond that on purpose, for the award; the measured load figures stay in the write-up.

**Assumptions** (2026-11-14; we have little access to GLDB's technical side, so these stand until the bank says otherwise): GLDB's platform is unknown, so GKE stands in for the bank's own Kubernetes and the chart is what its platform team would install. The demo line: *"Production runs on Kubernetes (GKE Autopilot). We have no access to GLDB's infrastructure, so we run the same Helm chart on our own Google Cloud project; the bank's platform team would install it on theirs, on-premises or in their cloud, with no code changes."* After handover the cluster is GLDB's if it adopts the chart; otherwise it is deleted after the assessment.

**Cost** (rough; check Google's calculator before quoting): production on GKE Autopilot in the lean profile (one each of Kong, api, frontend, Grafana and audit-metrics always on, 1.6 vCPU; workers 0 to 1, 4.85 vCPU at peak, under the free trial's 8) plus NAT, load balancer and Cloud Armor is roughly $130–150 a month (about $4–5 a day), plus per-document costs and about $2–3 of image scans per push; about $1.50 a day with every pod at 0 (`deploy/terraform/README.md`). Cloud Run costs about $0 idle, cents per document. The Helm chart on kind in CI costs nothing. Unlike Cloud Run, the cluster costs money while idle; the budget alert stays.

**Risks, and what answers them:**
- Database written, publish lost: the sweeper recovers it.
- Supabase Realtime carries all live updates; untested under load.
- Kong is a single entry point: a wrong `Host` or CORS rule breaks the frontend (test with compose first).
- One environment, no staging: the post-deploy check, the phased move and rollback are the safety net; the kind test runs on every chart change.
- Shared Supabase: local and cloud see each other's data; whole-database scans (the sweeper) run only in the cloud.
- The chart drifting from what Cloud Run ran: values are generated from `roles.json` (CI checks).
- A message processed twice during a switch: handlers are idempotent; one subscription per role.
- A wrong network policy or Cloud Armor rule breaking a flow: the kind test has must-pass and must-block calls; Cloud Armor rules can run in preview first (`cloud_armor_preview`).
- A slow start from zero tripping the sweeper: keep doc-worker warm for demos.
- Nobody to run it after handover: Autopilot, Cloud Deploy, a short runbook.
- Data leaving Singapore: the cluster and registry are in Singapore, but the Supabase project is in Seoul (`ap-northeast-2`; a region can't be changed in place) and Claude calls go to the Anthropic API. Both are stated limitations; moving the database is a new Supabase project in Singapore plus a copy (`PENDING_WORK.md`).
- Too few officer verdicts to read accuracy: show the count with each figure; a person decides below 30 cases.

## 10. How it's tested

- **Backend** (`backend/tests/`, pytest): one test file per check module; `test_detection.py` (endpoints), `test_detection_tools.py` (the tool contract), `test_detection_verdict.py` (the merge), `test_output_validation.py` (`_collect_node`). Tests can't reach real Supabase or Claude: `tests/conftest.py` blocks both. Coverage below 85% fails CI (`pyproject.toml`).
- **Regression suites** (`test_regression_*.py`) test code, not AI accuracy: Claude is scripted. Their fixtures are regenerated from the code (`scripts/regenerate_regression_fixtures.py`), so they catch changes, not mistakes. Real AI behaviour is checked by a manual live run each sprint.
- **Playwright** (`frontend/e2e/`) runs against a mocked backend, so it checks what the page shows, not backend logic. Rules both sides need, such as approval, come from one shared fixture checked by the backend, the page and the mock (`approval_rules.json`), so the copies can't drift.
- **Guards that keep the suite honest:** every route but the public ones must answer 401 without a login (`test_auth.py`); every backend route is either mocked for Playwright or listed as not mocked (`mockCoverage.test.ts`).
- A known bug is pinned with `xfail(strict=True, raises=...)` (`it.fails` in Vitest, `test.fail()` in Playwright).
- **Keep in mind:** all test documents are synthetic (GLDB can't share real ones), so real fraud may look different. There are two company-name matchers on purpose: `identifiers.company_names_match` is lenient (does this document belong to the case?), `company_identity.cross_document_company_names_match` is strict (a fraud signal). Case match is triage, not a fraud signal.
- **Synthetic fraud data** (`backend/app/injection/`): `injectors.py` (layout, font, text and metadata defects), `page_defects.py`, `schemas.py`, `service.py`, `store.py`; exposed through `routers/injection.py`, mounted only in local uvicorn mode.
- **Health (2026-11-16):** backend about 3,490 tests (CI: 93.6% line coverage); Playwright 276 tests in 15 specs; Vitest 76 tests in 9 files.

## 11. Known rough edges

- `exiftool` is not installed on the dev machine, so the metadata check and the AI-provenance part of the AI-generation check report "could not run". The summary says so.
- No checkpointing, by choice: a crashed run is rerun from the start (the sweeper recovers it); a checkpointer would only keep the model calls already made. If it is ever wanted: `langgraph-checkpoint-postgres`, its own schema, no personal data in state.
- "Run again" on unchanged documents spends the tokens again; only an automatic start is skipped when the fingerprint matches.
- The first requester of a burst of snapshot requests can wait for a follow-up build.
- Known limits of the split:
  - On-demand single checks run in the api process.
  - A WebSocket ticket replayed within its 10 s on a second api instance would pass.
  - There's no shared Anthropic rate budget across instances.

  Stuck work, cancels across instances, unpublished approvals and signed tickets are handled (see "Stuck work" above).

---

## 12. Where to start reading

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
