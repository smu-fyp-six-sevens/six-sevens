# Regression Test Plan — Sprint 1 & 2

Covers SCRUM-6, 8, 9, 17, 19, 20 (Sprint 1 stories, completed in Sprint 2 where noted),
78, 83, 96, 109, 114, 118. Built from `regression_feedback.md`, checked against the code.
Where the AC and the code disagree, the code is treated as the truth and the conflict is flagged.

Nothing here has been implemented yet. Part 6 lists the decisions needed before any test is written.

---

## Part 0 — Answers to your questions

**1. DI-015: what toast is it checking? There are no toasts.**
There is a toast. It is a custom in-page banner, not a library. `UploadPage.tsx:1048-1066`
renders a `div.toast.all-ok` (or `.mixed`) after an upload finishes, with the text
"N of M documents uploaded. All documents uploaded successfully." The test checks that text.
Nothing is wrong with DI-015.
- feedback: ok, this test is ok.
**2. DI-others: does the unrecognised document really start blank?**
Yes. The code does this on purpose: `DocumentDetailsPage.tsx:977-982` says an unconfirmed
"unrecognised" document starts blank, so picking "Other" is an explicit decision. The disabled
placeholder reads "Unrecognised — select a type…" (line 1450). The Confirm button stays disabled
until a choice is made. The test is correct; I would tag it DI-others as you suggested.
- feedback: ok, this test is ok.

**3. Stuck threshold: did we change it to 10 minutes?**
Only partly. There are two different thresholds in `config.py`:
- `STUCK_THRESHOLD_SECONDS = 10` (line 62). This is the "stuck" flag used by the monitoring
  dashboard and the case status. It is **10 seconds**, which matches SCRUM-17's AC literally.
- `STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS = 600` (line 67). This is **10 minutes**. It only decides
  when an officer may manually retry a PROCESSING document (`monitoring.py:27-40`,
  `routers/documents.py:945`). It is not the stuck flag.

So the AC is not outdated for the stuck flag. Please tell me which one you meant before I write the
test. I plan to test both values separately.

-feedback: ok i see. lets test only the second one for now. i think the STUCK_THRESHOLD_SECONDS is outdated and will need to be reworked later. 

**4. REG-002 and REG-003: why are these defects, and can they be fixed?**
These are two tests that expect to fail. `test.fail` means the test passes while the bug exists. As
soon as someone fixes the bug, the test fails, which tells them to remove the marker.

