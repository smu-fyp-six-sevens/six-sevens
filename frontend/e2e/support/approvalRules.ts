/// <reference types="node" />
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The mocked backend's copy of the approval rules (backend document_service.approval_refusal). Kept apart from the
 * rest of the mock so a unit test can run it against the same cases as the backend and the real frontend rules
 * (approvalRules.test.ts, from tests/fixtures/regression/approval_rules.json).
 */

/** Each document type's required extraction fields, exported from the backend's EXTRACTION_SCHEMAS. */
const REQUIRED_FIELDS: Record<
  string,
  { required: string[]; allow_empty: string[]; required_when_individual: string[] }
> = JSON.parse(
  readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../backend/tests/fixtures/regression/required_fields.json'),
    'utf8',
  ),
);

// settings.EXTRACTION_CONFIDENCE_THRESHOLD and CLASSIFICATION_CONFIDENCE_THRESHOLD in backend config.py.
const EXTRACTION_CONFIDENCE_THRESHOLD = 0.9;
const CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.85;

export interface ApprovalInput {
  status: string;
  document_type: string | null;
  classification_confidence: number | null;
  extracted_fields: Record<string, unknown> | null;
  fields_reviewed_at?: string | null;
}

const unwrap = (raw: unknown): unknown =>
  raw && typeof raw === 'object' && 'value' in (raw as object) ? (raw as { value: unknown }).value : raw;

const isEmpty = (value: unknown): boolean => {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return !value.trim();
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value as object).length === 0;
  return false;
};

/** Same rule as backend case_service.missing_required_fields. */
export function missingRequiredFields(document: Pick<ApprovalInput, 'document_type' | 'extracted_fields'>): string[] {
  const fields = document.extracted_fields ?? {};
  const schema = REQUIRED_FIELDS[document.document_type ?? ''] ?? {
    required: [],
    allow_empty: [],
    required_when_individual: [],
  };
  const hasValue = (name: string) => !isEmpty(unwrap(fields[name]));
  const individualAccount =
    schema.required_when_individual.length > 0 && hasValue('account_holder_name') && !hasValue('uen');
  const required = individualAccount ? schema.required_when_individual : schema.required;
  return [...required].sort().filter((name) => {
    if (!(name in fields)) return true;
    const value = unwrap(fields[name]);
    if (Array.isArray(value) && value.length === 0) return !schema.allow_empty.includes(name);
    return isEmpty(value);
  });
}

/** Same rule as backend models.is_classification_confirmed: an Other or untyped document needs the officer. */
export function isClassificationConfirmed(document: Pick<ApprovalInput, 'document_type' | 'classification_confidence'>): boolean {
  const confidence = document.classification_confidence;
  if (confidence === null) return false;
  if (!document.document_type || document.document_type === 'unrecognised') return confidence >= 1.0;
  return confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD;
}

/** Same rule as backend case_service.has_extracted_information: at least one non-empty value. */
function hasExtractedInformation(document: Pick<ApprovalInput, 'extracted_fields'>): boolean {
  return Object.values(document.extracted_fields ?? {}).some((field) => !isEmpty(unwrap(field)));
}

/** Same rules, in the same order, as backend document_service.approval_refusal: why it can't be approved, or null. */
export function approvalRefusal(document: ApprovalInput): string | null {
  if (document.status !== 'analyzed') return 'Only completed documents can be approved.';
  if (!isClassificationConfirmed(document)) return 'Cannot approve document. Confirm the document type first.';
  if (!hasExtractedInformation(document)) return 'Cannot approve document. No information was extracted from it.';
  const missing = missingRequiredFields(document);
  if (missing.length > 0) return `Cannot approve document. Required fields are missing or empty: ${missing.join(', ')}`;
  const lowConfidence = Object.entries(document.extracted_fields ?? {})
    .filter(([, field]) => {
      const confidence = field && typeof field === 'object' ? (field as { confidence?: unknown }).confidence : null;
      return typeof confidence === 'number' && confidence < EXTRACTION_CONFIDENCE_THRESHOLD;
    })
    .map(([name]) => name)
    .sort();
  if (lowConfidence.length > 0) {
    return `Cannot approve document. Low-confidence fields must be reviewed and saved first: ${lowConfidence.join(', ')}`;
  }
  if (document.document_type === 'unrecognised' && !document.fields_reviewed_at) {
    return 'Cannot approve document. Review and save the fields of a document set to Other first.';
  }
  return null;
}
