import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CaseStatusSnapshot } from '../../services/caseSocket';
import { makeCaseSummary, makeDocument } from '../../test/records';
import { useCaseSnapshot } from './useCaseSnapshot';

// The case socket, replaced: each subscription is recorded, and a test pushes snapshots through it.
const socket = vi.hoisted(() => ({
  subscriptions: [] as {
    caseId: string;
    push: (snapshot: CaseStatusSnapshot) => void;
    connected: (connected: boolean) => void;
    unsubscribe: ReturnType<typeof vi.fn>;
  }[],
}));

vi.mock('../../services/caseSocket', () => ({
  subscribeToCaseStatus: (
    caseId: string,
    onSnapshot: (snapshot: CaseStatusSnapshot) => void,
    onConnectionChange?: (connected: boolean) => void,
  ) => {
    const unsubscribe = vi.fn();
    socket.subscriptions.push({ caseId, push: onSnapshot, connected: (c) => onConnectionChange?.(c), unsubscribe });
    return { unsubscribe, refresh: vi.fn() };
  },
}));

const latest = () => socket.subscriptions[socket.subscriptions.length - 1];

function push(snapshot: Partial<CaseStatusSnapshot>) {
  act(() => latest().push({ documents: [], caseSummary: makeCaseSummary(), caseMatch: null, ...snapshot }));
}

describe('useCaseSnapshot', () => {
  beforeEach(() => {
    socket.subscriptions.length = 0;
  });

  it('has nothing loaded until the first snapshot, then the case as it was pushed', () => {
    const { result } = renderHook(() => useCaseSnapshot('case-1'));
    expect(result.current.snapshotLoaded).toBe(false);

    act(() => latest().connected(true));
    push({ caseSummary: makeCaseSummary({ status: 'pending_review', reviews_required: false, findings_to_review: 2 }) });

    expect(result.current.snapshotLoaded).toBe(true);
    expect(result.current.wsConnected).toBe(true);
    expect([result.current.stage, result.current.reviewsRequired, result.current.findingsToReview]).toEqual([
      'pending_review', false, 2,
    ]);
  });

  it('lists documents oldest upload first, and splits them by the officer’s decision', () => {
    const { result } = renderHook(() => useCaseSnapshot('case-1'));

    push({
      documents: [
        makeDocument({ id: 'rejected', status: 'rejected', uploaded_at: '2026-11-01T04:00:00Z' }),
        makeDocument({ id: 'detecting', status: 'detecting', uploaded_at: '2026-11-01T03:00:00Z' }),
        makeDocument({ id: 'analyzed', status: 'analyzed', uploaded_at: '2026-11-01T01:00:00Z' }),
        makeDocument({ id: 'approved', status: 'approved', uploaded_at: '2026-11-01T02:00:00Z' }),
      ],
    });

    const ids = (documents: { id: string }[]) => documents.map((d) => d.id);
    expect(ids(result.current.documents)).toEqual(['analyzed', 'approved', 'detecting', 'rejected']);
    // Approved is the whole family: a document being scanned stays approved.
    expect(ids(result.current.approvedDocuments)).toEqual(['approved', 'detecting']);
    expect(ids(result.current.extractedDocuments)).toEqual(['analyzed']);
    expect(ids(result.current.rejectedDocuments)).toEqual(['rejected']);
    expect(result.current.caseHasInFlightDocument).toBe(true);
  });

  it('flags each document that needs the officer before approval, and none already decided', () => {
    const { result } = renderHook(() => useCaseSnapshot('case-1'));

    push({
      documents: [
        makeDocument({ id: 'clean' }),
        makeDocument({ id: 'failed', status: 'failed' }),
        makeDocument({ id: 'manual-review', routing_status: 'manual_review' }),
        makeDocument({ id: 'unconfirmed-type', classification_confidence: 0.6 }),
        makeDocument({ id: 'nothing-extracted', extracted_fields: {} }),
        makeDocument({ id: 'low-confidence', extracted_fields: { net_pay: { value: '1', confidence: 0.5 } } }),
        makeDocument({ id: 'missing-field', missing_required_fields: ['net_pay'] }),
        makeDocument({ id: 'wrong-company' }),
        makeDocument({ id: 'approved-anyway', status: 'approved', routing_status: 'manual_review' }),
        makeDocument({ id: 'rejected', status: 'rejected', routing_status: 'manual_review' }),
      ],
      caseMatch: {
        documents: [
          { document_id: 'wrong-company', status: 'manual_review', reason: 'The company differs from the case.', comparisons: [] },
        ],
      } as unknown as CaseStatusSnapshot['caseMatch'],
    });

    expect(result.current.flaggedDocuments.map((d) => d.id).sort()).toEqual([
      'failed', 'low-confidence', 'manual-review', 'missing-field', 'nothing-extracted', 'unconfirmed-type', 'wrong-company',
    ]);
    expect(result.current.caseMatchNeedsReview(result.current.documentDetails.get('wrong-company')!)).toBe(true);
  });

  it('subscribes to the new case when the case changes, and stops listening on unmount', () => {
    const { rerender, unmount } = renderHook(({ caseId }) => useCaseSnapshot(caseId), { initialProps: { caseId: 'case-1' } });
    const first = latest();

    rerender({ caseId: 'case-2' });

    expect(first.unsubscribe).toHaveBeenCalledOnce();
    expect(latest().caseId).toBe('case-2');
    unmount();
    expect(latest().unsubscribe).toHaveBeenCalledOnce();
  });
});
