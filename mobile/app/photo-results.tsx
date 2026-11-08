import { useLocalSearchParams, useRouter } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import {
  ApiError,
  createTopicStudyOutput,
  downloadTopicResultExport,
  getTopicResult,
} from '@/services/apiClient';
import {
  formatSourceRef,
  matchStatusLabel,
  materialStatusLabel,
  modeNeedsAi,
} from '@/services/topicWorkflow';
import {
  ExportFormat,
  TopicStudyMode,
  TopicStudyOutputResponse,
  TopicStudyStatus,
} from '@/types/api';

const DOCX_MIME =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

const MODE_LABELS: Record<TopicStudyMode, string> = {
  source_passages: 'A. Original passages only',
  ai_study_material: 'B. AI study material',
  both: 'C. Original passages + AI study material',
};

function asMode(value: string | undefined): TopicStudyMode {
  return value === 'ai_study_material' || value === 'both'
    ? value
    : 'source_passages';
}

function statusLabel(status: TopicStudyStatus): string {
  if (status === 'completed') {
    return 'Completed';
  }
  if (status === 'partial') {
    return 'Partially completed';
  }
  return 'Failed';
}

function FormattedText({ text }: { text: string }) {
  return (
    <View style={styles.textBlock}>
      {text.split('\n').map((line, index) => {
        if (!line.trim()) {
          return <View key={`gap-${index}`} style={styles.gap} />;
        }
        if (line.startsWith('- ') || line.startsWith('• ')) {
          return (
            <View key={index} style={styles.bulletRow}>
              <Text style={styles.bullet}>•</Text>
              <Text style={styles.bodyText}>{line.slice(2)}</Text>
            </View>
          );
        }
        return (
          <Text key={index} style={styles.bodyText}>
            {line}
          </Text>
        );
      })}
    </View>
  );
}

