# Success metrics

10 case(s) scored: C001, C003, C005, C008, C011, C013, C014, C015, C021, C028.

| Metric | Result | Count |
|---|---|---|
| Classification accuracy | 99.3% | 143 of 144 documents |
| Extraction accuracy (key fields) | 92.3% | 676 of 732 fields |
| Risk score accuracy | 90.0% | 9 of 10 cases |
| Anomaly detection accuracy | 88.9% | 8 of 9 forged documents |
| False positive rate (clean documents flagged) | 13.3% | 18 of 135 scanned clean documents |
| Processing time (upload to completed analysis, officer time excluded) | 6.3 min average | 10 cases, 4.1 min to 8.4 min |

## Processing time

The time the system worked on each case: each document's upload until it was extracted, its approval until detection finished, and the analysis run, overlaps counted once. The officer's review and approval in between is left out; the wall clock below includes it.

| Case | Processing time | Wall clock | Case analysis |
|---|---|---|---|
| C001 | 4.1 min | 10.9 min | 18 s |
| C003 | 5.4 min | 12.9 min | 22 s |
| C005 | 5.9 min | 10.2 min | 18 s |
| C008 | 8.4 min | 895.5 min | 20 s |
| C011 | 4.6 min | 2071.9 min | 38 s |
| C013 | 7.8 min | 182.8 min | 23 s |
| C014 | 6.7 min | 9.3 min | 21 s |
| C015 | 8.1 min | 25.3 min | 23 s |
| C021 | 7.1 min | 111.9 min | 19 s |
| C028 | 4.8 min | 5.6 min | 20 s |

## Classification

| Document type | Accuracy | Count |
|---|---|---|
| bank_statement | 100.0% | 10 of 10 |
| certificate_of_incorporation | 100.0% | 8 of 8 |
| certificate_of_incumbency | 100.0% | 10 of 10 |
| company_constitution | 100.0% | 9 of 9 |
| credit_report | 100.0% | 9 of 9 |
| fatca_crs_declaration | 100.0% | 10 of 10 |
| list_of_loans | 100.0% | 8 of 8 |
| notice_of_assessment | 100.0% | 10 of 10 |
| nric | 100.0% | 30 of 30 |
| passport | 100.0% | 8 of 8 |
| payslip | 100.0% | 10 of 10 |
| proof_of_address | 92.3% | 12 of 13 |
| source_of_wealth | 100.0% | 9 of 9 |

| Case | Document | Expected | Classified as | Confidence |
|---|---|---|---|---|
| C008 | SP_Utilities_IBS.pdf | proof_of_address | unrecognised | 1.00 |

## Extraction

| Reading | Accuracy | Count |
|---|---|---|
| Key fields, right name | 92.3% | 676 of 732 |
| All truth fields, right name | 87.5% | 1216 of 1390 |
| All truth fields, any name | 88.2% | 1226 of 1390 |
| Table rows found | 89.7% | 685 of 764 |
| Key fields on forged documents | 97.3% | 36 of 37 |

| Document type | Key-field accuracy | Count |
|---|---|---|
| bank_statement | 86.5% | 32 of 37 |
| certificate_of_incorporation | 100.0% | 48 of 48 |
| certificate_of_incumbency | 100.0% | 80 of 80 |
| company_constitution | 100.0% | 63 of 63 |
| credit_report | 100.0% | 62 of 62 |
| fatca_crs_declaration | 100.0% | 50 of 50 |
| list_of_loans | 50.0% | 40 of 80 |
| notice_of_assessment | 100.0% | 70 of 70 |
| nric | 95.3% | 102 of 107 |
| passport | 100.0% | 56 of 56 |
| payslip | 100.0% | 30 of 30 |
| proof_of_address | 86.7% | 26 of 30 |
| source_of_wealth | 89.5% | 17 of 19 |

## Risk

| Expected -> system | Cases |
|---|---|
| Critical -> Critical | 1 |
| Critical -> High | 1 |
| High -> High | 2 |
| Low -> High | 1 |
| Low -> Low | 4 |
| Medium -> Low | 1 |

## Anomaly detection

| Reading | Result | Count |
|---|---|---|
| Forged documents flagged | 88.9% | 8 of 9 |
| Forgeries caught by the right check | 81.8% | 9 of 11 |
| Forgeries where a finding names the changed value | 100.0% | 4 of 4 |
| Clean documents flagged (false positives) | 13.3% | 18 of 135 |

| Forgery type | Caught by the right check |
|---|---|
| AI-Generated | 1 of 1 |
| Cross-Document | 1 of 1 |
| Date Anomalies | 1 of 1 |
| Font/Size | 1 of 1 |
| Metadata | 1 of 3 |
| Misalignment | 2 of 2 |
| Numerical | 1 of 1 |
| Text Overlay | 1 of 1 |

## Cases with the wrong risk level

