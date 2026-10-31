"""How officers see document types and checklist requirements: the names the frontend uses
(services/uploadService/documentTypes.ts), for text the backend writes itself (the case report, API messages)."""

from __future__ import annotations

DOCUMENT_TYPE_LABELS = {
    "bank_statement": "Bank Statement",
    "certificate_of_incorporation": "Certificate of Incorporation",
    "payslip": "Payslip",
    "passport": "Passport",
    "nric": "NRIC",
    "fatca_crs_declaration": "FATCA/CRS Declaration",
    "certificate_of_incumbency": "Certificate of Incumbency",
    "company_constitution": "Company Constitution",
    "proof_of_address": "Proof of Address",
    "source_of_wealth": "Source of Wealth",
    "credit_report": "Credit Report",
    "list_of_loans": "List of Loans",
    "notice_of_assessment": "Notice of Assessment",
    "unrecognised": "Unrecognised",
}
REQUIREMENT_LABELS = {
    "id_document": "Identity Document (NRIC/Passport)",
    "bank_statement": "Corporate Bank Statement",
    "proof_of_address": "Personal Proof of Address",
    "payslip": "Payslips",
    "source_of_wealth": "Source of Corporate Wealth",
    "company_proof_of_address": "Company Proof of Address",
    "source_of_personal_wealth": "Source of Personal Wealth",
}


def document_type_label(code: str | None) -> str:
    if not code:
        return "Not classified"
    return DOCUMENT_TYPE_LABELS.get(code, code.replace("_", " ").capitalize())


def requirement_label(code: str) -> str:
    return REQUIREMENT_LABELS.get(code) or DOCUMENT_TYPE_LABELS.get(code) or code.replace("_", " ").capitalize()
