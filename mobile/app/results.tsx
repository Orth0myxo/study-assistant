import { useLocalSearchParams, useRouter } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import {
  ApiError,
  downloadStudyExport,
  getStudyJob,
  startStudyJob,
} from '@/services/apiClient';
import {
  ExportFormat,
  SourceRef,
  StudyJobResponse,
  StudyMode,
} from '@/types/api';

const TERMINAL_STATUSES = ['completed', 'partial', 'failed'];
const EXPORTABLE_STATUSES = ['completed', 'partial'];
const POLL_INTERVAL_MS = 2000;

const DOCX_MIME =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

const MODE_LABELS: Record<StudyMode, string> = {
  extracted_text_only: 'Extracted text only',
  text_and_summaries: 'Text + AI summaries',
  summaries_only: 'AI summaries only',
};

function formatRef(ref: SourceRef): string {
  if (ref.kind === 'pdf_page' && typeof ref.page === 'number') {
    return `Page ${ref.page}`;
  }
  const parts: string[] = [];
  if (ref.section_label) {
    parts.push(`Section: ${ref.section_label}`);
  }
  if (typeof ref.paragraph_index === 'number') {
    parts.push(`paragraph ${ref.paragraph_index}`);
  }
  return parts.join(' · ') || 'Section';
}

function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.includes(status);
}

function isExportable(status: string): boolean {
  return EXPORTABLE_STATUSES.includes(status);
}

function SummaryText({ text }: { text: string }) {
  return (
    <View style={styles.summaryBlock}>
      {text.split('\n').map((line, index) => {
        if (!line.trim()) {
          return <View key={`gap-${index}`} style={styles.summaryGap} />;
        }
        if (line.startsWith('- ') || line.startsWith('• ')) {
          return (
            <View key={index} style={styles.bulletRow}>
              <Text style={styles.bulletDot}>•</Text>
              <Text style={[styles.summary, styles.summaryText]}>
                {line.slice(2)}
              </Text>
            </View>
          );
        }
        return (
          <Text key={index} style={styles.summary}>
            {line}
          </Text>
        );
      })}
    </View>
  );
}

