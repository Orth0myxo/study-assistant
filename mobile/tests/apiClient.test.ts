import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('expo-constants', () => ({
  default: {
    expoConfig: { hostUri: null },
    expoGoConfig: null,
  },
}));

vi.mock('expo-file-system', () => ({
  File: class {
    uri: string;

    constructor(_directory: unknown, name: string) {
      this.uri = `file://cache/${name}`;
    }
  },
  Paths: { cache: 'file://cache' },
}));

vi.mock('expo-secure-store', () => ({
  getItemAsync: vi.fn(async () => 'stored-token'),
  setItemAsync: vi.fn(async () => undefined),
  deleteItemAsync: vi.fn(async () => undefined),
}));

import {
  ApiError,
  createTopicSource,
  createTopicStudyOutput,
  requestChatCommand,
  startRangeStudy,
  getTopicSource,
  reviewTopicSource,
  setBackendWakeListener,
  setBaseUrl,
  updateTopicSource,
} from '@/services/apiClient';

const BASE = 'http://10.0.2.2:8000';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestAt(index = 0): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[index] as [string, RequestInit];
  return { url: call[0], init: call[1] };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  setBaseUrl(BASE);
  setBackendWakeListener(null);
});

afterEach(() => {
  setBackendWakeListener(null);
  setBaseUrl(BASE);
  vi.unstubAllGlobals();
});

