# Test suite audit — unnecessary test triage

## Context

After the backend dependency cleanup (see `BACKEND_CLEANUP_PLAN.md`) got the suite back to
**781 passed, 0 failed**, you asked for an audit of whether all 781 tests are actually
necessary, plus asked to delete the two dead reference test files
(`tests/test_orchestrator.py`, `tests/test_dify_client.py` — both already done: deleted, and the
`collect_ignore` workaround removed from `conftest.py`; suite stayed green at 781 after that,
since those two were never collected in the first place).

This file is the audit's findings, written down so the session can be compacted without losing
them. **No test deletions from this audit have been made yet** — this is a recommendation
pending your go-ahead.

## Inventory (test count / line count per file, largest first)

```
  88 tests   1797 lines  tests/test_pdf.py                       <- flagged, see below
  78 tests   1850 lines  tests/test_layout_anomalies.py
  54 tests    621 lines  tests/test_metadata_analysis.py
  49 tests   1094 lines  tests/test_documents.py
  46 tests    720 lines  tests/test_case_service.py
  45 tests    495 lines  tests/test_numerical_validation.py
  35 tests    812 lines  tests/test_mapper.py
  35 tests    542 lines  tests/test_case_match.py
  22 tests    218 lines  tests/test_langgraph.py
  22 tests    209 lines  tests/test_extraction_prompts.py
  21 tests    241 lines  tests/test_spelling_anomaly.py
  18 tests    281 lines  tests/test_cases.py
  17 tests    314 lines  tests/test_document_repository.py
  16 tests    420 lines  tests/test_detection.py
  15 tests    352 lines  tests/test_fraud_injections.py
  13 tests    220 lines  tests/test_ingestion.py
  13 tests    204 lines  tests/test_realtime_listener.py
  13 tests    194 lines  tests/test_case_repository.py
  13 tests    116 lines  tests/test_field_text.py
  12 tests    342 lines  tests/test_font_consistency.py
  12 tests    263 lines  tests/test_extraction_router.py
  12 tests    235 lines  tests/test_extracted_fields_repository.py
  11 tests    245 lines  tests/test_ws.py
   9 tests    149 lines  tests/test_store.py
   9 tests     76 lines  tests/test_auth.py
   8 tests    202 lines  tests/test_injection_router.py
   8 tests    106 lines  tests/test_health.py
   7 tests    117 lines  tests/test_injectors.py
   6 tests    139 lines  tests/test_monitoring.py
   6 tests    134 lines  tests/test_queue_service.py
   6 tests     83 lines  tests/test_storage_service.py
   6 tests     63 lines  tests/test_case_broadcaster.py
   5 tests    104 lines  tests/test_case_service_repository.py
   5 tests     57 lines  tests/test_supabase_client.py
   4 tests     88 lines  tests/test_extraction.py
```

## Finding: most of this is legitimate, not bloat

Spot-checked the largest files by reading actual test names/bodies, not just counting:

- **`test_layout_anomalies.py`** (78), **`test_metadata_analysis.py`** (54),
  **`test_numerical_validation.py`** (45) — test `app/fraud_detection/*.py`, the actual scored
  fraud-detection checks. Every test name maps to a distinct real forgery scenario (e.g.
  `test_flags_vertically_misaligned_label_and_amount`,
  `test_row_alignment_pairs_noa_amount_with_label_not_column_header`,
  `test_signature_is_grouped_separately_from_body`) — deep coverage because the domain has many
  genuinely different edge cases, not repeated/redundant tests of the same behaviour.
- **`test_mapper.py`** (35) — cross-document identity/address matching (NRIC linking, dedup,
  malformed-data resilience). Same pattern: each test is a distinct real scenario
  (`test_progress_deduplicates_same_nric_number_despite_mismatched_names`,
  `test_id_document_two_backs_for_same_identity_still_incomplete`).
- **`test_case_service.py`** (46) — checklist/requirement-coverage combinatorics. Same pattern.

None of these are recommended for trimming — they're doing their job.

## Finding: `tests/test_pdf.py` is the clear outlier — recommend trimming hard

**88 tests / 1797 lines testing `app/injection/pdf.py`** — the **dev-only synthetic
fraud-injection tool** (generates fake documents to test the detector against; explicitly *not*
part of the graded detection pipeline — see `CLAUDE.md`'s injection.py description).

The tests go deep into **private implementation plumbing** rather than the module's actual
behaviour:
- Font-fallback chains: `_pil_font`, `_load_truetype`, `_iter_system_font_faces`,
  `_font_insert_kwargs`, `_builtin_fallback_font`, `_font_key`, `_is_serif_font_name`
- Glyph/ink geometry: `_gray_ink_metrics`, `_glyph_box`, `_crop_halo`, `_snap_cut`,
  `_union_wipe`, `_rect_overlap`, `_mask_fill`, `_connected_boxes`
- OCR box-splitting math: `_separate_stacked_ocr_boxes`, `_iter_ocr_lines`
- PIL image-mode conversion edge cases, TrueType face-index lookup fallbacks, system-font
  directory scanning

**Why this is excessive:** `tests/test_fraud_injections.py` (15 tests) and
`tests/test_injectors.py` (7 tests) already test this same tool's actual *observable* behaviour
end-to-end — "does injecting a font-size/emphasis/misalignment/image-manipulation defect produce
the right output on a PDF/PNG/JPEG" — via the real public API
(`discover_fields_from_pdf/image`, `replace_field_text`, `apply_text_emphasis`,
`overlay_field_text`, `manipulate_field_region`, the injector classes). `test_pdf.py`'s 88 tests
mostly re-test the private helpers *underneath* that already-covered behaviour — textbook
over-testing of a helper module instead of the feature it supports. A bug in `_font_key` or
`_gray_ink_metrics` would already surface as a failure in the black-box injection tests; testing
it a second time at the private-function level adds maintenance cost without adding real
protection.

## Recommendation (not yet actioned)

Trim `tests/test_pdf.py` from 88 tests down to roughly **10-15**, covering only the module's
public entry points at a black-box level:
- `discover_fields_from_pdf` / `discover_fields_from_image`
- `replace_field_text`
- `apply_text_emphasis`
- `overlay_field_text`
- `manipulate_field_region`
- `find_template_path` / `ensure_data_dirs` (simple, cheap, worth 1-2 tests each)

Drop the rest: font-resolution internals, glyph/OCR geometry helpers, PIL mode-conversion edge
cases, system-font directory scanning, TrueType index fallback branches — all implementation
detail of a dev tool, indirectly covered already by the black-box injection tests.

**Not touched by this recommendation:** `test_injectors.py`, `test_injection_router.py`,
`test_fraud_injections.py` — these test observable injection behaviour and stay as-is.

## Next step when resumed

Decide: (a) go ahead with the trim as scoped above, (b) see the exact list of which of the 88
test functions would be kept vs. cut before deciding, or (c) leave `test_pdf.py` as-is. Nothing
has been deleted yet — say which and pick up from here.
