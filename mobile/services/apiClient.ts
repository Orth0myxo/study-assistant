import Constants from 'expo-constants';
import { File, Paths } from 'expo-file-system';
import * as SecureStore from 'expo-secure-store';

import {
  DocumentResponse,
  ChatCommand,
  ExportFormat,
  HealthResponse,
  RangeExtractionResponse,
  StudyJobResponse,
  StudyJobSummary,
  StudyMode,
  TopicAiScope,
  TopicReviewResponse,
  TopicSourceResponse,
  TopicSourceSummary,
  TopicStudyMode,
  TopicStudyOutputResponse,
  TopicStudyResultSummary,
} from '@/types/api';

const EMULATOR_BASE_URL = 'http://10.0.2.2:8000';
const BACKEND_PORT = '8000';
const REQUEST_TIMEOUT_MS = 15000;
const IDENTITY_TIMEOUT_MS = 30000;
const UPLOAD_TIMEOUT_MS = 960000;
const STUDY_TIMEOUT_MS = 300000;
const TOPIC_OCR_TIMEOUT_MS = 600000;
const TOPIC_REVIEW_TIMEOUT_MS = 60000;

// Optional shared application gate. Embedded in the app bundle, so it is a
// mild outer gate for a temporary public backend. It is shared by every
// install and is never used to identify a user.
const ACCESS_TOKEN = process.env.EXPO_PUBLIC_API_TOKEN;

// Per-install identity issued by the backend. The opaque token is stored in
// the Android Keystore via expo-secure-store and sent as a bearer credential.
// It is the only thing that determines which documents and jobs are visible.
const USER_TOKEN_KEY = 'study_assistant_user_token';

let userToken: string | null = null;
let identityPromise: Promise<string> | null = null;

async function readStoredUserToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(USER_TOKEN_KEY);
  } catch {
    // Secure storage unavailable (for example on web); stay in memory only.
    return null;
  }
}

async function persistUserToken(token: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(USER_TOKEN_KEY, token);
  } catch {
    // Keep the in-memory token; a restart will create a fresh identity.
  }
}

async function forgetUserToken(): Promise<void> {
  userToken = null;
  try {
    await SecureStore.deleteItemAsync(USER_TOKEN_KEY);
  } catch {
    // Nothing else to do; the next request will register again.
  }
}

