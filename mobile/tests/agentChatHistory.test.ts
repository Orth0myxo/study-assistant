import { describe, expect, it, vi } from 'vitest';

vi.mock('expo-file-system', () => ({
  File: class {
    exists = false;
    text = async () => '';
    write = () => {};
  },
  Paths: { document: '' },
}));

import {
  loadAgentChatHistory,
  parseAgentChatHistory,
  saveAgentChatHistory,
  serializeAgentChatHistory,
  type AgentChatStorage,
} from '@/services/agentChatHistory';

describe('agent chat history', () => {
  it('round-trips local messages and conversation state', () => {
    const snapshot = {
      version: 1 as const,
      conversationId: 'conversation-1',
      messages: [
        { id: '1', role: 'user' as const, text: 'Summarize page 3.' },
        {
          id: '2',
          role: 'assistant' as const,
          text: 'Summary text.',
          warnings: ['One warning.'],
          blocks: [{ type: 'summary' as const, text: 'Summary text.' }],
        },
      ],
    };

    expect(parseAgentChatHistory(serializeAgentChatHistory(snapshot))).toEqual(
      snapshot,
    );
  });

  it('rejects corrupt history instead of failing the screen', async () => {
    const storage: AgentChatStorage = {
      read: async () => '{not json',
      write: async () => {},
    };

    expect(await loadAgentChatHistory(storage)).toBeNull();
    expect(parseAgentChatHistory('{"version":2,"messages":[]}')).toBeNull();
  });

  it('drops malformed messages and keeps the newest saved messages', () => {
    const messages = Array.from({ length: 205 }, (_, index) => ({
      id: String(index),
      role: 'user',
      text: `message ${index}`,
    }));
    messages.push({ id: '', role: 'user', text: 'invalid' } as never);

    const parsed = parseAgentChatHistory(
      JSON.stringify({ version: 1, conversationId: null, messages }),
    );

    expect(parsed?.messages).toHaveLength(200);
    expect(parsed?.messages[0].text).toBe('message 5');
    expect(parsed?.messages.at(-1)?.text).toBe('message 204');
  });

  it('writes through the provided device storage', async () => {
    let saved = '';
    const storage: AgentChatStorage = {
      read: async () => saved,
      write: async (value) => {
        saved = value;
      },
    };

    await saveAgentChatHistory(
      {
        version: 1,
        conversationId: null,
        messages: [{ id: '1', role: 'user', text: 'hello' }],
      },
      storage,
    );

    expect(await loadAgentChatHistory(storage)).toEqual({
      version: 1,
      conversationId: null,
      messages: [{ id: '1', role: 'user', text: 'hello' }],
    });
  });
});
