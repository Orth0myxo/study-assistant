import {
  DocumentFormat,
  StudyJobSummary,
  StudyMode,
  TopicStudyMode,
  TopicStudyResultSummary,
} from '@/types/api';

export type SessionOutputKind = 'study' | 'topic';

export interface SessionOutput {
  kind: SessionOutputKind;
  id: string;
  mode: StudyMode | TopicStudyMode;
  label: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  documentAvailable: boolean;
}

export interface RecentStudySession {
  key: string;
  documentId: string | null;
  sourceId: string | null;
  filename: string;
  format: DocumentFormat | null;
  lastActivity: string;
  documentAvailable: boolean;
  outputs: SessionOutput[];
}

const STUDY_MODE_LABELS: Record<StudyMode, string> = {
  extracted_text_only: 'Source text',
  text_and_summaries: 'Text + AI',
  summaries_only: 'AI summary',
};

const TOPIC_MODE_LABELS: Record<TopicStudyMode, string> = {
  source_passages: 'Passages',
  ai_study_material: 'AI material',
  both: 'Passages + AI',
};

function activityTime(value: { created_at: string; updated_at?: string }): string {
  return value.updated_at || value.created_at;
}

function studyOutput(job: StudyJobSummary): SessionOutput {
  return {
    kind: 'study',
    id: job.job_id,
    mode: job.mode,
    label: STUDY_MODE_LABELS[job.mode],
    status: job.status,
    createdAt: job.created_at,
    updatedAt: activityTime(job),
    documentAvailable: job.document_available,
  };
}

function topicOutput(result: TopicStudyResultSummary): SessionOutput {
  return {
    kind: 'topic',
    id: result.result_id,
    mode: result.mode,
    label: TOPIC_MODE_LABELS[result.mode],
    status: result.status,
    createdAt: result.created_at,
    updatedAt: activityTime(result),
    documentAvailable: result.document_available,
  };
}

function studyKey(job: StudyJobSummary): string {
  return job.document_id ? `doc:${job.document_id}` : `job:${job.job_id}`;
}

function topicKey(result: TopicStudyResultSummary): string {
  return result.document_id
    ? `doc:${result.document_id}`
    : `source:${result.source_id}`;
}

function addOutput(
  sessions: Map<string, RecentStudySession>,
  key: string,
  output: SessionOutput,
  details: {
    documentId: string | null;
    sourceId: string | null;
    filename: string;
    format: DocumentFormat | null;
  },
): void {
  const existing = sessions.get(key);
  if (existing) {
    existing.outputs.push(output);
    existing.documentId = existing.documentId ?? details.documentId;
    existing.sourceId = existing.sourceId ?? details.sourceId;
    existing.format = existing.format ?? details.format;
    existing.documentAvailable =
      existing.documentAvailable || output.documentAvailable;
    if (!existing.filename && details.filename) {
      existing.filename = details.filename;
    }
    return;
  }
  sessions.set(key, {
    key,
    documentId: details.documentId,
    sourceId: details.sourceId,
    filename: details.filename,
    format: details.format,
    lastActivity: output.updatedAt,
    documentAvailable: output.documentAvailable,
    outputs: [output],
  });
}

export function groupRecentSessions(
  jobs: StudyJobSummary[],
  results: TopicStudyResultSummary[],
): RecentStudySession[] {
  const sessions = new Map<string, RecentStudySession>();

  for (const job of jobs) {
    addOutput(sessions, studyKey(job), studyOutput(job), {
      documentId: job.document_id || null,
      sourceId: null,
      filename: job.filename,
      format: job.format,
    });
  }

  for (const result of results) {
    addOutput(sessions, topicKey(result), topicOutput(result), {
      documentId: result.document_id,
      sourceId: result.source_id,
      filename: result.filename,
      format: result.format,
    });
  }

  return [...sessions.values()]
    .map((session) => {
      const outputs = [...session.outputs].sort((left, right) =>
        right.updatedAt.localeCompare(left.updatedAt),
      );
      return {
        ...session,
        outputs,
        lastActivity: outputs[0]?.updatedAt ?? session.lastActivity,
        documentAvailable: outputs.some((output) => output.documentAvailable),
      };
    })
    .sort((left, right) => right.lastActivity.localeCompare(left.lastActivity));
}

export function latestOutput(
  session: RecentStudySession,
): SessionOutput | null {
  return session.outputs[0] ?? null;
}

export function uniqueOutputLabels(session: RecentStudySession): string[] {
  const seen = new Set<string>();
  const labels: string[] = [];
  for (const output of session.outputs) {
    if (!seen.has(output.label)) {
      seen.add(output.label);
      labels.push(output.label);
    }
  }
  return labels;
}