async function registerUserToken(): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), IDENTITY_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/v1/users`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(ACCESS_TOKEN ? { 'X-App-Token': ACCESS_TOKEN } : {}),
      },
    });
  } catch (error) {
    if (__DEV__) {
      console.warn(
        '[api] identity registration transport failure:',
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
  if (!response.ok) {
    if (__DEV__) {
      console.warn(`[api] identity registration failed (${response.status})`);
    }
    throw new ApiError(
      'Could not create a device identity for this app.',
      'identity_registration_failed',
      response.status,
    );
  }
  const body = (await response.json()) as { token?: string };
  if (!body?.token) {
    throw new ApiError(
      'The identity service returned an invalid response.',
      'identity_registration_failed',
      0,
    );
  }
  userToken = body.token;
  await persistUserToken(body.token);
  return body.token;
}

export async function ensureUserToken(): Promise<string> {
  if (userToken) {
    return userToken;
  }
  if (!identityPromise) {
    identityPromise = (async () => {
      const stored = await readStoredUserToken();
      if (stored) {
        userToken = stored;
        return stored;
      }
      return registerUserToken();
    })().finally(() => {
      identityPromise = null;
    });
  }
  return identityPromise;
}

let requestCounter = 0;

function nextRequestId(): string {
  requestCounter = (requestCounter + 1) % 1_000_000;
  return `app_${Date.now().toString(36)}_${requestCounter.toString(36)}`;
}

function parseServerTiming(header: string | null): Record<string, number> {
  if (!header) {
    return {};
  }
  const timing: Record<string, number> = {};
  for (const part of header.split(',')) {
    const [rawName, ...params] = part.trim().split(';');
    const name = rawName.trim();
    if (!name) {
      continue;
    }
    for (const param of params) {
      const [rawKey, rawValue] = param.trim().split('=');
      if (rawKey?.trim() !== 'dur' || rawValue === undefined) {
        continue;
      }
      const value = Number(rawValue);
      if (Number.isFinite(value)) {
        timing[name] = value;
      }
    }
  }
  return timing;
}

function timingValue(value: number | undefined): string {
  return value === undefined ? 'n/a' : `${Math.round(value)}ms`;
}

function logUploadTiming(params: {
  requestId: string;
  serverRequestId?: string | null;
  clientTotalMs: number;
  serverTiming?: string | null;
  status?: number;
  outcome: 'success' | 'http_error' | 'transport_error';
  errorCode?: string;
}): void {
  const timing = parseServerTiming(params.serverTiming ?? null);
  // client_total_ms spans upload, server work, and response transfer.
  // server_read_ms is the server's spool-file read, not network transfer.
  console.info(
    '[api] upload timing' +
      ` request_id=${params.requestId}` +
      ` server_request_id=${params.serverRequestId || params.requestId}` +
      ` client_total_ms=${Math.round(params.clientTotalMs)}` +
      ` server_total_ms=${timingValue(timing.total)}` +
      ` server_read_ms=${timingValue(timing.read)}` +
      ` server_parse_ms=${timingValue(timing.parse)}` +
      ` server_store_ms=${timingValue(timing.store)}` +
      ` server_ocr_ms=${timingValue(timing.ocr)}` +
      ` status=${params.status ?? 0}` +
      ` outcome=${params.outcome}` +
      (params.errorCode ? ` error_code=${params.errorCode}` : ''),
  );
}

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

export function absoluteUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  return `${baseUrl}${path.startsWith('/') ? '' : '/'}${path}`;
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
  requestId: string = nextRequestId(),
  allowIdentityRetry: boolean = true,
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  const url = `${baseUrl}${path}`;
  const isPublic = path === '/health';
  const token = isPublic ? null : await ensureUserToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = Date.now();
  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        'X-Request-ID': requestId,
        ...(ACCESS_TOKEN ? { 'X-App-Token': ACCESS_TOKEN } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
    });
    if (__DEV__) {
      const serverTiming = response.headers.get('server-timing');
      console.info(
        `[api] ${method} ${path} ${response.status} ${Date.now() - startedAt}ms` +
          (serverTiming ? ` server=${serverTiming}` : ''),
      );
    }
    const challenge = response.headers.get('www-authenticate') ?? '';
    if (
      token &&
      allowIdentityRetry &&
      response.status === 401 &&
      challenge.toLowerCase().includes('bearer')
    ) {
      // The backend rejected this install's identity (for example after a
      // redeploy with an ephemeral database). Register again and retry once.
      await forgetUserToken();
      return send(path, init, timeoutMs, nextRequestId(), false);
    }
    return response;
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

async function requestJson<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const response = await send(path, init, timeoutMs);
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health');
}

export function requestChatCommand(
  message: string,
  context: {
    document_names: string[];
    page_count: number | null;
    doc_format: 'pdf' | 'docx' | null;
  },
): Promise<ChatCommand> {
  return requestJson<ChatCommand>(
    '/api/v1/chat/command',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, context }),
    },
    35000,
  );
}

export function extractDocumentRange(
  documentId: string,
  selector: string,
): Promise<RangeExtractionResponse> {
  return requestJson<RangeExtractionResponse>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/ranges`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selector }),
    },
    STUDY_TIMEOUT_MS,
  );
}

