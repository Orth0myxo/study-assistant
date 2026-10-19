import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

export default function RootLayout() {
  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerTitleAlign: 'center',
          headerStyle: { backgroundColor: '#208AEF' },
          headerTintColor: '#ffffff',
          contentStyle: { backgroundColor: '#f5f7fa' },
        }}>
        <Stack.Screen name="index" options={{ title: 'Study Assistant' }} />
        <Stack.Screen name="upload" options={{ title: 'Upload Material' }} />
        <Stack.Screen name="topics" options={{ title: 'Topics & Mode' }} />
        <Stack.Screen name="results" options={{ title: 'Study Notes' }} />
      </Stack>
    </>
  );
}
