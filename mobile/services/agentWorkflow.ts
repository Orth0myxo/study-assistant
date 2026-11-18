import * as Sharing from 'expo-sharing';

import {
  createTopicSource,
  downloadAgentFile,
  downloadDocumentPageImage,
  getAgentExport,
  getAgentResult,
  startAgentExport,
  uploadDocument,
  waitForDocument,
  type AgentAttachment,
  type AgentExportFormat,
  type AgentExportJob,
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

export type AgentExportStage = AgentExportJob['status'] | 'opening';

type AgentExportApi = FileSharingApi & {
  startAgentExport: typeof startAgentExport;
  getAgentExport: typeof getAgentExport;
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
const agentExportApi: AgentExportApi = {
  ...fileSharingApi,
  startAgentExport,
  getAgentExport,
};
const AGENT_EXPORT_POLL_MS = 800;
const AGENT_EXPORT_TIMEOUT_MS = 180000;

const AGENT_FAILURE_MESSAGES: Record<string, string> = {
  agent_rate_limited: 'The AI provider is rate limited.',
  agent_model_not_found: 'The configured agent model was not found.',
  agent_provider_auth_failed: 'The AI provider rejected the configured credentials.',
  agent_provider_unavailable: 'The AI provider is unavailable.',
  agent_provider_error: 'The AI provider rejected the request.',
  agent_timed_out: 'The agent request timed out.',
};

export function agentMessageText(input: string): string | null {
  return input.trim() ? input : null;
}

export function agentFailureMessage(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return 'The request could not be completed.';
  }
  const code = 'code' in error ? String(error.code) : '';
  const message = AGENT_FAILURE_MESSAGES[code];
  if (message) {
    const details =
      'details' in error && error.details && typeof error.details === 'object'
        ? (error.details as Record<string, unknown>)
        : {};
    const retryAfter = details.retry_after_s;
    if (
      code === 'agent_rate_limited' &&
      typeof retryAfter === 'number' &&
      Number.isFinite(retryAfter) &&
      retryAfter > 0
    ) {
      return `${message} Try again in ${Math.ceil(retryAfter)} seconds.`;
    }
    return message;
  }
  if ('message' in error && typeof error.message === 'string' && error.message) {
    return error.message;
  }
  return 'The request could not be completed.';
}

export function uniqueAgentWarnings(warnings: string[]): string[] {
  return [...new Set(warnings)];
}

export function agentResponseWarnings(
  status: string | null | undefined,
  warnings: string[],
): string[] {
  return uniqueAgentWarnings([
    ...warnings,
    ...(status === 'agent_output_truncated'
      ? ['The reply was cut off before it finished. The partial answer is shown above.']
      : []),
  ]);
}

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

export async function exportAgentResult(
  resultId: string,
  format: AgentExportFormat,
  onStage?: (stage: AgentExportStage) => void,
  api: AgentExportApi = agentExportApi,
  wait: (milliseconds: number) => Promise<void> = (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds)),
  now: () => number = Date.now,
): Promise<{ file_id: string; name: string }> {
  let lastStage: AgentExportStage | undefined;
  const report = (stage: AgentExportStage) => {
    if (stage !== lastStage) {
      lastStage = stage;
      onStage?.(stage);
    }
  };
  report('queued');
  let job = await api.startAgentExport(resultId, format);
  report(job.status);
  const startedAt = now();
  while (job.status === 'queued' || job.status === 'running') {
    if (now() - startedAt >= AGENT_EXPORT_TIMEOUT_MS) {
      throw new Error('The export took too long. Please try again.');
    }
    await wait(AGENT_EXPORT_POLL_MS);
    job = await api.getAgentExport(job.job_id);
    report(job.status);
  }
  if (job.status !== 'completed' || !job.file_id) {
    throw new Error(job.error?.message || 'The export could not be generated.');
  }
  const name = job.name || `study-results.${format}`;
  report('opening');
  await shareAgentFile(job.file_id, name, api);
  return { file_id: job.file_id, name };
}
