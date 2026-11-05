import type {
  DocumentRecord,
} from './types';

// The one list of document types (the code the backend stores, and how it reads in the UI). The table labels,
// the type dropdown and the checklist all come from it, so a type is named or renamed here and nowhere else.
const DOCUMENT_TYPES = [
  { value: 'bank_statement', label: 'Bank Statement' },
  { value: 'certificate_of_incorporation', label: 'Certificate of Incorporation' },
  { value: 'payslip', label: 'Payslip' },
  { value: 'passport', label: 'Passport' },
  { value: 'nric', label: 'NRIC' },
  { value: 'fatca_crs_declaration', label: 'FATCA/CRS Declaration' },
  { value: 'certificate_of_incumbency', label: 'Certificate of Incumbency' },
  { value: 'company_constitution', label: 'Company Constitution' },
  { value: 'proof_of_address', label: 'Proof of Address' },
  { value: 'source_of_wealth', label: 'Source of Wealth' },
  { value: 'credit_report', label: 'Credit Report' },
  { value: 'list_of_loans', label: 'List of Loans' },
  { value: 'notice_of_assessment', label: 'Notice of Assessment' },
  { value: 'unrecognised', label: 'Unrecognised' },
];

export const DOCUMENT_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  DOCUMENT_TYPES.map(({ value, label }) => [value, label]),
);

// The type dropdown: the same list, with the "couldn't tell" type offered as the catch-all "Other".
export const DOCUMENT_TYPE_OPTIONS: { value: string; label: string }[] = DOCUMENT_TYPES.map((type) =>
  type.value === 'unrecognised' ? { ...type, label: 'Other' } : type,
);

function sentenceCase(code: string): string {
  const words = code.replace(/_/g, ' ');

  return words.charAt(0).toUpperCase() + words.slice(1);
}

// The checklist names what a case needs, which is not always one document type, so a few read differently from
// the type they are met by. Every other requirement is named like its document type.
const REQUIREMENT_LABELS: Record<string, string> = {
  id_document: 'Identity Document (NRIC/Passport)',
  bank_statement: 'Corporate Bank Statement',
  proof_of_address: 'Personal Proof of Address',
  payslip: 'Payslips',
  source_of_wealth: 'Source of Corporate Wealth',
  company_proof_of_address: 'Company Proof of Address',
  source_of_personal_wealth: 'Source of Personal Wealth',
};

function requirement(value: string): { value: string; label: string } {
  return { value, label: REQUIREMENT_LABELS[value] ?? DOCUMENT_TYPE_LABELS[value] ?? sentenceCase(value) };
}

// Mirrors REQUIRED_DOCUMENT_TYPES in backend/app/models.py (the codes, in checklist order).
export const REQUIRED_DOCUMENT_TYPES = [
  'id_document',
  'bank_statement',
  'proof_of_address',
  'fatca_crs_declaration',
  'certificate_of_incorporation',
  'certificate_of_incumbency',
  'company_constitution',
  'credit_report',
  'payslip',
  'source_of_wealth',
  'notice_of_assessment',
].map(requirement);

// Mirrors ADDITIONAL_DOCUMENT_TYPES in backend/app/models.py.
export const ADDITIONAL_DOCUMENT_TYPES = [
  'company_proof_of_address',
  'list_of_loans',
  'source_of_personal_wealth',
].map(requirement);

// A document's type as listed, with "(Proof of Address)" after it when the document stands as someone's proof of address:
// an NRIC or a personal bank statement that shows an address, say. The backend decides that once the document is
// classified and its fields are read (fulfilled_requirements); until then, or for any other document, it is just the
// type. A document typed Proof of Address already says so.
export function formatDocumentLabel(
  document: Pick<DocumentRecord, 'document_type' | 'classification_confidence' | 'fulfilled_requirements'>,
): string {
  const label = formatDocumentType(document.document_type, document.classification_confidence);
  const standsAsAddressProof =
    document.document_type !== 'proof_of_address' && (document.fulfilled_requirements ?? []).includes('proof_of_address');

  return standsAsAddressProof ? `${label} (Proof of Address)` : label;
}

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
    return classificationConfidence === 1 ? 'Other' : DOCUMENT_TYPE_LABELS.unrecognised;
  }

  return DOCUMENT_TYPE_LABELS[documentType] ?? sentenceCase(documentType);
}