| Case | Expected | System |
|---|---|---|
| C014 | Low | High |
| C015 | Critical | High |
| C028 | Medium | Low |

## Forged documents not flagged

- C028/FS_VPIS_2025.pdf

## False positives

Findings above "info" on clean documents, from each document's own scan.

| Check | Findings |
|---|---|
| external_validation_tool | 12 |
| numerical_checks_tool | 4 |
| font_consistency_tool | 2 |
| date_consistency_tool | 1 |

| Case | Document | Check | Severity | Finding |
|---|---|---|---|---|
| C003 | ACRA_Incumbency_20260829.pdf | external_validation_tool | low | Company Address is HDB block 2A BOON TIONG RD, which HDB lists as flats only, with no commercial units: the company is registered at a home. Also printed in Shareholder 1 (LOW KOK WAI), Address and Of |
| C003 | BankStmt_LIS_2026-04to07.pdf | numerical_checks_tool | medium | The statement prints no opening balance, so its earliest transaction (-7,160.00, GIRO Payment PREMISES RENTAL APR26) can't be checked |
| C003 | IRAS_NOA_Company_20251114.pdf | external_validation_tool | low | Company Address is HDB block 2A BOON TIONG RD, which HDB lists as flats only, with no commercial units: the company is registered at a home |
| C008 | IBS_BalanceSheet_Audited.pdf | numerical_checks_tool | high | Equity items sum to 1,000,000.00, but the stated total is 2,287,900.00 |
| C011 | FATCA_SelfCert_TMS_2026.pdf | font_consistency_tool | medium | Font deviation on 'signatory_designation': font 'Helvetica' vs. expected 'Aptos'; size 8.999999046325684 vs. expected 9.0 (1 occurrence). |
| C011 | FATCA_SelfCert_TMS_2026.pdf | font_consistency_tool | medium | Font deviation on 'declaration_date': font 'Helvetica' vs. expected 'Aptos'; size 9.00000286102295 vs. expected 9.0 (1 occurrence). |
| C014 | AcctStatement_MHB_20260810.pdf | external_validation_tool | high | Account number 431867700 has 9 digits, but OVERSEA-CHINESE BANKING CORPORATION LIMITED account numbers have 10 or 12 |
| C014 | Company_Constitution_20260810.pdf | external_validation_tool | low | Company Address is HDB block 248 PASIR RIS ST 21, which HDB lists as flats only, with no commercial units: the company is registered at a home. Also printed in Subscriber 1 (Teo Kai Wen), Address |
| C014 | MHB_BalanceSheet_Audited.pdf | date_consistency_tool | high | signing_date (2026-11-23) is after today's date (2026-11-19) — nothing dated later than today can be genuine yet |
| C014 | MaplecrestHomeBakery_BizFile_Officers_20260810.pdf | external_validation_tool | low | Company Address is HDB block 248 PASIR RIS ST 21, which HDB lists as flats only, with no commercial units: the company is registered at a home. Also printed in Shareholder 1 (TEO KAI WEN), Address and |
| C014 | MaplecrestHomeBakery_FATCA_CRS.pdf | external_validation_tool | low | Company Address is HDB block 248 PASIR RIS ST 21, which HDB lists as flats only, with no commercial units: the company is registered at a home |
| C015 | InvestmentStatement_HoML.pdf | numerical_checks_tool | high | Holdings sum to 8,793.47, but the stated portfolio value is 11,770.26 |
| C021 | eStmt_download_566.pdf | numerical_checks_tool | low | The statement prints no opening balance, so its earliest transaction (-3,420.00, GIRO Payment PREMISES RENTAL APR26) can't be checked |
| C028 | AcctStatement_VPIS_20260629.pdf | external_validation_tool | low | Company Address is HDB block 116 MARSILING RISE, which HDB lists as flats only, with no commercial units: the company is registered at a home |
| C028 | Payslip_Jun2026_ChanSF.pdf | external_validation_tool | low | Company Address is HDB block 116 MARSILING RISE, which HDB lists as flats only, with no commercial units: the company is registered at a home |
| C028 | VPIS_Constitution.pdf | external_validation_tool | low | Company Address is HDB block 116 MARSILING RISE, which HDB lists as flats only, with no commercial units: the company is registered at a home. Also printed in Subscriber 1 (Ng Zhen Yu), Address |
| C028 | VantagePointITSolutions_BizFile_Officers_20260629.pdf | external_validation_tool | low | Company Address is HDB block 116 MARSILING RISE, which HDB lists as flats only, with no commercial units: the company is registered at a home. Also printed in Shareholder 1 (NG ZHEN YU), Address and O |
| C028 | VantagePointITSolutions_FATCA_CRS.pdf | external_validation_tool | low | Company Address is HDB block 116 MARSILING RISE, which HDB lists as flats only, with no commercial units: the company is registered at a home |
| C028 | VantagePointITSolutions_NOA_YA2025.pdf | external_validation_tool | low | Company Address is HDB block 116 MARSILING RISE, which HDB lists as flats only, with no commercial units: the company is registered at a home |

