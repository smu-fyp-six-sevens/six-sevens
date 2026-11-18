# Success metrics

1 case(s) scored: C013.

| Metric | Result | Count |
|---|---|---|
| Classification accuracy | 100.0% | 2 of 2 documents |
| Extraction accuracy (key fields) | 100.0% | 10 of 10 fields |
| Risk score accuracy | 0.0% | 0 of 1 cases |
| Anomaly detection accuracy | n/a | 0 of 0 forged documents |
| False positive rate (clean documents flagged) | 0.0% | 0 of 2 scanned clean documents |
| False positive rate (clean cases rated above Low) | 100.0% | 1 of 1 clean cases |

## Classification

| Document type | Accuracy | Count |
|---|---|---|
| list_of_loans | 100.0% | 2 of 2 |

Every document was given the right type.

## Extraction

| Reading | Accuracy | Count |
|---|---|---|
| Key fields, right name | 100.0% | 10 of 10 |
| All truth fields, right name | 94.7% | 18 of 19 |
| All truth fields, any name | 94.7% | 18 of 19 |
| Table rows found | n/a | 0 of 0 |
| Key fields on forged documents | n/a | 0 of 0 |

| Document type | Key-field accuracy | Count |
|---|---|---|
| list_of_loans | 100.0% | 10 of 10 |

## Risk

| Expected -> system | Cases |
|---|---|
| Low -> Medium | 1 |

## Anomaly detection

| Reading | Result | Count |
|---|---|---|
| Forged documents flagged | n/a | 0 of 0 |
| Forgeries caught by the right check | n/a | 0 of 0 |
| Forgeries where a finding names the changed value | n/a | 0 of 0 |
| Clean documents flagged (false positives) | 0.0% | 0 of 2 |

| Forgery type | Caught by the right check |
|---|---|

## Cases with the wrong risk level

| Case | Expected | System |
|---|---|---|
| C013 | Low | Medium |

## Gaps in the run

- C013: BankStmt_HDM_2026-04to07.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: FATCA_SelfCert_HDM_2026.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: HDM_BalanceSheet_Audited.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: HDM_COI_scan.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: HDM_Constitution.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: HarbourviewDigitalMedia_BizFile_Officers_20260803.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: HarbourviewDigitalMedia_CreditBureau.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: HarbourviewDigitalMedia_NOA_YA2025.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: KohCK_Salary_Slip_20260803.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: NRIC_4298_back.png doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: NRIC_4298_front.png doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: NRIC_8419_back.jpeg doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: NRIC_8419_front.jpeg doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: POR_BankStmt_20260803.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C013: POR_NRIC_20260803.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
