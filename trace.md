▥
Customer Risk Summary
High risk
Score 64/100
Based on 2 documents
2 high · 2 medium · 4 low
Rated High from the combined severity of 8 finding(s).

C007_CertIncorp.pdf
Certificate of incorporation
1 high
External validation: UEN 201567890G is not in ACRA's register or any other UEN issuance agency's register
›

C007_NOA.pdf
Notice of assessment
1 high
2 medium
External validation: UEN 201567890G is not in ACRA's register or any other UEN issuance agency's register
›
Show 4 low-severity findings
Couldn't verify (2)
◇
Case Analysis
Run again
This application presents a high fraud risk (score 64/100), primarily because the company's registration number does not appear in any official business registry. This same invalid number appears on both the certificate of incorporation and the tax notice, which raises concerns about document authenticity. Additionally, the tax notice shows unusual financial figures where the tax owed, a tax credit, and a tax payment are all identical at $32,521, resulting in zero net tax despite substantial income—an outcome that would be extremely rare in legitimate tax assessments. The tax notice also shows signs of physical alteration, with the tax discharge amount positioned significantly off from where it should normally appear on the document. Several checks for common fraud indicators, including font consistency, image manipulation, and spelling errors, found nothing wrong. To substantially improve confidence in this application, the applicant would need to provide official correspondence from the tax authority explaining how these identical figures were calculated, or an independent auditor's report commenting on the tax position and any unusual adjustments.

medium
Unusual behaviour: The amended NOA shows tax payable, tax rebate, and tax discharge amount all equal at 32,521.00, resulting in zero net tax on substantial chargeable income of 382,600 — an unusual outcome for a legitimate amended assessment.
All three figures (tax_payable, tax_rebate, tax_discharge_amount) are identically 32,521.00 per the extracted fields, meaning the amended assessment produces exactly zero net tax on a chargeable income of 382,600. The refutation check (a legitimate amended NOA where rebate equals full tax payable) describes a scenario that is itself the anomaly, not a refutation of it.
What would change the score
Currently 64 (High). If everything below supports the client, the best case is 63 (High).

Tax Authority Correspondence or Assessment Details — would become 63 (High)
Official communication from the tax authority explaining the amended NOA calculation, including how tax payable, rebate, and discharge were determined and reconciled.
Auditor's Report or Financial Statement Notes — would become 63 (High)
Independent auditor commentary on the tax position and any unusual tax adjustments or credits claimed in the year corresponding to the amended NOA.
An estimate that assumes the evidence supports the client. If it does not, the score stays where it is.


