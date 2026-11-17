export type WelcomeActionId = 'passages' | 'summary' | 'both' | 'pages';

export type WelcomeAction = {
  id: WelcomeActionId;
  label: string;
  field: 'topic' | 'pages';
};

export const WELCOME_ACTIONS: WelcomeAction[] = [
  { id: 'passages', label: 'Original passages', field: 'topic' },
  { id: 'summary', label: 'Summary', field: 'topic' },
  { id: 'both', label: 'Both', field: 'topic' },
  { id: 'pages', label: 'Page range', field: 'pages' },
];

export function buildWelcomeRequest(
  id: WelcomeActionId,
  value: string,
): string | null {
  const entered = value.trim();
  if (id === 'pages') {
    return entered ? `pages ${entered}` : null;
  }
  const target = entered || 'the whole document';
  if (id === 'passages') {
    return `verbatim text about ${target}`;
  }
  if (id === 'summary') {
    return `summary of ${target}`;
  }
  return `verbatim text about ${target} with a summary`;
}