describe('topic source API', () => {
  it('retries Render health checks with wake feedback before the first request', async () => {
    const waking = vi.fn();
    setBackendWakeListener(waking);
    setBaseUrl('https://study-assistant.onrender.com');
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ status: 'starting' }, 503))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
      .mockResolvedValueOnce(jsonResponse({ action: 'help' }));

    await requestChatCommand('help', {
      document_names: [],
      page_count: null,
      doc_format: null,
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][0]).toBe('https://study-assistant.onrender.com/health');
    expect(waking).toHaveBeenCalled();
  });

  it('maps an expired uploaded file to an actionable restart message', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: 'document_not_found', message: 'The document was not found.' } },
        404,
      ),
    );
    await expect(
      requestChatCommand('help', {
        document_names: [],
        page_count: null,
        doc_format: null,
      }),
    ).rejects.toMatchObject({
      message: 'Your uploaded file expired after the server restarted. Please upload it again.',
      code: 'document_not_found',
    });
  });

  it('sends a chat command with only the current message and metadata context', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ action: 'help' }));
    await requestChatCommand('pages 2-3', {
      document_names: ['lecture.pdf'],
      page_count: 10,
      doc_format: 'pdf',
    });
    const { url, init } = requestAt();
    expect(url).toBe(`${BASE}/api/v1/chat/command`);
    expect(JSON.parse(init.body as string)).toEqual({
      message: 'pages 2-3',
      context: {
        document_names: ['lecture.pdf'],
        page_count: 10,
        doc_format: 'pdf',
      },
    });
  });

  it('dispatches selected page ranges through the existing study job contract', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ job_id: 'range-job' }));
    await startRangeStudy('doc-1', 'pages 2-3', 'both');
    const { url, init } = requestAt();
    expect(url).toBe(`${BASE}/api/v1/documents/doc-1/range-study`);
    expect(JSON.parse(init.body as string)).toEqual({
      selector: 'pages 2-3',
      mode: 'both',
    });
  });

  it('sends auto topic review with the stored bearer identity', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        source_id: 'source-1',
        topics: [],
        warnings: [],
      }),
    );

    await reviewTopicSource('source-1');

    const { url, init } = requestAt();
    expect(url).toBe(
      `${BASE}/api/v1/topic-sources/source-1/topic-review`,
    );
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({});
    expect((init.headers as Record<string, string>).Authorization).toBe(
      'Bearer stored-token',
    );
  });

  it('sends reviewed topics without persisting them implicitly', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ topics: [] }));

    await reviewTopicSource('source-1', ['Calvin cycle', 'Photosynthesis']);

    const { init } = requestAt();
    expect(JSON.parse(init.body as string)).toEqual({
      topics: ['Calvin cycle', 'Photosynthesis'],
    });
  });

  it('uploads multiple photos as repeated multipart fields', async () => {
    class FakeFormData {
      fields: [string, unknown][] = [];

      append(name: string, value: unknown) {
        this.fields.push([name, value]);
      }
    }
    vi.stubGlobal('FormData', FakeFormData);
    fetchMock.mockResolvedValueOnce(jsonResponse({ source_id: 'source-2' }));

    await createTopicSource('document-1', [
      {
        uri: 'file://notes-1.png',
        name: 'notes-1.png',
        mimeType: null,
        size: 10,
      },
      {
        uri: 'file://notes-2.jpg',
        name: 'notes-2.jpg',
        mimeType: 'image/jpeg',
        size: 20,
      },
    ]);

    const { url, init } = requestAt();
    expect(url).toBe(`${BASE}/api/v1/topic-sources`);
    const form = init.body as unknown as FakeFormData;
    expect(form.fields[0]).toEqual(['document_id', 'document-1']);
    expect(form.fields[1][1]).toMatchObject({
      uri: 'file://notes-1.png',
      name: 'notes-1.png',
      type: 'image/png',
    });
    expect(form.fields[2][1]).toMatchObject({
      uri: 'file://notes-2.jpg',
      name: 'notes-2.jpg',
      type: 'image/jpeg',
    });
  });

  it('uploads text-only jottings without photos', async () => {
    class FakeFormData {
      fields: [string, unknown][] = [];

      append(name: string, value: unknown) {
        this.fields.push([name, value]);
      }
    }
    vi.stubGlobal('FormData', FakeFormData);
    fetchMock.mockResolvedValueOnce(jsonResponse({ source_id: 'source-text' }));

    await createTopicSource('document-1', [], '  Calvin cycle  ');

    const { url, init } = requestAt();
    expect(url).toBe(`${BASE}/api/v1/topic-sources`);
    const form = init.body as unknown as FakeFormData;
    expect(form.fields).toEqual([
      ['document_id', 'document-1'],
      ['jottings_text', 'Calvin cycle'],
    ]);
  });

  it('rejects a submission with no text and no photos', async () => {
    await expect(createTopicSource('document-1')).rejects.toMatchObject({
      code: 'jottings_required',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('patches corrected transcripts and topics', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ source_id: 'source-1' }));

    await updateTopicSource('source-1', {
      topics: ['Calvin cycle'],
      transcripts: [{ index: 0, text: 'Corrected text' }],
    });

    const { url, init } = requestAt();
    expect(url).toBe(`${BASE}/api/v1/topic-sources/source-1`);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({
      topics: ['Calvin cycle'],
      transcripts: [{ index: 0, text: 'Corrected text' }],
    });
  });

  it('maps an expired source to the documented error', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: 'topic_source_not_found',
            message: 'The topic source was not found or has expired.',
          },
        },
        404,
      ),
    );

    await expect(getTopicSource('missing')).rejects.toMatchObject({
      code: 'topic_source_not_found',
      status: 404,
    });
  });

  it('maps AI-not-configured study output failures', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: 'ai_not_configured',
            message: 'AI is not configured.',
          },
        },
        503,
      ),
    );

    await expect(
      createTopicStudyOutput('source-1', 'both', ['Calvin cycle']),
    ).rejects.toMatchObject({
      code: 'ai_not_configured',
      status: 503,
    });
  });

  it('deduplicates concurrent study-output requests', async () => {
    let resolveFetch: ((response: Response) => void) | undefined;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    const first = createTopicStudyOutput('source-dedupe', 'both');
    const second = createTopicStudyOutput('source-dedupe', 'both');

    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    resolveFetch?.(jsonResponse({ status: 'completed' }));
    await Promise.all([first, second]);

    const { url, init } = requestAt();
    expect(url).toBe(
      `${BASE}/api/v1/topic-sources/source-dedupe/study-output`,
    );
    expect(JSON.parse(init.body as string)).toEqual({
      mode: 'both',
      ai_scope: 'source_only',
    });
  });

  it('surfaces a typed ApiError for non-JSON failures', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('gateway down', { status: 502 }),
    );

    await expect(
      createTopicStudyOutput('source-1', 'source_passages'),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