Case analysis trace (debug)
run_id: 3c774244-0c32-49f6-8a6a-9b093ab63464
status: complete
started_at: 2026-10-14T16:58:25.476768Z
finished_at: 2026-10-14T16:58:54.383069Z
hypotheses proposed / kept after verification: 2 / 1
Scoring
High · score 64/100 · 23.75 points from 8 finding(s) on 2/2 documents
without cross-document findings: High · 62 (1 added)
Rated High from the combined severity of 8 finding(s).
points	severity	category	tool	document
8	high	registry_discrepancy	external_validation_tool	C007_CertIncorp.pdf
8	high	registry_discrepancy	external_validation_tool	C007_NOA.pdf
3	medium	tampering_forensic	layout_anomalies_tool	C007_NOA.pdf
3	medium	behavioural_anomaly	cross_document_analysis	C007_NOA.pdf (case)
1	low	tampering_forensic	layout_anomalies_tool	C007_CertIncorp.pdf
0.25	low	tampering_forensic	layout_anomalies_tool	C007_CertIncorp.pdf
0.25	low	tampering_forensic	layout_anomalies_tool	C007_NOA.pdf
0.25	low	tampering_forensic	layout_anomalies_tool	C007_NOA.pdf
Rules applied
{
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
  "critical_needs_high_documents": 3,
  "noise_cap_per_document": 6,
  "repeat_weight": 0.25,
  "unassessed_checks": [
    {
      "tool": "metadata_tool",
      "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
      "document": "C007_NOA.pdf"
    },
    {
      "tool": "metadata_tool",
      "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
      "document": "C007_CertIncorp.pdf"
    }
  ]
}
Outlook (current 64, best case 63)
Tax Authority Correspondence or Assessment Details → 63 (High)
{
  "title": "Tax Authority Correspondence or Assessment Details",
  "effect": "partial",
  "scored": true,
  "source": "agent",
  "dropped": [],
  "addressed": [
    {
      "points": 3,
      "category": "behavioural_anomaly",
      "finding_id": "xd-h2",
      "points_after": 1.5
    }
  ],
  "level_if_supports": "High",
  "score_if_supports": 63
}
Auditor's Report or Financial Statement Notes → 63 (High)
{
  "title": "Auditor's Report or Financial Statement Notes",
  "effect": "partial",
  "scored": true,
  "source": "agent",
  "dropped": [],
  "addressed": [
    {
      "points": 3,
      "category": "behavioural_anomaly",
      "finding_id": "xd-h2",
      "points_after": 1.5
    }
  ],
  "level_if_supports": "High",
  "score_if_supports": 63
}
Full step log (18 steps)
[
  {
    "at": 0,
    "step": "run_start",
    "case_id": "1172d529-65ef-4be7-b098-1b2e2ff66f33",
    "documents": [
      {
        "id": "50a6f8fb-9865-4592-b77e-e51a60201ff8",
        "type": "notice_of_assessment",
        "status": "detected",
        "filename": "C007_NOA.pdf"
      },
      {
        "id": "82454cd5-95e9-49fc-be6c-4168cae63c19",
        "type": "certificate_of_incorporation",
        "status": "detected",
        "filename": "C007_CertIncorp.pdf"
      }
    ],
    "fingerprint": "90a106f145debb87ddc55d9bc4795c7d5d8f11f623f6ad16c294e8753282067e"
  },
  {
    "at": 0,
    "step": "dossier",
    "documents": {
      "notice_of_assessment_1": {
        "id": "50a6f8fb-9865-4592-b77e-e51a60201ff8",
        "filename": "C007_NOA.pdf",
        "findings": 4
      },
      "certificate_of_incorporation_1": {
        "id": "82454cd5-95e9-49fc-be6c-4168cae63c19",
        "filename": "C007_CertIncorp.pdf",
        "findings": 3
      }
    }
  },
  {
    "at": 12.72,
    "raw": null,
    "via": "tool",
    "step": "hypotheses",
    "usage": {
      "input_tokens": 3578,
      "total_tokens": 4391,
      "output_tokens": 813,
      "input_token_details": {
        "cache_read": 0,
        "cache_creation": 0,
        "ephemeral_1h_input_tokens": 0,
        "ephemeral_5m_input_tokens": 1606
      }
    },
    "proposed": [
      {
        "id": "h1",
        "claim": "The UEN 201567890G on the Certificate of Incorporation and the Notice of Assessment does not exist in ACRA's register, meaning the company may not be legitimately incorporated and the documents may be fabricated.",
        "proof": "Both documents share the same unregistered UEN, confirmed by high-severity external validation findings on both.",
        "checks": [
          {
            "args": {
              "items": [
                {
                  "field": "uen",
                  "value": "201567890G",
                  "document": "certificate_of_incorporation_1"
                },
                {
                  "field": "tax_reference_number",
                  "value": "201567890G",
                  "document": "notice_of_assessment_1"
                }
              ]
            },
            "tool": "compare_identifiers",
            "refutation_probe": false
          },
          {
            "args": {
              "names": [
                "uen",
                "incorporation_date",
                "company_name"
              ],
              "document": "certificate_of_incorporation_1"
            },
            "tool": "get_fields",
            "refutation_probe": false
          },
          {
            "args": {
              "names": [
                "tax_reference_number",
                "company_name",
                "year_of_assessment",
                "noa_date"
              ],
              "document": "notice_of_assessment_1"
            },
            "tool": "get_fields",
            "refutation_probe": true
          }
        ],
        "documents": [
          "certificate_of_incorporation_1",
          "notice_of_assessment_1"
        ],
        "refutation_test": "A UEN lookup returning a valid registered entity for 201567890G would refute this.",
        "refutation_document": null
      },
      {
        "id": "h2",
        "claim": "The Notice of Assessment for Year of Assessment 2025 is dated 14 Jul 2025, but the company was only incorporated on 23 Mar 2015, and the NOA tax rebate exactly equals the full tax payable (making net tax zero), which is arithmetically implausible for a legitimate amended assessment.",
        "proof": "tax_payable = 32,521.00 and tax_rebate = 32,521.00 and tax_discharge_amount = 32,521.00 — all three are identical, meaning the assessment results in zero net tax, which is unusual for an amended NOA.",
        "checks": [
          {
            "args": {
              "names": [
                "tax_payable",
                "tax_rebate",
                "tax_discharge_amount",
                "chargeable_income_after_exempt",
                "eci",
                "assessment_type",
                "noa_date",
                "previous_assessment_date"
              ],
              "document": "notice_of_assessment_1"
            },
            "tool": "get_fields",
            "refutation_probe": false
          },
          {
            "args": {
              "names": [
                "incorporation_date",
                "company_name"
              ],
              "document": "certificate_of_incorporation_1"
            },
            "tool": "get_fields",
            "refutation_probe": true
          }
        ],
        "documents": [
          "notice_of_assessment_1"
        ],
        "refutation_test": "A legitimate amended NOA where the rebate equals the full tax payable and discharge amount would refute this as anomalous.",
        "refutation_document": null
      }
    ],
    "stop_reason": "tool_use",
    "dossier_chars": 3976,
    "dropped_for_bias": []
  },
  {
    "at": 12.88,
    "args": {
      "items": [
        {
          "field": "uen",
          "value": "201567890G",
          "document": "certificate_of_incorporation_1"
        },
        {
          "field": "tax_reference_number",
          "value": "201567890G",
          "document": "notice_of_assessment_1"
        }
      ]
    },
    "step": "tool_call",
    "tool": "compare_identifiers",
    "probe": false,
    "result": "{\"available\": true, \"same\": true, \"mismatches\": [], \"breach_flags\": [], \"values\": [{\"document\": \"certificate_of_incorporation_1\", \"field\": \"uen\", \"value\": \"201567890G\", \"ref\": \"ref:certificate_of_incorporation_1:uen\"}, {\"document\": \"notice_of_assessment_1\", \"field\": \"tax_reference_number\", \"value\": \"201567890G\", \"ref\": \"ref:notice_of_assessment_1:tax_reference_number\"}], \"missing\": []}",
    "hypothesis": "h1"
  },
  {
    "at": 12.88,
    "args": {
      "names": [
        "tax_payable",
        "tax_rebate",
        "tax_discharge_amount",
        "chargeable_income_after_exempt",
        "eci",
        "assessment_type",
        "noa_date",
        "previous_assessment_date"
      ],
      "document": "notice_of_assessment_1"
    },
    "step": "tool_call",
    "tool": "get_fields",
    "probe": false,
    "result": "{\"available\": true, \"fields\": [{\"field\": \"tax_payable\", \"value\": \"\\\"32,521.00\\\"\", \"ref\": \"ref:notice_of_assessment_1:tax_payable\"}, {\"field\": \"tax_rebate\", \"value\": \"\\\"32,521.00\\\"\", \"ref\": \"ref:notice_of_assessment_1:tax_rebate\"}, {\"field\": \"tax_discharge_amount\", \"value\": \"\\\"32,521.00\\\"\", \"ref\": \"ref:notice_of_assessment_1:tax_discharge_amount\"}, {\"field\": \"chargeable_income_after_exempt\", \"value\": \"\\\"382,600\\\"\", \"ref\": \"ref:notice_of_assessment_1:chargeable_income_after_exempt\"}, {\"field\": \"eci\", \"value\": \"\\\"485,100\\\"\", \"ref\": \"ref:notice_of_assessment_1:eci\"}, {\"field\": \"assessment_type\", \"value\": \"\\\"Amended\\\"\", \"ref\": \"ref:notice_of_assessment_1:assessment_type\"}, {\"field\": \"noa_date\", \"",
    "hypothesis": "h2"
  },
  {
    "at": 12.88,
    "args": {
      "names": [
        "uen",
        "incorporation_date",
        "company_name"
      ],
      "document": "certificate_of_incorporation_1"
    },
    "step": "tool_call",
    "tool": "get_fields",
    "probe": false,
    "result": "{\"available\": true, \"fields\": [{\"field\": \"uen\", \"value\": \"\\\"201567890G\\\"\", \"ref\": \"ref:certificate_of_incorporation_1:uen\"}, {\"field\": \"incorporation_date\", \"value\": \"\\\"23 Mar 2015\\\"\", \"ref\": \"ref:certificate_of_incorporation_1:incorporation_date\"}, {\"field\": \"company_name\", \"value\": \"\\\"IVORY PHARMA DISTRIBUTION PTE LTD\\\"\", \"ref\": \"ref:certificate_of_incorporation_1:company_name\"}]}",
    "hypothesis": "h1"
  },
  {
    "at": 12.88,
    "args": {
      "names": [
        "incorporation_date",
        "company_name"
      ],
      "document": "certificate_of_incorporation_1"
    },
    "step": "tool_call",
    "tool": "get_fields",
    "probe": true,
    "result": "{\"available\": true, \"fields\": [{\"field\": \"incorporation_date\", \"value\": \"\\\"23 Mar 2015\\\"\", \"ref\": \"ref:certificate_of_incorporation_1:incorporation_date\"}, {\"field\": \"company_name\", \"value\": \"\\\"IVORY PHARMA DISTRIBUTION PTE LTD\\\"\", \"ref\": \"ref:certificate_of_incorporation_1:company_name\"}]}",
    "hypothesis": "h2"
  },
  {
    "at": 12.88,
    "args": {
      "names": [
        "tax_reference_number",
        "company_name",
        "year_of_assessment",
        "noa_date"
      ],
      "document": "notice_of_assessment_1"
    },
    "step": "tool_call",
    "tool": "get_fields",
    "probe": true,
    "result": "{\"available\": true, \"fields\": [{\"field\": \"tax_reference_number\", \"value\": \"\\\"201567890G\\\"\", \"ref\": \"ref:notice_of_assessment_1:tax_reference_number\"}, {\"field\": \"company_name\", \"value\": \"\\\"IVORY PHARMA DISTRIBUTION PTE LTD\\\"\", \"ref\": \"ref:notice_of_assessment_1:company_name\"}, {\"field\": \"year_of_assessment\", \"value\": \"2025\", \"ref\": \"ref:notice_of_assessment_1:year_of_assessment\"}, {\"field\": \"noa_date\", \"value\": \"\\\"14 Jul 2025\\\"\", \"ref\": \"ref:notice_of_assessment_1:noa_date\"}]}",
    "hypothesis": "h1"
  },
  {
    "at": 21.95,
    "raw": null,
    "via": "tool",
    "step": "judge_reply",
    "usage": {
      "input_tokens": 3911,
      "total_tokens": 4363,
      "output_tokens": 452,
      "input_token_details": {
        "cache_read": 1606,
        "cache_creation": 0,
        "ephemeral_1h_input_tokens": 0,
        "ephemeral_5m_input_tokens": 0
      }
    },
    "parsed": true,
    "stop_reason": "tool_use"
  },
  {
    "at": 21.95,
    "step": "verdict",
    "parsed": true,
    "verdict": {
      "id": "h1",
      "summary": "The UEN 201567890G is consistent across both documents, but no external ACRA registry lookup was performed, so fabrication cannot be confirmed from the available evidence.",
      "conclusive": false
    },
    "hypothesis": "h1"
  },
  {
    "at": 21.95,
    "step": "verdict",
    "parsed": true,
    "verdict": {
      "id": "h2",
      "summary": "The amended NOA shows tax payable, tax rebate, and tax discharge amount all equal at 32,521.00, resulting in zero net tax on substantial chargeable income of 382,600 — an unusual outcome for a legitimate amended assessment.",
      "category": "behavioural_anomaly",
      "severity": "medium",
      "citations": [
        "ref:notice_of_assessment_1:tax_payable",
        "ref:notice_of_assessment_1:tax_rebate",
        "ref:notice_of_assessment_1:tax_discharge_amount",
        "ref:notice_of_assessment_1:chargeable_income_after_exempt",
        "ref:notice_of_assessment_1:assessment_type"
      ],
      "rationale": "All three figures (tax_payable, tax_rebate, tax_discharge_amount) are identically 32,521.00 per the extracted fields, meaning the amended assessment produces exactly zero net tax on a chargeable income of 382,600. The refutation check (a legitimate amended NOA where rebate equals full tax payable) describes a scenario that is itself the anomaly, not a refutation of it.",
      "conclusive": true,
      "probe_summary": "The refutation check did not identify any legitimate basis for a full rebate equalling full tax payable on an amended NOA; the three identical values remain unexplained."
    },
    "hypothesis": "h2"
  },
  {
    "at": 21.95,
    "kept": false,
    "step": "verify",
    "reason": "not conclusive",
    "finding": null,
    "hypothesis": "h1"
  },
  {
    "at": 21.95,
    "kept": true,
    "step": "verify",
    "reason": null,
    "finding": {
      "id": "xd-h2",
      "summary": "The amended NOA shows tax payable, tax rebate, and tax discharge amount all equal at 32,521.00, resulting in zero net tax on substantial chargeable income of 382,600 — an unusual outcome for a legitimate amended assessment.",
      "category": "behavioural_anomaly",
      "severity": "medium",
      "citations": [
        "ref:notice_of_assessment_1:tax_payable",
        "ref:notice_of_assessment_1:tax_rebate",
        "ref:notice_of_assessment_1:tax_discharge_amount",
        "ref:notice_of_assessment_1:chargeable_income_after_exempt",
        "ref:notice_of_assessment_1:assessment_type"
      ],
      "rationale": "All three figures (tax_payable, tax_rebate, tax_discharge_amount) are identically 32,521.00 per the extracted fields, meaning the amended assessment produces exactly zero net tax on a chargeable income of 382,600. The refutation check (a legitimate amended NOA where rebate equals full tax payable) describes a scenario that is itself the anomaly, not a refutation of it.",
      "breach_flags": [],
      "document_ids": [
        "50a6f8fb-9865-4592-b77e-e51a60201ff8"
      ],
      "hypothesis_id": "h2",
      "probe_summary": "The refutation check did not identify any legitimate basis for a full rebate equalling full tax payable on an amended NOA; the three identical values remain unexplained."
    },
    "hypothesis": "h2"
  },
  {
    "at": 21.97,
    "step": "scoring",
    "level": "High",
    "score": 64,
    "state": "scored",
    "points": 23.75,
    "reasons": [
      "Rated High from the combined severity of 8 finding(s)."
    ],
    "findings": [
      {
        "tool": "external_validation_tool",
        "points": 8,
        "source": "document",
        "category": "registry_discrepancy",
        "document": "C007_CertIncorp.pdf",
        "severity": "high",
        "finding_id": "82454cd5:external_validation_tool-1",
        "unreviewed": false
      },
      {
        "tool": "external_validation_tool",
        "points": 8,
        "source": "document",
        "category": "registry_discrepancy",
        "document": "C007_NOA.pdf",
        "severity": "high",
        "finding_id": "50a6f8fb:external_validation_tool-1",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 3,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C007_NOA.pdf",
        "severity": "medium",
        "finding_id": "50a6f8fb:layout_anomalies_tool-1",
        "unreviewed": false
      },
      {
        "tool": "cross_document_analysis",
        "points": 3,
        "source": "cross_document",
        "category": "behavioural_anomaly",
        "document": "C007_NOA.pdf",
        "severity": "medium",
        "finding_id": "xd-h2",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 1,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C007_CertIncorp.pdf",
        "severity": "low",
        "finding_id": "82454cd5:layout_anomalies_tool-1",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 0.25,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C007_CertIncorp.pdf",
        "severity": "low",
        "finding_id": "82454cd5:layout_anomalies_tool-2",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 0.25,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C007_NOA.pdf",
        "severity": "low",
        "finding_id": "50a6f8fb:layout_anomalies_tool-2",
        "unreviewed": false
      },
      {
        "tool": "layout_anomalies_tool",
        "points": 0.25,
        "source": "document",
        "category": "tampering_forensic",
        "document": "C007_NOA.pdf",
        "severity": "low",
        "finding_id": "50a6f8fb:layout_anomalies_tool-3",
        "unreviewed": false
      }
    ],
    "repeat_weight": 0.25,
    "documents_total": 2,
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
    "documents_scanned": 2,
    "unassessed_checks": [
      {
        "tool": "metadata_tool",
        "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
        "document": "C007_NOA.pdf"
      },
      {
        "tool": "metadata_tool",
        "reason": "The file's metadata couldn't be read when it was uploaded: exiftool is not installed on the server that processed this document. This check didn't run, so it says nothing about whether the file was edited.",
        "document": "C007_CertIncorp.pdf"
      }
    ],
    "noise_cap_per_document": 6,
    "cross_document_findings": 1,
    "level_without_cross_document": "High",
    "score_without_cross_document": 62,
    "critical_needs_high_documents": 3
  },
  {
    "at": 24.41,
    "step": "evidence_requests",
    "proposed": [
      {
        "title": "Tax Authority Correspondence or Assessment Details",
        "source": "agent",
        "addresses": [
          "xd-h2"
        ],
        "what_it_shows": "Official communication from the tax authority explaining the amended NOA calculation, including how tax payable, rebate, and discharge were determined and reconciled.",
        "expected_effect": "partial"
      },
      {
        "title": "Auditor's Report or Financial Statement Notes",
        "source": "agent",
        "addresses": [
          "xd-h2"
        ],
        "what_it_shows": "Independent auditor commentary on the tax position and any unusual tax adjustments or credits claimed in the year corresponding to the amended NOA.",
        "expected_effect": "partial"
      }
    ],
    "supporting_documents": []
  },
  {
    "at": 24.41,
    "step": "outlook",
    "requests": [
      {
        "title": "Tax Authority Correspondence or Assessment Details",
        "effect": "partial",
        "scored": true,
        "source": "agent",
        "dropped": [],
        "addressed": [
          {
            "points": 3,
            "category": "behavioural_anomaly",
            "finding_id": "xd-h2",
            "points_after": 1.5
          }
        ],
        "level_if_supports": "High",
        "score_if_supports": 63
      },
      {
        "title": "Auditor's Report or Financial Statement Notes",
        "effect": "partial",
        "scored": true,
        "source": "agent",
        "dropped": [],
        "addressed": [
          {
            "points": 3,
            "category": "behavioural_anomaly",
            "finding_id": "xd-h2",
            "points_after": 1.5
          }
        ],
        "level_if_supports": "High",
        "score_if_supports": 63
      }
    ],
    "current_score": 64,
    "best_case_level": "High",
    "best_case_score": 63,
    "remaining_share": {
      "full": 0,
      "partial": 0.5
    },
    "unreducible_finding_ids": [
      "82454cd5:external_validation_tool-1",
      "50a6f8fb:external_validation_tool-1",
      "50a6f8fb:layout_anomalies_tool-1",
      "82454cd5:layout_anomalies_tool-1",
      "82454cd5:layout_anomalies_tool-2",
      "50a6f8fb:layout_anomalies_tool-2",
      "50a6f8fb:layout_anomalies_tool-3"
    ]
  },
  {
    "at": 28.59,
    "step": "summary",
    "text": "This application presents a high fraud risk (score 64/100), primarily because the company's registration number does not appear in any official business registry. This same invalid number appears on both the certificate of incorporation and the tax notice, which raises concerns about document authenticity. Additionally, the tax notice shows unusual financial figures where the tax owed, a tax credit, and a tax payment are all identical at $32,521, resulting in zero net tax despite substantial income—an outcome that would be extremely rare in legitimate tax assessments. The tax notice also shows signs of physical alteration, with the tax discharge amount positioned significantly off from where it should normally appear on the document. Several checks for common fraud indicators, including font consistency, image manipulation, and spelling errors, found nothing wrong. To substantially improve confidence in this application, the applicant would need to provide official correspondence from the tax authority explaining how these identical figures were calculated, or an independent auditor's report commenting on the tax position and any unusual adjustments."
  },
  {
    "at": 28.59,
    "step": "done",
    "status": "complete",
    "findings": 1
  }
]
Audit (5 tool calls)
{
  "models": {
    "analysis": "claude-sonnet-4-6"
  },
  "prompt_version": "cross-doc-v3",
  "hypotheses_tried": 2,
  "findings_discarded": 1,
  "tool_calls": [
    {
      "args": {
        "items": [
          {
            "field": "uen",
            "document": "certificate_of_incorporation_1"
          },
          {
            "field": "tax_reference_number",
            "document": "notice_of_assessment_1"
          }
        ]
      },
      "refs": [
        "ref:certificate_of_incorporation_1:uen",
        "ref:notice_of_assessment_1:tax_reference_number"
      ],
      "tool": "compare_identifiers",
      "probe": false,
      "document": null,
      "available": true,
      "hypothesis": "h1",
      "breach_flags": []
    },
    {
      "args": {
        "names": [
          "uen",
          "incorporation_date",
          "company_name"
        ],
        "document": "certificate_of_incorporation_1"
      },
      "refs": [
        "ref:certificate_of_incorporation_1:uen",
        "ref:certificate_of_incorporation_1:incorporation_date",
        "ref:certificate_of_incorporation_1:company_name"
      ],
      "tool": "get_fields",
      "probe": false,
      "document": "certificate_of_incorporation_1",
      "available": true,
      "hypothesis": "h1",
      "breach_flags": []
    },
    {
      "args": {
        "names": [
          "tax_reference_number",
          "company_name",
          "year_of_assessment",
          "noa_date"
        ],
        "document": "notice_of_assessment_1"
      },
      "refs": [
        "ref:notice_of_assessment_1:tax_reference_number",
        "ref:notice_of_assessment_1:company_name",
        "ref:notice_of_assessment_1:year_of_assessment",
        "ref:notice_of_assessment_1:noa_date"
      ],
      "tool": "get_fields",
      "probe": true,
      "document": "notice_of_assessment_1",
      "available": true,
      "hypothesis": "h1",
      "breach_flags": []
    },
    {
      "args": {
        "names": [
          "tax_payable",
          "tax_rebate",
          "tax_discharge_amount",
          "chargeable_income_after_exempt",
          "eci",
          "assessment_type",
          "noa_date",
          "previous_assessment_date"
        ],
        "document": "notice_of_assessment_1"
      },
      "refs": [
        "ref:notice_of_assessment_1:tax_payable",
        "ref:notice_of_assessment_1:tax_rebate",
        "ref:notice_of_assessment_1:tax_discharge_amount",
        "ref:notice_of_assessment_1:chargeable_income_after_exempt",
        "ref:notice_of_assessment_1:eci",
        "ref:notice_of_assessment_1:assessment_type",
        "ref:notice_of_assessment_1:noa_date",
        "ref:notice_of_assessment_1:previous_assessment_date"
      ],
      "tool": "get_fields",
      "probe": false,
      "document": "notice_of_assessment_1",
      "available": true,
      "hypothesis": "h2",
      "breach_flags": []
    },
    {
      "args": {
        "names": [
          "incorporation_date",
          "company_name"
        ],
        "document": "certificate_of_incorporation_1"
      },
      "refs": [
        "ref:certificate_of_incorporation_1:incorporation_date",
        "ref:certificate_of_incorporation_1:company_name"
      ],
      "tool": "get_fields",
      "probe": true,
      "document": "certificate_of_incorporation_1",
      "available": true,
      "hypothesis": "h2",
      "breach_flags": []
    }
  ]
}
⊕
Recommended Supporting Documents
✓
No additional documents needed.
If anything in the submitted documents needs further evidence, the documents to request from the client will be listed here.