import { describe, expect, it } from 'vitest';

import {
  studySectionsForDisplay,
  verbatimSourceText,
} from '@/services/studyPresentation';
import { StudyItem } from '@/types/api';

function item(overrides: Partial<StudyItem>): StudyItem {
  return {
    index: 0,
    source_refs: [],
    text: null,
    summary: null,
    warnings: [],
    ...overrides,
  };
}

describe('study presentation helpers', () => {
  it('returns extracted text exactly as received', () => {
    const exactText =
      'Introduction\n\n  Indented source line with trailing spaces   \nSecond paragraph.';

    expect(
      verbatimSourceText(item({ text: exactText })),
    ).toBe(exactText);
  });

  it('does not treat missing or blank text as a source preview', () => {
    expect(verbatimSourceText(item({ text: null }))).toBeNull();
    expect(verbatimSourceText(item({ text: '   \n\t' }))).toBeNull();
  });

  it('keeps text-only, summary-only, and combined sections', () => {
    const textOnly = item({
      index: 0,
      text: 'Exact page text.',
      source_refs: [{ kind: 'pdf_page', page: 2, page_index: 1 }],
    });
    const summaryOnly = item({ index: 1, summary: 'Grounded summary.' });
    const combined = item({
      index: 2,
      text: 'Another exact passage.',
      summary: 'Another summary.',
      source_refs: [{ kind: 'pdf_page', page: 3, page_index: 2 }],
    });
    const empty = item({ index: 3 });

    expect(
      studySectionsForDisplay([textOnly, summaryOnly, combined, empty]),
    ).toEqual([textOnly, summaryOnly, combined]);
  });

  it('preserves page references alongside source text', () => {
    const textOnly = item({
      text: 'Exact page text.',
      source_refs: [{ kind: 'pdf_page', page: 12, page_index: 11 }],
    });

    const [section] = studySectionsForDisplay([textOnly]);

    expect(section.text).toBe('Exact page text.');
    expect(section.source_refs).toEqual([
      { kind: 'pdf_page', page: 12, page_index: 11 },
    ]);
  });
});
