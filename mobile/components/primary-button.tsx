import { Pressable, StyleSheet, Text, View } from 'react-native';

import { palette, radii, spacing } from '@/theme';

type Props = {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary';
};

export function PrimaryButton({
  label,
  onPress,
  disabled = false,
  variant = 'primary',
}: Props) {
  const secondary = variant === 'secondary';
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        secondary ? styles.secondary : null,
        pressed && !disabled
          ? secondary
            ? styles.secondaryPressed
            : styles.pressed
          : null,
        disabled ? styles.disabled : null,
      ]}>
      <Text style={[styles.label, secondary ? styles.secondaryLabel : null]}>
        {label}
      </Text>
    </Pressable>
  );
}

export function Card({ children }: { children: React.ReactNode }) {
  return <View style={styles.card}>{children}</View>;
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: palette.accent,
    paddingVertical: 15,
    paddingHorizontal: spacing.xl,
    borderRadius: radii.md,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 50,
  },
  pressed: {
    backgroundColor: palette.accentPressed,
  },
  disabled: {
    opacity: 0.45,
  },
  label: {
    color: palette.surface,
    fontSize: 15,
    fontWeight: '600',
    letterSpacing: 0.2,
  },
  secondary: {
    backgroundColor: 'transparent',
    borderWidth: 1,
    borderColor: palette.lineStrong,
  },
  secondaryPressed: {
    backgroundColor: palette.accentSoft,
  },
  secondaryLabel: {
    color: palette.accentInk,
  },
  card: {
    backgroundColor: palette.surface,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: palette.line,
  },
});
