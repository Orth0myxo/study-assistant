import { describe, expect, it } from 'vitest';

import { estimateSummaryCalls } from '@/services/summaryEstimate';

describe('estimateSummaryCalls', () => {
  it('uses one request for small documents', () => {
    expect(estimateSummaryCalls(1)).toBe(1);
    expect(estimateSummaryCalls(12000)).toBe(1);
  });

  it('adds an overview request for larger documents', () => {
    expect(estimateSummaryCalls(12001)).toBe(5);
    expect(estimateSummaryCalls(40000)).toBe(11);
  });

  it('does not estimate without a known character count', () => {
    expect(estimateSummaryCalls(null)).toBeNull();
    expect(estimateSummaryCalls(0)).toBeNull();
    expect(estimateSummaryCalls(Number.NaN)).toBeNull();
  });
});
