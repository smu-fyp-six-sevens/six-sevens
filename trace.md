◇
Case Analysis
Run again
The case is rated Critical risk (score 82 out of 100). The main findings are: S8000959H in NRIC Number has an invalid check letter — expected C, got H; Postal code 101897 in Individual Address doesn't exist in OneMap's register of Singapore addresses; UEN 201878901H is not in ACRA's register or any other UEN issuance agency's register.

✓
No cross-document findings.
Nothing was proven across this case's documents.


Case analysis trace (debug)
run_id: be863b7d-c490-4030-98fb-1447f0f817a6
status: complete
started_at: 2026-10-14T14:51:25.537170Z
finished_at: 2026-10-14T14:52:01.002960Z
hypotheses proposed / kept after verification: 0 / 0
Scoring
Critical · score 82/100 · 31.24 points from 14 finding(s) on 4/4 documents
without cross-document findings: Critical · 82 (0 added)
Rated Critical: high-severity findings on 3 documents.
points	severity	category	tool	document
8	high	tampering_forensic	numerical_checks_tool	C008-P2_NRICPassport.pdf
8	high	registry_discrepancy	external_validation_tool	C008-P2_ProofAddress.pdf
8	high	registry_discrepancy	external_validation_tool	C008_CertIncorp.pdf
2.25	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
1	low	tampering_forensic	layout_anomalies_tool	C008_CertIncorp.pdf
0.56	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.56	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.56	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.56	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.56	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.56	medium	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.25	low	tampering_forensic	layout_anomalies_tool	C008_CertIncorp.pdf
0.19	low	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
0.19	low	tampering_forensic	numerical_checks_tool	C008-P2_ProofAddress.pdf
Rules applied
Full step log (8 steps)
[
  {
    "at": 0,
    "step": "run_start",
    "case_id": "019a16e6-1f14-41d0-b56d-825e29eebf41",
    "documents": [
      {
        "id": "c452133a-cc3e-4c6f-83fd-ff0dbd25ff8e",
        "type": "bank_statement",
        "status": "detected",
        "filename": "C008-P2_ProofAddress.pdf"
      },
      {
        "id": "32dd5e9c-ffe0-48c9-b08c-f55aa41b3c9f",
        "type": "payslip",
        "status": "detected",
        "filename": "C008-P2_Payslip.pdf"
      },
      {
        "id": "a2a5ac14-b1d2-4020-9fe8-7420a75a2f09",
        "type": "passport",
        "status": "detected",
        "filename": "C008-P2_NRICPassport.pdf"
      },
      {
        "id": "a25f9fc4-3428-40fa-8b10-4b340f8e3660",
        "type": "certificate_of_incorporation",
        "status": "detected",
        "filename": "C008_CertIncorp.pdf"
      }
    ],
    "fingerprint": "ba8ae98faf9a35c2935ab981e70483175d1ae8b535e14b1ced92ddf74015a3ec"
  },
  {
    "at": 0,
    "step": "dossier",
    "documents": {
      "payslip_1": {
        "id": "32dd5e9c-ffe0-48c9-b08c-f55aa41b3c9f",
        "filename": "C008-P2_Payslip.pdf",
        "findings": 0
      },
      "passport_1": {
        "id": "a2a5ac14-b1d2-4020-9fe8-7420a75a2f09",
        "filename": "C008-P2_NRICPassport.pdf",
        "findings": 1
      },
      "bank_statement_1": {
        "id": "c452133a-cc3e-4c6f-83fd-ff0dbd25ff8e",
        "filename": "C008-P2_ProofAddress.pdf",
        "findings": 10
      },
      "certificate_of_incorporation_1": {
        "id": "a25f9fc4-3428-40fa-8b10-4b340f8e3660",
        "filename": "C008_CertIncorp.pdf",
        "findings": 3
      }
    }
  },
  {
    "at": 30.81,
    "raw": "I'll analyze the documents systematically before forming hypotheses.\n\nKey observations:\n1. Payslip shows net_pay = SGD 8,027.85 for January 2026 — this exact amount appears as max_size in bank_statement_1 transactions\n2. Bank statement period is Jun–Aug 2026; payslip is January 2026 — different periods, but the net pay matching max transaction is worth checking\n3. UEN 201878901H is not in ACRA's register (already flagged)\n4. Passport sex = M but individual_name = GOH WAN TING (typically female name in Singapore context) — but rules say I cannot use gender/name assumptions\n5. MRZ date of birth:",
    "step": "hypotheses",
    "parsed": false,
    "proposed": [],
    "stop_reason": "max_tokens",
    "dossier_chars": 6890
  },
  {
    "at": 30.83,
    "step": "scoring",
    "level": "Critical",
    "score": 82,
    "state": "scored",
    "points": 31.24,
    "reasons": [
      "Rated Critical: high-severity findings on 3 documents."
    ],
    "findings": [
      {
        "tool": "numerical_checks_tool",
        "points": 8,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_NRICPassport.pdf",
        "severity": "high",
        "finding_id": "numerical_checks_tool-1",
        "unreviewed": false
      },
      {
        "tool": "external_validation_tool",
        "points": 8,
        "source": "document",
        "category": "registry_discrepancy",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "high",
        "finding_id": "external_validation_tool-1",
        "unreviewed": false
      },
      {
        "tool": "external_validation_tool",
        "points": 8,
        "source": "document",
        "category": "registry_discrepancy",
        "document": "C008_CertIncorp.pdf",
        "severity": "high",
        "finding_id": "external_validation_tool-1",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 2.25,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-1",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 1,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008_CertIncorp.pdf",
        "severity": "low",
        "finding_id": "layout_anomalies_tool-1",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.56,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-2",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.56,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-3",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.56,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-6",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.56,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-7",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.56,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-8",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.56,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "medium",
        "finding_id": "numerical_checks_tool-9",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 0.25,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008_CertIncorp.pdf",
        "severity": "low",
        "finding_id": "layout_anomalies_tool-2",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.19,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "low",
        "finding_id": "numerical_checks_tool-4",
        "unreviewed": false
      },
      {
        "tool": "numerical_checks_tool",
        "points": 0.19,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C008-P2_ProofAddress.pdf",
        "severity": "low",
        "finding_id": "numerical_checks_tool-5",
        "unreviewed": false
      }
    ],
    "repeat_weight": 0.25,
    "documents_total": 4,
    "severity_points": {
      "low": 1,
      "high": 8,
      "info": 0,
      "medium": 3
    },
    "level_thresholds": [
      [
        8,
        "High"
      ],
      [
        3,
        "Medium"
      ],
      [
        0,
        "Low"
      ]
    ],
    "documents_scanned": 4,
    "unassessed_checks": [
      {
        "tool": "metadata_tool",
        "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
        "document": "C008-P2_ProofAddress.pdf"
      },
      {
        "tool": "external_validation_tool",
        "reason": "No address with a Singapore postal code extracted; Required field(s) not extracted: employee_cpf (or a CPF line in deductions_items); Required field(s) not extracted: employee_cpf and ordinary_wages (or a CPF deduction line and earnings lines)",
        "document": "C008-P2_Payslip.pdf"
      },
      {
        "tool": "metadata_tool",
        "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
        "document": "C008-P2_Payslip.pdf"
      },
      {
        "tool": "metadata_tool",
        "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
        "document": "C008-P2_NRICPassport.pdf"
      },
      {
        "tool": "metadata_tool",
        "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
        "document": "C008_CertIncorp.pdf"
      }
    ],
    "noise_cap_per_document": 6,
    "cross_document_findings": 0,
    "level_without_cross_document": "Critical",
    "score_without_cross_document": 82,
    "critical_needs_high_documents": 3
  },
  {
    "at": 30.83,
    "step": "evidence_requests",
    "proposed": [],
    "rule_based": 0
  },
  {
    "at": 30.83,
    "step": "outlook",
    "requests": [],
    "current_score": 82,
    "best_case_level": "Critical",
    "best_case_score": 82,
    "remaining_share": {
      "full": 0,
      "partial": 0.5
    },
    "unreducible_finding_ids": [
      "numerical_checks_tool-1",
      "external_validation_tool-1",
      "external_validation_tool-1",
      "numerical_checks_tool-1",
      "layout_anomalies_tool-1",
      "numerical_checks_tool-2",
      "numerical_checks_tool-3",
      "numerical_checks_tool-6",
      "numerical_checks_tool-7",
      "numerical_checks_tool-8",
      "numerical_checks_tool-9",
      "layout_anomalies_tool-2",
      "numerical_checks_tool-4",
      "numerical_checks_tool-5"
    ]
  },
  {
    "at": 34.98,
    "step": "summary",
    "text": "The case is rated Critical risk (score 82 out of 100). The main findings are: S8000959H in NRIC Number has an invalid check letter — expected C, got H; Postal code 101897 in Individual Address doesn't exist in OneMap's register of Singapore addresses; UEN 201878901H is not in ACRA's register or any other UEN issuance agency's register."
  },
  {
    "at": 34.98,
    "step": "done",
    "status": "complete",
    "findings": 0
  }
]
Audit (0 tool calls)
{
  "models": {
    "analysis": "claude-sonnet-4-6"
  },
  "prompt_version": "cross-doc-v2",
  "hypotheses_tried": 0,
  "findings_discarded": 0,
  "tool_calls": []
}