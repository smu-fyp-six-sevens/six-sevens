# Plan: analysis refinements

Status: plan only, nothing implemented (2026-10-18). Decisions are recorded under each item and in "Decided".

## Notes for a fresh session

- Do not run pytest. Check new tests by calling the test functions directly, and run `ruff check app` and `pyright app`.
- Keep comments to one line. Nothing is committed. No one else is editing this code.
- Evidence came from read-only queries of the dev database (the cloud app uses the same one), using `app.supabase_client`:
  - Cases are found by `cases.greenlink_case_id` ("GLDB-122"), not by id.
  - `case_analysis_runs` (`result`, `trace`) holds each analysis: risk, findings, outlook, supporting documents, and trace steps (`hypotheses`, `tool_call`, `verdict`, `verify`, `evidence_requests`, `outlook`, `scoring`).
  - `langgraph_trace` (`trace`) holds each document's steps and its `detection_report`.
  - `documents.metadata` holds the stored file metadata; `extracted_fields` holds the fields.
- Fraud checks: per document, a lead plus 3 specialists call tools (`langgraph/detection/`). Per case, the cross-document agent (`case_pipeline/cross_document/`) plans hypotheses, runs read-only evidence tools, judges them, and code verifies. Risk is scored only in a case analysis run (`case_pipeline/risk/case_risk.py`).

| # | Issue | Root cause | Size |
|---|---|---|---|
| 1 | Same anomaly counted on every document | Dedupe is only per (document, tool) | S |
| 2 | Company-name mismatch missing from the verdict | The agent dismisses a tool-flagged mismatch as "not conclusive" | M |
| 3 | Corporate bank statement read as an individual's | The schema splits the account holder into `individual_*` and `company_*` | M |
| 4 | NRIC front "inconclusive" on the address check | The specialist is not told it is a front | S |
| 5 | Lead says metadata was "stripped" when present | The metadata tool reports only anomalies | S |
| 6 | Cross-document identity matching runs per document | It sits in each document's detection run | M |
| 7 | Supporting documents inconsistent | Two producers, two cards, requests dropped silently | M |
| 8 | Credit report: example values extracted | Example blocks are printed and nothing says to ignore them | S |
| 9 | Transaction-pattern checks run per document | They are behaviour, not tampering | M |

**Order:** 4, 8, 5, then 7, then 6 with 2 and 9 (the cross-document agent), then 1, then 3 (widest change).

---

## 1. Same anomaly counted on every document

**Now** ([case_risk.py](backend/app/case_pipeline/risk/case_risk.py)): points info 0, low 1, medium 3, high 8. Per document, a check's first finding counts in full and repeats count 25%; low and medium are capped at 6 per document; high is uncapped. Nothing dedupes across documents. Critical = high findings on 3 or more documents.

**Evidence:** in 4 of the last 21 runs one finding repeats: "UEN … is not in ACRA's register", 8 points on each of 3 documents (24 points). Case `ef1402be` scored Critical 92 on 75 points ("high on 5 documents").

**Decided:** a repeat scores **0**; its row is kept and labelled.

**Change**
- After the per-document pass, group findings by `(tool, check_code, stated value)` from the stored report finding. No new id or schema change. Checked on GLDB-122: the same UEN finding sits on 3 documents under different field names (`uen`, `tax_reference_number`) with one check code `REG.UEN_EXISTS` and stated value.
- A finding with no stated value is keyed on its summary with page numbers and field labels removed, lowercased.
- The first finding keeps its points. Later ones score 0 and say "also found on N other documents".
- The Critical rule counts a document only if it has a high finding that scores points.
- Not covered: one fact reported by two tools (registry and identity). Item 6 removes the per-document identity findings.
- Roll out in shadow mode (old and new scores in the trace).

**Files:** `case_risk.py`, `CaseRiskCard.tsx`, `test_case_risk.py` (UEN on 3 documents counts once; Critical not reached by one repeated fact).

