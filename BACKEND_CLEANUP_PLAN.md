# Backend dependency/redundancy cleanup (post-merge)

## Progress (updated during execution, before a session compact)

**Steps 1-5: done and verified** (compiled + smoke-tested with mocked repos via standalone
scripts, not the full `pytest` suite per your instruction to stop running it every step):
- **Step 1**: `routers/detection.py`'s dead `update_metadata_anomalies` call removed, its stale
  docstring fixed, the commented-out `date_anomalies` scaffolding deleted from
  `tests/test_detection.py`. Confirmed via `pytest -q`: 791 passed (was 1 failed/790 passed
  before this step — this was the one step verified against the real suite, since it was
  specifically needed to un-break it).
- **Step 2**: `IN_FLIGHT_STATUSES` added to `models.py`, replacing the three independent
  `{UPLOADED, QUEUED, PROCESSING, EXTRACTING}` definitions in `monitoring.py`,
  `routers/documents.py`, `routers/detection.py`.
- **Step 3**: `case_status.py` gained `_fetch_case_and_documents` (shared fetch),
  `get_case_match_for_case`, `get_case_match_for_document`. `routers/cases.py` and
  `routers/detection.py`'s `get_case_match` endpoints now call these instead of duplicating the
  assembly; `build_case_snapshot` uses the same shared fetch. Verified with a standalone mocked
  smoke script (5 cases: happy path, case-not-found, document-append-when-missing,
  document-not-found, document-with-no-case-id) — all passed.
- **Step 4**: new `routers/_dependencies.py` (`get_document_or_404`, `get_case_or_404`).
  Converted every repeated fetch-or-404 block: 9 endpoints in `routers/documents.py`, 2 in
  `routers/cases.py`, 8 in `routers/detection.py` (also fixed a real redundant double-fetch in
  `detection.py` along the way — `_fetch_document_bytes` used to re-fetch the document a second
  time even in endpoints that had already fetched it themselves; replaced with
  `_download_document_bytes(document)` taking the already-fetched record).
  `routers/documents.py` went 796 → 690 lines. Verified with a `TestClient` smoke script against
  the real FastAPI app (mocked repos): 404, 200, 404-no-storage-path, 502-on-fetch-error,
  409-approved-delete, 404-missing-case — all passed.
- **Step 5**: `app/langgraph/detection/tools.py`'s five tool bodies (`font_consistency_tool`,
  `spelling_tool`, `date_consistency_tool`, `transaction_balance_tool`, `metadata_tool`) replaced
  with stubs returning `unable_to_assess=True, reason="Not yet correctly implemented — pending
  rewire"`. Kept exactly the helpers you said not to delete (`_document`, `_extracted_fields`,
  `_file_bytes`, `_result`, `_with_ids`, `_short`, `_field_value`, `_number`, `_parse_date`);
  removed the now-fully-dead detection-specific constants/helpers (`_EDITING_SOFTWARE`,
  `_SOFTWARE_KEYS`, `_EXIF_DATE`, `_exif_datetime`, `_DATE_ORDER_PAIRS`, `_FUTURE_ONLY_FIELDS`,
  `_BALANCE_TOLERANCE`) since nothing references them even for the rewire (the old logic is in
  git history). Verified: both detection graphs + all 3 specialist graphs still build, and a
  stub tool invoked directly returns the expected pending-rewire result.

**Step 6: done and verified.** Moved 7 single-importer models out of `models.py` into their
owning router: `UploadResponse`/`BulkUploadItemResult`/`BulkUploadResponse` →
`routers/upload.py`; `DocumentDownloadResponse` → `routers/documents.py`;
`ExtractionTestPage`/`ExtractionTestResponse` → `routers/extraction.py`; `MonitoringIssues` →
`routers/monitoring.py`. Confirmed via grep beforehand that no other file in `app/` or `tests/`
imports any of these from `..models`. Verified via `python -m py_compile` and `python -m
pyflakes` across all Step 1-6 touched files (`models.py`, all 5 routers, `_dependencies.py`,
`case_status.py`, `monitoring.py`, `langgraph/detection/tools.py`) — clean, no errors, no
unused/missing imports.

**Step 7: deliberately deferred, not started.** Per your instruction: don't touch
`tests/test_orchestrator.py`/`tests/test_dify_client.py` yet — they'll be reworked together with
the rest of the test suite after Phase 2 lands, since Phase 2's call-site changes would just
conflict with test edits made now. Revisit Step 7 after Phase 2.

