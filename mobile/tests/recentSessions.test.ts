import { describe, expect, it } from 'vitest';

import {
  groupRecentSessions,
  latestOutput,
  uniqueOutputLabels,
} from '@/services/recentSessions';
import { StudyJobSummary, TopicStudyResultSummary } from '@/types/api';

function job(
  overrides: Partial<StudyJobSummary> & Pick<StudyJobSummary, 'job_id'>,
): StudyJobSummary {
  return {
    document_id: 'doc-1',
    filename: 'lecture.pdf',
    format: 'pdf',
    mode: 'text_and_summaries',
    status: 'completed',
    created_at: '2026-01-01T10:00:00Z',
    updated_at: '2026-01-01T10:05:00Z',
    page_count: 10,
    item_count: 5,
    document_available: true,
    ...overrides,
  };
}

function result(
  overrides: Partial<TopicStudyResultSummary> &
    Pick<TopicStudyResultSummary, 'result_id'>,
): TopicStudyResultSummary {
  return {
    source_id: 'source-1',
    document_id: 'doc-1',
    filename: 'lecture.pdf',
    format: 'pdf',
    mode: 'source_passages',
    ai_scope: 'source_only',
    status: 'completed',
    topic_count: 1,
    created_at: '2026-01-01T11:00:00Z',
    updated_at: '2026-01-01T11:02:00Z',
    expires_at: '2026-01-02T11:00:00Z',
    document_available: true,
    ...overrides,
  };
}

describe('groupRecentSessions', () => {
  it('groups multiple study modes for one document into one session', () => {
    const sessions = groupRecentSessions(
      [
        job({
          job_id: 'job-1',
          mode: 'extracted_text_only',
          updated_at: '2026-01-01T10:03:00Z',
        }),
        job({
          job_id: 'job-2',
          mode: 'text_and_summaries',
          updated_at: '2026-01-01T10:04:00Z',
        }),
        job({
          job_id: 'job-3',
          mode: 'summaries_only',
          updated_at: '2026-01-01T10:05:00Z',
        }),
      ],
      [],
    );

    expect(sessions).toHaveLength(1);
    expect(sessions[0].outputs).toHaveLength(3);
    expect(uniqueOutputLabels(sessions[0])).toEqual([
      'AI summary',
      'Text + AI',
      'Source text',
    ]);
  });

  it('groups study jobs and topic outputs that share a document id', () => {
    const sessions = groupRecentSessions(
      [job({ job_id: 'job-1' })],
      [result({ result_id: 'result-1' })],
    );

    expect(sessions).toHaveLength(1);
    expect(sessions[0].outputs.map((output) => output.kind)).toEqual([
      'topic',
      'study',
    ]);
    expect(latestOutput(sessions[0])?.id).toBe('result-1');
  });

  it('keeps different documents in separate sessions', () => {
    const sessions = groupRecentSessions(
      [
        job({ job_id: 'job-1', document_id: 'doc-1' }),
        job({
          job_id: 'job-2',
          document_id: 'doc-2',
          filename: 'seminar.docx',
          format: 'docx',
          created_at: '2026-01-01T11:00:00Z',
          updated_at: '2026-01-01T11:05:00Z',
        }),
      ],
      [],
    );

    expect(sessions).toHaveLength(2);
    expect(sessions.map((session) => session.filename)).toEqual([
      'seminar.docx',
      'lecture.pdf',
    ]);
  });

  it('groups unlinked photo results by topic source', () => {
    const sessions = groupRecentSessions(
      [],
      [
        result({
          result_id: 'result-1',
          document_id: null,
          source_id: 'source-a',
          mode: 'source_passages',
        }),
        result({
          result_id: 'result-2',
          document_id: null,
          source_id: 'source-a',
          mode: 'ai_study_material',
        }),
        result({
          result_id: 'result-3',
          document_id: null,
          source_id: 'source-b',
          mode: 'both',
          created_at: '2026-01-01T13:00:00Z',
          updated_at: '2026-01-01T13:02:00Z',
        }),
      ],
    );

    expect(sessions).toHaveLength(2);
    expect(sessions[0].outputs[0].id).toBe('result-3');
    expect(sessions[1].outputs).toHaveLength(2);
  });

  it('uses updated_at for last activity and latest output', () => {
    const sessions = groupRecentSessions(
      [
        job({
          job_id: 'job-1',
          created_at: '2026-01-01T10:00:00Z',
          updated_at: '2026-01-01T18:00:00Z',
        }),
      ],
      [
        result({
          result_id: 'result-1',
          created_at: '2026-01-01T12:00:00Z',
          updated_at: '2026-01-01T12:05:00Z',
        }),
      ],
    );

    expect(sessions[0].lastActivity).toBe('2026-01-01T18:00:00Z');
    expect(latestOutput(sessions[0])?.kind).toBe('study');
  });

  it('keeps a session only when any output still has its document', () => {
    const sessions = groupRecentSessions(
      [job({ job_id: 'job-1', document_available: false })],
      [result({ result_id: 'result-1', document_available: false })],
    );

    expect(sessions[0].documentAvailable).toBe(false);
  });
});
