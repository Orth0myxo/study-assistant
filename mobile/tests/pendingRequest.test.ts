import { describe, expect, it } from 'vitest';

import { takePendingRequest } from '../services/pendingRequest';

describe('pending request after attachment', () => {
  it('runs the remembered request once the material is ready', () => {
    const queue = { current: 'give me the Gram stain procedure' };

    expect(takePendingRequest(queue, true)).toBe(
      'give me the Gram stain procedure',
    );
    expect(queue.current).toBeNull();
    expect(takePendingRequest(queue, true)).toBeNull();
  });

  it('keeps the request while the app is busy and drops only on success', () => {
    const queue = { current: 'summarize this' };

    expect(takePendingRequest(queue, false)).toBeNull();
    expect(queue.current).toBe('summarize this');
    expect(takePendingRequest(queue, true)).toBe('summarize this');
  });
});
