import { describe, expect, it } from 'vitest';

import { decodeFilename } from '@/services/displayName';

describe('decodeFilename', () => {
  it('decodes encoded spaces and punctuation for display', () => {
    expect(decodeFilename('Lecture%20notes%20%282%29.pdf')).toBe(
      'Lecture notes (2).pdf',
    );
  });

  it('keeps malformed names unchanged', () => {
    expect(decodeFilename('Lecture%2notes.pdf')).toBe('Lecture%2notes.pdf');
  });
});
