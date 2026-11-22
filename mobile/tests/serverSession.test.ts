import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getHealth: vi.fn(),
  getTopicSource: vi.fn(),
}));

vi.mock('expo-file-system', () => ({
  File: class {
    exists = false;
    text = async () => '';
    write = () => {};
  },
  Paths: { document: '' },
}));

vi.mock('@/services/apiClient', () => ({
  getDocument: mocks.getDocument,
  getHealth: mocks.getHealth,
  getTopicSource: mocks.getTopicSource,
}));

import type { AgentAttachment } from '@/services/apiClient';
import {
  checkServerSession,
  findMissingAttachments,
  type ServerSessionStorage,
} from '@/services/serverSession';

function memoryStorage(initial: string | null = null): ServerSessionStorage {
  let value = initial;
  return {
    read: async () => value,
    write: async (bootId) => {
      value = bootId;
    },
  };
}

const document: AgentAttachment = {
  id: 'document-1',
  kind: 'pdf',
  name: 'lecture.pdf',
};
const images: AgentAttachment = {
  id: 'source-1',
  kind: 'image',
  name: 'notes.jpg',
};

describe('server session checks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getHealth.mockResolvedValue({ boot_id: 'boot-new' });
    mocks.getDocument.mockResolvedValue({});
    mocks.getTopicSource.mockResolvedValue({});
  });

  it('detects a changed boot id and stores the new one', async () => {
    const storage = memoryStorage('boot-old');

    const result = await checkServerSession([], storage);

    expect(result.restarted).toBe(true);
    expect(result.missingAttachmentIds).toEqual([]);
    await expect(storage.read()).resolves.toBe('boot-new');
  });

  it('does not report a restart when the boot id is unchanged', async () => {
    const result = await checkServerSession([], memoryStorage('boot-new'));

    expect(result.restarted).toBe(false);
  });

  it('reports only attachments the server answers 404 for', async () => {
    mocks.getDocument.mockRejectedValue({ status: 404 });
    mocks.getTopicSource.mockRejectedValue({ status: 500 });

    const missing = await findMissingAttachments([document, images]);

    expect(missing).toEqual(['document-1']);
  });

  it('reports a missing document together with a changed boot id', async () => {
    mocks.getDocument.mockRejectedValue({ status: 404 });

    const result = await checkServerSession(
      [document],
      memoryStorage('boot-old'),
    );

    expect(result.restarted).toBe(true);
    expect(result.missingAttachmentIds).toEqual(['document-1']);
  });
});
