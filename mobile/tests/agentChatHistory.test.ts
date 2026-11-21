import { describe, expect, it, vi } from 'vitest';

vi.mock('expo-file-system', () => ({
  File: class {
    exists = false;
    text = async () => '';
    write = () => {};
    delete = () => {};
  },
  Paths: { document: '' },
}));

import {
  agentChatTitle,
  deleteAgentChat,
  deleteAllAgentChats,
  loadAgentChats,
  newAgentChat,
  parseAgentChat,
  parseAgentChatHistory,
  parseAgentChatList,
  renameAgentChat,
  saveAgentChat,
  serializeAgentChat,
  serializeAgentChatHistory,
  type AgentChatList,
  type AgentChatRecord,
  type AgentChatStorage,
} from '@/services/agentChatHistory';

function memoryStorage(legacy?: string) {
  const index = new Map<string, string>();
  const chats = new Map<string, string>();
  const storage: AgentChatStorage = {
    readIndex: async () => index.get('index') ?? null,
    writeIndex: async (value) => {
      index.set('index', value);
    },
    readChat: async (id) => chats.get(id) ?? null,
    writeChat: async (id, value) => {
      chats.set(id, value);
    },
    deleteChat: async (id) => {
      chats.delete(id);
    },
    readLegacy: async () => legacy ?? null,
  };
  return { storage, index, chats };
}

function record(overrides: Partial<AgentChatRecord> = {}): AgentChatRecord {
  const base = newAgentChat(1_000);
  return {
    ...base,
    title: 'First question',
    conversationId: 'conversation-1',
    messages: [{ id: '1', role: 'user', text: 'First question' }],
    ...overrides,
  };
}

