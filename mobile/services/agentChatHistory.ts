import { File, Paths } from 'expo-file-system';

import type { AgentAttachment, AgentBlock } from '@/services/apiClient';

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

export type AgentChatRecord = {
  version: 2;
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  conversationId: string | null;
  messages: AgentChatMessage[];
  attachments: AgentAttachment[];
  activeAttachmentId: string | null;
};

export type SavedAgentChat = {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
};

export type AgentChatList = {
  version: 2;
  activeChatId: string | null;
  chats: SavedAgentChat[];
};

export type AgentChatLoad = {
  list: AgentChatList;
  active: AgentChatRecord | null;
};

export type AgentChatStorage = {
  readIndex: () => Promise<string | null>;
  writeIndex: (value: string) => Promise<void>;
  readChat: (id: string) => Promise<string | null>;
  writeChat: (id: string, value: string) => Promise<void>;
  deleteChat: (id: string) => Promise<void>;
  readLegacy?: () => Promise<string | null>;
};

const INDEX_FILENAME = 'agent-chat-index.json';
const CHAT_PREFIX = 'agent-chat-';
const LEGACY_FILENAME = 'agent-chat-history.json';
const MAX_MESSAGES = 200;
const MAX_TEXT = 20_000;
const MAX_TITLE = 80;
const MAX_BLOCK_TEXT = 20_000;
const MAX_OPTIONS = 20;
const MAX_ID = 100;
const ID_PATTERN = /^[a-z0-9-]{1,80}$/;

function nativeStorage(): AgentChatStorage {
  const indexFile = new File(Paths.document, INDEX_FILENAME);
  const legacyFile = new File(Paths.document, LEGACY_FILENAME);
  const chatFile = (id: string) =>
    new File(Paths.document, `${CHAT_PREFIX}${id}.json`);
  return {
    readIndex: async () => (indexFile.exists ? indexFile.text() : null),
    writeIndex: async (value) => {
      indexFile.write(value);
    },
    readChat: async (id) => {
      if (!ID_PATTERN.test(id)) {
        return null;
      }
      const file = chatFile(id);
      return file.exists ? file.text() : null;
    },
    writeChat: async (id, value) => {
      if (!ID_PATTERN.test(id)) {
        return;
      }
      chatFile(id).write(value);
    },
    deleteChat: async (id) => {
      if (!ID_PATTERN.test(id)) {
        return;
      }
      const file = chatFile(id);
      if (file.exists) {
        file.delete();
      }
    },
    readLegacy: async () => (legacyFile.exists ? legacyFile.text() : null),
  };
}

function safeString(value: unknown, limit: number): string | null {
  return typeof value === 'string' ? value.slice(0, limit) : null;
}

function safeTimestamp(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : fallback;
}

function cleanTitle(value: unknown): string {
  const title = (safeString(value, MAX_TITLE) ?? '').replace(/\s+/g, ' ').trim();
  return title || 'New chat';
}

function parseBlock(value: unknown): AgentBlock | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const item = value as Record<string, unknown>;
  if (
    item.type !== 'passages' &&
    item.type !== 'summary' &&
    item.type !== 'file' &&
    item.type !== 'question'
  ) {
    return null;
  }
  const block: AgentBlock = { type: item.type };
  const resultId = safeString(item.result_id, MAX_ID);
  const fileId = safeString(item.file_id, MAX_ID);
  const name = safeString(item.name, 200);
  const text = safeString(item.text, MAX_BLOCK_TEXT);
  const label = safeString(item.label, 200);
  const basis = safeString(item.basis, 500);
  if (resultId) block.result_id = resultId;
  if (fileId) block.file_id = fileId;
  if (item.format === 'docx' || item.format === 'pdf') {
    block.format = item.format;
  }
  if (name) block.name = name;
  if (text !== null) block.text = text;
  if (label) block.label = label;
  if (basis) block.basis = basis;
  if (Array.isArray(item.options)) {
    const options = item.options
      .map((option) => safeString(option, 300))
      .filter((option): option is string => option !== null)
      .slice(0, MAX_OPTIONS);
    if (options.length > 0) block.options = options;
  }
  return block;
}

function parseMessage(value: unknown): AgentChatMessage | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const item = value as Record<string, unknown>;
  const id = safeString(item.id, MAX_ID);
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
  const blocks = Array.isArray(item.blocks)
    ? item.blocks
        .map(parseBlock)
        .filter((block): block is AgentBlock => block !== null)
    : undefined;
  return {
    id,
    role,
    text,
    ...(item.tone === 'warn' ? { tone: 'warn' as const } : {}),
    ...(blocks && blocks.length > 0 ? { blocks } : {}),
    ...(warnings && warnings.length > 0 ? { warnings } : {}),
  };
}