**Also done this session, outside the numbered steps:** `BACKEND_CLEANUP_PLAN.md` (this file)
was written to the repo root at your request, mirroring the approved plan file at
`C:\Users\LENOVO\.claude\plans\velvet-mapping-mango.md`.

**Not yet done:** a full `pytest -q` run to confirm Steps 2-6 together didn't regress anything
(only Step 1 was checked against the real suite; Steps 2-6 were checked with standalone smoke
scripts / compile+pyflakes per your instruction not to run the full suite each step). Deferred
until Phase 2 is further along, since Step 7 (which touches the test suite directly) is also
waiting on Phase 2.

### Phase 2 progress

**Design correction from what's written below:** the "extracted_fields folds under
`document_repository.py`" part of the original Phase 2 design (see the "Documents domain"
paragraph further down) was tried and then explicitly rejected by you — "documents and extracted
fields are 2 separate tables... i think its best to keep them separate." `extracted_fields_repository.py`
stays its own directly-importable module; only `document_repository.py` itself is the documents-domain
owner. This was reverted cleanly (confirmed via `git diff` showing zero change on
`document_repository.py`, `langgraph/nodes.py`, `langgraph/pipeline.py`, `langgraph/detection/graph.py`
after the revert) before the cases-domain work below was done.

**Cases domain: done and verified.** `case_status.py` is now the sole importer of `case_repository.py`
(confirmed via grep — every remaining `case_repository.` reference in `app/` is inside
`case_status.py` itself). Added to `case_status.py`: `get_case` (thin passthrough), `create_case`
(thin passthrough), `update_case` (thin passthrough), `delete_case` (owns the
"delete every document first, then the case row" cascade that used to live inline in the
`routers/cases.py` endpoint). Callers migrated: `routers/cases.py` (`create_case`/`update_case`/
`delete_case` endpoints now call `case_status.*` instead of `case_repository.*` directly, and no
longer imports `case_repository`/`document_repository` at all — only `case_status`),
`routers/upload.py` (its pre-upload case-existence check now calls `case_status.get_case`),
`routers/_dependencies.py` (`get_case_or_404` now calls `case_status.get_case`). Verified: full
compile+pyflakes sweep of all touched files (clean), plus a standalone mocked smoke script
covering the `get_case`/`create_case`/`update_case` proxies, the `delete_case` cascade
(list_documents → remove_document per doc → case_repository.delete_case, all called in the right
order), the `POST /cases` endpoint end-to-end through a real `TestClient` (with a minted auth
token), and `get_case_or_404` returning 404 through the new `case_status.get_case` path — all
passed.

**Documents domain — repository-import boundary: already compliant, no work needed.**
`langgraph/detection/`, `langgraph/nodes.py`+`pipeline.py`, `routers/detection.py`, and
`routers/documents.py` all already only call `document_repository`/`extracted_fields_repository`
directly (both legitimate owning modules per the corrected design above) — there's no lower-level
"raw" module underneath either one that anything is reaching past, unlike the
`case_repository`/`case_status` gap that existed for cases. Nothing to migrate here.

**`sync_submission_finalized` consolidation: done and verified.** This was the one genuine
remaining piece of Phase 2. Before: 9 separate inline call sites (`nodes.py` x2, `pipeline.py` x1,
`routers/documents.py` x6), each remembering to call `case_status.sync_submission_finalized`
right after a write, with **inconsistent error handling** across them (`delete_document` swallowed
failures and logged; `update_extracted_fields`'s call sat outside its try/except entirely, so a
sync failure there would've escaped as a raw 500; `add_extracted_field`/`delete_extracted_field`
wrapped it inside their try, turning a sync failure into their normal 502). Asked you how to
resolve the inconsistency rather than picking silently — you agreed with the recommendation:
**always swallow and log**, since `submission_finalized` is a derived cache (`get_case_summary`/
`build_summary` already recompute coverage live from documents on every real read; they never
trust the cached flag), so a failed resync is self-healing on the next successful document write,
not worth failing the caller's request over.

