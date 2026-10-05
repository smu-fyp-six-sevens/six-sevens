import type {
  DocumentRecord,
} from './types';

// Mirrors REQUIRED_DOCUMENT_TYPES in backend/app/models.py.
export const REQUIRED_DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: 'id_document', label: 'Identity document (NRIC/Passport)' },
  { value: 'bank_statement', label: 'Corporate bank statement' },
  { value: 'proof_of_address', label: 'Personal proof of address' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS' },
  { value: 'certificate_of_incorporation', label: 'Certificate of incorporation' },
  { value: 'certificate_of_incumbency', label: 'Certificate of incumbency' },
  { value: 'company_constitution', label: 'Company constitution' },
  { value: 'credit_report', label: 'Credit report' },
  { value: 'payslip', label: 'Payslip(s)' },
  { value: 'source_of_wealth', label: 'Source of (corporate) wealth' },
  { value: 'notice_of_assessment', label: 'NOA' },
];

// Mirrors ADDITIONAL_DOCUMENT_TYPES in backend/app/models.py.
export const ADDITIONAL_DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: 'company_proof_of_address', label: 'Company proof of address' },
  { value: 'list_of_loans', label: 'List of loans' },
  { value: 'source_of_personal_wealth', label: 'Source of (personal) wealth' },
];

// Mirrors KNOWN_DOCUMENT_TYPES in backend/app/extraction_prompts.py.
export const DOCUMENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'bank_statement', label: 'Bank statement' },
  { value: 'certificate_of_incorporation', label: 'Certificate of incorporation' },
  { value: 'payslip', label: 'Payslip' },
  { value: 'passport', label: 'Passport' },
  { value: 'nric', label: 'NRIC' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS declaration' },
  { value: 'certificate_of_incumbency', label: 'Certificate of incumbency' },
  { value: 'company_constitution', label: 'Company constitution' },
  { value: 'proof_of_address', label: 'Proof of address' },
  { value: 'source_of_wealth', label: 'Source of wealth' },
  { value: 'credit_report', label: 'Credit report' },
  { value: 'list_of_loans', label: 'List of loans' },
  { value: 'notice_of_assessment', label: 'Notice of assessment' },
  { value: 'unrecognised', label: 'Other' },
];

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  bank_statement: 'Bank statement',
  certificate_of_incorporation: 'Certificate of incorporation',
  payslip: 'Payslip',
  passport: 'Passport',
  nric: 'NRIC',
  fatca_crs_declaration: 'FATCA/CRS Declaration',
  certificate_of_incumbency: 'Certificate of incumbency',
  company_constitution: 'Company constitution',
  proof_of_address: 'Proof of address',
  source_of_wealth: 'Source of wealth',
  credit_report: 'Credit report',
  list_of_loans: 'List of loans',
  notice_of_assessment: 'Notice of assessment',
  unrecognised: 'Unrecognised',
};

// 'unrecognised' starts out as the classifier's "couldn't tell" result and only becomes "Other" once.
export function isConfirmedOther(
  document: Pick<DocumentRecord, 'document_type' | 'classification_confidence'>,
): boolean {
  return (
    document.document_type === 'unrecognised' &&
    document.classification_confidence === 1
  );
}

export function formatDocumentType(
  documentType: string | null | undefined,
  classificationConfidence?: number | null,
): string {
  if (!documentType) {
    return '—';
  }

  if (documentType === 'unrecognised') {
    return classificationConfidence === 1 ? 'Other' : 'Unrecognised';
  }

  return (
    DOCUMENT_TYPE_LABELS[documentType] ??
    documentType
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase())
  );
}