function parseAttachment(value: unknown): AgentAttachment | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const item = value as Record<string, unknown>;
  const id = safeString(item.id, MAX_ID);
  const name = safeString(item.name, 300);
  const kind =
    item.kind === 'pdf' || item.kind === 'docx' || item.kind === 'image'
      ? item.kind
      : null;
  if (!id || !name || !kind) {
    return null;
  }
  return { id, kind, name };
}

export function agentChatTitle(messages: AgentChatMessage[]): string {
  const first = messages.find(
    (message) => message.role === 'user' && message.text.trim(),
  );
  return cleanTitle(first?.text ?? '');
}

export function newAgentChatId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function newAgentChat(now = Date.now()): AgentChatRecord {
  return {
    version: 2,
    id: newAgentChatId(),
    title: 'New chat',
    createdAt: now,
    updatedAt: now,
    conversationId: null,
    messages: [],
    attachments: [],
    activeAttachmentId: null,
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
        ? parsed.conversationId.slice(0, MAX_ID)
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

export function parseAgentChat(value: string): AgentChatRecord | null {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (parsed.version !== 2) {
      return null;
    }
    const id = safeString(parsed.id, MAX_ID);
    if (!id || !ID_PATTERN.test(id) || !Array.isArray(parsed.messages)) {
      return null;
    }
    const messages = parsed.messages
      .map(parseMessage)
      .filter((message): message is AgentChatMessage => message !== null)
      .slice(-MAX_MESSAGES);
    const attachments = Array.isArray(parsed.attachments)
      ? parsed.attachments
          .map(parseAttachment)
          .filter((item): item is AgentAttachment => item !== null)
      : [];
    const requestedActive = safeString(parsed.activeAttachmentId, MAX_ID);
    const now = Date.now();
    return {
      version: 2,
      id,
      title: cleanTitle(parsed.title),
      createdAt: safeTimestamp(parsed.createdAt, now),
      updatedAt: safeTimestamp(parsed.updatedAt, now),
      conversationId:
        typeof parsed.conversationId === 'string'
          ? parsed.conversationId.slice(0, MAX_ID)
          : null,
      messages,
      attachments,
      activeAttachmentId:
        requestedActive &&
        attachments.some((attachment) => attachment.id === requestedActive)
          ? requestedActive
          : attachments.at(-1)?.id ?? null,
    };
  } catch {
    return null;
  }
}

export function serializeAgentChat(record: AgentChatRecord): string {
  return JSON.stringify({
    ...record,
    title: cleanTitle(record.title),
    messages: record.messages.slice(-MAX_MESSAGES),
    attachments: record.attachments,
    activeAttachmentId:
      record.activeAttachmentId &&
      record.attachments.some((item) => item.id === record.activeAttachmentId)
        ? record.activeAttachmentId
        : null,
  });
}

export function parseAgentChatList(value: string): AgentChatList | null {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (parsed.version !== 2 || !Array.isArray(parsed.chats)) {
      return null;
    }
    const seen = new Set<string>();
    const now = Date.now();
    const chats: SavedAgentChat[] = [];
    for (const value of parsed.chats) {
      if (!value || typeof value !== 'object') {
        continue;
      }
      const item = value as Record<string, unknown>;
      const id = safeString(item.id, MAX_ID);
      if (!id || !ID_PATTERN.test(id) || seen.has(id)) {
        continue;
      }
      seen.add(id);
      chats.push({
        id,
        title: cleanTitle(item.title),
        createdAt: safeTimestamp(item.createdAt, now),
        updatedAt: safeTimestamp(item.updatedAt, now),
        messageCount:
          typeof item.messageCount === 'number' && item.messageCount >= 0
            ? Math.floor(item.messageCount)
            : 0,
      });
    }
    chats.sort((left, right) => right.updatedAt - left.updatedAt);
    const activeChatId = safeString(parsed.activeChatId, MAX_ID);
    return {
      version: 2,
      activeChatId:
        activeChatId && seen.has(activeChatId)
          ? activeChatId
          : chats[0]?.id ?? null,
      chats,
    };
  } catch {
    return null;
  }
}

export function summarizeAgentChat(record: AgentChatRecord): SavedAgentChat {
  return {
    id: record.id,
    title: cleanTitle(record.title),
    createdAt: record.createdAt,
    updatedAt: record.updatedAt,
    messageCount: record.messages.length,
  };
}

function withChat(list: AgentChatList, record: AgentChatRecord): AgentChatList {
  const chats = [
    summarizeAgentChat(record),
    ...list.chats.filter((chat) => chat.id !== record.id),
  ].sort((left, right) => right.updatedAt - left.updatedAt);
  return { version: 2, activeChatId: record.id, chats };
}

