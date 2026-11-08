import Constants from 'expo-constants';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import { ApiError, getBaseUrl, getHealth } from '@/services/apiClient';
import { displayFont, palette, radii, spacing } from '@/theme';
import { HealthResponse } from '@/types/api';

type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok'; health: HealthResponse }
  | { kind: 'error'; message: string };

export default function SettingsScreen() {
  const [health, setHealth] = useState<HealthState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    getHealth()
      .then((next) => {
        if (active) {
          setHealth({ kind: 'ok', health: next });
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setHealth({
            kind: 'error',
            message:
              error instanceof ApiError ? error.message : 'Unexpected error.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const refreshHealth = () => {
    setHealth({ kind: 'loading' });
    setReloadKey((current) => current + 1);
  };

  const version = Constants.expoConfig?.version ?? '0.1.0';

  return (
    <Screen>
      <View style={styles.intro}>
        <Text style={styles.introTitle}>A quiet place to study.</Text>
        <Text style={styles.introCopy}>
          Study Assistant keeps the conversation focused on the material you
          add. These details help you verify the connection.
        </Text>
      </View>

      <Card>
        <Text style={styles.cardTitle}>Connection</Text>
        <Text style={styles.label}>Study server</Text>
        <Text style={styles.value} selectable>
          {getBaseUrl()}
        </Text>
        <HealthLine health={health} onRetry={refreshHealth} />
      </Card>

      <Card>
        <Text style={styles.cardTitle}>How your material is handled</Text>
        <Text style={styles.copy}>
          Uploaded files are processed by your configured server. Saved
          summaries and study outputs remain available until the server&apos;s
          retention window ends.
        </Text>
        <Text style={styles.copy}>
          AI study material is generated only from the passages shown in the
          conversation. Optional background is clearly labelled when enabled.
        </Text>
      </Card>

      <Card>
        <Text style={styles.cardTitle}>About</Text>
        <View style={styles.aboutRow}>
          <Text style={styles.label}>App version</Text>
          <Text style={styles.value}>{version}</Text>
        </View>
        <Text style={styles.copy}>
          Study Assistant is an Android-first learning companion for PDF and
          DOCX study material.
        </Text>
      </Card>

      <PrimaryButton
        label="Check connection again"
        variant="secondary"
        onPress={refreshHealth}
      />
    </Screen>
  );
}

function HealthLine({
  health,
  onRetry,
}: {
  health: HealthState;
  onRetry: () => void;
}) {
  if (health.kind === 'loading') {
    return (
      <View style={styles.healthRow}>
        <ActivityIndicator size="small" color={palette.accent} />
        <Text style={styles.healthText}>Checking the server…</Text>
      </View>
    );
  }

  if (health.kind === 'error') {
    return (
      <View style={styles.healthBlock}>
        <View style={styles.healthRow}>
          <View style={[styles.statusDot, styles.statusError]} />
          <Text style={styles.healthText}>Not reachable</Text>
        </View>
        <Text style={styles.copy}>{health.message}</Text>
        <Pressable
          accessibilityRole="button"
          onPress={onRetry}
          style={styles.retryButton}>
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  const ai = health.health.ai;
  const aiText =
    ai.provider === 'configured'
      ? `AI ready${ai.model ? ` · ${ai.model}` : ''}`
      : 'AI is not configured on the server';

  return (
    <View style={styles.healthBlock}>
      <View style={styles.healthRow}>
        <View style={[styles.statusDot, styles.statusOk]} />
        <Text style={styles.healthText}>Connected · v{health.health.version}</Text>
      </View>
      <Text style={styles.copy}>{aiText}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  intro: {
    gap: spacing.sm,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  introTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 26,
    lineHeight: 32,
    fontWeight: '600',
  },
  introCopy: {
    color: palette.inkSoft,
    fontSize: 14,
    lineHeight: 21,
  },
  cardTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 19,
    fontWeight: '600',
    marginBottom: spacing.xs,
  },
  label: {
    color: palette.muted,
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  value: {
    color: palette.inkSoft,
    fontSize: 14,
    lineHeight: 20,
  },
  copy: {
    color: palette.muted,
    fontSize: 13,
    lineHeight: 20,
  },
  healthBlock: {
    gap: spacing.sm,
  },
  healthRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  statusOk: {
    backgroundColor: palette.accent,
  },
  statusError: {
    backgroundColor: palette.danger,
  },
  healthText: {
    color: palette.inkSoft,
    fontSize: 13,
    fontWeight: '600',
  },
  retryButton: {
    alignSelf: 'flex-start',
    minHeight: 40,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radii.sm,
    backgroundColor: palette.accentSoft,
  },
  retryText: {
    color: palette.accentInk,
    fontSize: 12,
    fontWeight: '700',
  },
  aboutRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
  },
});
