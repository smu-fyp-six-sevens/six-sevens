# Manual document-type correction + scoped extraction rerun

## Context

Today, when the Classifier's confidence falls below `CLASSIFICATION_CONFIDENCE_THRESHOLD`
(0.8) or a document ends up `unrecognised`, the officer can only manually correct the
*extracted fields* on the manual-review screen — there's no way to correct a wrong
*document type* itself. A misclassified type means Extraction ran against the wrong
schema (or the generic "unrecognised" field list), so its output is structurally wrong,
not just low-confidence on individual fields. This adds: (1) a document-type dropdown
that only appears when confidence is low, (2) saving a corrected type sets confidence to
1.0 (officer-confirmed) and immediately re-runs Extraction against the new type — no
separate button, one action. The existing WebSocket/Realtime pipeline (`case_broadcaster.py`
+ `realtime_listener.py`, built in a prior session) already pushes any document-row change
to the frontend live, so the rerun's result needs no new polling/plumbing.

## Backend

**1. `PATCH /documents/{id}/document-type`** in `backend/app/routers/documents.py`
(new route, follow the existing `PATCH /documents/{id}/extracted-fields` handler's
shape/error-handling style).
- Body: `{document_type: str}`. Response: updated `DocumentRecord`.
- Validate `document_type` is in `extraction_prompts.KNOWN_DOCUMENT_TYPES` (13 canonical
  types) or `"unrecognised"` (the existing sentinel — this already IS the "Other"
  catch-all end-to-end, no new value needed) → 422 otherwise.
- Fetch the document; 404 if missing.
- **Server-side confidence gate**: reject with 400 if `document.confidence is not None and
  document.confidence >= settings.CLASSIFICATION_CONFIDENCE_THRESHOLD`. Enforce here, not
  just in the frontend — otherwise a scripted API call could silently override an
  already-trusted classification.
- Call `document_repository.record_classification(document_id, document_type=new_type,
  confidence=1.0, classification_outcome=ClassificationOutcome.ACCEPTED.value,
  evidence=[], fulfilled_requirements=compute_fulfilled_requirements(new_type,
  document.extracted_fields))`. Recomputing `fulfilled_requirements` from the document's
  *existing* `extracted_fields` (already available at this point, unlike at first
  classification) gives a more accurate value than leaving it stale from the old type;
  clearing `evidence` to `[]` since the old evidence was reasoning for the wrong type and
  would be misleading to keep attached to the corrected one.
- `await asyncio.to_thread(case_status.sync_submission_finalized, document.case_id)`
  afterward, mirroring `_finalise_classification`'s existing pattern in `orchestrator.py`
  (line ~604) — this endpoint is a manual substitute for that function's write path.
  **This is the checklist propagation step**: `sync_submission_finalized`
  (`case_status.py:96`) always re-fetches *all* of the case's documents fresh and
  recomputes coverage via `compute_requirement_coverage` (`case_status.py:59`), which reads
  each document's live `classification_outcome`/`fulfilled_requirements` — so as long as
  the `record_classification` write above lands first, the checklist/case-completion
  state is guaranteed correct on this call, including flipping back down if the correction
  *removes* a previously-fulfilled requirement (it's fully recomputed from scratch each
  time, not incremented). No separate "update the checklist" step is needed beyond calling
  this in the right order. The frontend's checklist view then updates automatically via the
  existing `case_broadcaster`/`realtime_listener` push on the documents/cases row change —
  no new frontend plumbing required.
