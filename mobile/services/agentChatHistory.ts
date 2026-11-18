import { File, Paths } from 'expo-file-system';

import type { AgentBlock } from '@/services/apiClient';

export type AgentChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tone?: 'warn';
  blocks?: AgentBlock[];
  warnings?: string[];
};

export type AgentChatSnapshot = {
  version: 1;
  conversationId: string | null;
  messages: AgentChatMessage[];
};

export type AgentChatStorage = {
  read: () => Promise<string | null>;
  write: (value: string) => Promise<void>;
};

const HISTORY_FILENAME = 'agent-chat-history.json';
const MAX_MESSAGES = 200;
const MAX_TEXT = 20_000;

function nativeStorage(): AgentChatStorage {
  const file = new File(Paths.document, HISTORY_FILENAME);
  return {
    read: async () => (file.exists ? file.text() : null),
    write: async (value) => {
      file.write(value);
    },
  };
}

function safeString(value: unknown, limit: number): string | null {
  return typeof value === 'string' ? value.slice(0, limit) : null;
}

function parseMessage(value: unknown): AgentChatMessage | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const item = value as Record<string, unknown>;
  const id = safeString(item.id, 100);
  const text = safeString(item.text, MAX_TEXT);
  const role = item.role === 'user' || item.role === 'assistant' ? item.role : null;
  if (!id || text === null || !role) {
    return null;
  }
  const warnings = Array.isArray(item.warnings)
    ? item.warnings
        .map((warning) => safeString(warning, 1_000))
        .filter((warning): warning is string => warning !== null)
    : undefined;
  return {
    id,
    role,
    text,
    ...(item.tone === 'warn' ? { tone: 'warn' as const } : {}),
    ...(Array.isArray(item.blocks)
      ? { blocks: item.blocks.filter((block) => Boolean(block)) as AgentBlock[] }
      : {}),
    ...(warnings && warnings.length > 0 ? { warnings } : {}),
  };
}

export function parseAgentChatHistory(value: string): AgentChatSnapshot | null {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (parsed.version !== 1 || !Array.isArray(parsed.messages)) {
      return null;
    }
    const messages = parsed.messages
      .map(parseMessage)
      .filter((message): message is AgentChatMessage => message !== null)
      .slice(-MAX_MESSAGES);
    const conversationId =
      typeof parsed.conversationId === 'string'
        ? parsed.conversationId.slice(0, 100)
        : null;
    return { version: 1, conversationId, messages };
  } catch {
    return null;
  }
}

export function serializeAgentChatHistory(snapshot: AgentChatSnapshot): string {
  return JSON.stringify({
    version: 1,
    conversationId: snapshot.conversationId,
    messages: snapshot.messages.slice(-MAX_MESSAGES),
  });
}

export async function loadAgentChatHistory(
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatSnapshot | null> {
  try {
    const value = await storage.read();
    return value ? parseAgentChatHistory(value) : null;
  } catch {
    return null;
  }
}

export async function saveAgentChatHistory(
  snapshot: AgentChatSnapshot,
  storage: AgentChatStorage = nativeStorage(),
): Promise<void> {
  await storage.write(serializeAgentChatHistory(snapshot));
}
