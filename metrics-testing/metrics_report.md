# Success metrics

Seeds 0, 1; 2 case run(s) scored.

| Metric | Result | Count |
|---|---|---|
| Extraction accuracy (key fields) | 96.0% | 144 of 150 fields |
| Risk score accuracy | 100.0% | 2 of 2 cases |
| Anomaly detection accuracy | 100.0% | 8 of 8 forged documents |
| False positive rate (clean documents flagged) | 25.0% | 6 of 24 clean documents |
| False positive rate (clean cases rated above Low) | n/a | 0 of 0 clean cases |

## Extraction

| Reading | Accuracy | Count |
|---|---|---|
| Key fields, right name | 96.0% | 144 of 150 |
| All truth fields, right name | 89.5% | 256 of 286 |
| All truth fields, any name | 94.1% | 269 of 286 |
| Table rows found | 84.4% | 97 of 115 |
| Key fields on forged documents | 97.1% | 33 of 34 |
| Documents classified as the right type | 100.0% | 32 of 32 |

| Document type | Key-field accuracy | Count |
|---|---|---|
| bank_statement | 57.1% | 4 of 7 |
| certificate_of_incorporation | 100.0% | 12 of 12 |
| certificate_of_incumbency | 100.0% | 16 of 16 |
| company_constitution | 100.0% | 14 of 14 |
| credit_report | 100.0% | 13 of 13 |
| fatca_crs_declaration | 100.0% | 10 of 10 |
| list_of_loans | 100.0% | 10 of 10 |
| notice_of_assessment | 100.0% | 14 of 14 |
| nric | 100.0% | 22 of 22 |
| passport | 100.0% | 14 of 14 |
| payslip | 100.0% | 6 of 6 |
| proof_of_address | 62.5% | 5 of 8 |
| source_of_wealth | 100.0% | 4 of 4 |

## Risk

| Expected -> system | Cases |
|---|---|
| Critical -> Critical | 2 |

## Anomaly detection

| Reading | Result | Count |
|---|---|---|
| Forged documents flagged | 100.0% | 8 of 8 |
| Forgeries caught by the right check | 100.0% | 8 of 8 |
| Forgeries where a finding names the changed value | 100.0% | 4 of 4 |
| Clean documents flagged (false positives) | 25.0% | 6 of 24 |

| Forgery type | Caught by the right check |
|---|---|
| AI-Generated | 2 of 2 |
| Cross-Document | 2 of 2 |
| Metadata | 2 of 2 |
| Text Overlay | 2 of 2 |

## By seed

| Seed | Extraction (key fields) | Risk | Anomaly detection |
|---|---|---|---|
| 0 | 96.0% | 100.0% | 100.0% |
| 1 | 96.0% | 100.0% | 100.0% |

## False positives

Findings above "info" on clean documents, from each document's own scan.

| Check | Findings |
|---|---|
| font_consistency_tool | 4 |
| date_consistency_tool | 3 |
| numerical_checks_tool | 2 |

| Seed | Case | Document | Check | Severity | Finding |
|---|---|---|---|---|---|
| 0 | C011 | CBR_TMS_2026-09-26.pdf | date_consistency_tool | high | Account 1 was opened at age 21.0, below the minimum of 21 for this product type |
| 0 | C011 | CBR_TMS_2026-09-26.pdf | date_consistency_tool | high | Account 3 was opened at age 21.0, below the minimum of 21 for this product type |
| 0 | C011 | FATCA_SelfCert_TMS_2026.pdf | font_consistency_tool | medium | Font deviation on 'signatory_designation': font 'Helvetica' vs. expected 'Aptos'; size 8.999999046325684 vs. expected 9.0 (1 occurrence). |
| 0 | C011 | FATCA_SelfCert_TMS_2026.pdf | font_consistency_tool | medium | Font deviation on 'declaration_date': font 'Helvetica' vs. expected 'Aptos'; size 9.00000286102295 vs. expected 9.0 (1 occurrence). |
| 0 | C011 | POR_UtilityBill_20260925.jpeg | date_consistency_tool | low | Contract end date (2032-03-10) is more than 5 years beyond the bill date — unusually long for a utility contract |
| 0 | C011 | TMS_BalanceSheet_Audited.pdf | numerical_checks_tool | low | Tax expense (96,600.00) is a flat 17% of profit before tax with no sign of the partial tax exemption a genuine Singapore company would apply |
| 1 | C011 | FATCA_SelfCert_TMS_2026.pdf | font_consistency_tool | medium | Font deviation on 'signatory_designation': font 'Helvetica' vs. expected 'Aptos'; size 8.999999046325684 vs. expected 9.0 (1 occurrence). |
| 1 | C011 | FATCA_SelfCert_TMS_2026.pdf | font_consistency_tool | medium | Font deviation on 'declaration_date': font 'Helvetica' vs. expected 'Aptos'; size 9.00000286102295 vs. expected 9.0 (1 occurrence). |
| 1 | C011 | NRIC_8175_front.jpg | numerical_checks_tool | high | S9798175B does not match any valid UEN format |

## Gaps in the run

- seed 1: no results for C004, C008, C010, C012, C015, C020, C024, C025, C027
- C011: officer step on BankStmt_TMS_2026-05to08.pdf: saved the fields unchanged (entity_type low-confidence)
- C011: officer step on TayYS_Salary_Slip_20260925.pdf: saved the fields unchanged (basic_pay low-confidence)
- C011: officer step on CBR_TMS_2026-09-26.pdf: saved the fields unchanged (accounts low-confidence)
- C011: officer step on POR_NRIC_20260925.pdf: saved the fields unchanged (nric_number low-confidence)
- C011: officer step on POR_UtilityBill_20260925.jpeg: confirmed the classifier's type; saved the fields unchanged (consumption_trend low-confidence)
- C011: officer step on SimTH_Passport_scan.jpg: saved the fields unchanged (mrz low-confidence)
- C011: officer step on SP_Utilities_TMS.pdf: saved the fields unchanged (consumption_trend low-confidence)
- C011: analysis start answered 409: {"detail":"An analysis is already running for this case."}
- C011: not approved: POR_UtilityBill_20260925.jpeg: Only completed documents can be approved.
- C011: officer step on POR_NRIC_20260925.pdf: saved the fields unchanged (nric_number low-confidence)
- C011: officer step on SimTH_Passport_scan.jpg: saved the fields unchanged (mrz low-confidence)
- C011: officer step on BankStmt_TMS_2026-05to08.pdf: saved the fields unchanged (bank_name, entity_type low-confidence)
- C011: officer step on CBR_TMS_2026-09-26.pdf: saved the fields unchanged (accounts low-confidence)
- C011: officer step on SP_Utilities_TMS.pdf: confirmed the classifier's type; saved the fields unchanged (document_title, utility_provider low-confidence)
- C011: officer step on TayYS_Salary_Slip_20260925.pdf: saved the fields unchanged (basic_pay low-confidence)
- C011: officer step on POR_UtilityBill_20260925.jpeg: confirmed the classifier's type; saved the fields unchanged (document_title, company_name low-confidence)
- C011: not scanned: POR_UtilityBill_20260925.jpeg: status analyzed
