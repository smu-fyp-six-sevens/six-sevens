import { describe, expect, it } from 'vitest';
import type { LangGraphTrace } from '../../services/uploadService';
import { fieldReason, lowConfidenceReasons } from './fieldReasons';

function trace(...reasons: Array<Record<string, unknown> | undefined>): LangGraphTrace {
  return {
    document_id: 'doc-1',
    started_at: 0,
    steps: reasons.map((low_confidence_reasons, index) => ({ step: 'extraction', at: index, low_confidence_reasons })),
  };
}

describe('lowConfidenceReasons', () => {
  it('reads the last extraction step, since a retype adds a newer one', () => {
    const reasons = lowConfidenceReasons(trace({ net_pay: 'From the first run.' }, { uen: 'From the retype.' }));

    expect(reasons).toEqual({ uen: 'From the retype.' });
  });

  it('ignores steps that are not extractions', () => {
    const later = { step: 'detection_done', at: 9, low_confidence_reasons: { net_pay: 'Not an extraction.' } };
    const saved = trace({ uen: 'Faint.' });

    expect(lowConfidenceReasons({ ...saved, steps: [...saved.steps, later] })).toEqual({ uen: 'Faint.' });
  });

  it('gives nothing when the trace is missing or has no reasons', () => {
    expect(lowConfidenceReasons(null)).toEqual({});
    expect(lowConfidenceReasons(trace(undefined))).toEqual({});
  });

  it('keeps only text reasons', () => {
    expect(lowConfidenceReasons(trace({ uen: 'Faint.', net_pay: 3, name: '' }))).toEqual({ uen: 'Faint.' });
  });
});

describe('fieldReason', () => {
  const reasons = { uen: 'The text reads 900.00 but the page image reads 990.00. Check the original page.' };

  it('shows the reason under a low-confidence field', () => {
    expect(fieldReason(reasons, 'uen', 0.5)).toBe(reasons.uen);
  });

  it('shows nothing once the officer has saved the field', () => {
    expect(fieldReason(reasons, 'uen', 1)).toBeNull();
  });

  it('shows nothing for a field with no confidence or no reason', () => {
    expect(fieldReason(reasons, 'uen', null)).toBeNull();
    expect(fieldReason(reasons, 'net_pay', 0.5)).toBeNull();
  });
});
