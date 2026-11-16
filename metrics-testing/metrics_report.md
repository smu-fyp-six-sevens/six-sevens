# Success metrics

1 case(s) scored: C001.

| Metric | Result | Count |
|---|---|---|
| Classification accuracy | 100.0% | 15 of 15 documents |
| Extraction accuracy (key fields) | 100.0% | 68 of 68 fields |
| Risk score accuracy | 100.0% | 1 of 1 cases |
| Anomaly detection accuracy | n/a | 0 of 0 forged documents |
| False positive rate (clean documents flagged) | 0.0% | 0 of 15 scanned clean documents |
| False positive rate (clean cases rated above Low) | 0.0% | 0 of 1 clean cases |

## Classification

| Document type | Accuracy | Count |
|---|---|---|
| bank_statement | 100.0% | 1 of 1 |
| certificate_of_incorporation | 100.0% | 1 of 1 |
| certificate_of_incumbency | 100.0% | 1 of 1 |
| company_constitution | 100.0% | 1 of 1 |
| credit_report | 100.0% | 1 of 1 |
| fatca_crs_declaration | 100.0% | 1 of 1 |
| list_of_loans | 100.0% | 1 of 1 |
| notice_of_assessment | 100.0% | 1 of 1 |
| nric | 100.0% | 4 of 4 |
| payslip | 100.0% | 1 of 1 |
| proof_of_address | 100.0% | 1 of 1 |
| source_of_wealth | 100.0% | 1 of 1 |

Every document was given the right type.

## Extraction

| Reading | Accuracy | Count |
|---|---|---|
| Key fields, right name | 100.0% | 68 of 68 |
| All truth fields, right name | 94.7% | 124 of 131 |
| All truth fields, any name | 95.4% | 125 of 131 |
| Table rows found | 94.6% | 87 of 92 |
| Key fields on forged documents | n/a | 0 of 0 |

| Document type | Key-field accuracy | Count |
|---|---|---|
| bank_statement | 100.0% | 4 of 4 |
| certificate_of_incorporation | 100.0% | 6 of 6 |
| certificate_of_incumbency | 100.0% | 8 of 8 |
| company_constitution | 100.0% | 7 of 7 |
| credit_report | 100.0% | 7 of 7 |
| fatca_crs_declaration | 100.0% | 5 of 5 |
| list_of_loans | 100.0% | 5 of 5 |
| notice_of_assessment | 100.0% | 7 of 7 |
| nric | 100.0% | 12 of 12 |
| payslip | 100.0% | 3 of 3 |
| proof_of_address | 100.0% | 2 of 2 |
| source_of_wealth | 100.0% | 2 of 2 |

## Risk

| Expected -> system | Cases |
|---|---|
| Low -> Low | 1 |

## Anomaly detection

| Reading | Result | Count |
|---|---|---|
| Forged documents flagged | n/a | 0 of 0 |
| Forgeries caught by the right check | n/a | 0 of 0 |
| Forgeries where a finding names the changed value | n/a | 0 of 0 |
| Clean documents flagged (false positives) | 0.0% | 0 of 15 |

| Forgery type | Caught by the right check |
|---|---|

## Gaps in the run

- no results for C002, C003, C004, C005, C006, C007, C008, C009, C010, C011, C012, C013, C014, C015, C016, C017, C018, C019, C020, C021, C022, C023, C024, C025, C026, C027, C028, C029, C030
- C001: officer step on CBR_BA_2026-08-29.pdf: saved the fields unchanged (accounts, total_outstanding_balance, report_date low-confidence)
- C001: officer step on POR_BankStmt_20260831.pdf: saved the fields unchanged (ledger_balance low-confidence)
- C001: BankStmt_BA_2026-05to08.pdf re-uploaded by hand (document 5f540fba-ec71-437c-b52e-c992be6925ea): the first upload was extracted with the generic schema, so its transactions were never read and it couldn't be approved. The re-upload's extraction and scan are scored; the case analysis is the one that ran after it.
