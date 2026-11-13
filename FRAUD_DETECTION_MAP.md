# Fraud detection: implementation map

Where detection logic lives and how it's wired. Updated 2026-11-13. Behaviour in plain words: `HOW_IT_WORKS.md`; case-analysis design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

## 0. Summary

Detection runs in the background after an officer approves a document (approval locks it).

1. **Lead briefs:** an LLM writes each specialist a one-sentence brief from the document's type.
2. **Three specialists in parallel**, always all three. Each is a small LLM loop: picks its own tools, reads the results, gives each finding a severity and reason or dismisses it. Tools do the checks; the LLM never computes numbers.
3. **Lead review:** may ask one other specialist to recheck one uncertain finding, once per run.
4. **Merge (code):** one report with a verdict (`flagged`, `inconclusive`, `clean`), stored in `detection_results` and the trace.
5. **Single checks:** an officer can rerun one check after a full run.

**Findings:** every finding, kept and dismissed, is fingerprinted (`finding_keys.py`) and written to `findings` (`findings_repository.py`) before the document becomes `detected`; a failed write is noted on the trace (`findings_error`) and doesn't fail the run. **Cancel and rerun:** a run can be cancelled; a rerun that doesn't finish leaves the earlier report standing.

**"Other" documents** are scanned too: type-specific checks report "nothing to check", layout runs in generic mode.

## 1. Check modules (`backend/app/fraud_detection/`)

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

**Addresses** (`registry_lookups/addresses.py`, each checked once): HDB blocks against HDB's register; private buildings against URA approved use, then Master Plan zoning (a ~12 MB local index built into the image; without it zoning is skipped).

| Code | Meaning | Severity |
|---|---|---|
| `REG.POSTAL_CODE_EXISTS` | Postal code not in OneMap | impossible |
| `REG.ADDRESS_MATCH` | Block and street not at that postal code | inconsistent |
| `REG.RESIDENTIAL_ADDRESS` | A home that can't be lived in | inconsistent / implausible / signal |
| `REG.COMPANY_ADDRESS_RESIDENTIAL` | Company registered at a home | signal |
| `REG.UNIT_STOREY` | HDB unit above the top storey | impossible |
| `REG.UNIT_MISSING` | No or unreadable unit number | skipped, not a finding |

Addresses compare after `address_matching.normalize_address` ("Blk 50 … #01-30 S639107" = "50 TUAS AVENUE 11, Unit 01-30, Singapore 639107").

**`case_match.py`** is the upload check (is this document from this case?), not fraud. Both it and the cross-document code build keys through `identifiers.py`.

## 1B. Case pipeline (`backend/app/case_pipeline/`)

Runs on its own once every document is decided and every approved one scanned (`service.start_if_due`), or on request.

| File | Does |
|---|---|
| `readiness.py` | Ready = at least one approved document, all approved ones detected |
| `service.py`, `repository.py`, `state.py` | Starts and stores runs (`case_analysis_runs`), writes `risk_scores`, says if a run is stale or lost. One running run per case and fingerprint (unique index) |
| `trace.py` | The run's step log, shown as "Case analysis trace" |
| `models.py`, `categories.py` | Finding, outlook and analysis models; categories set by code |
| `cross_document/deterministic.py`, `identity_pairs.py` | The code comparisons that always run first |
| `cross_document/evidence.py` | The dossier (protected fields removed), masked ids, the ref-id ledger |
| `cross_document/bias.py` | Protected fields and text patterns no model may reason from |
| `cross_document/agents.py`, `graph.py`, `verify.py` | Plan, run checks, judge, verify. Two Sonnet calls (effort medium), each retried once if it calls no tool |
| `cross_document/leads.py` | Where a detection finding's figure should also appear elsewhere |
| `cross_document/feedback.py` | Officer feedback from other cases |
| `cross_document/tools/` | Read-only evidence tools |
| `adverse_media.py` | Web search for bad news; never scored |
| `risk/case_risk.py` | The score. The same check and value on several documents scores once |
| `risk/store.py`, `risk/outlook.py`, `risk/additional_documents.py`, `risk/summary.py`, `risk/supporting_documents.py` | Score storage, what-if re-scoring, the merged evidence list, the summary, rule-based recommendations |

API: `POST /cases/{id}/analysis/run` (409 unless ready or if already running), `GET /cases/{id}/analysis`.

`app/identifiers.py` is a leaf module (key functions `alnum`, `id_key`, `uen_key`, `name_key`, `company_key`, name matchers, person extraction). `llm_io.py` reads and `llm_contracts.py` checks every model reply.

## 2. Detection agent files (`backend/app/langgraph/detection/`)

