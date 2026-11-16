import { describe, expect, it } from 'vitest';

import { ocrProgressLabel } from '@/services/ocrProgressLabel';
import type { OcrProgressResponse } from '@/types/api';

const pendingProgress: OcrProgressResponse = {
  document_id: 'doc-1',
  format: 'pdf',
  page_count: 11,
  status: 'pending',
  total_pages: 11,
  needs_ocr_pages: 5,
  completed_pages: 2,
  pending_pages: [3, 6, 9],
  failed_pages: [],
  warnings: [],
};

describe('ocrProgressLabel', () => {
  it('names the next pending page against the document page count', () => {
    expect(ocrProgressLabel(pendingProgress, 'lecture.pdf')).toBe(
      'Reading page 3 of 11 · lecture.pdf',
    );
  });

  it('shows completed scan count when no page remains pending', () => {
    expect(
      ocrProgressLabel(
        { ...pendingProgress, status: 'complete', completed_pages: 5, pending_pages: [] },
        'lecture.pdf',
      ),
    ).toBe('5 of 5 scanned pages read · lecture.pdf');
  });
});
