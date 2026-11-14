import { Image } from 'expo-image';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import {
  absoluteUrl,
  ApiError,
  ensureUserToken,
  getTopicSource,
  interpretTopicOutline,
  reviewTopicSource,
  updateTopicSource,
} from '@/services/apiClient';
import {
  TOPIC_MAX_TOPICS,
  TOPIC_MAX_TOPIC_CHARS,
  canGenerate,
  cleanTopics,
  formatSourceLabels,
  formatSourceRef,
  matchStatusLabel,
  matchedTopicCount,
  modeNeedsAi,
  reviewCounts,
  validateTopics,
} from '@/services/topicWorkflow';
import {
  TopicReviewResponse,
  TopicSourceResponse,
  TopicStudyMode,
} from '@/types/api';

const MODE_OPTIONS: {
  mode: TopicStudyMode;
  label: string;
  hint: string;
}[] = [
  {
    mode: 'source_passages',
    label: 'A. Original passages only',
    hint: 'Show the exact matched source text with references. No AI is used.',
  },
  {
    mode: 'ai_study_material',
    label: 'B. AI study material',
    hint: 'Explain each matched topic using only its matched source passages.',
  },
  {
    mode: 'both',
    label: 'C. Both',
    hint: 'Show the original passages together with the grounded explanation.',
  },
];

function preview(text: string): string {
  const normalized = text.trim().replace(/\s+/g, ' ');
  return normalized.length > 260
    ? `${normalized.slice(0, 260)}...`
    : normalized;
}

