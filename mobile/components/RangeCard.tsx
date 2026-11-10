import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { CommandExportFormat } from '@/services/chatOrchestrator';
import type { ExtractionResult } from '@/services/commandClient';
import { palette, radii, spacing } from '@/theme';

/**
 * Shows a page/question/chapter extraction. Original text is displayed exactly
 * as the backend returned it; summaries are labelled as AI-written.
 */
export function RangeCard({
  result,
  exporting,
  exportError,
  onExport,
}: {
  result: ExtractionResult;
  exporting: CommandExportFormat | null;
  exportError: string | null;
  onExport: (format: CommandExportFormat) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const PREVIEW_LIMIT = 6;
  const shown = expanded ? result.passages : result.passages.slice(0, PREVIEW_LIMIT);

  return (
    <View style={styles.wrap}>
      {result.passages.length > 0 && (
        <View style={styles.block}>
          <Text style={styles.label}>
            Original text · {result.passages.length} passage
            {result.passages.length === 1 ? '' : 's'}
          </Text>
          {shown.map((passage) => (
            <View key={passage.passage_id} style={styles.passage}>
              <Text style={styles.ref}>{passage.label}</Text>
              <Text selectable style={styles.body}>
                {passage.text}
              </Text>
            </View>
          ))}
          {result.passages.length > PREVIEW_LIMIT && (
            <Pressable accessibilityRole="button" onPress={() => setExpanded((v) => !v)}>
              <Text style={styles.link}>
                {expanded
                  ? 'Show fewer'
                  : `Show all ${result.passages.length} (the export always contains everything)`}
              </Text>
            </Pressable>
          )}
        </View>
      )}

      {result.summaries.length > 0 && (
        <View style={styles.block}>
          <Text style={styles.label}>AI summary</Text>
          {result.summaries.map((summary, index) => (
            <View key={`${summary.label}-${index}`} style={styles.passage}>
              <Text style={styles.ref}>{summary.label}</Text>
              <Text selectable style={styles.body}>
                {summary.text}
              </Text>
            </View>
          ))}
        </View>
      )}

      {result.warnings.map((warning, index) => (
        <Text key={`${warning.code}-${index}`} style={styles.warn}>
          {warning.message}
        </Text>
      ))}

      <View style={styles.block}>
        <Text style={styles.label}>Export</Text>
        <View style={styles.row}>
          {(['pdf', 'docx'] as const).map((format) => (
            <Pressable
              key={format}
              accessibilityRole="button"
              disabled={exporting !== null}
              onPress={() => onExport(format)}
              style={[styles.button, exporting !== null && styles.disabled]}>
              <Text style={styles.buttonText}>
                {exporting === format ? 'Preparing…' : format === 'pdf' ? 'PDF' : 'Word (.docx)'}
              </Text>
            </Pressable>
          ))}
        </View>
        {exportError ? <Text style={styles.warn}>{exportError}</Text> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.md },
  block: { gap: spacing.sm },
  label: { color: palette.muted, fontSize: 12, fontWeight: '600', letterSpacing: 0.4, textTransform: 'uppercase' },
  passage: {
    gap: 4,
    padding: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: palette.surfaceMuted,
  },
  ref: { color: palette.accentInk, fontSize: 12, fontWeight: '600' },
  body: { color: palette.ink, fontSize: 15, lineHeight: 22 },
  link: { color: palette.accent, fontSize: 13, fontWeight: '600' },
  warn: { color: palette.amber, fontSize: 13 },
  row: { flexDirection: 'row', gap: spacing.sm },
  button: {
    minHeight: 44,
    paddingHorizontal: spacing.md,
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: palette.accentSoft,
  },
  disabled: { opacity: 0.5 },
  buttonText: { color: palette.accentInk, fontWeight: '600' },
});