- **REG-003 (clean credit report can't be approved).** The required-field check treats an empty
  list as missing (`case_service.missing_required_fields`). A credit report with no defaults has
  `defaults: []`, which is the normal, correct value. The frontend has the same problem:
  `isEmptyField` in `DocumentDetailsPage.tsx:1859` turns `[]` into an empty string and counts it as
  empty. **Fixable.** The fix is to treat `[]` as present for fields where an empty list is a valid
  answer. The schema needs a way to say which fields those are. Both the backend check and the
  frontend check need the same rule.

- **REG-002 (Save fields blocked by a legitimately empty field).** `handleSaveFields`
  (`DocumentDetailsPage.tsx:1202-1206`) checks every extracted field, not only the ones flagged
  for review. So a field such as "Company Former Name", which is correctly blank with confidence 1.0,
  blocks the save. **Fixable.** Limit the check to the fields the officer is being asked to fix. The
  code change is small, but we should decide what a blank, unflagged field saves as (empty string or
  null).

I checked both against the code today. Both are still live, so neither test is stale.

-feedback: ok i totally understand now. leave it

**5. Low-confidence but correct type on first extraction: generic or type-specific prompt?**
**Type-specific.** If the classifier names a real type, even with low confidence, extraction runs
against that type's own schema. The generic prompt is used only when the type is unnamed or
"unrecognised" (`nodes.py:142-146`, `record_classification_result`). This does **not** match
SCRUM-19 AC2 as written, which says "unrecognised **or** below threshold → generic schema". Code
wins, so I will test the type-specific behaviour and flag the AC.

-feedback: ok this is not the correct behaviour. seems like somewhere someone messed up. lets put this as a `test.fail` as well. when it is low confidence on first extract, it should go to generic, not type specific. code here is actually not correct

Then, on the re-type endpoint (`routers/documents.py:843-848`):
- **Officer confirms the same type** → no re-extraction. Only the classification columns change
  and confidence goes to 1.0. **Confirmed in code.** This matches your "if second" case.
- **Officer picks a different type** → extraction re-runs once against the new schema. This is the
  "if first" case.

Both will get a test.
-feedback: keep in mind what i said above

**6. SCRUM-17 AC3: "invocation retried 3 times, then failed"?**
**Not implemented.** The queue does not retry (`queue_service.py:25`, "this layer doesn't retry").
There is no retry-count setting anywhere in `config.py` or the LangGraph code. The only retries are
the Anthropic SDK's own HTTP retries, which are per Claude request, not per workflow run. When the
pipeline hits an unexpected error, the document is marked FAILED straight away, and it can only be
retried by an officer. Code wins, so I will test that FAILED behaviour and mark AC3's automatic
retries as not built.
-feedback: i agree. i think that, auto retry doesnt make sense. lets keep this as a test for retry.

**7. SCRUM-19 AC7: exhausted retry → finalised as "Other" or sent to manual review?**
**Conflict.** When the single reclassify attempt is used up, `mark_unresolved_escalation_node`
(`nodes.py:291-305`) sets the document to **FAILED** with a failure reason. It does not finalise it
as "Other" and does not route it to manual review. Code wins. I will test FAILED with the reason and
flag AC7.
-feedback: i dont think this is correct. from what i see, when i upload an unrecognised document, the reclassify triggers, but the document is not failed. it is classified as unrecognised, and the user has to confirm its type (which in this case is "others")

**8. SCRUM-8 AC3 / SCRUM-19 AC2: "below threshold → shall not send to analysis"?**
The wording conflicts, but the behaviour is coherent. A below-threshold document **does** run
extraction (`nodes.py:142`, "Extraction always runs after this"). It is marked MANUAL_REVIEW and
cannot be approved until its type is confirmed (`routers/documents.py:725`). Detection never runs
until approval, so nothing reaches "analysis" automatically. I read "analysis" as detection, which
is correct. I will test that detection is never started for a below-threshold document.
-feedback: yes, analysis is detection. you dont need to test detection is never started for a below-threshold document, just test that the approve button doesnt work. 

**9. SCRUM-9 AC5: a missing field "is displayed as '-'"?**
The code shows **"—" (em dash)** (`DocumentDetailsPage.tsx:1753`), not "-" (hyphen). DI-021 already
asserts the em dash. Code wins. I suggest we update the AC wording. The approval logic also treats
a hyphen as empty (line 1862), so both characters are handled.
-feedback: this is minor no problem

**10. SCRUM-20 AC1: flag fields that "do not meet the expected format"?**
**Not implemented in the approval gate.** `missing_required_fields` checks only presence and
emptiness. It does not check format (e.g. UEN or NRIC pattern). The format checksums exist in the
fraud-detection checks, which are out of scope here. I will test presence only and flag the format
part of AC1.
-feedback: ignore ac here. dont test format


**11. Can fields, classification and documents be edited after approval or rejection?**
**No, and the code enforces it in two places.**
- Frontend: `isLocked = isApproved || rejected` (`DocumentDetailsPage.tsx:860`). When locked, the
  field editor, "+ Add field", delete buttons and the type dropdown are all hidden
  (`canEditFields`, `canCorrectType`, lines 961-972).
- Backend: `_ensure_unlocked` (`routers/documents.py:53-57`) returns an error on field edits,
  re-typing and moves for APPROVED or REJECTED documents. Line 438, 554, 644 and 850 call it.

Both get tests. The backend tests should check the status code and that nothing is written.
-feedback: actually this is not a part of the logic from sprint 1 and sprint 2. lets defer this

**12. Is there any external call to Supabase or Claude in the test suite?**
Mostly no, but the guard has one real hole. Details are in Part 1.

**13. SCRUM-96 100-character limit: is it enforced?**
Yes. `validateCompanyName` (`caseValidation.ts:8-9`) rejects anything longer than
`MAX_COMPANY_NAME_LENGTH = 100` with "Company name must be 100 characters or fewer." The input also
has `maxLength={100}` (`CreateCasePage.tsx:107`). A test can check that exactly 100 characters is
accepted and 101 gives the error. I will test the error message, because `maxLength` does not stop a
value set by script.
-feedback: correct


**14. SCRUM-109: are the top-3 tools implemented?**
Yes. `TOP_K = 3` in `rule_classify.py:402` and `llm_classify.py:18`. `_normalise` makes the
confidences sum to 1.0 exactly, by absorbing rounding drift into the top candidate. Two things the
AC does not cover: the rule path gives "matched wording" as reasoning, and the LLM path's reasoning
field needs checking before I write its test. Also, zero candidates returns an empty list, so the
sum is 0. I will test the three-candidate, sum-to-1 and reasoning cases.
-feedback: correct

**15. What do we defer?**
You asked to defer e2e tests for delete case and the detection modules (cancel/retry), since they
were built this sprint. I will follow that. Reject Document is not in that list, so it needs your
decision (see Part 6).

---

## Part 1 — Global rule: no external calls to Supabase or Claude

### What is blocked today (`backend/tests/conftest.py`)

| Target | Guard | Where | Status |
|---|---|---|---|
| Claude | Patches `ChatAnthropic._generate/_agenerate/_stream/_astream` to raise. Clears `ANTHROPIC_API_KEY` and points the base URL at `anthropic.invalid`. | `_block_real_ai_calls` (autouse, line 79) | Good. No direct `anthropic.Anthropic(...)` client exists in `app/`. |
| Supabase publishable client, table calls | Replaces `supabase_client.table` with a raising stub. | `_block_real_supabase_network` (autouse, line 104) | Good. |
| Supabase publishable client, storage | Replaces `supabase_client._storage` with a raising stub. | same | Good. |
| Frontend API | All `${API_URL}/**` routed through `mockBackend`; anything unmocked returns status 599. API host is `http://api.test`, which cannot resolve. | `e2e/support/mockBackend.ts:167, 377`, `playwright.config.ts:39` | Good. |
| Frontend WebSocket | Only `/ws/cases/` is routed. | `mockBackend.ts:168` | Acceptable. Any other socket would fail to connect rather than reach Supabase. |

### The hole

`app/supabase_client.py` has two more clients that bypass the guard:

1. **`service_supabase_client()`** (secret key, line 44). **Every DELETE** goes through it:
   `case_repository.py:86`, `document_repository.py:301` and `:325`,
   `extracted_fields_repository.py:165` and `:174`. The guard does not cover it, so a delete in a test
   that forgets to mock its repository would send a **real DELETE to Supabase** using the secret key
   from the local `.env`.
2. **`build_async_supabase_client()`** (secret key, line 69). Used only by `realtime_listener.py:88`.
   Not guarded. It opens a real Realtime connection if the app lifespan runs in a test.

**Current state:** every repository test I checked patches `service_supabase_client` in its own
module (for example `test_case_repository.py:174`), so no existing test is making a real delete. The
protection is manual, one patch per test. Nothing stops the next test from forgetting it.

**Fix (small):** add `service_supabase_client` and `build_async_supabase_client` to the autouse
guard in `conftest.py`, in the same way as the publishable client. Then add a short canary test that
checks the guard is active. The canary would fail if someone removed the guard, which keeps the
protection honest.

### Other observations
- `backend/scripts/capture_regression_fixture.py` calls real Supabase on purpose. It is a manual
  script, not in the test paths, and no test imports it. That is correct as long as it stays that
  way. The canary should also check that no test imports `scripts.`.

---

## Part 2 — Sprint 1

### SCRUM-6 Upload (add the 4 gap tests you agreed to)

Tag: `@sprint1 @SCRUM-6`. ID: `DI-others`, with a short suffix per case.

| # | Test | Layer | Expected behaviour (from `fileValidation.ts`) |
|---|---|---|---|
| 1 | Empty (0-byte) file is rejected | e2e | Row shows "Rejected"; text "File is empty (0 bytes)."; no upload request sent |
| 2 | Unsafe filename is rejected | e2e | Name that sanitises to nothing → "Filename is invalid or unsafe." |
| 3 | Same file uploaded twice is rejected | e2e | Second copy → "This exact file has already been uploaded for this case."; checked per case |
| 4 | More than 20 files is refused | e2e | `checkFileCount` message; list is capped at 20 |

Test 3 needs the first upload to finish, so the hash is saved. Test 4 needs 21 small files built
in the test helper.

### SCRUM-8 Classification (regression, backend)

| AC | Covered by | Status |
|---|---|---|
| AC1 correct type and confidence recorded | `test_each_clean_file_is_recorded_with_its_type_confidence_and_routing` (parametrised) | Covered |
| AC2 no match → Unrecognised + manual review | `test_an_unrecognised_file_is_routed_to_manual_review` | Covered |
| AC3 below threshold → manual review, not analysis | `test_a_confident_type_below_the_threshold_is_kept_but_sent_to_manual_review` | **Partial.** Does not assert that detection is never launched. Add that assertion. |
| AC4 at or above threshold → accepted | Parametrised test above | Covered |
| AC5 type, confidence and routing saved | Parametrised test above | Covered |

Threshold values: classification 0.85, extraction 0.90 (`config.py:79, 84`). Add a boundary test at
exactly 0.85 (should be accepted, since the rule is "meets or exceeds").

### SCRUM-9 Extraction (regression, backend)

| AC | Covered by | Status |
|---|---|---|
| AC1 type-specific fields saved | `test_each_document_type_is_extracted_against_its_own_fields` (also in Sprint 2 block) | Covered |
| AC2 unrecognised → generic fields | `test_an_unrecognised_document_still_proceeds_to_extraction` and `..._is_still_extracted_without_being_blocked` | Covered |
| AC3 OCR for images and scans | `test_images_and_scanned_pdfs_are_read_and_extracted` | Covered |
| AC4 per-field and overall confidence saved | `test_document_confidence_is_the_average_of_its_field_confidences` | Partial. Per-field values are not asserted as saved. Add one. |
| AC5 missing field saved as null | None | **Gap.** Add a test: a required field the model cannot find is stored as null. |
| AC5 missing field shown as "—" | DI-021 (e2e) | Covered, with the em-dash conflict noted in Part 0 #9. |

---

## Part 3 — Sprint 2 (includes SCRUM-17, 19 and 20, which completed in Sprint 2)

### SCRUM-17 Auto-trigger classification (tag `@sprint2`)

| AC | Covered by | Status |
|---|---|---|
| AC1 stored → queued | Unit test tagged MA-001 | Covered |
| AC2 dequeued → classifier within 1 second, status processing | `test_a_queued_document_is_handed_to_the_classifier_within_one_second` | Covered |
| AC3 3 automatic retries, then FAILED | None | **Not implemented** (Part 0 #6). Test the current behaviour: pipeline error → FAILED with reason, and the manual retry is then allowed. Flag AC3. |
| AC4 stuck after 10 s with no update | Unit tests only | Add a regression test for the 10-second flag. Add a second for the 10-minute retry eligibility, once you have decided which the AC means. |

### SCRUM-19 Auto-trigger extraction and reclassification (tag `@sprint2`)

| AC | Covered by | Status |
|---|---|---|
| AC1 confident type → extraction with type schema | `test_extraction_starts_automatically_once_classification_is_recorded` | Covered |
| AC2 unrecognised → generic schema | `test_an_unrecognised_document_still_proceeds_to_extraction` | Covered for unrecognised. **Below-threshold named type uses its own schema** (Part 0 #5). Test that, and flag AC2. |
| AC3 generic extraction suggests a type → reclassify once | None found by name | **Gap.** Add a test where the escalation comes from a generic (unrecognised) extraction. |
| AC4 extraction disagrees with type → reclassify with reason | `test_extraction_disagreeing_with_the_type_sends_it_back_for_reclassification` | Covered |
| AC5 at most one re-check | None | **Gap.** Add a test where the second disagreement does not trigger a third attempt (`MAX_RECLASSIFICATION_ATTEMPTS = 1`). |
| AC6 corrected schema re-runs and proceeds | Partial | **Gap.** Add a test where re-classification changes the type and extraction runs again with the new schema. |
| AC7 exhausted → "Other" or manual review | None | **Conflict** (Part 0 #7). Test FAILED with reason. Flag AC7. |
| AC8 both attempts and the reason are retrievable | Not checked | **To verify.** Check the trace (`langgraph/trace.py`) before writing the test. |

**Officer confirms a type (your "low-confidence but recognised" flow):**
- Confirm the same type → no extraction re-run; status and fields untouched. **Gap, add.**
- Pick a different real type → extraction re-runs once with the new schema. **Gap, add (backend), plus one e2e test.**
- The e2e test "unrecognised → Other" (untagged) stays as it is, tagged DI-others.

### SCRUM-20 Validate extracted data and manual correction (tag `@sprint2`)

Already covered: DI-023, DI-024, DI-025, DI-026 (e2e and backend), and the two expected-failure
tests. Gaps to add:

| # | Test | Layer |
|---|---|---|
| 1 | Add a custom field, then save it | e2e |
| 2 | Delete a custom field | e2e |
| 3 | Edit an array field through manual review (gap 8 from the earlier list) | e2e |
| 4 | After approval: no edit, no type change, no add or delete (buttons hidden) | e2e |
| 5 | After rejection: same as #4 | e2e |
| 6 | Backend: field edit, re-type and add/delete on an APPROVED or REJECTED document return an error and write nothing | backend |
| 7 | Format validation (AC1) | **Not built.** Flag it and do not write a test. |

### SCRUM-78 View cases: gap tests (you said yes)

Five status chips exist (`CaseQueuePage.tsx:22-26`). CM-004 tests only two. Add one test for each
missing chip: **Documents failed**, **Processing stuck**, **Documents pending**. Also add a fixture
for each status, since no fixture sets `documents_status` to `failed` or `stuck` today. Confirm the
mapping of "Documents pending" to its status value before writing the test.

### SCRUM-96 Create case

- Add: exactly 100 characters is accepted.
- Add: 101 characters shows "Company name must be 100 characters or fewer."

### SCRUM-109 Top-3 classifier tools (backend, new)

- Returns exactly 3 candidates when there are at least 3 matching types.
- Confidences sum to 1.0 (exact, after rounding).
- Each candidate carries reasoning (rule path: matched wording; LLM path: confirm the field first).
- Fewer than 3 matches: returns what exists, still summing to 1.0.
- Zero matches: returns an empty list. Not in the AC, so this test documents current behaviour.

### SCRUM-114 / SCRUM-118 — the two hints (you said these are crucial)

- **Unmatched proof-of-address names** (`UploadPage.tsx:1720`): one proof of address whose name does
  not match any identified person → the hint lists that name.
- **NRIC missing side** (`UploadPage.tsx:1685`): a single identity with only the front uploaded →
  the hint reads "front uploaded, back missing" (and the reverse for back only).

Both are e2e tests against the existing checklist fixtures, with the document list changed in the
test.

---

## Part 4 — Known-defect tests (keep, do not fix in this pass)

| Test | Defect | Fix needed | Test change when fixed |
|---|---|---|---|
| REG-003 (`test_regression_approval.py`, `document-review.spec.ts`) | Empty list counted as missing | Per-field "empty is allowed" flag in the schema; apply in backend and frontend | Remove `xfail` / `test.fail` |
| REG-002 (`document-review.spec.ts`) | Save checks all fields, not only flagged ones | Limit `hasEmptyField` to flagged fields | Remove `test.fail` |

Both tests fail loudly once fixed, so they will not be forgotten.

---

## Part 5 — Test count estimate

| Area | New tests |
|---|---|
| Part 1 guard hardening + canary | 1 (backend) |
| SCRUM-6 gaps | 4 (e2e) |
| SCRUM-8 / 9 gaps | ~3 (backend) |
| SCRUM-17 | ~3 (backend) |
| SCRUM-19 incl. confirm-type flows | ~6 (backend) + 1 (e2e) |
| SCRUM-20 gaps | ~6 (e2e, mostly) + 2 (backend) |
| SCRUM-78 chips | 3 (e2e) |
| SCRUM-96 | 2 (e2e) |
| SCRUM-109 | ~4 (backend) |
| Hints (NRIC, proof of address) | 3-4 (e2e) |
| **Total (approx.)** | **~40** |

Deferred (not counted): delete case e2e, detection cancel/retry e2e, and Reject Document (pending
your decision).

---

## Part 6 — Decisions needed before writing tests

1. **Stuck threshold.** Is the 10-second flag right, the 10-minute retry gate right, or both? (Part 0 #3)
-feedback: as said above

2. **SCRUM-17 AC3 (3 automatic retries).** Drop it from the AC, or build it? I will test the current behaviour either way.
-feedback: as said above

3. **SCRUM-19 AC7 (exhausted → Other or manual review).** Keep FAILED, or change the code to match the AC?
-feedback: as said above
4. **SCRUM-19 AC2 (below threshold → generic schema).** Keep the type-specific schema (current code), or change the code to match the AC?
-feedback: as said above

5. **SCRUM-9 AC5 wording.** Update the AC from "-" to "—", or change the code?
-feedback: dont change code. ignore the ac wording its fine.
6. **REG-002 and REG-003.** Fix now, or leave as tracked known defects for this sprint review?
-feedback: yes leave it.
7. **Reject Document e2e.** Add it now, or defer with delete case and detection?
-feedback: good question. dont do it now. it is a part of another story done in this sprint
8. **Tagging.** SCRUM-17/19/20 tagged `sprint2` as you asked. OK?
-feedback: yes

## Part 7 — Order of work (once decisions are in)

1. Guard hardening in `conftest.py` and the canary test (Part 1). Do this first, so every later
   test runs with the stronger guard.
2. Sprint 1 gap tests (Part 2).
3. Backend regression tests for SCRUM-8, 9, 17, 19 and 109.
4. Frontend e2e tests for SCRUM-20 editability, custom fields and the hints.
5. Remaining e2e gaps for SCRUM-78 and SCRUM-96.
6. Run the full backend suite and the full Playwright suite after each group, not only at the end.

## Part 8 — Updates after feedback (supersedes the matching parts above)

**Corrections to my earlier answers**
- **SCRUM-19 AC7 (feedback #7).** FAILED is set only when a *typed* extraction disagrees with its
  type a second time (`mark_unresolved_escalation_node`, `nodes.py:291`). An unrecognised document
  with no suggested type raises no escalation (`extraction_prompts.py:722-727`), so it ends ANALYZED,
  routed to MANUAL_REVIEW, waiting for the officer to confirm its type. My Part 0 #7 was too broad.

**Scope changes**
- **Stuck threshold (Part 0 #3, feedback).** Test only the 10-minute manual retry gate
  (`STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS`). Leave the 10-second flag untested; it will be reworked later.
- **SCRUM-17 AC3 (feedback).** No automatic retry. Test manual retry only: a FAILED document can be
  re-queued by an officer.
- **Below-threshold documents (Part 0 #8, feedback).** No detection test. Test only that Approve is
  blocked until the type is confirmed (`routers/documents.py:725-731`, 400 "Confirm the document type
  first."), in both the backend and the UI.
- **SCRUM-19 AC7 (Part 3).** Replace the planned FAILED test with: an unrecognised document after one
  re-check stays ANALYZED, routed to manual review, and needs officer confirmation. This passes today.
- **SCRUM-9 AC5 wording (feedback).** No code change, no AC change.
- **SCRUM-20 AC1 format (feedback).** Do not test format.
- **Editability after approval/rejection (Part 0 #11, feedback).** Deferred. Not Sprint 1 or 2 logic.
  Remove from Part 3 (items 4–6 of the SCRUM-20 table).
- **Reject Document e2e (feedback).** Not now. Belongs to another story done this sprint.
- **REG-002 / REG-003 (feedback).** Leave as known defects. No fix this pass.

**New known-defect tests (`test.fail`, desired behaviour, code not changed)**
- **REG-004: low-confidence first extraction uses the generic schema.** Feedback #5 says the desired
  behaviour is: a low-confidence named type on first extraction goes to the generic prompt. Code uses
  the named type's schema (`nodes.py:142-146`). Test asserts generic; marked `test.fail`.
- **REG-005: confirming the classifier's type re-runs extraction when the first pass was generic.**
  Follows from REG-004. Under the current code, confirming the same type skips re-extraction
  (`routers/documents.py:843-848`), which is only correct because the first pass was typed. Once REG-004
  is fixed, confirming a type must re-run extraction against that type's schema. Test asserts the
  re-run; marked `test.fail`.

  *Please confirm this reading of "keep in mind what I said above" before I write REG-005.*

**Still to do, unchanged:** conftest guard hardening and canary (Part 1), SCRUM-6 four gap tests
(Part 2), SCRUM-8/9/17/19/109 backend tests, SCRUM-78 chips, SCRUM-96 length, NRIC and proof-of-address
hints (Part 3).