- **`tools.py`:** 13 tools (hidden text, font, layout, spelling, page consistency, date, metadata, prompt injection, image manipulation, image tampering, AI generation, plus `numerical_checks_tool` and `external_validation_tool` in `field_checks/`). Each returns `{tool, unable_to_assess, reason, findings, truncated}`; up to 15 findings, ids `<tool>-<n>`, the tool's `severity_hint` kept apart from the specialist's rating. The field tools take `bindings` the specialist fills; the document type comes from run state and decides which checks run. Per-run fetch cache: the document, fields and file are fetched once and shared.
- **`specialists.py`:**
  - Layout & Rendering: hidden text (run first), font, layout, image manipulation, image tampering.
  - Content Validation: spelling, dates, pages, numerical, external (its prompt lists the fields and check menus).
  - Digital Authenticity: prompt injection (run first), metadata, AI generation.
  - Floors: a tool with a floor (`_MIN_SEVERITY_BY_TOOL`, or `severity_floor` on the finding) can't be dismissed or rated below it. A **hard breach** (`severity_hint` impossible, extraction confident) can't be dismissed or rated below `high`. An unassessed finding stays at `medium`, `ai_accepted: false`.
- **`graph.py`:** the lead, the merge, `run_detection()`. **`feedback.py`:** officer feedback appended to tool results.

### How a run goes

1. The worker claims the document as `detecting` (compare-and-swap) and holds one of `DETECTION_CONCURRENCY` (2) slots.
2. Graph (`recursion_limit=40`):

```
load_context ─► lead_plan ─(×3)─► specialist ─► lead_review ─► synthesize ─► END
                                                  │  ▲
                                                  ▼  │
                                             run_second_look   (at most once)
```

3. Error: save it, put the document back (`approved`, or `detected` if an earlier report stands). Success: save the report, store findings, mark `detected` (in that order).

- **Lead** (Sonnet, effort medium, 4,000 tokens, cached prompt): an unusable plan gives every specialist a generic brief; an unusable review gives no summary and no second look. It sees summaries and findings only, not dismissed ones. A second-look request is kept only if the finding exists and goes to a different, known specialist.
- **Specialist** (Haiku, 6,144 tokens): up to 3 tool rounds, then a wrap-up turn with no tools. Its reply is JSON (`summary`, `assessments`, `uncertain`). If it can't be read, or no tool ran, the report is marked uncertain.
- **Merge (code):** findings keyed by `(tool, id)`; duplicates take the higher severity. `flagged` = anything above `info`; `inconclusive` = nothing flagged but a check couldn't run; else `clean`.
- **Trace events:** `detection_start`, `detection_plan`, `detection_specialist`, `detection_second_look`, `detection_done`, `detection_error`, `findings_error`, `run_audit`. Spans: `detection.run`, `.lead_plan`, `.lead_review`, `.review.<name>`, `.specialist.<name>`, `tool.<name>`.
- Specialist prompts are too short for Haiku's cache minimum, so they don't cache.

## 3. API (`backend/app/routers/detection.py`)

- `GET /documents/{id}/detection/<check>`: single checks, 409 until a report exists. Numerical and external use one Haiku call to bind inputs.
- `GET …/detection/report` (200 report; 502 if the last run failed with none; 404 if never run), `POST …/detection/cancel`, `POST …/detection/retry` (409 while running or queued), `GET …/detection/case-match`, `GET /cases/{id}/detection/reports`.
- No "run detection" route: approval starts it.

## 4. Frontend

- **`DetectionPanel.tsx`:** verdict, each specialist's brief, tools and dismissals, the second look, findings, what couldn't be assessed. Cancel while scanning; "Run detection again" after a failed/cancelled run or over a report (confirmed first).
- **`CaseAnalysisCard.tsx`:** progress, "Run again" for a failed or missing run, summary, findings, "Checked and consistent", what-if list, "Out of date" notice.

## 5. Tests

One test file per check module; `test_detection.py` (endpoints), `test_detection_tools.py` (tool contract), `test_detection_verdict.py` (merge), `test_output_validation.py` (`_collect_node`). Not tested: `_normalise_plan`, `_validated_second_look`.

## 6. Known detection bugs

Found by reading the code; none reproduced. Delete a row once fixed.

| # | Sev | In plain words | Where |
|---|---|---|---|
| 1 | Low | If the lead's call fails, specialists get a generic brief and the failure is only logged, not traced | `graph.py` `_ask_lead`, `_normalise_plan` |
| 2 | Medium (local only) | A run lost mid-scan can't be cancelled or retried; in the cloud the sweeper recovers it after 15 min, locally run `python -m app.sweeper --once --case <id>` | `routers/detection.py`, `DetectionPanel.tsx` |
| 3 | Medium–High | A finding the specialist never assessed counts as `medium` and can flag the document and raise case risk | `specialists.py` `_collect_node` |
| 4 | Medium | `metadata_tool` blocks the event loop (runs a sync check in an async function) | `tools.py` `metadata_tool` |
| 5 | Medium | If a specialist calls the same tool twice, the second call's findings silently overwrite the first's (ids repeat) | `tools.py` `_with_ids`; `specialists.py` `_collect_node` |

**Design questions:** "call each tool at most once" is asked in the prompt, not enforced. A wrongly dismissed finding disappears from the lead's view and the report (the panel still lists it with the reason); check how often real documents get dismissals.

## 7. Synthetic fraud data (`backend/app/injection/`)

Builds the test data: `injectors.py` (layout, font, text, metadata defects), `page_defects.py`, `schemas.py`, `service.py`, `store.py`. Exposed through `routers/injection.py`, mounted only in local uvicorn mode.
