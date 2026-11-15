# C011 (seed 1): expected vs actual

**Case:** C011, Tidewater Marine Supplies Pte Ltd. Run as GLDB-2 on the local backend, through the real classifier, extractor, detection agents and case analysis.
**Documents:** 16 uploaded. 4 are forged and 12 are clean.
**Scored by:** `python -m scripts.metrics.score --seeds 1`. Raw results are in `seed-1/results/C011.json`.

## The three success metrics

| Metric | Expected | Actual | Result |
|---|---|---|---|
| Extraction accuracy (key fields) | 74 of 74 | **71 of 74 (96.0%)** | 3 misses, listed below |
| Risk score accuracy | Critical | **Critical (score 91)** | Match. C011 is labelled Critical now that the mastersheet uses the system's four levels. |
| Anomaly detection accuracy | 4 of 4 forged documents flagged | **4 of 4 (100%)** | Each one was caught by the check we expected |

Supporting figures:

| Reading | Actual |
|---|---|
| Document type classified correctly | 16 of 16 |
| All printed values, read under the right field name | 123 of 142 (86.6%) |
| All printed values, read under any field name | 132 of 142 (93.0%) |
| Table rows found (transactions and other tables) | 48 of 57 (84.2%) |
| Forgeries where a finding names the changed value | 2 of 2 that changed a value |
| Clean documents flagged by their own scan (false positives) | **2 of 12** |

## Forged documents

| Document | What was forged | Expected check | What fired | Caught |
|---|---|---|---|---|
| `POR_NRIC_20260925.pdf` | AI-generated proof of address (simulated generator traces) | `ai_generation_tool` | `ai_generation_tool`: the PDF's creator is DALL-E (high), and the image scored 0.42 for AI generation (medium). `metadata_tool` also fired: OpenAI is named in the metadata. | Yes |
| `SimTH_Passport_scan.jpg` | Photoshop written into the image's EXIF Software field | `metadata_tool` | `metadata_tool`: "Adobe Photoshop can repaint document content" (high) | Yes |
| `TayYS_Salary_Slip_20260925.pdf` | Net pay 10,325.04 drawn over the real 5,325.04 | `layout_anomalies_tool` | `layout_anomalies_tool`: the two NET PAY figures overlap (high). `numerical_checks_tool`: the earnings minus deductions give 5,325.04 but the slip shows 10,325.04 (high). | Yes, and the finding names both values |
| `SP_Utilities_TMS.pdf` | Unit number #01-21 changed to #01-12 | Cross-document analysis | The case analysis found the bill's address #01-12 against #01-21 on the company's other documents (medium) | Yes, and the finding names both units |

## Risk: why Critical

Critical needs high-severity findings on three or more documents. This case had high findings on four documents:

| Points | Severity | Document | Finding | Genuine? |
|---|---|---|---|---|
| 8 + 8 | high | Payslip | Overlapping net pay; net pay doesn't add up | Yes, the forgery |
| 8 + 2 | high | AI proof of address | DALL-E / OpenAI named in the metadata | Yes, the forgery |
| 8 | high | Passport | Photoshop in the metadata | Yes, the forgery |
| 8 | high | Passport | "MRZ must be two lines of 44 characters; got 1 line (88 chars)" | **No: false positive** (see below) |
| 8 | high | NRIC front | "S9798175B does not match any valid UEN format" | **No: false positive** (see below) |
| 4.5 | medium | Business profile | Company address differs from the utility bill | Yes, the cross-document forgery |
| 3 + 0.75 | medium | FATCA form | Font "Helvetica vs expected Aptos" | **No: false positive** (see below) |

Without the false positives, the forgeries alone still put high findings on three documents: the payslip, the AI proof of address and the passport. So the case would still be Critical, and the match does not depend on the false positives.

## Extraction misses

**Key fields (the headline 3 misses):**
- **Bank statement, `transactions`:** the extractor read 48 of the 49 transaction rows.
- **Personal utility bill, `individual_address`:** no address was saved under the expected name. The value was read but filed under a different field name.
- **Company utility bill, `company_address`:** the same problem as the personal bill.

**Other printed values missed:**
- **Both utility bills:** `bill_date`, `due_date` and `total_payable` were read but saved under other names. `entity_type` and `billing_period_end` were not read.
- **Payslip:** `employee_cpf` was not read as its own field.
- **FATCA form:** the second controlling person was not read.
- **AI proof of address:** `sex` was not read.

The utility bills cause most of the gap between the key-field score and the all-values score. The extractor reads their values but doesn't use the schema's field names for them.

## False positives found

These would affect every case, so they are worth fixing before the full run:

1. **NRIC number checked as a UEN.** `numerical_checks_tool` ran `ID.UEN_FORMAT` on the NRIC front and reported the NRIC number as an invalid UEN. This was rated high, so it alone can push a clean case to High.
2. **Passport MRZ read as one line.** The extractor joined the two 44-character MRZ lines into one 88-character line, and the MRZ check then failed it. This is either an extraction problem or a check that is too strict about line breaks.
3. **FATCA font reference.** The FATCA form is compared against a reference that expects the Aptos font, but the generated form uses Helvetica. This will flag every case's FATCA form.

## Run notes

- An officer step was needed on 7 documents before they could be approved. The runner did what an officer accepting the agents' work would do: it confirmed the classifier's type, or saved the fields unchanged. No value was corrected, and extraction was scored before these steps.
- **One clean document was not scanned:** `POR_UtilityBill_20260925.jpeg`. Confirming its type sent it back through processing, and approval was retried before it had finished. The runner now waits for documents to settle before retrying, so this won't recur.
- The earlier run of this case, GLDB-1, used the old runner, so none of its forged documents were scanned. Both GLDB-1 and GLDB-2 are still in Supabase.
- The secretary NRIC false positive from the first run is gone. Every NRIC in the regenerated documents now has a valid check letter.