export function emptyAgentChatList(): AgentChatList {
  return { version: 2, activeChatId: null, chats: [] };
}

export async function loadAgentChats(
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatLoad> {
  try {
    const rawIndex = await storage.readIndex();
    const parsedIndex = rawIndex ? parseAgentChatList(rawIndex) : null;
    if (parsedIndex) {
      const records = await Promise.all(
        parsedIndex.chats.map(async (chat) => {
          const raw = await storage.readChat(chat.id);
          return raw ? parseAgentChat(raw) : null;
        }),
      );
      const valid = records.filter(
        (record): record is AgentChatRecord =>
          record !== null && parsedIndex.chats.some((chat) => chat.id === record.id),
      );
      const chats = valid.map(summarizeAgentChat).sort(
        (left, right) => right.updatedAt - left.updatedAt,
      );
      const list: AgentChatList = {
        version: 2,
        activeChatId:
          chats.find((chat) => chat.id === parsedIndex.activeChatId)?.id ??
          chats[0]?.id ??
          null,
        chats,
      };
      if (chats.length !== parsedIndex.chats.length) {
        await storage.writeIndex(JSON.stringify(list));
      }
      return {
        list,
        active:
          valid.find((record) => record.id === list.activeChatId) ?? null,
      };
    }
    return await migrateLegacyChat(storage);
  } catch {
    return { list: emptyAgentChatList(), active: null };
  }
}

async function migrateLegacyChat(
  storage: AgentChatStorage,
): Promise<AgentChatLoad> {
  const legacyRaw = storage.readLegacy ? await storage.readLegacy() : null;
  const legacy = legacyRaw ? parseAgentChatHistory(legacyRaw) : null;
  if (!legacy || (!legacy.conversationId && legacy.messages.length === 0)) {
    const list = emptyAgentChatList();
    await storage.writeIndex(JSON.stringify(list));
    return { list, active: null };
  }
  const now = Date.now();
  const record: AgentChatRecord = {
    version: 2,
    id: newAgentChatId(),
    title: agentChatTitle(legacy.messages),
    createdAt: now,
    updatedAt: now,
    conversationId: legacy.conversationId,
    messages: legacy.messages,
    attachments: [],
    activeAttachmentId: null,
  };
  const list = withChat(emptyAgentChatList(), record);
  await storage.writeChat(record.id, serializeAgentChat(record));
  await storage.writeIndex(JSON.stringify(list));
  return { list, active: record };
}

export async function loadAgentChat(
  id: string,
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatRecord | null> {
  try {
    const raw = await storage.readChat(id);
    return raw ? parseAgentChat(raw) : null;
  } catch {
    return null;
  }
}

export async function saveAgentChat(
  record: AgentChatRecord,
  list: AgentChatList,
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatList> {
  const next = withChat(list, record);
  await storage.writeChat(record.id, serializeAgentChat(record));
  await storage.writeIndex(JSON.stringify(next));
  return next;
}

export async function renameAgentChat(
  id: string,
  title: string,
  list: AgentChatList,
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatList> {
  const record = await loadAgentChat(id, storage);
  if (!record) {
    return list;
  }
  const nextRecord = {
    ...record,
    title: cleanTitle(title),
    updatedAt: Date.now(),
  };
  await storage.writeChat(nextRecord.id, serializeAgentChat(nextRecord));
  const chats = list.chats
    .map((chat) =>
      chat.id === id
        ? {
            ...chat,
            title: nextRecord.title,
            updatedAt: nextRecord.updatedAt,
          }
        : chat,
    )
    .sort((left, right) => right.updatedAt - left.updatedAt);
  const next = { ...list, chats };
  await storage.writeIndex(JSON.stringify(next));
  return next;
}

export async function deleteAgentChat(
  id: string,
  list: AgentChatList,
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatList> {
  await storage.deleteChat(id);
  const chats = list.chats.filter((chat) => chat.id !== id);
  const next: AgentChatList = {
    version: 2,
    activeChatId:
      list.activeChatId === id
        ? chats[0]?.id ?? null
        : list.activeChatId &&
            chats.some((chat) => chat.id === list.activeChatId)
          ? list.activeChatId
          : chats[0]?.id ?? null,
    chats,
  };
  await storage.writeIndex(JSON.stringify(next));
  return next;
}

export async function deleteAllAgentChats(
  list: AgentChatList,
  storage: AgentChatStorage = nativeStorage(),
): Promise<AgentChatList> {
  await Promise.all(list.chats.map((chat) => storage.deleteChat(chat.id)));
  const next = emptyAgentChatList();
  await storage.writeIndex(JSON.stringify(next));
  return next;
}