export default function ResultsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    documentId?: string;
    mode?: StudyMode;
    jobId?: string;
  }>();
  const documentId = typeof params.documentId === 'string' ? params.documentId : null;
  const jobId = typeof params.jobId === 'string' ? params.jobId : null;
  const mode: StudyMode = params.mode ?? 'text_and_summaries';

  const [job, setJob] = useState<StudyJobResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  // Reuse a single POST per document+mode within this screen so a repeated
  // effect invocation (for example a double-invoke) cannot start two jobs.
  // A genuinely new selection changes the key and starts a fresh job.
  const startRef = useRef<{
    key: string;
    promise: Promise<StudyJobResponse>;
  } | null>(null);

  useEffect(() => {
    if (!documentId || jobId) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fail = (err: unknown, fallback: string) => {
      if (cancelled) {
        return;
      }
      setError(err instanceof ApiError ? err.message : fallback);
    };

    const poll = async (id: string) => {
      try {
        const next = await getStudyJob(id);
        if (cancelled) {
          return;
        }
        setJob(next);
        if (!isTerminal(next.status)) {
          timer = setTimeout(() => void poll(id), POLL_INTERVAL_MS);
        }
      } catch (err) {
        fail(err, 'Could not fetch the results.');
      }
    };

    const run = async () => {
      try {
        const key = `${documentId}:${mode}`;
        let entry = startRef.current;
        if (!entry || entry.key !== key) {
          entry = { key, promise: startStudyJob(documentId, mode) };
          startRef.current = entry;
        }
        const started = await entry.promise;
        if (cancelled) {
          return;
        }
        setJob(started);
        try {
          // Persist the job id in the route so a remount or back-navigation
          // loads this job instead of creating another one. The second effect
          // below handles polling once the param is set.
          router.setParams({ jobId: started.job_id });
        } catch {
          // Fall back to in-screen polling if the route cannot be updated.
          if (!isTerminal(started.status)) {
            timer = setTimeout(() => void poll(started.job_id), POLL_INTERVAL_MS);
          }
        }
      } catch (err) {
        fail(err, 'The study job could not be started.');
      }
    };

    void run();
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [documentId, mode, jobId, router]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async (id: string) => {
      try {
        const next = await getStudyJob(id);
        if (cancelled) {
          return;
        }
        setJob(next);
        if (!isTerminal(next.status)) {
          timer = setTimeout(() => void poll(id), POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        setError(
          err instanceof ApiError ? err.message : 'Could not load this session.',
        );
      }
    };

    void poll(jobId);
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [jobId]);

  const runExport = async (format: ExportFormat) => {
    if (!job) {
      return;
    }
    setExportError(null);
    setExporting(format);
    try {
      const stem = job.filename.replace(/\.[^/.]+$/, '') || 'study';
      const filename = `${stem}-${job.mode}.${format}`;

      let uri: string;
      try {
        uri = await downloadStudyExport(job.job_id, format, filename);
      } catch (err) {
        setExportError(
          err instanceof ApiError
            ? err.message
            : 'The export could not be downloaded. Please try again.',
        );
        return;
      }

      try {
        if (await Sharing.isAvailableAsync()) {
          await Sharing.shareAsync(uri, {
            mimeType: format === 'pdf' ? 'application/pdf' : DOCX_MIME,
            dialogTitle: 'Share study results',
          });
        } else {
          setExportError('Sharing is not available on this device.');
        }
      } catch {
        setExportError(
          'The export was downloaded but could not be shared. Please try again.',
        );
      }
    } finally {
      setExporting(null);
    }
  };

  if (!documentId && !jobId) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>No document</Text>
          <Text style={styles.error}>
            No document was selected. Go back and upload a file first.
          </Text>
        </Card>
      </Screen>
    );
  }

  if (error) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>Could not load results</Text>
          <Text style={styles.error}>{error}</Text>
          <Text style={styles.muted}>
            {mode === 'extracted_text_only'
              ? 'Try again, or choose a different file.'
              : 'AI modes need a Gemini API key configured on the server.'}
          </Text>
        </Card>
      </Screen>
    );
  }

  if (!job) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>Processing…</Text>
          <Text style={styles.muted}>Starting the study job.</Text>
          <ActivityIndicator style={styles.spinner} />
        </Card>
      </Screen>
    );
  }

  const canExport = isExportable(job.status);

  return (
    <Screen>
      <Card>
        <Text style={styles.title}>{job.filename}</Text>
        <Text style={styles.muted}>
          {MODE_LABELS[job.mode]} · {job.status}
        </Text>
        {!isTerminal(job.status) && (
          <>
            <Text style={styles.muted}>
              {job.progress.processed_units} of {job.progress.total_units} parts
              processed
            </Text>
            <ActivityIndicator style={styles.spinner} />
          </>
        )}
        {!job.document_available && (
          <Text style={styles.warn}>
            The original source file is no longer available. These results remain
            readable and exportable, but a new study job cannot be started for
            this document.
          </Text>
        )}
      </Card>

      <PrimaryButton
        label="Continue in chat"
        onPress={() =>
          router.push({
            pathname: '/workspace',
            params: { jobId: job.job_id },
          })
        }
      />

      {job.error && (
        <Card>
          <Text style={styles.errorTitle}>Processing failed</Text>
          <Text style={styles.error}>{job.error.message}</Text>
        </Card>
      )}

      {job.document_summary && (
        <Card>
          <Text style={styles.summaryLabel}>Overall AI summary</Text>
          <SummaryText text={job.document_summary} />
        </Card>
      )}

      {job.items.map((item) => (
        <Card key={item.index}>
          <Text style={styles.refs}>
            {item.source_refs.map(formatRef).join(' · ') || 'Source'}
          </Text>
          {item.text !== null && (
            <>
              <Text style={styles.sourceLabel}>Source text</Text>
              <Text style={styles.source}>{item.text}</Text>
            </>
          )}
          {item.summary !== null && (
            <>
              <Text style={styles.summaryLabel}>AI summary</Text>
              <SummaryText text={item.summary} />
            </>
          )}
          {item.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warn}>
              {warning.message}
            </Text>
          ))}
        </Card>
      ))}

      {job.warnings.length > 0 && (
        <Card>
          <Text style={styles.title}>Notes</Text>
          {job.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warn}>
              {warning.message}
            </Text>
          ))}
        </Card>
      )}

      {exportError && (
        <Card>
          <Text style={styles.error}>{exportError}</Text>
        </Card>
      )}

      <PrimaryButton
        label={exporting === 'pdf' ? 'Exporting…' : 'Export PDF'}
        disabled={!canExport || exporting !== null}
        onPress={() => void runExport('pdf')}
      />
      <PrimaryButton
        label={exporting === 'docx' ? 'Exporting…' : 'Export DOCX'}
        disabled={!canExport || exporting !== null}
        onPress={() => void runExport('docx')}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1C2A22',
  },
  muted: {
    color: '#5E6B61',
    fontSize: 14,
    lineHeight: 20,
  },
  errorTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: '#9B3F34',
  },
  error: {
    color: '#9B3F34',
    fontSize: 14,
    lineHeight: 20,
  },
  warn: {
    color: '#94601F',
    fontSize: 13,
    lineHeight: 19,
  },
  refs: {
    color: '#5E6B61',
    fontSize: 12,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  sourceLabel: {
    color: '#244936',
    fontSize: 13,
    fontWeight: '700',
  },
  source: {
    color: '#1C2A22',
    fontSize: 14,
    lineHeight: 21,
  },
  summaryLabel: {
    color: '#2E5B43',
    fontSize: 13,
    fontWeight: '700',
  },
  summaryBlock: {
    marginTop: 2,
  },
  summaryGap: {
    height: 6,
  },
  bulletRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 2,
  },
  bulletDot: {
    color: '#2E5B43',
    fontSize: 14,
    lineHeight: 21,
    marginRight: 6,
  },
  summaryText: {
    flex: 1,
  },
  summary: {
    color: '#1C2A22',
    fontSize: 14,
    lineHeight: 21,
  },
  spinner: {
    marginTop: 4,
  },
});
