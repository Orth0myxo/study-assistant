import { describe, expect, it } from 'vitest';

import { estimateSummaryCalls } from '@/services/summaryEstimate';

describe('estimateSummaryCalls', () => {
  it('uses one request for ordinary lecture documents', () => {
    expect(estimateSummaryCalls(1)).toBe(1);
    expect(estimateSummaryCalls(30000)).toBe(1);
    expect(estimateSummaryCalls(40000)).toBe(1);
  });

  it('adds an overview request above the single-call limit', () => {
    expect(estimateSummaryCalls(40001)).toBe(12);
    expect(estimateSummaryCalls(100000)).toBe(26);
  });

  it('does not estimate without a known character count', () => {
    expect(estimateSummaryCalls(null)).toBeNull();
    expect(estimateSummaryCalls(0)).toBeNull();
    expect(estimateSummaryCalls(Number.NaN)).toBeNull();
  });
});
