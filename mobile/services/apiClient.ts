import Constants from 'expo-constants';
import { File, Paths } from 'expo-file-system';

import {
  DocumentResponse,
  ExportFormat,
  HealthResponse,
  StudyJobResponse,
  StudyJobSummary,
  StudyMode,
} from '@/types/api';

const EMULATOR_BASE_URL = 'http://10.0.2.2:8000';
const BACKEND_PORT = '8000';
const REQUEST_TIMEOUT_MS = 15000;
const UPLOAD_TIMEOUT_MS = 960000;
const STUDY_TIMEOUT_MS = 300000;

// Optional shared secret for a temporary public backend. Embedded in the app
// bundle, so it is a mild gate for personal testing, not a strong credential.
const ACCESS_TOKEN = process.env.EXPO_PUBLIC_API_TOKEN;

function resolveDefaultBaseUrl(): string {
  const override = process.env.EXPO_PUBLIC_API_URL;
  if (override) {
    return override;
  }
  const hostUri =
    Constants.expoConfig?.hostUri ??
    Constants.expoGoConfig?.debuggerHost ??
    null;
  const host = hostUri?.split(':')[0];
  if (host) {
    return `http://${host}:${BACKEND_PORT}`;
  }
  return EMULATOR_BASE_URL;
}

let baseUrl = resolveDefaultBaseUrl();

export function getBaseUrl(): string {
  return baseUrl;
}

export function setBaseUrl(url: string): void {
  baseUrl = url.replace(/\/+$/, '');
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
  };
}

export interface UploadFile {
  uri: string;
  name: string;
  mimeType?: string | null;
  size?: number;
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    (error as { name?: string }).name === 'AbortError'
  );
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = 'request_failed';
  let message = `Request failed (${response.status}).`;
  try {
    const body = (await response.json()) as ErrorEnvelope;
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
    }
  } catch {
    // non-JSON error body; keep defaults
  }
  return new ApiError(message, code, response.status);
}

async function send(
  path: string,
  init: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  const url = `${baseUrl}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(ACCESS_TOKEN ? { 'X-App-Token': ACCESS_TOKEN } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    if (__DEV__) {
      console.warn(
        `[api] ${method} ${url} rejected:`,
        error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      );
    }
    if (isAbortError(error)) {
      throw new ApiError('The request timed out. Please try again.', 'timeout', 0);
    }
    throw new ApiError('Cannot reach the backend.', 'network_error', 0);
  } finally {
    clearTimeout(timer);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, init ?? {});
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health');
}

export async function uploadDocument(
  file: UploadFile,
): Promise<DocumentResponse> {
  const form = new FormData();
  form.append('file', {
    uri: file.uri,
    name: file.name,
    type: file.mimeType ?? 'application/octet-stream',
  } as unknown as Blob);

  let response: Response;
  try {
    response = await send(
      '/api/v1/documents',
      { method: 'POST', body: form },
      UPLOAD_TIMEOUT_MS,
    );
  } catch (error) {
    if (error instanceof ApiError && error.code === 'network_error') {
      throw new ApiError(
        'The upload could not be sent. Check your connection and try again.',
        'upload_transport_error',
        0,
      );
    }
    throw error;
  }
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as DocumentResponse;
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await send(`/api/v1/documents/${documentId}`, {
    method: 'DELETE',
  });
  if (!response.ok && response.status !== 204) {
    throw await toApiError(response);
  }
}

export async function startStudyJob(
  documentId: string,
  mode: StudyMode,
): Promise<StudyJobResponse> {
  const response = await send(
    '/api/v1/study-jobs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_id: documentId, mode }),
    },
    STUDY_TIMEOUT_MS,
  );
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as StudyJobResponse;
}

export async function getStudyJob(jobId: string): Promise<StudyJobResponse> {
  return request<StudyJobResponse>(`/api/v1/study-jobs/${jobId}`);
}

export function listStudyJobs(limit = 20): Promise<StudyJobSummary[]> {
  return request<StudyJobSummary[]>(`/api/v1/study-jobs?limit=${limit}`);
}

export function getStudyExportUrl(jobId: string, format: ExportFormat): string {
  // Native file downloads cannot set headers, so the token rides as a query
  // parameter here (the backend accepts it either way).
  const tokenQuery = ACCESS_TOKEN
    ? `&token=${encodeURIComponent(ACCESS_TOKEN)}`
    : '';
  return `${baseUrl}/api/v1/study-jobs/${jobId}/export?format=${format}${tokenQuery}`;
}

export async function downloadStudyExport(
  jobId: string,
  format: ExportFormat,
  filename: string,
): Promise<string> {
  const path = `/api/v1/study-jobs/${jobId}/export?format=${format}`;
  const destination = new File(Paths.cache, filename);
  try {
    const file = await File.downloadFileAsync(
      getStudyExportUrl(jobId, format),
      destination,
      { idempotent: true },
    );
    return file.uri;
  } catch (error) {
    if (__DEV__) {
      console.warn('[api] export download failed:', String(error));
    }
    // The native download rejects with only the HTTP status in its message, so
    // re-request once to recover the server's structured error code/message.
    // If the server is healthy, treat the failure as a transport problem.
    try {
      const check = await send(path, { method: 'GET' });
      if (!check.ok) {
        throw await toApiError(check);
      }
    } catch (verifyError) {
      if (verifyError instanceof ApiError) {
        throw verifyError;
      }
    }
    throw new ApiError(
      'The export could not be downloaded. Check your connection and try again.',
      'export_transport_error',
      0,
    );
  }
}
