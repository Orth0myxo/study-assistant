import { Pressable, StyleSheet, Text, View } from 'react-native';

import { palette, radii, spacing } from '@/theme';

type Props = {
  onPress: () => void;
};

export function FileConverterButton({ onPress }: Props) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel="Open file converter"
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        pressed ? styles.pressed : null,
      ]}>
      <Text style={styles.icon}>⇄</Text>
      <View style={styles.copy}>
        <Text style={styles.label}>File Converter</Text>
        <Text style={styles.hint}>Excel, Word, PDF, CSV</Text>
      </View>
      <Text style={styles.chevron}>›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: palette.line,
    backgroundColor: palette.accentSoft,
  },
  pressed: { backgroundColor: palette.canvasDeep },
  icon: {
    color: palette.accent,
    fontSize: 18,
    fontWeight: '700',
  },
  copy: { flex: 1, gap: 1 },
  label: {
    color: palette.accentInk,
    fontSize: 14,
    fontWeight: '600',
  },
  hint: { color: palette.muted, fontSize: 12 },
  chevron: { color: palette.accent, fontSize: 22, lineHeight: 22 },
});
