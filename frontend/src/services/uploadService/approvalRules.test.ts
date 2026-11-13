import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { approvalRefusal, missingRequiredFields } from '../../../e2e/support/approvalRules';
import { canApproveDocument } from './documentStatus';
import type { DocumentRecord } from './types';

// The backend's answers for each approval case (test_regression_fixtures.py writes this file).
interface ApprovalCase {
  name: string;
  document: {
    status: DocumentRecord['status'];
    document_type: string | null;
    classification_confidence: number | null;
    fields_reviewed_at: string | null;
  };
  fields: Record<string, { value: unknown; confidence: number | null }>;
  missing_required_fields: string[];
  refusal: string | null;
}

const CASES: ApprovalCase[] = JSON.parse(
  readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../backend/tests/fixtures/regression/approval_rules.json'),
    'utf8',
  ),
);

describe('approval rules agree with the backend', () => {
  it.each(CASES.map((c) => [c.name, c] as const))('%s', (_name, approvalCase) => {
    const { document, fields, missing_required_fields, refusal } = approvalCase;
    const extracted = fields as Record<string, unknown>;
    const mockInput = {
      ...document,
      extracted_fields: extracted,
    };

    // The Playwright mock (what the approve endpoint answers in the e2e suite).
    expect(approvalRefusal(mockInput)).toBe(refusal);
    expect(missingRequiredFields(mockInput)).toEqual(missing_required_fields);

    // The page's button (canApproveDocument): enabled exactly when the backend would approve.
    expect(
      canApproveDocument({
        ...document,
        extracted_fields: extracted,
        missing_required_fields,
      }),
    ).toBe(refusal === null);
  });
});