export function startRangeStudy(
  documentId: string,
  selector: string,
  mode: ChatCommand['mode'],
): Promise<StudyJobResponse> {
  return requestJson<StudyJobResponse>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/range-study`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selector, mode }),
    },
    STUDY_TIMEOUT_MS,
  );
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

  const requestId = nextRequestId();
  const startedAt = Date.now();
  let response: Response;
  try {
    response = await send(
      '/api/v1/documents',
      { method: 'POST', body: form },
      UPLOAD_TIMEOUT_MS,
      requestId,
    );
  } catch (error) {
    logUploadTiming({
      requestId,
      clientTotalMs: Date.now() - startedAt,
      outcome: 'transport_error',
      errorCode: error instanceof ApiError ? error.code : undefined,
    });
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
    logUploadTiming({
      requestId,
      serverRequestId: response.headers.get('x-request-id'),
      clientTotalMs: Date.now() - startedAt,
      serverTiming: response.headers.get('server-timing'),
      status: response.status,
      outcome: 'http_error',
    });
    throw await toApiError(response);
  }

  const document = (await response.json()) as DocumentResponse;
  logUploadTiming({
    requestId,
    serverRequestId: response.headers.get('x-request-id'),
    clientTotalMs: Date.now() - startedAt,
    serverTiming: response.headers.get('server-timing'),
    status: response.status,
    outcome: 'success',
  });
  return document;
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await send(`/api/v1/documents/${documentId}`, {
    method: 'DELETE',
  });
  if (!response.ok && response.status !== 204) {
    throw await toApiError(response);
  }
}

function imageMimeType(file: UploadFile): string {
  const explicit = file.mimeType?.toLowerCase();
  if (explicit === 'image/png' || explicit === 'image/jpeg') {
    return explicit;
  }
  return /\.png$/i.test(file.name) ? 'image/png' : 'image/jpeg';
}

const inFlightTopicSources = new Map<string, Promise<TopicSourceResponse>>();

export function createTopicSource(
  documentId: string,
  images: UploadFile[] = [],
  jottingsText = '',
): Promise<TopicSourceResponse> {
  const typedJottings = jottingsText.trim();
  if (images.length === 0 && !typedJottings) {
    return Promise.reject(
      new ApiError(
        'Add typed jottings or at least one photo of your lecture notes.',
        'jottings_required',
        0,
      ),
    );
  }

  const key = `${documentId}:${typedJottings}:${images
    .map((image) => `${image.uri}:${image.size ?? ''}`)
    .join('|')}`;
  const existing = inFlightTopicSources.get(key);
  if (existing) {
    return existing;
  }

  const promise = (async () => {
    const form = new FormData();
    form.append('document_id', documentId);
    if (typedJottings) {
      form.append('jottings_text', typedJottings);
    }
    for (const image of images) {
      form.append('images', {
        uri: image.uri,
        name: image.name,
        type: imageMimeType(image),
      } as unknown as Blob);
    }
    return requestJson<TopicSourceResponse>(
      '/api/v1/topic-sources',
      { method: 'POST', body: form },
      TOPIC_OCR_TIMEOUT_MS,
    );
  })();

  inFlightTopicSources.set(key, promise);
  const clear = () => {
    if (inFlightTopicSources.get(key) === promise) {
      inFlightTopicSources.delete(key);
    }
  };
  void promise.then(clear, clear);
  return promise;
}

export function getTopicSource(
  sourceId: string,
): Promise<TopicSourceResponse> {
  return request<TopicSourceResponse>(`/api/v1/topic-sources/${sourceId}`);
}

export interface TopicTranscriptUpdate {
  index: number;
  text: string;
}

export interface TopicSourceUpdate {
  topics?: string[];
  transcripts?: TopicTranscriptUpdate[];
  jottings_text?: string;
}

export function updateTopicSource(
  sourceId: string,
  update: TopicSourceUpdate,
): Promise<TopicSourceResponse> {
  return requestJson<TopicSourceResponse>(
    `/api/v1/topic-sources/${sourceId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    },
  );
}

const inFlightTopicReviews = new Map<string, Promise<TopicReviewResponse>>();

export function reviewTopicSource(
  sourceId: string,
  topics?: string[] | null,
): Promise<TopicReviewResponse> {
  const key = `${sourceId}:${
    topics == null ? 'auto' : JSON.stringify(topics)
  }`;
  const existing = inFlightTopicReviews.get(key);
  if (existing) {
    return existing;
  }

  const promise = requestJson<TopicReviewResponse>(
    `/api/v1/topic-sources/${sourceId}/topic-review`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(topics == null ? {} : { topics }),
    },
    TOPIC_REVIEW_TIMEOUT_MS,
  );

  inFlightTopicReviews.set(key, promise);
  const clear = () => {
    if (inFlightTopicReviews.get(key) === promise) {
      inFlightTopicReviews.delete(key);
    }
  };
  void promise.then(clear, clear);
  return promise;
}

