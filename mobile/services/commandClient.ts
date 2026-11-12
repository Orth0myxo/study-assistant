import { File, Paths } from 'expo-file-system';

import { ApiError, ensureUserToken, getBaseUrl } from '@/services/apiClient';
import type {
  ChatCommand,
  CommandExportFormat,
  CommandMode,
  RangeSelectorType,
} from '@/services/chatOrchestrator';
import { normalizeCommand } from '@/services/commandDispatch';

const ACCESS_TOKEN = process.env.EXPO_PUBLIC_API_TOKEN;
const COMMAND_TIMEOUT_MS = 15_000;
const EXTRACT_TIMEOUT_MS = 90_000;

export interface RangeWarning {
  code: string;
  message: string;
}

export interface RangePassage {
  passage_id: string;
  label: string;
  text: string;
  item_number: number | null;
}

export interface RangeSummary {
  label: string;
  text: string;
  passage_ids: string[];
}

export interface ExtractionResult {
  document_id: string;
  filename: string;
  mode: CommandMode;
  selector: { type: RangeSelectorType; spec: string | null };
  passages: RangePassage[];
  summaries: RangeSummary[];
  warnings: RangeWarning[];
  ai: { used: boolean; cached_chunks: number };
}

export interface CommandContext {
  documentNames: string[];
  pageCount: number | null;
  docFormat: string | null;
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await ensureUserToken();
  return {
    Accept: 'application/json',
    Authorization: `Bearer ${token}`,
    ...(ACCESS_TOKEN ? { 'X-App-Token': ACCESS_TOKEN } : {}),
  };
}

async function postJson<T>(
  path: string,
  body: unknown,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${getBaseUrl()}${path}`, {
      method: 'POST',
      signal: controller.signal,
      headers: { ...(await authHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let code = 'request_failed';
      let message = `Request failed (${response.status}).`;
      try {
        const data = (await response.json()) as {
          error?: { code?: string; message?: string };
        };
        code = data.error?.code ?? code;
        message = data.error?.message ?? message;
      } catch {
        // keep defaults
      }
      throw new ApiError(message, code, response.status);
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    const aborted = (error as { name?: string } | null)?.name === 'AbortError';
    throw new ApiError(
      aborted ? 'The request timed out. Please try again.' : 'Cannot reach the backend.',
      aborted ? 'timeout' : 'network_error',
      0,
    );
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Ask the backend to turn a chat sentence into a command. Resolves to null for
 * any failure or "unavailable" answer so the caller can use the local parser.
 */
export async function requestChatCommand(
  message: string,
  context: CommandContext,
): Promise<ChatCommand | null> {
  try {
    const data = await postJson<Record<string, unknown>>(
      '/api/v1/chat/command',
      {
        message: message.slice(0, 500),
        context: {
          document_names: context.documentNames.slice(0, 5),
          page_count: context.pageCount,
          doc_format: context.docFormat,
        },
      },
      COMMAND_TIMEOUT_MS,
    );
    return normalizeCommand('command' in data ? data.command : data);
  } catch {
    return null;
  }
}

export function requestExtraction(
  documentId: string,
  selectorType: RangeSelectorType,
  spec: string | null,
  mode: CommandMode,
): Promise<ExtractionResult> {
  return postJson<ExtractionResult>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/extract`,
    { selector: { type: selectorType, spec: spec || null }, mode },
    EXTRACT_TIMEOUT_MS,
  );
}

export async function downloadExtractionExport(
  result: Pick<ExtractionResult, 'document_id' | 'selector' | 'mode'>,
  format: CommandExportFormat,
  filename: string,
): Promise<string> {
  const params = new URLSearchParams({
    format,
    type: result.selector.type,
    mode: result.mode,
  });
  if (result.selector.spec) {
    params.set('spec', result.selector.spec);
  }
  const url = `${getBaseUrl()}/api/v1/documents/${encodeURIComponent(
    result.document_id,
  )}/extract/export?${params.toString()}`;
  try {
    const headers = await authHeaders();
    const file = await File.downloadFileAsync(url, new File(Paths.cache, filename), {
      idempotent: true,
      headers,
    });
    return file.uri;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/\b404\b/.test(message)) {
      throw new ApiError('The document was not found or has expired. Upload it again.', 'document_not_found', 404);
    }
    if (/\b401\b/.test(message)) {
      throw new ApiError('Your session could not be verified. Please try again.', 'unauthorized', 401);
    }
    throw new ApiError(
      'The export could not be downloaded. Check your connection and try again.',
      'export_failed',
      0,
    );
  }
}
