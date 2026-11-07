import { describe, expect, it } from 'vitest';
import { makeDocument } from '../../test/records';
import { canApproveDocument, lowConfidenceFieldNames } from './documentStatus';

// The Approve button follows the backend's own rule (document_service.approve): a document it shows Approve for is
// one the approve endpoint accepts, and one it hides it for, the endpoint refuses.
describe('canApproveDocument', () => {
  it('approves an analysed document whose type is confirmed and whose fields are all reviewed', () => {
    expect(canApproveDocument(makeDocument())).toBe(true);
  });

  it.each([
    ['still extracting', { status: 'extracting' as const }],
    ['already approved', { status: 'approved' as const }],
    ['its type not confirmed (below 0.85)', { classification_confidence: 0.84 }],
    ['an unrecognised type the officer has not confirmed', { document_type: 'unrecognised', classification_confidence: 0.9 }],
    ['nothing extracted', { extracted_fields: {} }],
    ['a required field missing', { missing_required_fields: ['net_pay'] }],
    ['a field still under the review threshold', {
      extracted_fields: { net_pay: { value: '4160.00', confidence: 0.6 } },
    }],
    ['set to Other with its fields not yet saved', {
      document_type: 'unrecognised', classification_confidence: 1, fields_reviewed_at: null,
    }],
  ])('does not approve a document %s', (_, overrides) => {
    expect(canApproveDocument(makeDocument(overrides))).toBe(false);
  });

  it('approves a document set to Other once its fields are saved', () => {
    const other = makeDocument({ document_type: 'unrecognised', classification_confidence: 1, fields_reviewed_at: '2026-11-01T03:00:00Z' });

    expect(canApproveDocument(other)).toBe(true);
  });

  it('treats the classification threshold as inclusive', () => {
    expect(canApproveDocument(makeDocument({ classification_confidence: 0.85 }))).toBe(true);
  });
});

describe('lowConfidenceFieldNames', () => {
  it('lists the fields under 0.9 by name, in order, and ignores values with no confidence', () => {
    const fields = {
      net_pay: { value: '4160.00', confidence: 0.5 },
      employer_name: { value: 'Brightwave', confidence: 0.89 },
      employee_name: { value: 'Tan Wei Ming', confidence: 0.9 },
      remarks: 'printed without a confidence',
    };

    expect(lowConfidenceFieldNames(fields)).toEqual(['employer_name', 'net_pay']);
  });

  it('has nothing to review when nothing was extracted', () => {
    expect(lowConfidenceFieldNames(null)).toEqual([]);
  });
});
