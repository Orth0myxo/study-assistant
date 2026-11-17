import * as Sharing from 'expo-sharing';

import {
  createTopicSource,
  downloadAgentFile,
  downloadDocumentPageImage,
  getAgentResult,
  uploadDocument,
  waitForDocument,
  type AgentAttachment,
  type AgentPassageResult,
  type UploadFile,
} from '@/services/apiClient';
import type { DocumentResponse } from '@/types/api';

type DocumentUploadApi = {
  uploadDocument: typeof uploadDocument;
  waitForDocument: typeof waitForDocument;
};

type ImageUploadApi = {
  createTopicSource: typeof createTopicSource;
};

type PassageApi = {
  getAgentResult: typeof getAgentResult;
  downloadDocumentPageImage: typeof downloadDocumentPageImage;
};

type FileSharingApi = {
  downloadAgentFile: typeof downloadAgentFile;
  isSharingAvailable: typeof Sharing.isAvailableAsync;
  shareFile: typeof Sharing.shareAsync;
};

const documentUploadApi: DocumentUploadApi = { uploadDocument, waitForDocument };
const imageUploadApi: ImageUploadApi = { createTopicSource };
const passageApi: PassageApi = { getAgentResult, downloadDocumentPageImage };
const fileSharingApi: FileSharingApi = {
  downloadAgentFile,
  isSharingAvailable: Sharing.isAvailableAsync,
  shareFile: Sharing.shareAsync,
};

export async function uploadAgentDocument(
  file: UploadFile,
  onProgress?: (document: DocumentResponse) => void,
  api: DocumentUploadApi = documentUploadApi,
): Promise<AgentAttachment> {
  const uploaded = await api.uploadDocument(file);
  await api.waitForDocument(uploaded.document_id, onProgress);
  return {
    id: uploaded.document_id,
    kind: uploaded.format === 'docx' ? 'docx' : 'pdf',
    name: uploaded.filename || file.name,
  };
}

export async function uploadAgentImages(
  files: UploadFile[],
  api: ImageUploadApi = imageUploadApi,
): Promise<AgentAttachment> {
  const source = await api.createTopicSource(null, files);
  return {
    id: source.source_id,
    kind: 'image',
    name: `${source.image_count} image(s)`,
  };
}

export async function loadAgentPassages(
  resultId: string,
  api: PassageApi = passageApi,
): Promise<{ result: AgentPassageResult; pageImages: Record<number, string> }> {
  const result = await api.getAgentResult(resultId);
  const pages = [...new Set(result.topics.flatMap((topic) =>
    topic.passages
      .filter((passage) => Number.isInteger(passage.page) && passage.has_page_image)
      .map((passage) => passage.page as number),
  ))].slice(0, 12);
  const loaded = await Promise.all(pages.map(async (page) => {
    try {
      return [page, await api.downloadDocumentPageImage(result.document_id, page)] as const;
    } catch {
      return null;
    }
  }));
  return {
    result,
    pageImages: Object.fromEntries(
      loaded.filter((item): item is readonly [number, string] => item !== null),
    ),
  };
}

export function isExpiredAgentConversation(error: unknown): boolean {
  return Boolean(
    error &&
      typeof error === 'object' &&
      'code' in error &&
      (error as { code?: unknown }).code === 'conversation_not_found',
  );
}

export function conversationIdAfterAgentError(
  currentId: string | null,
  error: unknown,
): string | null {
  return isExpiredAgentConversation(error) ? null : currentId;
}

export async function shareAgentFile(
  fileId: string,
  filename: string,
  api: FileSharingApi = fileSharingApi,
): Promise<string> {
  const uri = await api.downloadAgentFile(fileId, filename);
  if (!(await api.isSharingAvailable())) {
    throw new Error('File sharing is unavailable on this device.');
  }
  await api.shareFile(uri);
  return uri;
}
