import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import { StudyMode } from '@/types/api';

const OPTIONS: { mode: StudyMode; label: string; hint: string }[] = [
  {
    mode: 'extracted_text_only',
    label: 'Extracted text only',
    hint: 'Show the source text exactly as extracted. No AI is used.',
  },
  {
    mode: 'text_and_summaries',
    label: 'Text + AI summaries',
    hint: 'Show the source text together with an AI summary of each part.',
  },
  {
    mode: 'summaries_only',
    label: 'AI summaries only',
    hint: 'Show AI summaries with source references, without the full text.',
  },
];

export default function TopicsScreen() {
  const router = useRouter();
  const { documentId } = useLocalSearchParams<{ documentId?: string }>();
  const [mode, setMode] = useState<StudyMode>('text_and_summaries');
  const ready = typeof documentId === 'string' && documentId.length > 0;

  return (
    <Screen>
      <Card>
        <Text style={styles.title}>Study mode</Text>
        <Text style={styles.muted}>
          Choose how the uploaded material should be presented. AI modes need a
          Gemini API key configured on the server.
        </Text>
        {!ready && (
          <Text style={styles.error}>
            No document selected. Go back and upload a file first.
          </Text>
        )}
      </Card>

      {OPTIONS.map((option) => {
        const active = option.mode === mode;
        return (
          <Pressable
            key={option.mode}
            accessibilityRole="button"
            onPress={() => setMode(option.mode)}
            style={[styles.option, active ? styles.optionActive : null]}>
            <Text
              style={[
                styles.optionLabel,
                active ? styles.optionLabelActive : null,
              ]}>
              {option.label}
            </Text>
            <Text style={styles.optionHint}>{option.hint}</Text>
          </Pressable>
        );
      })}

      <PrimaryButton
        label="Start processing"
        disabled={!ready}
        onPress={() =>
          router.push({
            pathname: '/results',
            params: { documentId: documentId as string, mode },
          })
        }
      />
    </Screen>
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
  error: {
    color: '#b3261e',
    fontSize: 14,
  },
  option: {
    backgroundColor: '#ffffff',
    borderRadius: 12,
    padding: 16,
    gap: 4,
    borderWidth: 1,
    borderColor: '#dfe6ee',
  },
  optionActive: {
    borderColor: '#208AEF',
    backgroundColor: '#eef6ff',
  },
  optionLabel: {
    fontSize: 15,
    fontWeight: '600',
    color: '#1b2a3a',
  },
  optionLabelActive: {
    color: '#1668bd',
  },
  optionHint: {
    color: '#5b6b7c',
    fontSize: 13,
    lineHeight: 18,
  },
});
