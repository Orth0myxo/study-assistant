import * as DocumentPicker from 'expo-document-picker';
import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text } from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import {
  ApiError,
  getHealth,
  uploadDocument,
  type UploadFile,
} from '@/services/apiClient';
import { DocumentResponse } from '@/types/api';

const PDF = 'application/pdf';
const DOCX =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

type Phase =
  | { kind: 'idle' }
  | { kind: 'picking' }
  | { kind: 'uploading'; name: string }
  | { kind: 'done'; document: DocumentResponse }
  | { kind: 'error'; message: string; retryFile?: UploadFile };

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(0)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadScreen() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [maxUploadMb, setMaxUploadMb] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const uploadingName = phase.kind === 'uploading' ? phase.name : null;

  useEffect(() => {
    if (!uploadingName) {
      return;
    }
    const started = Date.now();
    const timer = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [uploadingName]);

  const uploadAsset = async (file: UploadFile) => {
    const limit = maxUploadMb;
    if (limit === null && file.size !== undefined) {
      // Do not make the upload wait for a health check: a cold Render instance
      // can take a minute to answer, and the server enforces the limit anyway.
      void getHealth()
        .then((health) => setMaxUploadMb(health.limits.max_upload_mb))
        .catch(() => {});
    }

    if (
      limit !== null &&
      file.size !== undefined &&
      file.size > limit * 1024 * 1024
    ) {
      setPhase({
        kind: 'error',
        message: `${file.name} is ${formatSize(file.size)}. The upload limit is ${limit} MB.`,
      });
      return;
    }

    setElapsedSeconds(0);
    setPhase({ kind: 'uploading', name: file.name });
    try {
      const document = await uploadDocument(file);
      setPhase({ kind: 'done', document });
    } catch (error) {
      setPhase({
        kind: 'error',
        message:
          error instanceof ApiError ? error.message : 'The upload failed.',
        retryFile: file,
      });
    }
  };

  const pickAndUpload = async () => {
    setPhase({ kind: 'picking' });
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: [PDF, DOCX],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled) {
        setPhase({ kind: 'idle' });
        return;
      }
      const asset = result.assets?.[0];
      if (!asset) {
        setPhase({ kind: 'idle' });
        return;
      }
      const file: UploadFile = {
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        size: asset.size,
      };
      await uploadAsset(file);
    } catch (error) {
      setPhase({
        kind: 'error',
        message:
          error instanceof ApiError
            ? error.message
            : 'The file could not be selected.',
      });
    }
  };

  const busy = phase.kind === 'picking' || phase.kind === 'uploading';
  const retryFile = phase.kind === 'error' ? phase.retryFile : undefined;

  return (
    <Screen>
      <Card>
        <Text style={styles.title}>Select a document</Text>
        <Text style={styles.muted}>
          Choose a PDF or DOCX lecture file. The backend extracts the text and
          verifies that it contains readable content.
        </Text>
      </Card>

      <PrimaryButton
        label={busy ? 'Working…' : 'Choose file'}
        onPress={pickAndUpload}
        disabled={busy}
      />

      {phase.kind === 'picking' && (
        <ActivityIndicator style={styles.spinner} />
      )}

      {phase.kind === 'uploading' && (
        <Card>
          <Text style={styles.title}>{phase.name}</Text>
          <Text style={styles.muted}>
            Uploading and extracting…
            {elapsedSeconds > 0 ? ` ${elapsedSeconds}s` : ''}
          </Text>
          {elapsedSeconds >= 10 && (
            <Text style={styles.muted}>
              Large or scanned PDFs can take a while, especially when OCR is
              running or the server is waking up.
            </Text>
          )}
          <ActivityIndicator style={styles.spinner} />
        </Card>
      )}

      {phase.kind === 'done' && <ResultCard document={phase.document} />}

      {phase.kind === 'error' && (
        <Card>
          <Text style={styles.error}>{phase.message}</Text>
          {phase.retryFile && (
            <Text style={styles.muted}>Selected: {phase.retryFile.name}</Text>
          )}
        </Card>
      )}

      {retryFile && (
        <PrimaryButton
          label="Retry upload"
          onPress={() => uploadAsset(retryFile)}
          disabled={busy}
        />
      )}

      {phase.kind === 'done' && phase.document.extraction.text_extractable && (
        <PrimaryButton
          label="Continue to topics"
          onPress={() =>
            router.push({
              pathname: '/topics',
              params: { documentId: phase.document.document_id },
            })
          }
        />
      )}
    </Screen>
  );
}

function ResultCard({ document }: { document: DocumentResponse }) {
  const { extraction } = document;
  return (
    <Card>
      <Text style={styles.title}>{document.filename}</Text>
      <Text style={styles.muted}>
        {document.format.toUpperCase()} · {formatSize(document.size_bytes)}
      </Text>
      <Text style={styles.muted}>
        {document.page_count === null
          ? `${extraction.section_count} sections`
          : `${document.page_count} pages`}{' '}
        · {extraction.total_chars} characters
      </Text>
      {extraction.text_extractable ? (
        <Text style={styles.ok}>Text extracted successfully.</Text>
      ) : (
        <Text style={styles.error}>No extractable text found.</Text>
      )}
      {document.warnings.map((warning, index) => (
        <Text key={`${warning.code}-${index}`} style={styles.warn}>
          {warning.message}
        </Text>
      ))}
    </Card>
  );
}

const styles = StyleSheet.create({
  title: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1b2a3a',
  },
  muted: {
    color: '#5b6b7c',
    fontSize: 14,
    lineHeight: 20,
  },
  ok: {
    color: '#1a7f37',
    fontSize: 14,
  },
  error: {
    color: '#b3261e',
    fontSize: 14,
  },
  warn: {
    color: '#8a5a00',
    fontSize: 14,
    lineHeight: 20,
  },
  spinner: {
    marginTop: 4,
  },
});
