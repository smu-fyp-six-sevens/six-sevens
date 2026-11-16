import type { DocumentRecord } from '../../services/uploadService';
import { ADDITIONAL_DOCUMENT_TYPES, REQUIRED_DOCUMENT_TYPES, TERMINAL_DOCUMENT_STATUSES } from '../../services/uploadService';

export type DocumentGroup = 'review' | 'processing' | 'pending';

export interface Coverage {
  idDocument: { covered: number; total: number; missing_side: string | null };
  proofOfAddress: { covered: number; total: number; unmatched_names: string[] };
}

// A required document the case still lacks, with what is known about how far along it is.
export interface MissingRequirement {
  value: string;
  label: string;
  hint: string;
}

// Where an uploaded document waits: with the officer (it was flagged, or its run was cancelled), still being worked on
// by the pipeline, or ready for the officer to approve.
export function documentGroup(document: DocumentRecord, needsReview: boolean): DocumentGroup {
  // A run in flight comes first: whatever was flagged before is about to be replaced.
  if (!TERMINAL_DOCUMENT_STATUSES.has(document.status)) return 'processing';
  if (needsReview || document.status === 'cancelled') return 'review';
  return 'pending';
}

// A cancelled run can't be retried (only a failed or stuck one can), so the way on is a fresh upload.
export const CANCELLED_REASON = 'Cancelled. Delete it or upload it again.';

export function missingRequirements(fulfilled: Set<string>, coverage: Coverage): MissingRequirement[] {
  // Lacking, or met only in part: an address with no matching ID, or an ID with one side missing.
  const incomplete = (value: string) =>
    !fulfilled.has(value) ||
    (value === 'proof_of_address' && coverage.proofOfAddress.unmatched_names.length > 0) ||
    (value === 'id_document' && coverage.idDocument.missing_side !== null);
  return REQUIRED_DOCUMENT_TYPES.filter((requirement) => incomplete(requirement.value)).map((requirement) => {
    let hint = 'Not uploaded yet';
    if (requirement.value === 'proof_of_address') {
      const { covered, total, unmatched_names } = coverage.proofOfAddress;
      if (unmatched_names.length > 0) hint = `Address on file but no matching ID uploaded: ${unmatched_names.join(', ')}`;
      else if (total > 0) hint = `${covered} of ${total} uploaded`;
    } else if (requirement.value === 'id_document') {
      const { covered, total, missing_side } = coverage.idDocument;
      if (missing_side) hint = missing_side === 'front' ? 'Back photo present, front missing.' : 'Front photo present, back missing.';
      else if (total > 0) hint = `${covered} of ${total} uploaded`;
    }
    return { value: requirement.value, label: requirement.label, hint };
  });
}

// The optional documents the case has none of yet, for the card's footnote.
export function missingAdditional(fulfilled: Set<string>): string[] {
  return ADDITIONAL_DOCUMENT_TYPES.filter((requirement) => !fulfilled.has(requirement.value)).map((requirement) => requirement.label);
}