const inFlightTopicOutputs = new Map<
  string,
  Promise<TopicStudyOutputResponse>
>();

export function createTopicStudyOutput(
  sourceId: string,
  mode: TopicStudyMode,
  topics?: string[] | null,
  aiScope: TopicAiScope = 'source_only',
): Promise<TopicStudyOutputResponse> {
  const key = `${sourceId}:${mode}:${aiScope}:${
    topics == null ? 'auto' : JSON.stringify(topics)
  }`;
  const existing = inFlightTopicOutputs.get(key);
  if (existing) {
    return existing;
  }

  const body: {
    mode: TopicStudyMode;
    ai_scope: TopicAiScope;
    topics?: string[];
  } = { mode, ai_scope: aiScope };
  if (topics != null) {
    body.topics = topics;
  }

  const promise = requestJson<TopicStudyOutputResponse>(
    `/api/v1/topic-sources/${sourceId}/study-output`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    STUDY_TIMEOUT_MS,
  );

  inFlightTopicOutputs.set(key, promise);
  const clear = () => {
    if (inFlightTopicOutputs.get(key) === promise) {
      inFlightTopicOutputs.delete(key);
    }
  };
  void promise.then(clear, clear);
  return promise;
}

export async function deleteTopicSource(sourceId: string): Promise<void> {
  const response = await send(`/api/v1/topic-sources/${sourceId}`, {
    method: 'DELETE',
  });
  if (!response.ok && response.status !== 204) {
    throw await toApiError(response);
  }
}

export function listTopicSources(limit = 20): Promise<TopicSourceSummary[]> {
  return request<TopicSourceSummary[]>(`/api/v1/topic-sources?limit=${limit}`);
}

export function getTopicResult(
  resultId: string,
): Promise<TopicStudyOutputResponse> {
  return request<TopicStudyOutputResponse>(
    `/api/v1/topic-results/${resultId}`,
  );
}

export function listTopicResults(
  limit = 20,
): Promise<TopicStudyResultSummary[]> {
  return request<TopicStudyResultSummary[]>(
    `/api/v1/topic-results?limit=${limit}`,
  );
}

export async function deleteTopicResult(resultId: string): Promise<void> {
  const response = await send(`/api/v1/topic-results/${resultId}`, {
    method: 'DELETE',
  });
  if (!response.ok && response.status !== 204) {
    throw await toApiError(response);
  }
}

export function getTopicResultExportUrl(
  resultId: string,
  format: ExportFormat,
): string {
  return `${baseUrl}/api/v1/topic-results/${resultId}/export?format=${format}`;
}

export async function downloadTopicResultExport(
  resultId: string,
  format: ExportFormat,
  filename: string,
): Promise<string> {
  const destination = new File(Paths.cache, filename);
  try {
    const token = await ensureUserToken();
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      ...(ACCESS_TOKEN ? { 'X-App-Token': ACCESS_TOKEN } : {}),
    };
    const file = await File.downloadFileAsync(
      getTopicResultExportUrl(resultId, format),
      destination,
      { idempotent: true, headers },
    );
    return file.uri;
  } catch (error) {
    const status = downloadErrorStatus(error);
    if (status === 404) {
      throw new ApiError(
        'The study result was not found or has expired.',
        'topic_result_not_found',
        404,
      );
    }
    if (status === 401) {
      throw new ApiError(
        'Your session could not be verified. Please try again.',
        'unauthorized',
        401,
      );
    }
    if (status !== null && status >= 500) {
      throw new ApiError(
        'The export could not be generated.',
        'export_failed',
        status,
      );
    }
    throw new ApiError(
      'The export could not be downloaded. Check your connection and try again.',
      'export_transport_error',
      0,
    );
  }
}

