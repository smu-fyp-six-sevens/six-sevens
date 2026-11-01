import type {
  CaseRiskFinding,
  DocumentCaseMatch,
  DocumentRecord,
} from '../../services/uploadService';
import {
  formatRoutingOutcome,
} from '../../services/uploadService';
import type {
  FileIssue,
} from '../../utils/fileValidation';
import {
  MAX_FILE_SIZE_BYTES,
} from '../../utils/fileValidation';

const MAX_FILE_SIZE_MB = Math.floor(MAX_FILE_SIZE_BYTES / (1024 * 1024));

const SEVERITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

// Most severe first, then most points. Points alone would put a repeated high (scored at a quarter)
// below a medium.
export function compareFindings(a: CaseRiskFinding, b: CaseRiskFinding): number {
  return (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4) || b.points - a.points;
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