Implementation: `document_repository.py` gained a private `_sync_case(case_id)` helper (swallows
and logs, never raises) and now calls `case_status.sync_submission_finalized` from inside its own
write functions — `record_classification`, `update_extracted_fields`, and `approve_document`
(unconditionally, using the `case_id` off the row Supabase's `.update().execute()` already returns
— no extra fetch needed), `update_status` (**only** when the new status is `ANALYZED` — the other
transitions it handles, queued/processing/extracting/failed, never synced before either, and don't
change requirement coverage, so this deliberately doesn't add new syncs there), and
`remove_document` (using `document.case_id` directly, since the caller already has the full
record). This does introduce a new import: `document_repository.py` now imports `case_status.py`
(previously the dependency only ran the other way) — verified this circular import resolves
cleanly regardless of which module a caller imports first (both use `from . import x` module-level
imports, not `from .x import y`, so neither needs the other fully initialized at import time; the
`_sync_case` implementation only touches `case_status` from inside a function body, never at
module load). All 9 original call sites removed from `nodes.py`, `pipeline.py`, and
`routers/documents.py`, along with the now-unused `case_status` import in each (confirmed via grep
— none of the three had any other use of `case_status`). `nodes.record_classification_result` also
lost its now-dead `case_id` parameter (it only existed to feed the removed sync call); its one
caller (`finalise_classification_node`) updated to match.

Verified: full compile+pyflakes sweep (clean), a live import-order check in both directions
confirming the new circular import resolves, and a standalone mocked smoke script covering all 5
write functions — `record_classification` syncs when the row has a `case_id` and skips cleanly
when it doesn't, `update_status` syncs only on the `ANALYZED` transition and not on `QUEUED`,
`update_extracted_fields`/`approve_document`/`remove_document` all sync correctly, and `_sync_case`
itself swallows a `sync_submission_finalized` exception without propagating it.

**Known, accepted gap:** `tests/test_documents.py` has 9 assertions
(`mock_case_status.sync_submission_finalized.assert_called_once_with(...)` /
`assert_not_called()`) written against the old inline call sites — these will now fail since
`routers/documents.py` no longer imports `case_status` at all. Per your standing instruction, the
test suite isn't being run/fixed step-by-step; this is folded into Step 7's test-suite rework
(deferred until after Phase 2, so it isn't done twice).

## Context

`langgraph-test` was just merged into `main` (commit `a058605`). While it was diverged, `main`
also picked up independent work (numerical-anomaly detection, layout-anomaly rewrite, PDF
metadata handling). The merge itself, plus organic growth on both sides, left concrete tangles
and duplication behind. Earlier this session we already fixed one class of these (CI/pyproject
dependency drift, stale test imports of deleted modules). This plan covers what's left.

Explicitly **not** in scope: the 5-service microservice split, Kong, Prometheus/Grafana (all
discussed and deliberately deferred earlier).

This plan has two phases. **Phase 1** is the concrete, mechanical cleanup below — safe,
independently verifiable, done in this pass. **Phase 2** is the bigger move: giving each domain
(documents, cases) one owning module that's the only thing allowed to import its repository,
instead of `document_repository`/`case_repository`/`extracted_fields_repository` being called
directly from 8-13 files each (~90+ call sites total). Phase 2 is real work, not a rename — it's
documented here (design + migration order) but is its own separate execution pass, started only
after Phase 1 lands and is verified. Trying to do both at once would make it impossible to tell
which change broke something.

Also newly in scope: `app/langgraph/detection/tools.py`'s tool implementations are confirmed
incorrect (your call, not something I found) and need to be pulled out now, with correct
versions rewired in later. See Phase 1 Step 5 (revised).

## Findings (verified by reading the actual files, not just grep)

1. **Confirmed dead-code bug, now actually breaking the suite**: `routers/detection.py:278-283`
   (`get_metadata_anomaly`) calls `document_repository.update_metadata_anomalies(...)`, which
   does not exist — deleted by an earlier commit (`a6bcbe2` "removed metadata writing to
   database") that didn't update this call site. Verified via grep: the method is genuinely gone
   from `document_repository.py`. Raises `AttributeError` on every real call, silently swallowed
   by a broad `except Exception` — the endpoint still returns a correct result, so it went
   unnoticed at runtime. It's no longer silent in tests, though: a just-pulled commit
   (`8ecee11` "missed out a test for stop metadata anomaly from storing into db") updated
   `test_detection.py` to assert this call is *never* made (anticipating this exact fix), but the
   source fix itself wasn't included — so `pytest` is currently red:
   `test_get_metadata_anomaly_success_persists_result` fails (confirmed by running the suite:
   1 failed, 790 passed). Step 1 below is now required to get back to green, not just cleanup.
   Same commit also added a chunk of commented-out scaffolding for a not-yet-built
   `date_anomalies` module in `test_detection.py` (confirmed `app/fraud_detection/date_anomalies.py`
   doesn't exist) — inert, not breaking anything, but worth deleting alongside Step 1 while
   already in that file rather than leaving commented-out dead code sitting there.

2. **The same "in-flight" status set is independently defined 3 times**:
   `monitoring.py:9-14` (`_STUCK_ELIGIBLE_STATUSES`), `routers/documents.py:34-39`
   (`_TYPE_OVERRIDE_BLOCKED_STATUSES`), `routers/detection.py:141-146`
   (`_DETECTION_BLOCKED_STATUSES`) — all three are exactly
   `{UPLOADED, QUEUED, PROCESSING, EXTRACTING}` ("the pipeline is still actively running on this
   document"). Independently maintained, easy to silently drift if a status is ever added.

3. **Case-match assembly ("fetch case + its documents, run `check_case_match`") is
   independently implemented 3 times**: `case_status.py:161-184` (`build_case_snapshot`, used
   by the WS push path), `routers/cases.py:119-142` (`get_case_match` endpoint),
   `routers/detection.py:288-326` (`get_case_match` endpoint — the most fragile version: fetches
   the document first, derives the case from it, manually appends the document back into the
   list if missing, then does `next()` to pull just that one document's match back out).

4. **Fetch-or-404 boilerplate is copy-pasted, not shared**: the same ~10-line
   "await get_document → except → 502 → if None → 404" block appears **11 times** in
   `routers/documents.py` alone (e.g. lines 61-77, 113-124, 167-178, 480-496, 527-543,
   594-604...), plus equivalent repeats in `routers/cases.py` and `routers/detection.py` for
   case fetches. Pure control-flow duplication, not a design question.

5. **`app/langgraph/detection/tools.py`'s tool implementations are incorrect** (confirmed by
   you, not a code-reading finding). All five tools (`font_consistency_tool`, `spelling_tool`,
   `date_consistency_tool`, `metadata_tool`, `transaction_balance_tool`) need their logic pulled
   out now and rewired correctly later, in a separate pass. Worth noting for whenever that
   rewire happens: `transaction_balance_tool` was also a from-scratch reimplementation of
   `app/fraud_detection/numerical_validation.py`'s `_check_running_balance` (which already does
   the same reconciliation as part of a richer check — holdings/balance-sheet/share-allocation/
   Benford's Law) — the two tolerance constants had already drifted apart
   (`_MONEY_TOLERANCE = 0.011` vs. `_BALANCE_TOLERANCE = 0.01`), exactly the kind of bug
   duplicate logic invites. The rewire is a good place to reuse `numerical_validation.py`
   instead of reimplementing it a second time.

6. **`models.py` (448 lines) holds several single-importer models** that belong with their one
   caller instead of the shared domain module: `ExtractionTestPage`/`ExtractionTestResponse`
   (only `routers/extraction.py`), `BulkUploadItemResult`/`BulkUploadResponse`/`UploadResponse`
   (only `routers/upload.py`), `DocumentDownloadResponse` (only `routers/documents.py`),
   `MonitoringIssues` (only `routers/monitoring.py`). `app/injection/schemas.py` and
   `app/fraud_detection/*.py` already keep their own Pydantic models out of `models.py` — this
   just extends that existing pattern to the two places it was missed.

7. **Merge-artifact test files, currently in limbo**: `tests/test_orchestrator.py` and
   `tests/test_dify_client.py` test `app.orchestrator`/`app.dify_client` (both deleted earlier
   this session) — they came back during the merge because `main` had touched them after the
   branches diverged, so git kept the modified version over the deletion. Currently sidelined
   via `collect_ignore` in `conftest.py` at your request ("might be helpful reference"). This
   plan proposes actually resolving that limbo, not leaving it indefinitely.

**Also verified as non-findings** (checked, not duplicated): `compute_fulfilled_requirements`,
document-type validity checking, and the stuck-document status set (`monitoring.is_stuck`,
correctly reused by `case_status.py`) — single-sourced already. `queue_service.py` is the
cleanest module in the codebase (touches exactly one repo, hands off through exactly one
pipeline entry point) — it's the template the fixes below move other call sites toward, not
something needing its own fix. `config.py`'s 27 settings are all genuinely used — breadth, not
dead-code sprawl.

## Plan

**Step 1 — dead-code bug fix, required to un-break the suite** (`routers/detection.py`)
Delete the `update_metadata_anomalies` call and its now-stale "AC6: persists its result"
docstring paragraph in `get_metadata_anomaly`. One function, no behaviour change for real
callers (the endpoint already silently no-ops the write today) — but this is what
`test_get_metadata_anomaly_success_persists_result` in `test_detection.py` is currently failing
on after the commit pulled just now, so this step goes first, before anything else. While in
that file, also delete the commented-out `date_anomalies` scaffolding from the same commit
(inert dead code, not a behaviour change either).

**Step 2 — one shared "in-flight" status set**
Add `IN_FLIGHT_STATUSES = {UPLOADED, QUEUED, PROCESSING, EXTRACTING}` next to `DocumentStatus`
in `models.py` (it's a document-status concept, belongs with the enum). Replace the three
independent definitions in `monitoring.py`, `routers/documents.py`, `routers/detection.py` with
imports of it. Leave the *other* status checks alone (`case_status.py`'s FAILED/ANALYZED
terminal rollup, `documents.py`'s `status != ANALYZED` approve/reject/retry gates) — those are a
different concept (terminal vs. in-flight) and conflating them would be a behaviour change, not
a cleanup.

**Step 3 — one shared case-match assembly helper**
Add a function to `case_status.py` (which already owns "case + its documents" assembly via
`build_case_snapshot`) — e.g. `get_case_match_for_case(case_id) -> CaseMatchResult | None` and
`get_case_match_for_document(document_id) -> DocumentCaseMatch | None`. Reuses
`case_match.check_case_match` (already imported by all three current call sites) as the actual
check. `routers/cases.py`'s and `routers/detection.py`'s `get_case_match` endpoints become thin
wrappers; `case_status.build_case_snapshot` calls the same helper instead of duplicating the
fetch. This directly removes `routers/detection.py`'s fragile manual-append/`next()` logic.

**Step 4 — fetch-or-404 as FastAPI dependencies**
Add `get_document_or_404` and `get_case_or_404` dependency functions (natural home:
`routers/documents.py`/`routers/cases.py`, or a small shared `routers/_dependencies.py` if both
routers end up needing both — decide once the extraction is done and it's clear how much is
actually shared). Convert the repeated inline blocks in `routers/documents.py`,
`routers/cases.py`, and `routers/detection.py` to `document: DocumentRecord =
Depends(get_document_or_404)` (or case equivalent). This is the single biggest line-count
reduction here (~150+ lines) and the lowest-risk change (pure control-flow extraction, no logic
change).

**Step 5 — pull out `tools.py`'s incorrect tool implementations**
Replace the body of each of the five `@tool`-decorated functions in
`app/langgraph/detection/tools.py` (`font_consistency_tool`, `spelling_tool`,
`date_consistency_tool`, `metadata_tool`, `transaction_balance_tool`) with a stub that returns
`{"unable_to_assess": True, "reason": "Not yet correctly implemented — pending rewire", ...}` —
keep the function signatures, `@tool` decorators, and docstrings (so `specialists.py`'s
`SPECIALISTS` dict, the graph-compile tests, and the live endpoints/panel all keep working
exactly as you asked, just honestly reporting "can't assess" instead of silently returning wrong
findings). Keep the small private helpers at the top of the file (`_document`, `_extracted_fields`,
`_file_bytes`, `_result`, `_with_ids`, `_short`, `_field_value`, `_number`, `_parse_date`) — you
said not to delete anything else, and they're plumbing likely to be reused by the rewire, not
part of what's wrong. Remove the now-dead detection-logic-specific helpers/constants each stub
no longer calls (e.g. `_EDITING_SOFTWARE`, `_DATE_FORMATS`, the Layout/Content/Digital-Authenticity
regexes) only if they'd otherwise sit unused — leave them if you'd rather they stay as reference
for the rewire; your call when we get there.

**Step 6 — relocate single-importer models out of `models.py`**
Move `ExtractionTestPage`/`ExtractionTestResponse` into `routers/extraction.py`,
`BulkUploadItemResult`/`BulkUploadResponse`/`UploadResponse` into `routers/upload.py`,
`DocumentDownloadResponse` into `routers/documents.py`, `MonitoringIssues` into
`routers/monitoring.py`. Mechanical: move the class, fix the one import in each owning file,
confirm nothing else imports it from `models` (already confirmed single-importer via grep).

**Step 7 — resolve the `test_orchestrator.py`/`test_dify_client.py` limbo**
Read both files, port any genuinely-uncovered assertions onto the langgraph equivalents
(`tests/test_langgraph.py` or a new `tests/test_nodes.py` for `app/langgraph/nodes.py`'s
`record_classification_result`/`persist_extracted_fields`/`fail_document` — these don't have
direct unit coverage today, only indirect coverage via the graph-shape/integration tests).
Delete both old files and the `collect_ignore` workaround in `conftest.py` once done.

## Phase 1 verification

- `python -m pytest -q` after each step (not just at the end) — the suite is fast (~25-45s) and
  every step here is either pure extraction (dependency functions, shared helpers) or a
  same-shape logic swap, so it should stay green throughout; a red step means that step
  introduced a real behaviour change, not just a refactor.
- After Step 3: manually hit `GET /cases/{id}/case-match` and `GET /documents/{id}/detection/case-match`
  for the same case/document pair and confirm they still agree (same `DocumentCaseMatch` content).
- After Step 5: run the Detection Agent (`POST /documents/{id}/detection/run`) on any document and
  confirm every finding comes back as `unable_to_assess` with the "pending rewire" reason — no
  tool should silently produce a real-looking result anymore, and the run should still complete
  without erroring (lead plan/skip logic still exercises normally).
- After Step 1: hit `GET /documents/{id}/detection/metadata-anomaly` and confirm no
  `AttributeError` appears in the backend log (it was silently swallowed before, so "no longer
  logged" is the actual signal, not a response-shape change).

---

## Phase 2 — domain-owned repository access (separate, larger follow-up)

**Done and verified** — see "Phase 2 progress" near the top of this file for exactly what changed
and how it was checked. Originally documented here as a not-yet-started design for a later pass;
executed in the same session once Phase 1 landed. The design below is kept as the record of intent
(with one correction noted inline), not a to-do list anymore.

**Target shape:** one owning module per domain; every other file calls the owner, never the raw
repository. Two domains, not five — no `documents_service.py`/`cases_service.py`/
`extracted_fields_service.py` split of its own kind; instead:

- **Documents domain** — `document_repository.py` and `extracted_fields_repository.py` stay two
  separate modules, both directly importable (**revised**: the original draft of this plan
  proposed folding `extracted_fields_repository` under `document_repository`; tried during
  execution and reverted at your explicit request — "documents and extracted fields are 2
  separate tables... best to keep them separate". They're two distinct Supabase tables and stay
  two distinct repository modules; nothing calls a lower-level "raw" layer underneath either one
  today, so there's no further boundary-fixing needed here beyond what already exists).
  `document_repository.py` still carries the business-shaped functions
  (`record_classification`, `approve_document`, `reject_document`) it always has. This is where
  the "sync case status after a document mutation" business rule (currently
  called inline at 9 separate sites — `nodes.py` x2, `pipeline.py` x1, `routers/documents.py` x6)
  naturally moves to: the owning module's write functions call `case_status.sync_submission_finalized`
  themselves, once, instead of every caller remembering to.
- **Cases domain** — `case_status.py` already plays most of this role (it's the only thing that
  assembles case+document data into summaries/snapshots). Phase 2 extends it to also own the
  create/update/delete operations `routers/cases.py` currently sends straight to
  `case_repository.py` — `case_repository.py` stays pure CRUD underneath, same pattern as documents.

**Migration order** (smallest blast radius first, so early steps validate the pattern before the
big one): `routers/cases.py` (done, see Phase 2 progress above) → `app/langgraph/detection/` /
`app/langgraph/nodes.py` + `pipeline.py` / `routers/detection.py` (no import-boundary work left
here per the revised documents-domain design above — already compliant; only the
`sync_submission_finalized` consolidation below still applies to them) → `routers/documents.py`
last (the biggest single file, ~25 call sites, and the main remaining home of inline
`sync_submission_finalized` calls).

**Enforcing the boundary once migrated:** convention first (only the owning module imports the
raw repository; code review catches violations) — a lint rule (e.g. import-linter) or moving the
repositories into a private submodule is a possible later hardening step, not required to start.

**Explicitly deferred within Phase 2 too:** the event-bus idea for `sync_submission_finalized`
discussed earlier this session — Phase 2 makes that call happen once per owning-module write
instead of at every caller, which removes the duplication without needing an event bus at all.