export default function PhotoTopicsScreen() {
  const router = useRouter();
  const { sourceId } = useLocalSearchParams<{ sourceId?: string }>();
  const validSourceId = typeof sourceId === 'string' ? sourceId : null;

  const [source, setSource] = useState<TopicSourceResponse | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);
  const [review, setReview] = useState<TopicReviewResponse | null>(null);
  const [mode, setMode] = useState<TopicStudyMode>('source_passages');
  const [loading, setLoading] = useState(Boolean(validSourceId));
  const [reviewing, setReviewing] = useState(false);
  const [interpreting, setInterpreting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authHeaders, setAuthHeaders] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    void ensureUserToken()
      .then((token) => {
        if (active) {
          setAuthHeaders({ Authorization: `Bearer ${token}` });
        }
      })
      .catch(() => {
        // The image request will simply fail; the rest of the screen still works.
      });
    return () => {
      active = false;
    };
  }, []);

  const load = useCallback(async () => {
    if (!validSourceId) {
      return;
    }
    try {
      const nextSource = await getTopicSource(validSourceId);
      setSource(nextSource);
      const nextReview = await reviewTopicSource(
        validSourceId,
        nextSource.topics.length > 0 ? nextSource.topics : null,
      );
      setDrafts(nextReview.topics.map((topic) => topic.text));
      setReview(nextReview);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'The topics could not be loaded.',
      );
    } finally {
      setLoading(false);
    }
  }, [validSourceId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 0);
    return () => clearTimeout(timer);
  }, [load]);

  const topicError = useMemo(() => validateTopics(drafts), [drafts]);
  const counts = reviewCounts(review);
  const matched = matchedTopicCount(review);
  const generateEnabled =
    canGenerate(mode, review, drafts.length) && topicError === null;
  const aiNeedsMatch = modeNeedsAi(mode) && matched === 0;

  const editTopic = (index: number, text: string) => {
    setDrafts((current) =>
      current.map((topic, currentIndex) =>
        currentIndex === index ? text : topic,
      ),
    );
    setReview(null);
  };

  const removeTopic = (index: number) => {
    setDrafts((current) =>
      current.filter((_, currentIndex) => currentIndex !== index),
    );
    setReview(null);
  };

  const addTopic = () => {
    if (drafts.length >= TOPIC_MAX_TOPICS) {
      setError(`Keep the list to ${TOPIC_MAX_TOPICS} topics or fewer.`);
      return;
    }
    setDrafts((current) => [...current, '']);
    setReview(null);
  };

  const runReview = async () => {
    if (!validSourceId || !source) {
      return;
    }
    const cleaned = cleanTopics(drafts);
    const validation = validateTopics(cleaned);
    if (validation) {
      setError(validation);
      return;
    }
    setReviewing(true);
    setError(null);
    try {
      const updated = await updateTopicSource(validSourceId, {
        topics: cleaned,
      });
      setSource(updated);
      setDrafts(cleaned);
      const nextReview = await reviewTopicSource(validSourceId, cleaned);
      setReview(nextReview);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'The topic review could not be completed.',
      );
    } finally {
      setReviewing(false);
    }
  };

  const interpretOutline = async () => {
    if (!validSourceId || !source?.images.length) return;
    setInterpreting(true);
    setError(null);
    try {
      const result = await interpretTopicOutline(validSourceId);
      setDrafts(result.topics);
      setReview(null);
      if (!result.topics.length) {
        setError(result.warnings.map((warning) => warning.message).join('\n') || 'No topics were detected. Add topics manually.');
      } else if (result.warnings.length) {
        setError(result.warnings.map((warning) => warning.message).join('\n'));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The outline image could not be interpreted.');
    } finally {
      setInterpreting(false);
    }
  };

  const generate = () => {
    if (!validSourceId || !review || !generateEnabled) {
      return;
    }
    router.push({
      pathname: '/photo-results',
      params: { sourceId: validSourceId, mode },
    });
  };

  if (!validSourceId) {
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
          <Text style={styles.title}>Extracting topics...</Text>
          <Text style={styles.muted}>
            Reading the corrected transcripts and finding distinct topics.
          </Text>
          <ActivityIndicator style={styles.spinner} />
        </Card>
      </Screen>
    );
  }

  if (!source) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>Could not load topics</Text>
          <Text style={styles.error}>{error ?? 'The source was not found.'}</Text>
          <PrimaryButton
            label="Try again"
            onPress={() => {
              setLoading(true);
              setError(null);
              void load();
            }}
          />
          <PrimaryButton
            label="Start again"
            onPress={() =>
              router.replace('/photo-notes')
            }
          />
        </Card>
      </Screen>
    );
  }

  return (
    <Screen>
      <Card>
        <Text style={styles.title}>
          {source.document?.filename ?? 'Attached document'}
        </Text>
        <Text style={styles.muted}>
          Review or edit detected topics, add missing topics, then match them
          against the uploaded lecturer document.
        </Text>
        {review && review.source_material.status !== 'available' && (
          <Text style={styles.warn}>
            Source matching is unavailable for this document. You can still
            review topics, but no passages will be found.
          </Text>
        )}
      </Card>

      {source.images.length > 0 && (
        <Card>
          <Text style={styles.sectionTitle}>Outline and note photos</Text>
          <Text style={styles.muted}>
            The stored photos used for this jottings source.
          </Text>
          <View style={styles.imageRow}>
            {source.images.map((image) => (
              <Image
                key={image.index}
                accessibilityLabel={`Note photo ${image.index + 1}`}
                source={{
                  uri: absoluteUrl(image.image_url),
                  headers: authHeaders,
                }}
                style={styles.thumbnail}
                contentFit="cover"
                transition={150}
              />
            ))}
          </View>
          <PrimaryButton
            label={interpreting ? 'Reading outline image…' : 'Detect lecturer outline topics'}
            onPress={() => void interpretOutline()}
            disabled={interpreting || reviewing}
          />
        </Card>
      )}

      {error && (
        <Card>
          <Text style={styles.error}>{error}</Text>
        </Card>
      )}

      <Card>
        <Text style={styles.sectionTitle}>Topics</Text>
        <Text style={styles.muted}>
          Edit the list or add missing topics. Reviewing saves the list to this
          jottings source.
        </Text>
        {drafts.length === 0 ? (
          <Text style={styles.muted}>
            No topics were extracted. Add a topic manually.
          </Text>
        ) : (
          drafts.map((topic, index) => (
            <View key={index} style={styles.topicRow}>
              <TextInput
                value={topic}
                onChangeText={(text) => editTopic(index, text)}
                placeholder={`Topic ${index + 1}`}
                placeholderTextColor="#647168"
                style={styles.topicInput}
                maxLength={TOPIC_MAX_TOPIC_CHARS + 20}
              />
              <Pressable
                accessibilityRole="button"
                onPress={() => removeTopic(index)}
                disabled={reviewing}>
                <Text style={styles.remove}>Remove</Text>
              </Pressable>
            </View>
          ))
        )}
        <Pressable
          accessibilityRole="button"
          onPress={addTopic}
          disabled={reviewing}>
          <Text style={styles.add}>+ Add topic</Text>
        </Pressable>
        {topicError && <Text style={styles.warn}>{topicError}</Text>}
        <PrimaryButton
          label={reviewing ? 'Matching topics...' : 'Review matches'}
          onPress={() => void runReview()}
          disabled={reviewing || drafts.length === 0}
        />
      </Card>

      {review && (
        <>
          <Card>
            <Text style={styles.sectionTitle}>Match summary</Text>
            <Text style={styles.muted}>
              {counts.matched} matched · {counts.ambiguous} need review ·{' '}
              {counts.unmatched} unmatched
            </Text>
            {review.warnings.map((warning, index) => (
              <Text key={`${warning.code}-${index}`} style={styles.warn}>
                {warning.message}
              </Text>
            ))}
          </Card>

          {review.topics.map((topic) => (
            <Card key={topic.topic_id}>
              <View style={styles.topicHeader}>
                <Text style={styles.topicName}>{topic.text}</Text>
                <Text
                  style={[
                    styles.badge,
                    topic.status === 'matched'
                      ? styles.badgeMatched
                      : topic.status === 'ambiguous'
                        ? styles.badgeAmbiguous
                        : styles.badgeUnmatched,
                  ]}>
                  {matchStatusLabel(topic.status)}
                </Text>
              </View>
              {topic.source_labels.length > 0 && (
                <Text style={styles.meta}>
                  From: {formatSourceLabels(topic.source_labels)}
                </Text>
              )}
              <Text style={styles.meta}>
                Confidence {(topic.confidence * 100).toFixed(0)}%
              </Text>
              {topic.warnings.map((warning, index) => (
                <Text key={`${warning.code}-${index}`} style={styles.warn}>
                  {warning.message}
                </Text>
              ))}
              {topic.matches.map((match) => (
                <View key={match.passage_id} style={styles.match}>
                  <Text style={styles.refs}>
                    {formatSourceRef(match.anchor)}
                  </Text>
                  <Text style={styles.passage}>
                    {preview(match.excerpt || match.passage)}
                  </Text>
                </View>
              ))}
              {topic.matches.length === 0 && (
                <Text style={styles.muted}>
                  No exact source passage was selected for this topic.
                </Text>
              )}
            </Card>
          ))}
        </>
      )}

      <Card>
        <Text style={styles.sectionTitle}>Output</Text>
        <Text style={styles.muted}>
          Choose what to keep. AI material is grounded only in the matched
          passages.
        </Text>
        {MODE_OPTIONS.map((option) => {
          const active = option.mode === mode;
          const disabled = modeNeedsAi(option.mode) && matched === 0;
          return (
            <Pressable
              key={option.mode}
              accessibilityRole="button"
              disabled={disabled}
              onPress={() => setMode(option.mode)}
              style={[
                styles.mode,
                active ? styles.modeActive : null,
                disabled ? styles.modeDisabled : null,
              ]}>
              <Text
                style={[
                  styles.modeLabel,
                  active ? styles.modeLabelActive : null,
                ]}>
                {option.label}
              </Text>
              <Text style={styles.modeHint}>
                {disabled
                  ? 'Unavailable until at least one topic has a strong match.'
                  : option.hint}
              </Text>
            </Pressable>
          );
        })}
        {aiNeedsMatch && (
          <Text style={styles.warn}>
            Choose original passages or edit the topics until at least one is
            matched.
          </Text>
        )}
        <PrimaryButton
          label="Generate study material"
          onPress={generate}
          disabled={!generateEnabled}
        />
      </Card>
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
  spinner: {
    marginTop: 4,
  },
  imageRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  thumbnail: {
    width: 84,
    height: 84,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#cfd9e4',
    backgroundColor: '#eef2f6',
  },
  topicRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  topicInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#cfd9e4',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 9,
    color: '#1C2A22',
    fontSize: 14,
    backgroundColor: '#fbfcfe',
  },
  remove: {
    color: '#9B3F34',
    fontSize: 13,
    fontWeight: '600',
    padding: 6,
  },
  add: {
    color: '#244936',
    fontSize: 14,
    fontWeight: '700',
    paddingVertical: 4,
  },
  topicHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  topicName: {
    flex: 1,
    color: '#1C2A22',
    fontSize: 15,
    fontWeight: '700',
  },
  badge: {
    overflow: 'hidden',
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 3,
    fontSize: 11,
    fontWeight: '700',
  },
  badgeMatched: {
    color: '#146c43',
    backgroundColor: '#dff3e7',
  },
  badgeAmbiguous: {
    color: '#7a4d00',
    backgroundColor: '#fff0cc',
  },
  badgeUnmatched: {
    color: '#9b2c24',
    backgroundColor: '#fde4e1',
  },
  match: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#DFD8C8',
    paddingTop: 8,
    gap: 3,
  },
  refs: {
    color: '#244936',
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  passage: {
    color: '#1C2A22',
    fontSize: 14,
    lineHeight: 21,
  },
  mode: {
    borderWidth: 1,
    borderColor: '#DFD8C8',
    borderRadius: 10,
    padding: 12,
    gap: 3,
    backgroundColor: '#ffffff',
  },
  modeActive: {
    borderColor: '#2E5B43',
    backgroundColor: '#eef6ff',
  },
  modeDisabled: {
    opacity: 0.55,
  },
  modeLabel: {
    color: '#1C2A22',
    fontSize: 14,
    fontWeight: '700',
  },
  modeLabelActive: {
    color: '#244936',
  },
  modeHint: {
    color: '#5E6B61',
    fontSize: 13,
    lineHeight: 18,
  },
});