describe('agent chat history', () => {
  it('migrates the old single snapshot into a saved chat', async () => {
    const legacy = serializeAgentChatHistory({
      version: 1,
      conversationId: 'conversation-1',
      messages: [
        { id: '1', role: 'user', text: 'Summarize page 3.' },
        { id: '2', role: 'assistant', text: 'Summary text.' },
      ],
    });
    const memory = memoryStorage(legacy);

    const loaded = await loadAgentChats(memory.storage);

    expect(loaded.active?.title).toBe('Summarize page 3.');
    expect(loaded.active?.conversationId).toBe('conversation-1');
    expect(loaded.active?.messages).toHaveLength(2);
    expect(loaded.list.chats).toHaveLength(1);
    expect(memory.chats.size).toBe(1);
  });

  it('saves multiple chats newest first and restores the active chat', async () => {
    const memory = memoryStorage();
    let list: AgentChatList = { version: 2, activeChatId: null, chats: [] };
    const older = record({ id: 'older', updatedAt: 2_000 });
    const newer = record({
      id: 'newer',
      title: 'Newer question',
      updatedAt: 3_000,
    });

    list = await saveAgentChat(older, list, memory.storage);
    list = await saveAgentChat(newer, list, memory.storage);
    const loaded = await loadAgentChats(memory.storage);

    expect(list.chats.map((chat) => chat.id)).toEqual(['newer', 'older']);
    expect(loaded.list.activeChatId).toBe('newer');
    expect(loaded.active?.title).toBe('Newer question');
  });

  it('renames, deletes one chat, and deletes all chats', async () => {
    const memory = memoryStorage();
    let list: AgentChatList = { version: 2, activeChatId: null, chats: [] };
    list = await saveAgentChat(record({ id: 'first' }), list, memory.storage);
    list = await saveAgentChat(
      record({ id: 'second', title: 'Second', updatedAt: 2_000 }),
      list,
      memory.storage,
    );

    list = await renameAgentChat('first', 'Renamed chat', list, memory.storage);
    expect(list.chats.find((chat) => chat.id === 'first')?.title).toBe(
      'Renamed chat',
    );
    expect(parseAgentChat(memory.chats.get('first') ?? '')?.title).toBe(
      'Renamed chat',
    );

    list = await deleteAgentChat('first', list, memory.storage);
    expect(list.chats.map((chat) => chat.id)).toEqual(['second']);
    expect(memory.chats.has('first')).toBe(false);

    list = await deleteAllAgentChats(list, memory.storage);
    expect(list).toEqual({ version: 2, activeChatId: null, chats: [] });
    expect(memory.chats.size).toBe(0);
  });

  it('keeps a valid empty index instead of resurrecting the old snapshot', async () => {
    const memory = memoryStorage(
      serializeAgentChatHistory({
        version: 1,
        conversationId: 'old',
        messages: [{ id: '1', role: 'user', text: 'Old chat' }],
      }),
    );
    await memory.storage.writeIndex(
      JSON.stringify({ version: 2, activeChatId: null, chats: [] }),
    );

    const loaded = await loadAgentChats(memory.storage);

    expect(loaded.active).toBeNull();
    expect(loaded.list.chats).toEqual([]);
  });

  it('drops corrupt or missing chats from the index', async () => {
    const memory = memoryStorage();
    const good = record({ id: 'good' });
    await memory.storage.writeChat(
      'good',
      JSON.stringify({
        ...good,
        title: 'Good',
        createdAt: 1,
        updatedAt: 2,
        messages: [{ id: '', role: 'user', text: '' }],
      }),
    );
    await memory.storage.writeIndex(
      JSON.stringify({
        version: 2,
        activeChatId: 'missing',
        chats: [
          { id: 'good', title: 'Good', createdAt: 1, updatedAt: 2 },
          { id: 'missing', title: 'Missing', createdAt: 1, updatedAt: 1 },
          { id: 'bad id', title: 'Bad', createdAt: 1, updatedAt: 1 },
        ],
      }),
    );

    const loaded = await loadAgentChats(memory.storage);

    expect(loaded.list.chats).toEqual([
      { id: 'good', title: 'Good', createdAt: 1, updatedAt: 2, messageCount: 0 },
    ]);
    expect(loaded.list.activeChatId).toBe('good');
  });

  it('sanitizes stored messages, attachments, and bounds', () => {
    const saved = serializeAgentChat(
      record({
        id: 'chat-1',
        title: '   ',
        messages: [
          { id: '1', role: 'user', text: 'x'.repeat(25_000) },
          { id: '', role: 'user', text: 'invalid' },
        ],
        attachments: [
          { id: 'file-1', kind: 'pdf', name: 'notes.pdf' },
          { id: 'file-2', kind: 'exe' as never, name: 'bad' },
        ],
        activeAttachmentId: 'file-2',
      }),
    );

    const parsed = parseAgentChat(saved);

    expect(parsed?.title).toBe('New chat');
    expect(parsed?.messages).toHaveLength(1);
    expect(parsed?.messages[0].text).toHaveLength(20_000);
    expect(parsed?.attachments).toEqual([
      { id: 'file-1', kind: 'pdf', name: 'notes.pdf' },
    ]);
    expect(parsed?.activeAttachmentId).toBe('file-1');
  });

  it('keeps a paused approval block resumable after reload', () => {
    const saved = serializeAgentChat(
      record({
        messages: [
          {
            id: '1',
            role: 'assistant',
            text: 'Approval needed.',
            blocks: [
              {
                type: 'approval',
                pending_id: 'pending-1',
                provider: 'groq',
                pages: [1, 2],
                document_id: 'doc-1',
                source_id: 'source-1',
                label: 'AI reading needs your approval',
                text: 'Allow page images?',
              },
            ],
          },
        ],
      }),
    );

    const frame = parseAgentChat(saved)?.messages[0].blocks?.[0];

    expect(frame).toEqual(
      expect.objectContaining({
        type: 'approval',
        pending_id: 'pending-1',
        provider: 'groq',
        pages: [1, 2],
        document_id: 'doc-1',
        source_id: 'source-1',
      }),
    );
  });

  it('rejects corrupt history and unknown versions safely', async () => {
    const memory = memoryStorage('{not json');
    expect((await loadAgentChats(memory.storage)).active).toBeNull();
    expect(parseAgentChatHistory('{"version":2,"messages":[]}')).toBeNull();
    expect(parseAgentChatList('{"version":1,"chats":[]}')).toBeNull();
    expect(parseAgentChat('{"version":2,"id":"bad id"}')).toBeNull();
  });

  it('derives a title from the first user message', () => {
    expect(
      agentChatTitle([
        { id: '1', role: 'assistant', text: 'Hello' },
        { id: '2', role: 'user', text: '  First   question\ncontinued ' },
      ]),
    ).toBe('First question continued');
  });
});
