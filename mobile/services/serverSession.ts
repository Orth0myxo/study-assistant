import { File, Paths } from 'expo-file-system';

import {
  getDocument,
  getHealth,
  getTopicSource,
  type AgentAttachment,
} from '@/services/apiClient';

const BOOT_FILENAME = 'server-boot.json';

export type ServerSessionStorage = {
  read: () => Promise<string | null>;
  write: (bootId: string) => Promise<void>;
};

export type ServerSessionCheck = {
  restarted: boolean;
  missingAttachmentIds: string[];
  bootId: string | null;
};

function nativeStorage(): ServerSessionStorage {
  const file = new File(Paths.document, BOOT_FILENAME);
  return {
    read: async () => {
      if (!file.exists) {
        return null;
      }
      try {
        const parsed = JSON.parse(await file.text()) as { boot_id?: unknown };
        return typeof parsed.boot_id === 'string' && parsed.boot_id
          ? parsed.boot_id
          : null;
      } catch {
        return null;
      }
    },
    write: async (bootId) => {
      file.write(JSON.stringify({ boot_id: bootId }));
    },
  };
}

const defaultStorage = nativeStorage();

function isMissing(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'status' in error &&
    (error as { status?: unknown }).status === 404
  );
}

async function attachmentMissing(item: AgentAttachment): Promise<boolean> {
  try {
    if (item.kind === 'pdf' || item.kind === 'docx') {
      await getDocument(item.id);
    } else {
      await getTopicSource(item.id);
    }
    return false;
  } catch (error) {
    return isMissing(error);
  }
}

export async function findMissingAttachments(
  attachments: AgentAttachment[],
): Promise<string[]> {
  if (attachments.length === 0) {
    return [];
  }
  const results = await Promise.all(
    attachments.map(async (item) => ({
      id: item.id,
      missing: await attachmentMissing(item),
    })),
  );
  return results.filter((item) => item.missing).map((item) => item.id);
}

export async function checkServerSession(
  attachments: AgentAttachment[] = [],
  storage: ServerSessionStorage = defaultStorage,
): Promise<ServerSessionCheck> {
  let bootId: string | null = null;
  try {
    const health = await getHealth();
    bootId = health.boot_id ?? null;
  } catch {
    // A health failure must not interrupt the chat; material checks still run.
  }

  const previousBootId = await storage.read();
  const bootChanged = Boolean(
    bootId && previousBootId && bootId !== previousBootId,
  );
  if (bootId) {
    try {
      await storage.write(bootId);
    } catch {
      // The next check can retry; a local write failure must not block the chat.
    }
  }

  const missingAttachmentIds = await findMissingAttachments(attachments);

  return {
    restarted: bootChanged,
    missingAttachmentIds,
    bootId,
  };
}