---

## 2. Company-name mismatch missing from the analysis verdict

**Now:** one model call proposes up to 3 hypotheses with checks and a refutation probe; code runs the evidence tools; one call judges; code verifies. The prompts push to discard ("Be conservative", "If unsure, it is not conclusive", a probe that restates the claim fails). Discarded hypotheses are never shown.

**Evidence** (run `ef1402be`): `company_name_consistency_tool` returned `NAME_MISMATCH` ("BRIGHTSTONE ANALYTICS" on the NOA versus "BRIGHTWAVE ANALYTICS" on 3 documents). The planner paired it with `address_consistency_tool` as the "probe". The judge said not conclusive: "the UEN matches, suggesting a name typo" and "the refutation check was not run". A third hypothesis (identifier mismatch) was dropped because the compare tool was "unavailable".

**Decided:** keep the agent in charge and **rely on the prompt**; no code backstop for now. If it still dismisses mismatches, revisit.

**Change**
1. Planner and judge prompts: a mismatch a tool flags (`NAME_MISMATCH`, `IDENTIFIER_CONFLICT`, `ADDRESS_MISMATCH`, `PERSON_MISMATCH`) is a finding on its own. Report each as its own hypothesis: a different UEN and a different company name are two findings. One matching identifier does not excuse another that differs. "Probably a typo" is not a reason to drop it. The tool result is the proof.
2. Verification (`verify_verdict`): a verdict citing such a flag needs no separate refutation probe; the flag already sets the category and severity cap (identifier high, others medium).
3. Mismatch hypotheses sit outside the cap of 3.
4. Show what was checked and found consistent in the verdict, so an empty result is not read as "not checked".
5. Only if the prompt alone fails: put the comparison tools' results in the dossier as evidence before planning. The agent still proposes, judges and writes every finding.

**Files:** `agents.py` (prompts, cap), `verify.py`, `graph.py`, `CaseAnalysisCard.tsx`; tests (name and UEN reported separately; a matching UEN does not dismiss a name mismatch).

---

## 3. Corporate bank statement read as an individual's

**Now:** `bank_statement` has `company_name`, `individual_name`, `individual_address` and no company address; the prose says a corporate statement leaves `individual_*` null. The URA check ([addresses.py](backend/app/fraud_detection/external_validation/registry_lookups/addresses.py)) treats `individual_address` as residential and expects homes there.

**Evidence:** 5 distinct corporate statements (Powerline, BrightWave, Harbourline, Golden Harvest and others; 8 rows of 26) have `company_name` and `individual_address`, for example `1 RAFFLES PLACE #32-01`. A report on `BankStmt_BA` says "URA has no approval for residential use for #03-05 at 21 MEDIA CIRCLE (zoned Business Park), the unit Individual Address gives … inconsistent with a personal bank account holder's residential address": the specialist assumed a personal account.

**Decided:** only two holder kinds exist for now, **individual and corporate**; no `unknown` value.

