import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { displayFont, palette } from '@/theme';

export default function RootLayout() {
  return (
    <>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerTitleAlign: 'left',
          headerStyle: { backgroundColor: palette.canvas },
          headerTintColor: palette.ink,
          headerShadowVisible: false,
          headerTitleStyle: {
            fontFamily: displayFont,
            fontSize: 20,
            fontWeight: '600',
            color: palette.ink,
          },
          contentStyle: { backgroundColor: palette.canvas },
        }}>
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen
          name="workspace"
          options={{ headerShown: false }}
        />
        <Stack.Screen name="upload" options={{ title: 'New study' }} />
        <Stack.Screen name="topics" options={{ title: 'Topics & mode' }} />
        <Stack.Screen name="results" options={{ title: 'Study notes' }} />
        <Stack.Screen
          name="photo-notes"
          options={{ title: 'Jottings & source' }}
        />
        <Stack.Screen name="photo-topics" options={{ title: 'Review topics' }} />
        <Stack.Screen name="photo-results" options={{ title: 'Study material' }} />
        <Stack.Screen name="settings" options={{ title: 'Settings' }} />
      </Stack>
    </>
  );
}
