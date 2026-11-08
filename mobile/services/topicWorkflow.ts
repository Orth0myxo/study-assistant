import {
  SourceRef,
  TopicMatchStatus,
  TopicMaterialStatus,
  TopicReviewResponse,
  TopicStudyMode,
} from '@/types/api';

export const TOPIC_MAX_IMAGES = 10;
export const TOPIC_MAX_TOPICS = 20;
export const TOPIC_MAX_TOPIC_CHARS = 200;
export const TOPIC_MAX_JOTTINGS_CHARS = 50_000;

export function validateJottingInput(
  jottingsText: string,
  photoCount: number,
  hasDocument: boolean,
): string | null {
  if (!hasDocument) {
    return 'Choose a PDF or DOCX source document.';
  }
  if (!jottingsText.trim() && photoCount === 0) {
    return 'Add typed jottings or at least one photo.';
  }
  if (jottingsText.length > TOPIC_MAX_JOTTINGS_CHARS) {
    return `Keep typed jottings to ${TOPIC_MAX_JOTTINGS_CHARS} characters or fewer.`;
  }
  return null;
}

export function formatSourceLabels(labels: string[]): string {
  return labels.length > 0 ? labels.join(' + ') : 'Source not identified';
}

export function cleanTopics(values: string[]): string[] {
  const seen = new Set<string>();
  const topics: string[] = [];
  for (const value of values) {
    const text = value.trim().replace(/\s+/g, ' ');
    if (!text) {
      continue;
    }
    const key = text.toLocaleLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    topics.push(text);
  }
  return topics;
}

export function validateTopics(values: string[]): string | null {
  const topics = cleanTopics(values);
  if (topics.length === 0) {
    return 'Add at least one topic.';
  }
  if (topics.length > TOPIC_MAX_TOPICS) {
    return `Keep the list to ${TOPIC_MAX_TOPICS} topics or fewer.`;
  }
  if (topics.some((topic) => topic.length > TOPIC_MAX_TOPIC_CHARS)) {
    return `Each topic must be ${TOPIC_MAX_TOPIC_CHARS} characters or fewer.`;
  }
  return null;
}

export function formatSourceRef(ref: SourceRef): string {
  if (ref.kind === 'pdf_page' && typeof ref.page === 'number') {
    return `Page ${ref.page}`;
  }
  const parts: string[] = [];
  if (ref.section_label) {
    parts.push(`Section: ${ref.section_label}`);
  }
  if (typeof ref.paragraph_index === 'number') {
    parts.push(`Paragraph ${ref.paragraph_index}`);
  }
  return parts.join(' · ') || 'Source section';
}

export function matchStatusLabel(status: TopicMatchStatus): string {
  if (status === 'matched') {
    return 'Matched';
  }
  if (status === 'ambiguous') {
    return 'Needs review';
  }
  return 'No match';
}

export function materialStatusLabel(status: TopicMaterialStatus): string {
  const labels: Record<TopicMaterialStatus, string> = {
    not_requested: 'Source passages only',
    generated: 'Generated',
    skipped_ambiguous: 'Skipped: ambiguous match',
    skipped_unmatched: 'Skipped: no match',
    skipped_no_evidence: 'Skipped: no evidence',
    ai_failed: 'AI generation failed',
  };
  return labels[status];
}

export function modeNeedsAi(mode: TopicStudyMode): boolean {
  return mode !== 'source_passages';
}

export function matchedTopicCount(review: TopicReviewResponse | null): number {
  if (!review) {
    return 0;
  }
  return review.topics.filter((topic) => topic.status === 'matched').length;
}

export function canGenerate(
  mode: TopicStudyMode,
  review: TopicReviewResponse | null,
  topicCount: number,
): boolean {
  if (!review || topicCount === 0 || review.topics.length === 0) {
    return false;
  }
  return !modeNeedsAi(mode) || matchedTopicCount(review) > 0;
}

export function reviewCounts(review: TopicReviewResponse | null): {
  matched: number;
  ambiguous: number;
  unmatched: number;
} {
  const counts = { matched: 0, ambiguous: 0, unmatched: 0 };
  for (const topic of review?.topics ?? []) {
    counts[topic.status] += 1;
  }
  return counts;
}