**Change:** replace the split with `entity_name`, `entity_address` and `entity_type` (`individual` or `corporate`).
- Applies to single-holder types: `bank_statement`, `source_of_wealth` (statements), `list_of_loans` (borrower), `proof_of_address`, and "Other". Not payslip (employee and employer), NRIC, passport, credit report (always individual) or company documents (always company).
- Code cross-checks `entity_type` against the name ("PTE LTD", "LTD", "LLP", "BHD", "INC" mean corporate). A conflict lowers confidence so the officer reviews it.
- Consumers: the URA check uses residential rules for `individual` and company rules for `corporate`; proof-of-address coverage counts only `individual`; case matching reads `entity_name` by type.
- Blast radius (files that mention the old fields): `individual_name` 7 app and 14 tests, `individual_address` 8 and 9, `company_name` 13 and 21 (frontend 9, mostly the case's own company name), `company_address` 6 and 5.
- Safe rollout: an adapter `holder(fields)` reads `entity_*` first and the old fields otherwise, so stored documents keep working; extraction writes only `entity_*`; move consumers over one at a time; then delete the old fields; regenerate `required_fields.json` and the regression fixtures.

**Files:** `extraction_prompts.py`, `extract.py`, a new helper, `addresses.py`, `mapper.py`, `case_match.py`, `cross_document/address_matching.py`, `models.py`, fixtures, tests.

**Verified: `mapper.py` and `case_match.py` do not count a corporate statement's address as a person's proof of address.** `models.counts_as_address_proof` accepts a bank statement only if `is_personal_bank_statement` (an `individual_name` and no `company_name`); both modules call it. In the 14 stored statements with an `individual_address`, 8 are corporate (`company_name` set, no `individual_name`) and are ignored; the 6 personal ones are counted. Remaining risk: a corporate statement where the model fills `individual_name` and leaves `company_name` empty would count. When the fields become `entity_*`, switch `is_personal_bank_statement` to `entity_type == individual`, in the same change as the extraction prompt.

---

## 4. NRIC front "inconclusive" on the address check

**Now:** `external_validation_tool` runs live registry checks and static tables by document type. For an NRIC only the address check applies; with no address, `check_addresses` skips `REG.ADDRESS` ("No address with a Singapore postal code extracted"). That reason is not a "nothing to check" pattern, so it counts as failed and the verdict is "inconclusive" (reproduced). The extracted field `document_side` ("front"/"back") exists, but the specialist sees field names and types, not values.

**Decided:** the **specialist** decides which tool to call, not the lead. Leave the lead's prompt as it is.

**Change**
1. Put `document_side` (and which fields are present) in the specialist's per-document prompt: "This is the front of an NRIC: name, number, date of birth. The address is on the back."
2. Add a specialist prompt rule: skip a tool when this document has nothing for it, and say so in the summary. Not calling a tool is not a failure and does not make the verdict inconclusive.
3. Safety net: if the external tool runs on a document with no address field, the address check returns "Nothing to check: no address field", a pass. An NRIC back with no address still reports "unable".

**Files:** `specialists.py` (prompt and context), `addresses.py`, `detection/tools.py` (the `^Nothing to check:` pattern is listed twice); tests (the side reaches the specialist prompt; a front returns "nothing to check"; a back without an address is still "unable").

**Status: implemented, untested.** The side is added to the lead's context and the specialist prompt; the lead may now say a check does not apply; the NRIC-front safety net lives in `registry_lookups/runner.py`. The specialist's first turn is still forced to call a tool (`tool_choice="any"`), so it chooses which tool, and the safety net turns the external check on a front into a pass.

---

## 5. Lead says metadata was "stripped" when it is present

**Now:** metadata is read once at upload by `exiftool` and stored on the document. `metadata_tool` analyses it and returns **only anomalies**; with none, it says nothing about what it saw.

**Evidence:** document `56ce9f94` (`BusinessProfile_BA_20260831.pdf`, cloud): stored metadata has an Author, Producer (ReportLab) and CreateDate = ModifyDate; the check returns no anomalies. The specialist wrote "no embedded authorship, creation/modification timestamps, or producing-software signatures" and the lead said "stripped of authorship and timestamp information". In 18 of the last 98 reports the digital-authenticity specialist claims metadata is missing while the stored record has author or producer and dates. Separately, on local Windows runs exiftool is missing (59 of 80 "unable" reports); that does not explain the cloud case.

**Change**
1. `metadata_tool` returns what it observed even with no anomalies: producer, creator, author, created and modified, whether they are equal, which fields are present or absent, and one sentence ("Metadata present: author, producer ReportLab; created equals modified; no anomalies").
2. Prompt rule for the specialist and lead: never say metadata is missing or stripped unless the tool reports it absent.
3. Show the observed metadata in the report.
4. Local fixes: a startup warning when exiftool is missing, a PyMuPDF fallback, and re-reading the file when the stored record is a failure.

**Files:** `detection/tools.py`, `specialists.py` and `graph.py` prompts, `metadata_extractor.py`; tests. **Open:** you said the "metadata anomaly" panel shows something; for this document the check returns none, so tell me what the panel shows.

---

## 6. Move cross-document identity matching to the cross-document agent

`case_match.py` has two parts:

| Part | What it does | Moves? |
|---|---|---|
| **A. Reference check** (`_verify_incumbency`, `_match_document`) | Compares each document's company and people to the certificate of incumbency, to check the upload belongs to this case | **Stays** |
| **B. Cross-document identity check** (`_apply_pairwise_person_checks`, `_unique_person_pairs`, `_pairwise_person_findings`) | Links the same person across two documents and reports a different ID or name | **Moves** |

**Now (B):** `synthesize_node` ([graph.py](backend/app/langgraph/detection/graph.py)) calls it per document with `include_identity_mismatches=True`; `add_case_match_findings` writes each mismatch into that document's report as a `case_identity_match` finding (high for an ID, medium for a name), as a pseudo-specialist "Identity Matching". Each pair's mismatch is written to **both** documents, so it scores twice. A failure turns "clean" into "inconclusive". `add_case_match_findings` also writes part A's mismatches into detection findings.

**Decided:** part A mismatches show as **manual review** and are not copied into detection findings. Format checks (NRIC checksum, UEN format) and the ACRA lookups stay in content validation.

**What manual review looks like today (verified):** a document whose company (UEN or name) or a person does not match the incumbency gets status `manual_review`, `case_mismatch = true`, `can_proceed_to_fraud_detection = false`, with specific reasons ("UEN … does not match the certificate of incumbency's UEN …"). The document page shows "Sent to manual review" with each reason; the list shows a "Case mismatch" tag. Test: `test_document_with_other_uen_requires_manual_review`. Part A only escalates once the incumbency is corroborated by at least `MIN_CORROBORATING_DOCUMENTS` other documents; before that it shows "pending". Leave that.

**Change**
- Move part B into `case_pipeline/cross_document/` (for example `identity_pairs.py`), exposed through the existing `compare_people` evidence tool. One finding per pair. It follows the item 2 prompt rules.
- Remove from detection: `add_case_match_findings`, the identity branch in `synthesize_node`, the pseudo-specialist, and `include_identity_mismatches` from `case_service.get_case_match_for_document` and `check_case_match`.
- Old stored reports keep `case_identity_match` findings; `read_report` keeps them for display and scoring ignores them when a cross-document finding covers the same fact.
- **Guardrails:** part A code is untouched; add a test that another UEN still gives `manual_review`, `case_mismatch`, not allowed to proceed, with the reason text, before and after; part B never changes a document's live case-match status (already tested by `test_pairwise_identity_mismatches_are_not_in_live_case_match`). Optional: a lead line "This document does not match the case details" above the reasons (no older generic sentence exists in the history).

**Files:** `case_match.py`, `case_service.py`, `detection/graph.py`, `cross_document/` (new module, `tools/comparisons.py`), `categories.py`, `flags_repository.py`; tests `test_company_identity.py`, `test_detection_case_match.py`, `test_regression_detection.py`, `test_case_risk.py`, `test_case_match.py`.

---

## 7. Supporting documents: one place, always shown

**Now:** two producers. The rule list (`recommend_supporting_documents`: corporate bank statements and company financial statements) is stored on the run and shown in the "Recommended Supporting Documents" card below the analysis card, with no score effect. The model list (`propose_requests`) is re-scored into an outlook shown inside the analysis card as "What would change the score". The model is asked only when some finding is reducible by more evidence; code drops a request that addresses an unknown or non-reducible finding.

**Evidence:**

| | GLDB-121 (High 65) | GLDB-122 (Critical 92) |
|---|---|---|
| Scoring findings | 9, all non-reducible (registry, tampering) | 19, one reducible (`xd-h2`) |
| Rule list | 1: bank loan statements | empty |
| Model requests | none (never asked) | 2 proposed, 1 kept |
| Shown | rule card only, below the score, no score change | outlook in the analysis card, 92 to 91 |

In 121 the score cannot change by design but the UI never says so. In 122 the model proposed a "UEN Registration Confirmation Letter" and referred to findings by document id prefixes (`60172ada`) instead of real ids (`60172ada:external_validation_tool-1`), so code dropped it silently as "no such finding".

**Decided:** one card, always shown, with the overall score.

**Change**
- One `additional_documents` list on the run, built in code: `title`, `why`, `source` (checklist or finding), `addresses`, `affects_score`, and `score_if_supports` and `level_if_supports` when true.
- Keep model requests that cannot change the score, labelled "no effect on score" (for example the UEN letter). Rule items are also labelled "no effect on score".
- Match finding ids by unambiguous prefix and tell the model to copy ids exactly.
- Merge and dedupe by normalised title and document type.
- States: not analysed; none needed; could not generate (reason and retry). For a case like 121: "No document can change this score: every finding is a tampering or registry result", and still list the rule item.
- Best-case line only when some item affects the score.
- Remove `SupportingDocumentsCard` and the outlook section of `CaseAnalysisCard`; keep reading the old fields for one release. Record why a list is empty (`no_reducible_findings`, `model_failed`, `budget`).

**Files:** `case_pipeline/service.py`, `models.py`, `risk/outlook.py`, `risk/supporting_documents.py`, `case_service.py`, `UploadSummaryCards.tsx`, new `AdditionalDocumentsCard.tsx`, the Playwright mock and fixtures; tests (121-like: only non-reducible findings; 122-like: prefix ids, one scoring and one non-scoring).

---

## 8. Credit report: example values extracted as the applicant's

**Now:** the `credit_report` schema has `bureau_score`; the model also adds `risk_grade` and `probability_of_default`. The guidance covers accounts, defaults and bankruptcy only.

**Evidence** (`C001_CreditReport.pdf`): the Bureau Score section prints no applicant score. It has only "Example 1: Scored: Score 1420, Risk Grade GG, Probability of Default 5.14%" and "Example 2: Non-Scored". The model extracted Example 1 as the applicant's; its own low-confidence reasons say "may be illustrative". The figures are printed, so `_drop_unprinted_numbers` does not catch them.

**Change**
1. Prompt: Example blocks are not the applicant's; return `bureau_score`, `risk_grade` and `probability_of_default` only when printed for the applicant outside an Example block.
2. A general line for every type: sample, example, specimen or template values are not this document's data.
3. Code guard `_drop_example_values` in `extract.py`: an Example block runs from a line "Example N:" until "For more details" or "Explanation of". Collect tokens (figures, two-letter grades) printed only inside Example blocks, and drop a top-level scalar whose value is one of them. Record `dropped_example_values` in the trace (`nodes.py`).
4. Tests on the C001 text; one live extraction call to confirm.

---

## 9. Transaction-pattern checks belong in the cross-document agent

**Now:** `numerical_checks_tool` runs these bank-statement checks per document ([statements.py](backend/app/fraud_detection/numerical_validation/statements.py)):

| Payment behaviour (move) | Document's own arithmetic and statistics (stay) |
|---|---|
| `STAT.DUPLICATES`, `BS.CARD_DIVERSITY`, `BS.RECURRING_VARIANCE` | `BS.RUNNING_BALANCE`, `BS.OPEN_CLOSE`, `BS.TOTALS`, `BS.OVERDRAFT_LIMIT`, `BS.AVAILABLE_VS_LEDGER`, `BS.NARRATIVE_AMOUNT`, `BS.FEE_SCHEDULE`, `STAT.ROUND_NUMBERS`, `STAT.LAST_TWO_DIGITS`, `STAT.BENFORD_1D` |

The right column is proof the numbers were edited, or statistics of one statement's own amounts that no other document can inform (round-number, last-two-digit and Benford tests apply only to bank statements, so they stay). The left column describes how money moves, where other documents (a lease, a loan schedule, another statement) can explain or contradict it.

**Evidence** (a report on a corporate statement): "Transaction 25 repeats the same payee and amount as transaction 13", "Transaction 38 repeats … as transaction 25" (medium, medium), and "5 distinct masked card numbers across only 5 card transactions" (low). Findings 1 and 3 are separate because `STAT.DUPLICATES` emits one finding per repeat, comparing each transaction with the previous occurrence, so one pattern (the same payee and amount three times: 13, 25, 38) becomes two findings.

**Decided:** these checks are cross-document **hypotheses**, not per-document checks. Benford, round-number and last-two-digit tests stay per document.

**Change**
- Remove the left-column checks from the per-document numerical group (stop registering them for `bank_statement`).
- Reuse the same logic as new read-only evidence tools in `cross_document/tools/transactions.py`, each returning computed facts with ref ids, listed in `COMPUTED_TOOLS` so a finding may rest on one document: `find_repeated_transactions` (one group per payee and amount, with every transaction number and the count, which fixes the chained findings), `card_usage`, `recurring_payments`.
- Planner and judge prompts: a pattern is a hypothesis to test, not a finding (a repeated payment may be a real standing order); weigh it against the rest of the case. Severity is capped at medium without a hard breach flag.
- Reuse the existing check functions; do not copy them.
- Scoring effect: these findings stop scoring per document (3 points each) and appear, if the agent keeps them, as capped cross-document findings.

**Files:** `numerical_validation/statements.py` and `registry.py`, `cross_document/tools/transactions.py` and `tools/__init__.py`, `agents.py` (tool catalogue, prompts), `numerical_validation/runner.py` (`render_check_roles` menu). **Caution:** `conftest.py` maps all of `test_numerical_validation.py` to Sprint 3 regression tests FD-009 and FD-010, so moving checks means updating those tests and that mapping, and `detection-report-c028.json` if it contains one of these findings.

---

## Cross-cutting

- **Flags:** `FF_RISK_DEDUPE` (shadow mode), `FF_CROSS_DOC_IDENTITY` (2, 6, 9), `FF_ADDITIONAL_DOCUMENTS_CARD` (7), `FF_ENTITY_FIELDS` (3).
- **Stored data:** bump `schema_version` when a report or analysis shape changes and keep old shapes readable (`read_report`).
- **Tests to update:** regression fixtures (`detection-report-c028.json`, `required_fields.json`, `anchorpoint.json`), the Playwright mock for the unified card, the regression detection tests, and the Sprint 3 mapping in `conftest.py`.
- **Drift:** the Playwright mock re-implements the API by hand, so each shape change needs the mock and fixtures updated, ideally from recorded responses.
- **Dependencies:** 2, 6 and 9 share the agent's prompts and tools, so do them together. 1 is simpler after 6. 3 last.

## Decided

| Item | Decision |
|---|---|
| 1 | A repeat scores 0, row kept and labelled; key is `(tool, check_code, stated)` |
| 2 | Rely on the prompt for now; no code backstop; the agent flags UEN and name mismatches separately |
| 3 | Two holder kinds only (individual, corporate); `entity_name`, `entity_address`, `entity_type`; no `unknown` |
| 4 | The specialist decides which tool to call; the lead's prompt is unchanged |
| 6 | Part A mismatches show as manual review and are not detection findings; part B moves to the cross-document agent |
| 7 | One always-shown card with the overall score; model requests with no score effect are kept and labelled |
| 9 | Duplicates, card diversity and recurring variance move to the cross-document agent as hypotheses; Benford, round-number and last-two-digit tests stay |

## Not verified

- What the "metadata anomaly" panel shows for the linked document (item 5).