const inFlightStudyJobs = new Map<string, Promise<StudyJobResponse>>();

export function startStudyJob(
  documentId: string,
  mode: StudyMode,
): Promise<StudyJobResponse> {
  const key = `${documentId}:${mode}`;
  const existing = inFlightStudyJobs.get(key);
  if (existing) {
    return existing;
  }

  const promise = (async () => {
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
  })();

  inFlightStudyJobs.set(key, promise);
  const clear = () => {
    if (inFlightStudyJobs.get(key) === promise) {
      inFlightStudyJobs.delete(key);
    }
  };
  void promise.then(clear, clear);
  return promise;
}

export async function getStudyJob(jobId: string): Promise<StudyJobResponse> {
  return request<StudyJobResponse>(`/api/v1/study-jobs/${jobId}`);
}

export function listStudyJobs(limit = 20): Promise<StudyJobSummary[]> {
  return request<StudyJobSummary[]>(`/api/v1/study-jobs?limit=${limit}`);
}

export function getStudyExportUrl(jobId: string, format: ExportFormat): string {
  // Credentials travel in headers (see downloadStudyExport), never in the URL.
  return `${baseUrl}/api/v1/study-jobs/${jobId}/export?format=${format}`;
}

function downloadErrorStatus(error: unknown): number | null {
  const message = error instanceof Error ? error.message : String(error);
  const match =
    message.match(/\b(?:HTTP|status|code)\s*[:=]?\s*(4\d{2}|5\d{2})\b/i) ??
    message.match(/(?:^|:\s*)(4\d{2}|5\d{2})\s*$/);
  return match ? Number(match[1]) : null;
}

function exportStatusError(status: number): ApiError | null {
  if (status === 401) {
    return new ApiError(
      'Your session could not be verified. Please try again.',
      'unauthorized',
      401,
    );
  }
  if (status === 404) {
    return new ApiError(
      'The study job was not found or has expired.',
      'job_not_found',
      404,
    );
  }
  if (status === 409) {
    return new ApiError(
      'The study job is not ready for export yet.',
      'job_not_ready',
      409,
    );
  }
  if (status >= 500) {
    return new ApiError(
      'The export could not be generated.',
      'export_failed',
      status,
    );
  }
  return null;
}

export async function downloadStudyExport(
  jobId: string,
  format: ExportFormat,
  filename: string,
): Promise<string> {
  const destination = new File(Paths.cache, filename);
  const startedAt = Date.now();
  try {
    const token = await ensureUserToken();
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      ...(ACCESS_TOKEN ? { 'X-App-Token': ACCESS_TOKEN } : {}),
    };
    const file = await File.downloadFileAsync(
      getStudyExportUrl(jobId, format),
      destination,
      { idempotent: true, headers },
    );
    if (__DEV__) {
      console.info(
        `[api] export download ${format} ${Date.now() - startedAt}ms`,
      );
    }
    return file.uri;
  } catch (error) {
    const status = downloadErrorStatus(error);
    if (__DEV__) {
      // Never log the raw error: native download errors can include the URL
      // and internal paths.
      console.warn('[api] export download failed', { status });
    }

    if (status !== null) {
      const mapped = exportStatusError(status);
      if (mapped) {
        throw mapped;
      }
    }

    // Do not re-request the export here. A second GET would make the server
    // render the entire export again. A cheap job lookup is enough to tell
    // expired/not-ready jobs apart from a genuine transport failure.
    try {
      const job = await getStudyJob(jobId);
      if (job.status === 'failed') {
        throw new ApiError(
          job.error?.message ?? 'The study job failed, so it cannot be exported.',
          job.error?.code ?? 'job_failed',
          409,
        );
      }
      if (job.status !== 'completed' && job.status !== 'partial') {
        throw new ApiError(
          'The study job is still processing. Please try again shortly.',
          'job_not_ready',
          409,
        );
      }
    } catch (verifyError) {
      if (
        verifyError instanceof ApiError &&
        verifyError.code !== 'timeout' &&
        verifyError.code !== 'network_error'
      ) {
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
