import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DocumentRecord } from '../../services/uploadService';
import { makeDocument } from '../../test/records';
import { ExtractedInformationCard } from './ExtractedInformationCard';
import type { FieldEditing } from './useFieldEditing';

const editing = {
  addFieldError: null, deleteFieldError: null, deletingFieldId: null, isAddFieldOpen: false, isAddingField: false,
  newFieldName: '', newFieldValue: '', setAddFieldError: vi.fn(), setDeleteFieldError: vi.fn(),
  setDeletingFieldId: vi.fn(), setIsAddFieldOpen: vi.fn(), setIsAddingField: vi.fn(), setNewFieldName: vi.fn(),
  setNewFieldValue: vi.fn(),
} as unknown as FieldEditing;

function card(status: DocumentRecord['status']) {
  const document = makeDocument({ status });
  return (
    <ExtractedInformationCard
      canAddOrDeleteFields={false} document={document} fieldReasons={{}} isInFlight={false} lowestConfidence={null}
      setDocument={vi.fn()} setFieldDraft={vi.fn()} editing={editing}
      extractedFields={[['net_pay', { value: '4160.00', confidence: 0.97 }]]}
    />
  );
}

describe('ExtractedInformationCard folding', () => {
  it('opens expanded for an analyzed document', () => {
    render(card('analyzed'));

    expect(screen.getByRole('button', { name: 'Collapse extracted information' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('4160.00')).toBeTruthy();
  });

  it.each(['approved', 'detected'] as const)('opens folded for a %s document', (status) => {
    render(card(status));

    expect(screen.getByRole('button', { name: 'Expand extracted information' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('4160.00')).toBeNull();
  });

  it('folds when the document becomes approved while open', () => {
    const { rerender } = render(card('analyzed'));

    rerender(card('approved'));

    expect(screen.getByRole('button', { name: 'Expand extracted information' })).toBeTruthy();
    expect(screen.queryByText('4160.00')).toBeNull();
  });
});
