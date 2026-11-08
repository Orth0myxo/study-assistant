import { describe, expect, it } from 'vitest';

import {
  TOPIC_MAX_JOTTINGS_CHARS,
  TOPIC_MAX_TOPICS,
  canGenerate,
  cleanTopics,
  formatSourceLabels,
  formatSourceRef,
  matchStatusLabel,
  materialStatusLabel,
  modeNeedsAi,
  reviewCounts,
  validateJottingInput,
  validateTopics,
} from '@/services/topicWorkflow';
import { TopicReviewResponse, TopicReviewTopic } from '@/types/api';

function topic(
  status: TopicReviewTopic['status'],
  index: number,
): TopicReviewTopic {
  return {
    topic_id: `topic_${index}`,
    text: `Topic ${index}`,
    status,
    confidence: status === 'matched' ? 1 : 0,
    source_labels: [],
    matches: [],
    warnings: [],
  };
}

function review(
  statuses: TopicReviewTopic['status'][],
): TopicReviewResponse {
  return {
    source_id: 'source-1',
    document: null,
    document_available: false,
    source_material: { status: 'available', section_count: 1 },
    segmentation: { input: 'provided', topic_count: statuses.length, warnings: [] },
    topics: statuses.map(topic),
    warnings: [],
  };
}

describe('topic workflow helpers', () => {
  it('trims, collapses whitespace, and de-duplicates topics', () => {
    expect(
      cleanTopics([
        '  Calvin   cycle ',
        'Photosynthesis',
        'calvin cycle',
        '',
        '  ',
      ]),
    ).toEqual(['Calvin cycle', 'Photosynthesis']);
  });

  it('accepts text-only, photo-only, and combined jottings with a source', () => {
    expect(validateJottingInput('Calvin cycle', 0, true)).toBeNull();
    expect(validateJottingInput('', 2, true)).toBeNull();
    expect(validateJottingInput('Calvin cycle', 2, true)).toBeNull();
  });

  it('rejects missing source documents and empty jottings', () => {
    expect(validateJottingInput('Calvin cycle', 0, false)).toContain(
      'source document',
    );
    expect(validateJottingInput('   ', 0, true)).toContain(
      'typed jottings or at least one photo',
    );
    expect(
      validateJottingInput('x'.repeat(TOPIC_MAX_JOTTINGS_CHARS + 1), 0, true),
    ).toContain(`${TOPIC_MAX_JOTTINGS_CHARS}`);
  });

  it('labels provenance for reviewed topics', () => {
    expect(formatSourceLabels(['Typed jottings', 'Photo 1'])).toBe(
      'Typed jottings + Photo 1',
    );
    expect(formatSourceLabels([])).toBe('Source not identified');
  });

  it('validates empty, oversized, and overlong topic lists', () => {
    expect(validateTopics(['   '])).toBe('Add at least one topic.');
    expect(
      validateTopics(
        Array.from({ length: TOPIC_MAX_TOPICS + 1 }, (_, index) => `T${index}`),
      ),
    ).toContain(`${TOPIC_MAX_TOPICS}`);
    expect(validateTopics(['x'.repeat(201)])).toContain('200');
    expect(validateTopics(['Calvin cycle'])).toBeNull();
  });

  it('formats PDF and DOCX source references', () => {
    expect(
      formatSourceRef({ kind: 'pdf_page', page: 12, page_index: 11 }),
    ).toBe('Page 12');
    expect(
      formatSourceRef({
        kind: 'docx_section',
        section_label: 'Respiration',
        paragraph_index: 3,
      }),
    ).toBe('Section: Respiration · Paragraph 3');
  });

  it('labels match and material states clearly', () => {
    expect(matchStatusLabel('matched')).toBe('Matched');
    expect(matchStatusLabel('ambiguous')).toBe('Needs review');
    expect(matchStatusLabel('unmatched')).toBe('No match');
    expect(materialStatusLabel('ai_failed')).toBe('AI generation failed');
    expect(materialStatusLabel('skipped_unmatched')).toBe(
      'Skipped: no match',
    );
  });

  it('requires a strong match for AI modes but not for original passages', () => {
    const none = review(['ambiguous', 'unmatched']);
    const one = review(['matched', 'unmatched']);

    expect(modeNeedsAi('source_passages')).toBe(false);
    expect(modeNeedsAi('ai_study_material')).toBe(true);
    expect(modeNeedsAi('both')).toBe(true);
    expect(canGenerate('source_passages', none, 2)).toBe(true);
    expect(canGenerate('both', none, 2)).toBe(false);
    expect(canGenerate('both', one, 2)).toBe(true);
    expect(canGenerate('source_passages', null, 2)).toBe(false);
  });

  it('counts each review status', () => {
    expect(reviewCounts(review(['matched', 'matched', 'ambiguous', 'unmatched'])))
      .toEqual({
        matched: 2,
        ambiguous: 1,
        unmatched: 1,
      });
  });
});