- Then immediately kick off the rerun (see #2) via `BackgroundTasks.add_task` in the same
  handler — single request, single officer action, matching the "auto-trigger on save"
  decision. Set the document's status to `PROCESSING` before returning (see #2 step 1) so
  the response and the first WS push both reflect "now reprocessing."

**2. `rerun_extraction(document_id: str)`** — new public function in
`backend/app/orchestrator.py`, sibling to `process_document`. Reuses existing private
helpers rather than duplicating logic:
1. Fetch document via `document_repository.get_document`.
2. `update_status(document_id, DocumentStatus.PROCESSING)`.
3. `raw_text = await _ingest_document_text(document_id, document)` — `raw_text` is never
   persisted on the row (confirmed: it only lives as a local variable inside one
   `_process_document` run), so it must be re-extracted from the stored file.
4. If `raw_text` is empty, `_fail(...)` with the same message `_process_document` uses at
   line ~95 ("Extraction produced no usable text").
5. `extraction = await _run_extraction(document_id, document, document.document_type,
   raw_text)` — uses the document's now-corrected `document_type`; does **not** call
   `_run_intake_coordinator`/`_finalise_classification`.
6. `await _persist_extracted_fields(document_id, document.document_type, extraction)` —
   reuse exactly as-is.
7. Wrap 3-6 in try/except `DifyInvocationError` (and a catch-all, matching
   `process_document`'s outer guarantee) → `_fail(...)` with a clear reason on any error;
   otherwise `update_status(document_id, DocumentStatus.ANALYZED,
   document_type=document.document_type)` and
   `case_status.sync_submission_finalized(document.case_id)` on success (matches
   `_process_document`'s terminal contract: always ends at ANALYZED or FAILED) — this
   second sync call matters because `_persist_extracted_fields` recomputes
   `fulfilled_requirements` from the *actual* extracted fields (via
   `compute_fulfilled_requirements`), which can differ from the guess made at step 1 of
   the PATCH endpoint (before extraction had re-run) — so the checklist needs this final
   recompute to be accurate, not just the one from the type-correction write.

No new endpoint is needed for step 2 on its own — it's only ever invoked from inside the
`PATCH .../document-type` handler via `BackgroundTasks`, not exposed as its own route,
since the product decision is "one officer action does both." Triggered via FastAPI
`BackgroundTasks` rather than `document_queue.enqueue` — this is a single-document,
low-frequency, user-initiated action, not part of the bulk-intake path that
`QUEUE_WORKER_CONCURRENCY` throttles; routing it through the shared queue would add
unrelated coupling/backpressure to normal intake for no benefit, since the Realtime/WS
pipeline already delivers the eventual PROCESSING→ANALYZED/FAILED transition to the
frontend for free.

## Frontend

**3. `frontend/src/services/uploadService.ts`**
- Add `export const CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.8;` (mirrors backend
  `config.py`; frontend currently only has `EXTRACTION_CONFIDENCE_THRESHOLD`).
- Add `export const DOCUMENT_TYPE_OPTIONS: { value: string; label: string }[]` — the 13
  `KNOWN_DOCUMENT_TYPES` strings as `{value, label}` pairs, plus one appended
  `{ value: 'unrecognised', label: 'Other' }`. Deliberately a **new, separate** constant
  from `REQUIRED_DOCUMENT_TYPES` — that one collapses passport/nric into `id_document` for
  a different purpose (case checklist) and isn't 1:1 with real backend `document_type`
  values.
- Add `updateDocumentType(documentId: string, documentType: string): Promise<DocumentRecord>`
  — same shape as `updateExtractedFields` (line ~133): `authFetch` PATCH to
  `/documents/${documentId}/document-type`, JSON body `{ document_type: documentType }`,
  throw with response text on non-OK, return parsed `DocumentRecord`. (No separate
  `rerunExtraction` call needed from the frontend — the backend does both in one request.)

**4. `frontend/src/pages/DocumentDetailsPage.tsx`**
- Reuse/extend the existing `needsManualReview`/`isUnrecognisedDocument` gating booleans
  (~line 184-195) — add `document.confidence !== null && document.confidence <
  CLASSIFICATION_CONFIDENCE_THRESHOLD` to that same condition (or confirm it's already
  implied) and use it to conditionally render a `<select>` of `DOCUMENT_TYPE_OPTIONS` in
  place of the plain-text Document Type field (~line 362-387) — do not invent new gating
  logic.
- Local state mirroring `handleSaveFields`'s existing pattern (loading/disabled control,
  success/error banner): `selectedDocumentType`, `isSavingType`, `saveTypeError`.
- Single "Save & Re-run Extraction" button: disabled while `isSavingType`, or when
  `selectedDocumentType === document.document_type` (no-op — nothing changed). On click,
  call `updateDocumentType`; the response's `status` will already be `PROCESSING` (backend
  flips it before kicking off the background rerun), so just update local state from the
  response and let the existing WS snapshot subscription carry the eventual
  ANALYZED/FAILED transition and updated extracted fields through as normal — no manual
  polling, no second network call.

## Verification
- Manually classify a document with low confidence (or hand-edit a row's `confidence`
  below 0.8 in Supabase for a test document) and confirm the dropdown appears in place of
  plain text on `DocumentDetailsPage`; confirm it does NOT appear for an
  already-high-confidence document.
- Submit a corrected type via the dropdown; confirm via the network tab that
  `PATCH /documents/{id}/document-type` returns `status: "processing"`, `confidence: 1.0`,
  `classification_outcome: "accepted"` immediately, then confirm the page updates to
  `ANALYZED` (or `FAILED`, if the doc genuinely doesn't match) via the WS push shortly
  after, with `extracted_fields` reflecting the new type's schema.
- Hit the endpoint directly with a document whose `confidence >= 0.8` and confirm it's
  rejected with 400 (server-side gate, independent of the UI hiding the dropdown).
- Confirm `case_status` / case completion coverage updates correctly afterward (e.g. a
  case that was missing `proof_of_address` now shows it fulfilled if the corrected type
  supplies it).
