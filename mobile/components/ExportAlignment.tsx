import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { HeadingAlign, TextAlign } from '@/services/apiClient';
import { palette, radii, spacing } from '@/theme';

const TEXT_OPTIONS: TextAlign[] = ['left', 'center', 'right', 'justify'];
const HEADING_OPTIONS: HeadingAlign[] = ['left', 'center'];

export function ExportAlignment({
  textAlign,
  headingAlign,
  onTextAlign,
  onHeadingAlign,
  disabled = false,
}: {
  textAlign: TextAlign;
  headingAlign: HeadingAlign;
  onTextAlign: (value: TextAlign) => void;
  onHeadingAlign: (value: HeadingAlign) => void;
  disabled?: boolean;
}) {
  return (
    <View style={styles.wrap}>
      <Text style={styles.label}>Alignment</Text>
      <View style={styles.row}>
        <Text style={styles.rowLabel}>Body</Text>
        {TEXT_OPTIONS.map((value) => (
          <Option
            key={value}
            label={value}
            selected={textAlign === value}
            disabled={disabled}
            onPress={() => onTextAlign(value)}
          />
        ))}
      </View>
      <View style={styles.row}>
        <Text style={styles.rowLabel}>Headings</Text>
        {HEADING_OPTIONS.map((value) => (
          <Option
            key={value}
            label={value}
            selected={headingAlign === value}
            disabled={disabled}
            onPress={() => onHeadingAlign(value)}
          />
        ))}
      </View>
    </View>
  );
}

function Option({
  label,
  selected,
  disabled,
  onPress,
}: {
  label: string;
  selected: boolean;
  disabled: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected, disabled }}
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.option,
        selected && styles.optionSelected,
        disabled && styles.disabled,
      ]}>
      <Text style={[styles.optionText, selected && styles.optionTextSelected]}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.xs },
  label: {
    color: palette.muted,
    fontSize: 12,
    fontWeight: '600',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
  row: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6 },
  rowLabel: { color: palette.muted, fontSize: 12, width: 64 },
  option: {
    minHeight: 36,
    paddingHorizontal: 10,
    justifyContent: 'center',
    borderRadius: radii.sm,
    backgroundColor: palette.surfaceMuted,
  },
  optionSelected: { backgroundColor: palette.accentSoft },
  disabled: { opacity: 0.5 },
  optionText: { color: palette.ink, fontSize: 12, fontWeight: '600' },
  optionTextSelected: { color: palette.accentInk },
});