export default function PhotoResultsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    sourceId?: string;
    mode?: string;
    resultId?: string;
  }>();
  const sourceId =
    typeof params.sourceId === 'string' ? params.sourceId : null;
  const resultId =
    typeof params.resultId === 'string' ? params.resultId : null;
  const mode = asMode(params.mode);

  const [output, setOutput] = useState<TopicStudyOutputResponse | null>(null);
  const effectiveMode = output?.mode ?? mode;
  const [loading, setLoading] = useState(Boolean(sourceId || resultId));
  const [error, setError] = useState<ApiError | null>(null);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const run = useCallback(async () => {
    if (resultId) {
      try {
        setOutput(await getTopicResult(resultId));
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err
            : new ApiError('The study result could not be loaded.', 'unknown', 0),
        );
      } finally {
        setLoading(false);
      }
      return;
    }
    if (!sourceId) {
      return;
    }
    try {
      setOutput(await createTopicStudyOutput(sourceId, mode));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError('The study material could not be generated.', 'unknown', 0),
      );
    } finally {
      setLoading(false);
    }
  }, [mode, resultId, sourceId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void run();
    }, 0);
    return () => clearTimeout(timer);
  }, [run]);

  const retry = () => {
    setLoading(true);
    setError(null);
    void run();
  };

  const useOriginalPassages = () => {
    if (!sourceId) {
      return;
    }
    router.replace({
      pathname: '/photo-results',
      params: { sourceId, mode: 'source_passages' },
    });
  };

  const runExport = async (format: ExportFormat) => {
    if (!output?.result_id) {
      return;
    }
    setExportError(null);
    setExporting(format);
    try {
      const stem =
        (output.document?.filename ?? 'topic-study').replace(/\.[^/.]+$/, '') ||
        'topic-study';
      const filename = `${stem}-${output.mode}.${format}`;
      const uri = await downloadTopicResultExport(
        output.result_id,
        format,
        filename,
      );
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType: format === 'pdf' ? 'application/pdf' : DOCX_MIME,
          dialogTitle: 'Share study material',
        });
      } else {
        setExportError('Sharing is not available on this device.');
      }
    } catch (err) {
      setExportError(
        err instanceof ApiError
          ? err.message
          : 'The export could not be downloaded. Please try again.',
      );
    } finally {
      setExporting(null);
    }
  };

  if (!sourceId && !resultId) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>No jottings source</Text>
          <Text style={styles.error}>
            Start again with a source document and your typed or photographed
            jottings.
          </Text>
          <PrimaryButton
            label="Start new jottings"
            onPress={() =>
              router.replace('/photo-notes')
            }
          />
        </Card>
      </Screen>
    );
  }

  if (loading) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>Building study material...</Text>
          <Text style={styles.muted}>
            {modeNeedsAi(effectiveMode)
              ? 'Matching topics, then generating grounded explanations. This can take a moment.'
              : 'Collecting the exact matched source passages.'}
          </Text>
          <ActivityIndicator style={styles.spinner} />
        </Card>
      </Screen>
    );
  }

  if (error) {
    const missingSource = error.code === 'topic_source_not_found';
    const missingResult = error.code === 'topic_result_not_found';
    const aiMissing = error.code === 'ai_not_configured';
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>
            {missingResult ? 'Saved material unavailable' : 'Could not generate material'}
          </Text>
          <Text style={styles.error}>{error.message}</Text>
          {missingSource && (
            <Text style={styles.muted}>
              The jottings source or its document has expired. Upload the
              material again to continue.
            </Text>
          )}
          {missingResult && (
            <Text style={styles.muted}>
              This saved result has expired. Start a new jottings session to
              build it again.
            </Text>
          )}
          {aiMissing && (
            <Text style={styles.warn}>
              AI is not configured on the server. You can still view the original
              matched passages.
            </Text>
          )}
          {aiMissing && (
            <PrimaryButton
              label="View original passages"
              onPress={useOriginalPassages}
            />
          )}
          {!missingSource && (
            <PrimaryButton label="Try again" onPress={retry} />
          )}
          {(missingSource || missingResult) && (
            <PrimaryButton
              label="Start again"
              onPress={() =>
                router.replace('/photo-notes')
              }
            />
          )}
        </Card>
      </Screen>
    );
  }

  if (!output) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>No results</Text>
          <Text style={styles.error}>
            The server returned no study material. Try again.
          </Text>
          <PrimaryButton label="Try again" onPress={retry} />
        </Card>
      </Screen>
    );
  }

  const failed = output.status === 'failed';
  const canFallback =
    Boolean(sourceId) &&
    modeNeedsAi(effectiveMode) &&
    output.status !== 'completed';

  return (
    <Screen>
      <Card>
        <Text style={styles.title}>
          {output.document?.filename ?? 'Study material'}
        </Text>
        <Text style={styles.muted}>{MODE_LABELS[effectiveMode]}</Text>
        <Text
          style={[
            styles.status,
            output.status === 'completed'
              ? styles.statusOk
              : output.status === 'partial'
                ? styles.statusWarn
                : styles.statusError,
          ]}>
          {statusLabel(output.status)}
        </Text>
        <Text style={styles.meta}>
          {output.topic_count} topics · {output.ai.generated_count} AI generated ·{' '}
          {output.ai.skipped_count} skipped
          {output.ai.failed_count > 0
            ? ` · ${output.ai.failed_count} failed`
            : ''}
        </Text>
        {!output.document_available && (
          <Text style={styles.warn}>
            The original source document is no longer available. These results
            remain readable, but start a new session to change the source.
          </Text>
        )}
        {output.source_material.status !== 'available' && (
          <Text style={styles.warn}>
            Source matching was unavailable for this document.
          </Text>
        )}
        {output.result_id ? (
          <Text style={styles.meta}>
            Saved to recent sessions. Exports include the content shown here.
          </Text>
        ) : (
          <Text style={styles.warn}>
            This result was not saved, so the export is unavailable.
          </Text>
        )}
      </Card>

      {exportError && (
        <Card>
          <Text style={styles.error}>{exportError}</Text>
        </Card>
      )}

      {output.result_id && (
        <>
          <PrimaryButton
            label={exporting === 'pdf' ? 'Exporting…' : 'Export PDF'}
            disabled={exporting !== null}
            onPress={() => void runExport('pdf')}
          />
          <PrimaryButton
            label={exporting === 'docx' ? 'Exporting…' : 'Export DOCX'}
            disabled={exporting !== null}
            onPress={() => void runExport('docx')}
          />
        </>
      )}

      {output.error && (
        <Card>
          <Text style={styles.errorTitle}>Generation failed</Text>
          <Text style={styles.error}>{output.error.message}</Text>
          <PrimaryButton label="Try again" onPress={retry} />
          {canFallback && (
            <PrimaryButton
              label="View original passages"
              onPress={useOriginalPassages}
            />
          )}
        </Card>
      )}

      {output.warnings.length > 0 && (
        <Card>
          <Text style={styles.sectionTitle}>Notes</Text>
          {output.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warn}>
              {warning.message}
            </Text>
          ))}
        </Card>
      )}

      {output.topics.map((topic) => (
        <Card key={topic.topic_id}>
          <Text style={styles.topicName}>{topic.text}</Text>
          <Text style={styles.meta}>
            {matchStatusLabel(topic.match_status)} ·{' '}
            {materialStatusLabel(topic.material_status)}
          </Text>

          {topic.source_passages.length > 0 && (
            <View style={styles.section}>
              <Text style={styles.sourceLabel}>Original source passages</Text>
              {topic.source_passages.map((passage) => (
                <View key={passage.passage_id} style={styles.passage}>
                  <Text style={styles.refs}>
                    {formatSourceRef(passage.anchor)}
                  </Text>
                  <Text style={styles.sourceText}>{passage.passage}</Text>
                </View>
              ))}
            </View>
          )}

          {topic.study_material?.source_grounded && (
            <View style={styles.section}>
              <Text style={styles.aiLabel}>
                AI study material (source-grounded)
              </Text>
              <FormattedText
                text={topic.study_material.source_grounded.text}
              />
              {topic.study_material.source_grounded.basis_refs.length > 0 && (
                <Text style={styles.meta}>
                  Based on:{' '}
                  {topic.study_material.source_grounded.basis_refs
                    .map(formatSourceRef)
                    .join(' · ')}
                </Text>
              )}
            </View>
          )}

          {topic.study_material?.beyond_source && (
            <View style={styles.section}>
              <Text style={styles.beyondLabel}>
                Optional background beyond the uploaded material
              </Text>
              <Text style={styles.warn}>
                This is not grounded in the uploaded source.
              </Text>
              <FormattedText text={topic.study_material.beyond_source.text} />
            </View>
          )}

          {!topic.study_material && topic.source_passages.length === 0 && (
            <Text style={styles.muted}>
              No source passage or generated material is available for this
              topic.
            </Text>
          )}

          {topic.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warn}>
              {warning.message}
            </Text>
          ))}
        </Card>
      ))}

      {failed && (
        <Card>
          <Text style={styles.error}>
            No eligible topic produced study material. Try again later or use the
            original passages.
          </Text>
          {canFallback && (
            <PrimaryButton
              label="View original passages"
              onPress={useOriginalPassages}
            />
          )}
        </Card>
      )}

      {output.result_id && (
        <PrimaryButton
          label="Continue in chat"
          onPress={() =>
            router.push({
              pathname: '/workspace',
              params: { resultId: output.result_id },
            })
          }
        />
      )}

      <PrimaryButton
        label="Start new jottings"
        onPress={() =>
          router.replace({ pathname: '/upload', params: { flow: 'photo' } })
        }
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
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: '#1C2A22',
  },
  topicName: {
    color: '#1C2A22',
    fontSize: 16,
    fontWeight: '700',
  },
  muted: {
    color: '#5E6B61',
    fontSize: 14,
    lineHeight: 20,
  },
  meta: {
    color: '#5E6B61',
    fontSize: 12,
    lineHeight: 17,
  },
  errorTitle: {
    color: '#9B3F34',
    fontSize: 15,
    fontWeight: '700',
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
  status: {
    alignSelf: 'flex-start',
    overflow: 'hidden',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 3,
    fontSize: 12,
    fontWeight: '700',
  },
  statusOk: {
    color: '#146c43',
    backgroundColor: '#dff3e7',
  },
  statusWarn: {
    color: '#7a4d00',
    backgroundColor: '#fff0cc',
  },
  statusError: {
    color: '#9b2c24',
    backgroundColor: '#fde4e1',
  },
  spinner: {
    marginTop: 4,
  },
  section: {
    gap: 6,
    paddingTop: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#DFD8C8',
  },
  sourceLabel: {
    color: '#244936',
    fontSize: 13,
    fontWeight: '700',
  },
  aiLabel: {
    color: '#2E5B43',
    fontSize: 13,
    fontWeight: '700',
  },
  beyondLabel: {
    color: '#7a4d00',
    fontSize: 13,
    fontWeight: '700',
  },
  passage: {
    gap: 2,
  },
  refs: {
    color: '#244936',
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  sourceText: {
    color: '#1C2A22',
    fontSize: 14,
    lineHeight: 21,
  },
  textBlock: {
    gap: 2,
  },
  bodyText: {
    color: '#1C2A22',
    fontSize: 14,
    lineHeight: 21,
  },
  gap: {
    height: 6,
  },
  bulletRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  bullet: {
    color: '#2E5B43',
    fontSize: 14,
    lineHeight: 21,
    marginRight: 6,
  },
});
