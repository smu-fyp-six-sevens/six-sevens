import type {
  DocumentCaseMatch,
  DocumentRecord,
} from '../../services/uploadService';
import {
  ADDITIONAL_DOCUMENT_TYPES,
  REQUIRED_DOCUMENT_TYPES,
  formatRoutingOutcome,
} from '../../services/uploadService';
import type {
  FileIssue,
} from '../../utils/fileValidation';
import {
  MAX_FILE_SIZE_BYTES,
} from '../../utils/fileValidation';
import { humanizeFieldKey } from '../../utils/fieldLabels';

const MAX_FILE_SIZE_MB = Math.floor(MAX_FILE_SIZE_BYTES / (1024 * 1024));

export const REQUIREMENT_LABELS = new Map(
  [...REQUIRED_DOCUMENT_TYPES, ...ADDITIONAL_DOCUMENT_TYPES].map((requirement) => [
    requirement.value,
    requirement.label,
  ]),
);

export function isExtractionNotConfiguredFailure(reason: string | null | undefined): boolean {
  return Boolean(reason?.startsWith('Extraction invocation failed'));
}

// Fields that carry structured data (arrays/objects) rather than a single display-able value.
export const NON_DISPLAYED_FIELD_KEYS = new Set(['transactions']);

export function displayableFieldEntries(fields: Record<string, unknown>): [string, unknown][] {
  return Object.entries(fields).filter(([key]) => !NON_DISPLAYED_FIELD_KEYS.has(key));
}

// True for a value the LLM returned as an array or nested object rather than a single scalar.
export function isStructuredValue(value: unknown): boolean {
  return Array.isArray(value) || (typeof value === 'object' && value !== null);
}

// Renders any field value.
export function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) {
    return value.map((item) => formatFieldValue(item)).join('; ');
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, nested]) => `${humanizeFieldKey(key)}: ${formatFieldValue(nested)}`)
      .join(', ');
  }
  return String(value);
}

// Displayable fields whose value is structured (array/object).
export function structuredFieldEntries(fields: Record<string, unknown>): [string, unknown][] {
  return displayableFieldEntries(fields).filter(([, value]) => isStructuredValue(value));
}

export function fieldsToDraft(fields: Record<string, unknown>): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const [key, value] of displayableFieldEntries(fields)) {
    if (isStructuredValue(value)) continue;
    draft[key] = value === null || value === undefined ? '' : String(value);
  }
  return draft;
}

// The case snapshot wraps each extracted field as {value, confidence}.
export function extractedFieldValue(
  fields: Record<string, unknown> | null,
  key: string,
): unknown {
  const field = fields?.[key];
  if (field && typeof field === 'object' && 'value' in field) {
    return (field as { value: unknown }).value;
  }
  return field;
}

// Names of the individuals whose NRIC/passport has been accepted, one per person.
export function acceptedIdentityNames(documents: DocumentRecord[]): string[] {
  const namesByIdentity = new Map<string, string>();

  for (const document of documents) {
    if (
      document.status !== 'analyzed' ||
      document.routing_status !== 'accepted' ||
      (document.document_type !== 'nric' && document.document_type !== 'passport')
    ) {
      continue;
    }

    const name = extractedFieldValue(document.extracted_fields, 'individual_name');
    const idNumber = extractedFieldValue(document.extracted_fields, 'nric_number');
    const displayName = typeof name === 'string' ? name.trim() : '';
    const identityKey =
      (typeof idNumber === 'string' ? idNumber.replace(/\s+/g, '').toUpperCase() : '') ||
      displayName.toUpperCase().replace(/\s+/g, ' ');
    if (!identityKey) continue;

    if (displayName || !namesByIdentity.has(identityKey)) {
      namesByIdentity.set(identityKey, namesByIdentity.get(identityKey) || displayName);
    }
  }

  return Array.from(namesByIdentity.values()).filter(Boolean);
}

export function describeIssue(issue: FileIssue): string {
  switch (issue.type) {
    case 'empty':
      return 'File is empty (0 bytes).';
    case 'too-large':
      return `File exceeds the ${MAX_FILE_SIZE_MB} MB limit.`;
    case 'invalid-signature':
      return 'File content does not match a supported type.';
    case 'invalid-filename':
      return 'Filename is invalid or unsafe.';
    case 'duplicate':
      return 'This exact file has already been uploaded for this case.';
  }
}

// Eligible to select and approve: analyzed, with a clean case match.
export function isEligibleForApproval(
  document: DocumentRecord,
  caseMatches: Map<string, DocumentCaseMatch>,
): boolean {
  return (
    document.status === 'analyzed' &&
    formatRoutingOutcome(document, caseMatches.get(document.id)) === 'Accepted'
  );
}
