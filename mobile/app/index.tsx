import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import { ApiError, getHealth, listStudyJobs } from '@/services/apiClient';
import { HealthResponse, StudyJobSummary, StudyMode } from '@/types/api';

type StatusState =
  | { kind: 'loading' }
  | { kind: 'ok'; data: HealthResponse }
  | { kind: 'error'; message: string };

type RecentState =
  | { kind: 'loading' }
  | { kind: 'ok'; jobs: StudyJobSummary[] }
  | { kind: 'error'; message: string };

const MODE_LABELS: Record<StudyMode, string> = {
  extracted_text_only: 'Extracted text only',
  text_and_summaries: 'Text + AI summaries',
  summaries_only: 'AI summaries only',
};

async function fetchStatus(): Promise<StatusState> {
  try {
    const data = await getHealth();
    return { kind: 'ok', data };
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : 'Unexpected error.';
    return { kind: 'error', message };
  }
}

export default function HomeScreen() {
  const router = useRouter();
  const [status, setStatus] = useState<StatusState>({ kind: 'loading' });
  const [recent, setRecent] = useState<RecentState>({ kind: 'loading' });

  useEffect(() => {
    let active = true;
    fetchStatus().then((next) => {
      if (active) {
        setStatus(next);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const loadRecent = useCallback(async () => {
    try {
      const jobs = await listStudyJobs();
      setRecent({ kind: 'ok', jobs });
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : 'Unexpected error.';
      setRecent({ kind: 'error', message });
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setRecent({ kind: 'loading' });
      void loadRecent();
    }, [loadRecent]),
  );

  const refresh = useCallback(() => {
    setStatus({ kind: 'loading' });
    void fetchStatus().then(setStatus);
    setRecent({ kind: 'loading' });
    void loadRecent();
  }, [loadRecent]);

  return (
    <Screen>
      <Card>
        <Text style={styles.cardTitle}>Backend status</Text>
        <StatusLine status={status} />
        <Text style={styles.retry} onPress={refresh}>
          Tap to refresh
        </Text>
      </Card>

      <PrimaryButton label="New study" onPress={() => router.push('/upload')} />

      <Card>
        <Text style={styles.cardTitle}>Recent sessions</Text>
        <RecentList
          recent={recent}
          onOpen={(jobId) =>
            router.push({ pathname: '/results', params: { jobId } })
          }
        />
      </Card>
    </Screen>
  );
}

function RecentList({
  recent,
  onOpen,
}: {
  recent: RecentState;
  onOpen: (jobId: string) => void;
}) {
  if (recent.kind === 'loading') {
    return <Text style={styles.muted}>Loading sessions…</Text>;
  }
  if (recent.kind === 'error') {
    return <Text style={styles.error}>{recent.message}</Text>;
  }
  if (recent.jobs.length === 0) {
    return <Text style={styles.muted}>No saved sessions yet.</Text>;
  }
  return (
    <>
      {recent.jobs.map((job) => (
        <Pressable
          key={job.job_id}
          accessibilityRole="button"
          onPress={() => onOpen(job.job_id)}
          style={styles.sessionRow}>
          <Text style={styles.sessionTitle}>{job.filename}</Text>
          <Text style={styles.muted}>
            {MODE_LABELS[job.mode]} · {job.status}
          </Text>
          <Text style={styles.sessionMeta}>
            {new Date(job.created_at).toLocaleString()}
            {job.document_available ? '' : ' · source no longer available'}
          </Text>
        </Pressable>
      ))}
    </>
  );
}

function StatusLine({ status }: { status: StatusState }) {
  if (status.kind === 'loading') {
    return <Text style={styles.muted}>Checking backend…</Text>;
  }
  if (status.kind === 'error') {
    return <Text style={styles.error}>{status.message}</Text>;
  }
  const ai = status.data.ai;
  const aiText =
    ai.provider === 'configured'
      ? `AI configured${ai.model ? ` (${ai.model})` : ''}`
      : 'AI not configured';
  return (
    <>
      <Text style={styles.ok}>Backend online · v{status.data.version}</Text>
      <Text style={styles.muted}>{aiText}</Text>
    </>
  );
}

const styles = StyleSheet.create({
  cardTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1b2a3a',
  },
  ok: {
    color: '#1a7f37',
    fontSize: 14,
  },
  error: {
    color: '#b3261e',
    fontSize: 14,
  },
  muted: {
    color: '#5b6b7c',
    fontSize: 14,
  },
  retry: {
    color: '#208AEF',
    fontSize: 13,
    fontWeight: '600',
    marginTop: 4,
  },
  sessionRow: {
    paddingVertical: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#dfe6ee',
    gap: 2,
  },
  sessionTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: '#1b2a3a',
  },
  sessionMeta: {
    color: '#5b6b7c',
    fontSize: 12,
  },
});
