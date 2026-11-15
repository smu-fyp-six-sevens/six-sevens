# C011 (unseeded): expected vs actual

**Case:** C011, Tidewater Marine Supplies Pte Ltd. Run as GLDB-4 on the local backend, through the real classifier, extractor, detection agents and case analysis.
**Documents:** the unseeded documents, the same values as `FYP/v18_generated_documents` and the forgeries in `FYP/v18_fraud_documents`. 16 uploaded: 4 forged, 12 clean.
**Scored by:** `python -m scripts.metrics.score --seeds 0`. Raw results are in `seed-0/results/C011.json`.

## The three success metrics

| Metric | Expected | Actual | Result |
|---|---|---|---|
| Extraction accuracy (key fields) | 76 of 76 | **73 of 76 (96.0%)** | 3 misses, listed below |
| Risk score accuracy | **Critical** | **Critical (score 93)** | Match |
| Anomaly detection accuracy | 4 of 4 forged documents flagged | **4 of 4 (100%)** | Each one was caught by the check we expected |

Supporting figures:

| Reading | Actual |
|---|---|
| Document type classified correctly | 16 of 16 |
| All printed values, read under the right field name | 133 of 144 (92.4%) |
| All printed values, read under any field name | 137 of 144 (95.1%) |
| Table rows found | 49 of 58 (84.5%) |
| Forgeries where a finding names the changed value | 2 of 2 that changed a value |
| Clean documents flagged by their own scan (false positives) | **4 of 12** |

## Forged documents

| Document | What was forged | What fired | Caught |
|---|---|---|---|
| `POR_NRIC_20260925.pdf` | AI-generated proof of address (simulated generator traces) | `ai_generation_tool`: the PDF's creator is DALL-E (high), and the image scored 0.55 (medium). `metadata_tool` also fired: OpenAI is named in the metadata. | Yes |
| `SimTH_Passport_scan.jpg` | Photoshop written into the image's EXIF Software field | `metadata_tool`: Adobe Photoshop (high) | Yes |
| `TayYS_Salary_Slip_20260925.pdf` | Net pay 8,180.29 drawn over the real 3,180.29 | `layout_anomalies_tool`: the two figures overlap (high). `numerical_checks_tool`: the lines add up to 3,180.29 but the slip shows 8,180.29 (high). | Yes, and the findings name both values |
| `SP_Utilities_TMS.pdf` | Unit number #01-21 changed to #01-12 | The case analysis found the bill's address #01-12 against #01-21 on the business profile (medium) | Yes, and the finding names both units |

## Risk: why Critical

Critical needs high-severity findings on 3 or more documents. The forgeries alone put them on three: the payslip, the AI proof of address and the passport. So Critical is right for the right reason.

Two false positives added high-severity findings on top of that: one more on the passport and one on the company utility bill. A third false positive put high-severity findings on the credit report. They didn't change the level here, but they would push a clean case to High.

## Extraction misses

**Key fields (the headline 3 misses):**
- **Bank statement, `company_name`:** read as "TIDEWATER MARINE", with the rest of the name cut off.
- **Bank statement, `transactions`:** the extractor read 49 of the 50 transaction rows.
- **Personal utility bill, `individual_address`:** the address was read but saved under a different field name.

**Other printed values missed:**
- **Personal utility bill:** `bill_date`, `due_date` and `total_payable` were read but saved under other names. `entity_type` and `billing_period_end` were not read.
- **Payslip:** `employee_cpf` was not read as its own field.
- **FATCA form:** the second controlling person was not read.
- **AI proof of address:** `sex` was not read.

## False positives found

These are rated high, so each can push a clean case to High on its own:

1. **Credit report: "Account opened at age 21.0, below the minimum of 21".** This is an off-by-one in the date check: 21 is not below 21. It fired twice on this report.
2. **Company utility bill: "98892823 does not match any valid UEN format".** An account number on the bill was checked as if it were a UEN.
3. **Passport: "MRZ must be two lines of 44 characters; got 1 line (88 chars)".** The extractor joins the two MRZ lines into one, and the check then fails it. This also happened in the seeded run.

Lower-severity false positives:

4. **FATCA form:** the form uses Helvetica, but its reference expects the Aptos font (medium). This flags every case's FATCA form.
5. **Personal utility bill:** "contract end date more than 5 years after the bill date" (low). The generator draws contract lengths that are too long.
6. **Balance sheet:** "tax is a flat 17% with no partial exemption" (low). The generator doesn't apply Singapore's partial tax exemption.

## Compared with the seeded run (seed 1, GLDB-2)

| | Seed 1 | Unseeded |
|---|---|---|
| Extraction, key fields | 71 of 74 (96.0%) | 73 of 76 (96.0%) |
| Risk | Critical (matches) | Critical (matches) |
| Anomaly detection | 4 of 4 | 4 of 4 |
| Clean documents flagged | 2 of 12 | 4 of 12 |

The NRIC-as-UEN false positive from seed 1 didn't happen here. The credit report age check and the utility bill account number are new, so the false positives depend on which values a case happens to get.

## Run notes

- An officer step was needed on 7 documents before approval. The runner either confirmed the classifier's type or saved the fields unchanged. No value was corrected, and extraction was scored before these steps.
- All 16 documents were approved and scanned. The runner now waits for a document to be re-read after its type is confirmed.
- The case analysis was already running when the runner asked for one (the "409" in the results file). The runner waited for that analysis and used its result.
- Earlier runs of this case, GLDB-1 to GLDB-3, are still in Supabase. GLDB-3 was the unseeded run before the runner fix: its two utility bills weren't scanned, so its results were replaced by this run.
