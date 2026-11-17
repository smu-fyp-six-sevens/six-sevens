# Success metrics

1 case(s) scored: C008.

| Metric | Result | Count |
|---|---|---|
| Classification accuracy | 100.0% | 14 of 14 documents |
| Extraction accuracy (key fields) | 98.6% | 70 of 71 fields |
| Risk score accuracy | 0.0% | 0 of 1 cases |
| Anomaly detection accuracy | 50.0% | 1 of 2 forged documents |
| False positive rate (clean documents flagged) | 16.7% | 2 of 12 scanned clean documents |
| False positive rate (clean cases rated above Low) | n/a | 0 of 0 clean cases |

## Classification

| Document type | Accuracy | Count |
|---|---|---|
| bank_statement | 100.0% | 1 of 1 |
| certificate_of_incorporation | 100.0% | 1 of 1 |
| certificate_of_incumbency | 100.0% | 1 of 1 |
| company_constitution | 100.0% | 1 of 1 |
| credit_report | 100.0% | 1 of 1 |
| fatca_crs_declaration | 100.0% | 1 of 1 |
| notice_of_assessment | 100.0% | 1 of 1 |
| nric | 100.0% | 1 of 1 |
| passport | 100.0% | 2 of 2 |
| payslip | 100.0% | 1 of 1 |
| proof_of_address | 100.0% | 2 of 2 |
| source_of_wealth | 100.0% | 1 of 1 |

Every document was given the right type.

## Extraction

| Reading | Accuracy | Count |
|---|---|---|
| Key fields, right name | 98.6% | 70 of 71 |
| All truth fields, right name | 97.7% | 130 of 133 |
| All truth fields, any name | 97.7% | 130 of 133 |
| Table rows found | 98.3% | 57 of 58 |
| Key fields on forged documents | 100.0% | 12 of 12 |

| Document type | Key-field accuracy | Count |
|---|---|---|
| bank_statement | 75.0% | 3 of 4 |
| certificate_of_incorporation | 100.0% | 6 of 6 |
| certificate_of_incumbency | 100.0% | 8 of 8 |
| company_constitution | 100.0% | 7 of 7 |
| credit_report | 100.0% | 6 of 6 |
| fatca_crs_declaration | 100.0% | 5 of 5 |
| notice_of_assessment | 100.0% | 7 of 7 |
| nric | 100.0% | 5 of 5 |
| passport | 100.0% | 14 of 14 |
| payslip | 100.0% | 3 of 3 |
| proof_of_address | 100.0% | 4 of 4 |
| source_of_wealth | 100.0% | 2 of 2 |

## Risk

| Expected -> system | Cases |
|---|---|
| High -> Critical | 1 |

## Anomaly detection

| Reading | Result | Count |
|---|---|---|
| Forged documents flagged | 50.0% | 1 of 2 |
| Forgeries caught by the right check | 33.3% | 1 of 3 |
| Forgeries where a finding names the changed value | 100.0% | 1 of 1 |
| Clean documents flagged (false positives) | 16.7% | 2 of 12 |

| Forgery type | Caught by the right check |
|---|---|
| Date Anomalies | 1 of 1 |
| Metadata | 0 of 1 |
| Misalignment | 0 of 1 |

## Cases with the wrong risk level

| Case | Expected | System |
|---|---|---|
| C008 | High | Critical |

## Forged documents not flagged

- C008/CreditReport_CBS_20260808.pdf

## False positives

Findings above "info" on clean documents, from each document's own scan.

| Check | Findings |
|---|---|
| numerical_checks_tool | 3 |

| Case | Document | Check | Severity | Finding |
|---|---|---|---|---|
| C008 | IBS_BalanceSheet_Audited.pdf | numerical_checks_tool | high | Equity items sum to 1,000,000.00, but the stated total is 2,287,900.00 |
| C008 | SP_Utilities_IBS.pdf | numerical_checks_tool | high | Previous balance 592.65 - payment 454.13 = 138.52, but outstanding balance shows 0.00 |
| C008 | SP_Utilities_IBS.pdf | numerical_checks_tool | high | Outstanding balance 0.00 + current charges 573.08 = 573.08, but total payable shows 711.60 |

## Gaps in the run

- C008: officer step on ChuaZY_Passport_scan.png: edited mrz (undone for scoring)
- C008: officer step on IBS_BudgetProposal_202608.pdf: type unrecognised -> unrecognised (undone for scoring)
- C008: officer step on PO-2352_IBS.pdf: type unrecognised -> unrecognised (undone for scoring)
- C008: officer step on POR_UtilityBill_20260808.pdf: type proof_of_address -> proof_of_address (undone for scoring)
- C008: officer step on SP_Utilities_IBS.pdf: type proof_of_address -> proof_of_address (undone for scoring)