## Gaps in the run

- not scanned, so not checked: C001/POR_NRIC_20260831.pdf
- not scanned, so not checked: C003/LoanOffer_70274.pdf
- not scanned, so not checked: C003/SOW_Declaration_8522.pdf
- not scanned, so not checked: C005/FacilityLetter_STC_1.pdf
- not scanned, so not checked: C005/FacilityLetter_STC_2.pdf
- not scanned, so not checked: C005/STC_Company_ProofAddr.pdf
- not scanned, so not checked: C014/LoanFacility_34945_2.pdf
- not scanned, so not checked: C014/LoanFacility_66367_1.pdf
- not scanned, so not checked: C014/SP_Utilities_MHB.pdf
- not scanned, so not checked: C021/InvestmentStatement_Vikram.pdf
- not scanned, so not checked: C021/LoanFacility_19960_2.pdf
- not scanned, so not checked: C028/FacilityLetter_VPIS_1.pdf
- not scanned, so not checked: C028/LoanFacility_90540_2.pdf
- no result for C001/POR_NRIC_20260831.pdf
- no result for C003/LoanOffer_70274.pdf
- no result for C003/SOW_Declaration_8522.pdf
- no result for C005/FacilityLetter_STC_1.pdf
- no result for C005/FacilityLetter_STC_2.pdf
- no result for C005/STC_Company_ProofAddr.pdf
- no result for C014/LoanFacility_34945_2.pdf
- no result for C014/LoanFacility_66367_1.pdf
- no result for C014/SP_Utilities_MHB.pdf
- no result for C021/InvestmentStatement_Vikram.pdf
- no result for C021/LoanFacility_19960_2.pdf
- no result for C028/FacilityLetter_VPIS_1.pdf
- no result for C028/LoanFacility_90540_2.pdf
- C001: BrightwaveAnalytics_Incorporation_Cert.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C003: officer step on BankStmt_LIS_2026-04to07.pdf: type bank_statement -> bank_statement; added bank_name; deleted bank_name (undone for scoring)
- C003: LIS_BalanceSheet_Audited.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C003: LIS_Constitution.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C003: officer step on POR_UtilityBill_20260829.pdf: type proof_of_address -> proof_of_address (undone for scoring)
- C005: officer step on NRIC_8915_back.jpeg: deleted document_reference_number (undone for scoring)
- C005: officer step on POR_UtilityBill_20260714.jpg: type proof_of_address -> proof_of_address (undone for scoring)
- C008: officer step on ChuaZY_Passport_scan.png: edited mrz (undone for scoring)
- C008: officer step on IBS_BudgetProposal_202608.pdf: type unrecognised -> unrecognised (undone for scoring)
- C008: officer step on PO-2352_IBS.pdf: type unrecognised -> unrecognised (undone for scoring)
- C008: officer step on POR_UtilityBill_20260808.pdf: type proof_of_address -> proof_of_address (undone for scoring)
- C008: officer step on SP_Utilities_IBS.pdf: type unrecognised -> proof_of_address (undone for scoring)
- C011: ACRA_CertIncorp_201657409E.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C011: CBR_TMS_2026-09-26.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C011: POR_UtilityBill_20260925.jpeg doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C011: SP_Utilities_TMS.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C011: TMS_BalanceSheet_Audited.pdf doesn't print what the dataset's file prints (another build, or the clean copy of a forged document): left out of the scores
- C014: officer step on MHB_BudgetProposal_202608.pdf: type unrecognised -> unrecognised (undone for scoring)
- C014: officer step on NRIC_1634_back.jpg: deleted card_number (undone for scoring)
- C014: officer step on POR_UtilityBill_20260810.pdf: type proof_of_address -> proof_of_address (undone for scoring)
- C015: officer step on OLW_BudgetProposal_202609.pdf: type unrecognised -> unrecognised (undone for scoring)
- C015: officer step on PO-2386_OLW.pdf: type unrecognised -> unrecognised (undone for scoring)
- C015: officer step on POR_UtilityBill_20260904.png: type proof_of_address -> proof_of_address (undone for scoring)
- C015: officer step on eStmt_download_630.pdf: type bank_statement -> bank_statement; added bank_name; added transactions (undone for scoring)
- C021: officer step on NRIC_2285_back.jpg: deleted serial_number (undone for scoring)
- C021: officer step on NRIC_3495_back.png: deleted card_serial_number (undone for scoring)
- C021: officer step on POR_UtilityBill_20260825.jpg: type proof_of_address -> proof_of_address (undone for scoring)
- C021: officer step on eStmt_download_566.pdf: type bank_statement -> bank_statement (undone for scoring)
- C028: not scanned: PO-2438_VPIS.pdf: status rejected
- C028: officer step on PO-2438_VPIS.pdf: type unrecognised -> unrecognised (undone for scoring)
