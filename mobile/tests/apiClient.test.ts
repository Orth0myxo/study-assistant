import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('expo-constants', () => ({
  default: {
    expoConfig: { hostUri: null },
    expoGoConfig: null,
  },
}));

const fileMocks = vi.hoisted(() => ({
  downloadFileAsync: vi.fn(),
}));

vi.mock('expo-file-system', () => ({
  File: class {
    uri: string;

    static downloadFileAsync = fileMocks.downloadFileAsync;

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
  requestAgentChat,
  getAgentExport,
  getAgentResult,
  getUserPreferences,
  startAgentExport,
  downloadAgentFile,
  downloadDocumentPageImage,
  deleteTableResult,
  downloadConversionFile,
  exportConversionTable,
  getConversionFormats,
  getConversionJob,
  startConversion,
  downloadTableFile,
  getTableExport,
  readTableImage,
  readTableImages,
  readTableText,
  startTableExport,
  normalizeFilenames,
  createTopicSource,
  createTopicStudyOutput,
  downloadStudyExport,
  downloadTopicResultExport,
  getStudyExportUrl,
  getTopicResultExportUrl,
  requestChatCommand,
  startRangeStudy,
  getTopicSource,
  interpretTopicOutline,
  prepareDocumentOcr,
  prepareOcrBatch,
  previewAgentResult,
  reviewTopicSource,
  setBackendWakeListener,
  setBaseUrl,
  updateTopicSource,
  updateAiReadingPreference,
} from '@/services/apiClient';
import { downloadExtractionExport } from '@/services/commandClient';

const BASE = 'http://10.0.2.2:8000';

describe('export alignment', () => {
  it('adds alignment query parameters to study and topic export URLs', () => {
    expect(
      getStudyExportUrl('job-1', 'docx', {
        textAlign: 'right',
        headingAlign: 'center',
      }),
    ).toBe(
      `${BASE}/api/v1/study-jobs/job-1/export?format=docx&text_align=right&heading_align=center`,
    );
    expect(
      getTopicResultExportUrl('result-1', 'pdf', {
        textAlign: 'justify',
      }),
    ).toBe(
      `${BASE}/api/v1/topic-results/result-1/export?format=pdf&text_align=justify`,
    );
  });

  it('adds alignment query parameters to range extraction downloads', async () => {
    fileMocks.downloadFileAsync.mockResolvedValueOnce({
      uri: 'file://cache/range.docx',
    });

    await downloadExtractionExport(
      {
        document_id: 'doc-1',
        selector: { type: 'pages', spec: '1' },
        mode: 'source_passages',
      },
      'docx',
      'range.docx',
      { textAlign: 'center', headingAlign: 'center' },
    );

    expect(String(fileMocks.downloadFileAsync.mock.calls[0][0])).toBe(
      `${BASE}/api/v1/documents/doc-1/extract/export?format=docx&type=pages&mode=source_passages&spec=1&text_align=center&heading_align=center`,
    );
  });
});

describe('API filename normalization', () => {
  it('decodes encoded names once and preserves malformed escape sequences', () => {
    expect(normalizeFilenames({
      filename: 'Lecture%20notes.pdf',
      nested: [{ filename: 'bad%2name.pdf' }],
    })).toEqual({
      filename: 'Lecture notes.pdf',
      nested: [{ filename: 'bad%2name.pdf' }],
    });
  });
});

describe('agent API', () => {
  it('sends one agent turn with the server conversation id and owned attachments', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      reply: 'Ready',
      conversation_id: 'conversation-1',
      blocks: [{ type: 'summary', text: 'A short summary', label: 'AI summary' }],
      warnings: [],
      usage: { model_calls: 1, tool_calls: 1 },
    }));

    await requestAgentChat(
      'request A',
      'conversation-1',
      [{ id: 'doc-1', kind: 'pdf', name: 'lecture.pdf' }],
      { id: 'doc-1', kind: 'pdf' },
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = requestAt();
    expect(url).toBe(`${BASE}/api/v1/agent/chat`);
    expect(JSON.parse(init.body as string)).toEqual({
      message: 'request A',
      conversation_id: 'conversation-1',
      attachments: [{ id: 'doc-1', kind: 'pdf', name: 'lecture.pdf' }],
      active_source: { id: 'doc-1', kind: 'pdf' },
    });
  });

  it('sends the user text without adding or trimming wording', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      reply: 'Ready',
      conversation_id: 'conversation-2',
      blocks: [],
      warnings: [],
      usage: { model_calls: 1, tool_calls: 0 },
    }));
    const text = '  Compare pages 2 and 3, then explain the last line.\n';

    await requestAgentChat(text, null, []);

    expect(JSON.parse(requestAt().init.body as string)).toEqual({
      message: text,
      attachments: [],
    });
  });

  it('starts and polls a direct result export by id', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        job_id: 'job-1', result_id: 'result-1', format: 'pdf',
        status: 'queued', file_id: null, name: null, error: null,
      }, 202))
      .mockResolvedValueOnce(jsonResponse({
        job_id: 'job-1', result_id: 'result-1', format: 'pdf',
        status: 'completed', file_id: 'file-1', name: 'study.pdf', error: null,
      }));

    await startAgentExport('result-1', 'pdf', true);
    expect(requestAt(0).url).toBe(`${BASE}/api/v1/agent/results/result-1/exports`);
    expect(requestAt(0).init.method).toBe('POST');
    expect(JSON.parse(requestAt(0).init.body as string)).toEqual({
      format: 'pdf', allow_incomplete_ocr: true,
    });

    await expect(getAgentExport('job-1')).resolves.toMatchObject({
      status: 'completed', file_id: 'file-1',
    });
    expect(requestAt(1).url).toBe(`${BASE}/api/v1/agent/exports/job-1`);
  });

  it('sends local result edits to export and preview endpoints', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        job_id: 'job-2', result_id: 'result-1', format: 'docx',
        status: 'queued', file_id: null, name: null, error: null,
      }, 202))
      .mockResolvedValueOnce(jsonResponse({
        result_id: 'result-1', blocks: [], total_blocks: 0, has_more: false,
      }));

    const edits = {
      summary: 'Edited summary.',
      removed_passage_ids: ['doc-1:1'],
      ordered_passage_ids: ['doc-1:0', 'doc-1:1'],
      included_pages: [1],
    };
    await startAgentExport('result-1', 'docx', false, edits);
    expect(JSON.parse(requestAt(0).init.body as string)).toEqual({
      format: 'docx', edits,
    });

    await previewAgentResult('result-1', edits, 8);
    expect(requestAt(1).url).toBe(
      `${BASE}/api/v1/agent/results/result-1/preview`,
    );
    expect(JSON.parse(requestAt(1).init.body as string)).toEqual({
      edits, limit: 8,
    });
  });

  it('loads passage data and downloads owner-authenticated files and page images', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      result_id: 'result-1', document_id: 'doc-1', topics: [],
    }));
    await getAgentResult('result-1');
    expect(requestAt().url).toBe(`${BASE}/api/v1/agent/results/result-1`);

    fileMocks.downloadFileAsync
      .mockResolvedValueOnce({ uri: 'file://cache/study.docx' })
      .mockResolvedValueOnce({ uri: 'file://cache/page.png' });
    await expect(downloadAgentFile('file-1', 'study.docx')).resolves.toBe('file://cache/study.docx');
    await expect(downloadDocumentPageImage('doc-1', 3)).resolves.toBe('file://cache/page.png');
    expect(String(fileMocks.downloadFileAsync.mock.calls[0][0])).toBe(
      `${BASE}/api/v1/agent/files/file-1`,
    );
    expect(String(fileMocks.downloadFileAsync.mock.calls[1][0])).toBe(
      `${BASE}/api/v1/documents/doc-1/pages/3/image`,
    );
  });

  it('wakes Render and retries an owner-authenticated download after a stale identity', async () => {
    setBaseUrl('https://study-assistant.onrender.com');
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'ok' }));
    fileMocks.downloadFileAsync
      .mockRejectedValueOnce(new Error('HTTP 401'))
      .mockResolvedValueOnce({ uri: 'file://cache/study.docx' });

    await expect(downloadAgentFile('file-1', 'study.docx')).resolves.toBe(
      'file://cache/study.docx',
    );

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      'https://study-assistant.onrender.com/health',
    );
    expect(fileMocks.downloadFileAsync).toHaveBeenCalledTimes(2);
  });

  it('rejects agent messages and attachment lists beyond the server limits', async () => {
    await expect(requestAgentChat('x'.repeat(5001), null, [])).rejects.toMatchObject({
      code: 'agent_message_too_long',
    });
    await expect(requestAgentChat('request A', null, Array.from({ length: 21 }, (_, index) => ({
      id: `doc-${index}`, kind: 'pdf' as const, name: `doc-${index}.pdf`,
    })))).rejects.toMatchObject({
      code: 'too_many_agent_attachments',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('table converter API', () => {
  it('loads and updates the server-owned AI page-reading preference', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ ai_reading_scanned_pages: 'ask' }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ai_reading_scanned_pages: 'always' }),
      );

    await expect(getUserPreferences()).resolves.toEqual({
      ai_reading_scanned_pages: 'ask',
    });
    expect(requestAt(0).url).toBe(`${BASE}/api/v1/users/preferences`);
    expect(requestAt(0).init.method).toBe('GET');

    await expect(updateAiReadingPreference('always')).resolves.toEqual({
      ai_reading_scanned_pages: 'always',
    });
    expect(requestAt(1).init.method).toBe('PUT');
    expect(JSON.parse(requestAt(1).init.body as string)).toEqual({
      ai_reading_scanned_pages: 'always',
    });
  });

  it('reads photos and typed text and exports the edited grid', async () => {
    const columns = ['k1', 'k2', 'k3'];
    const rows = [['v1', 'v2', 'v3']];
    const result = {
      result_id: 'table-1',
      source_id: 'source-1',
      image_index: 0,
      engine: 'local',
      title: 'heading',
      has_header: true,
      columns,
      rows,
      confidence: [[90, 90, 90]],
      low_confidence: [[false, false, false]],
      row_count: rows.length,
      column_count: columns.length,
      warnings: [],
      created_at: 1,
      expires_at: 2,
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(result, 201))
      .mockResolvedValueOnce(jsonResponse(result, 201))
      .mockResolvedValueOnce(jsonResponse({ ...result, engine: 'text', source_id: '' }, 201))
      .mockResolvedValueOnce(jsonResponse({
        job_id: 'job-1', result_id: 'table-1', format: 'xlsx',
        status: 'queued', file_id: null, name: null, error: null,
      }, 202))
      .mockResolvedValueOnce(jsonResponse({
        job_id: 'job-1', result_id: 'table-1', format: 'xlsx',
        status: 'completed', file_id: 'file-1', name: 'table.xlsx', error: null,
      }));

    await readTableImage('source-1', 0);
    expect(requestAt(0).url).toBe(`${BASE}/api/v1/tables`);
    expect(JSON.parse(requestAt(0).init.body as string)).toEqual({
      source_id: 'source-1', image_index: 0, engine: 'local',
    });

    await readTableImages('source-1', [0, 1]);
    expect(JSON.parse(requestAt(1).init.body as string)).toEqual({
      source_id: 'source-1', image_indices: [0, 1], engine: 'local',
    });

    await readTableText('k1\tk2\nv1\tv2');
    expect(requestAt(2).url).toBe(`${BASE}/api/v1/tables/text`);
    expect(JSON.parse(requestAt(2).init.body as string)).toEqual({
      text: 'k1\tk2\nv1\tv2',
    });

    await startTableExport('table-1', 'xlsx', columns, rows, 'heading');
    expect(requestAt(3).url).toBe(
      `${BASE}/api/v1/tables/results/table-1/exports`,
    );
    expect(JSON.parse(requestAt(3).init.body as string)).toEqual({
      format: 'xlsx',
      title: 'heading',
      columns,
      rows,
    });

    await expect(getTableExport('job-1')).resolves.toMatchObject({
      status: 'completed', file_id: 'file-1',
    });
    expect(requestAt(4).url).toBe(`${BASE}/api/v1/tables/exports/job-1`);
  });

  it('sends explicit page-image consent only after the user chooses', async () => {
    const result = {
      result_id: 'table-ai', source_id: 'source-1', image_index: 0,
      engine: 'ai', title: '', has_header: true, columns: ['Value'],
      rows: [['7']], confidence: [[1]], low_confidence: [[false]],
      row_count: 1, column_count: 1, warnings: [], created_at: 1, expires_at: 2,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(result, 201));

    await readTableImages('source-1', [0], 'ai', 'allow_once');

    expect(JSON.parse(requestAt().init.body as string)).toEqual({
      source_id: 'source-1',
      image_indices: [0],
      engine: 'ai',
      consent: 'allow_once',
    });
  });

  it('sends outline-image consent in the request body', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ topics: [], warnings: [] }));
    await interpretTopicOutline('source-2', 'remember');
    expect(requestAt().url).toBe(
      `${BASE}/api/v1/topic-sources/source-2/interpret-outline`,
    );
    expect(JSON.parse(requestAt().init.body as string)).toEqual({
      consent: 'remember',
    });
  });

  it('downloads and deletes owner-scoped table files', async () => {
    fileMocks.downloadFileAsync.mockResolvedValueOnce({
      uri: 'file://cache/table.csv',
    });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(downloadTableFile('file-1', 'table.csv')).resolves.toBe(
      'file://cache/table.csv',
    );
    expect(String(fileMocks.downloadFileAsync.mock.calls[0][0])).toBe(
      `${BASE}/api/v1/tables/files/file-1`,
    );
    await deleteTableResult('table-1');
    expect(requestAt().url).toBe(`${BASE}/api/v1/tables/results/table-1`);
    expect(requestAt().init.method).toBe('DELETE');
  });
});

describe('File Converter API', () => {
  it('loads the registry and sends file conversion fields', async () => {
    class FakeFormData {
      fields: [string, unknown][] = [];

      append(name: string, value: unknown) {
        this.fields.push([name, value]);
      }
    }
    vi.stubGlobal('FormData', FakeFormData);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        inputs: [
          {
            kind: 'csv',
            label: 'CSV',
            accept: ['.csv'],
            supports_text: false,
            conversions: [],
          },
        ],
        limits: {
          max_file_mb: 25,
          max_rows: 5000,
          max_columns: 200,
          max_cell_chars: 5000,
          max_pages: 50,
        },
      }),
    );

    await expect(getConversionFormats()).resolves.toMatchObject({
      inputs: [{ kind: 'csv' }],
    });
    expect(requestAt().url).toBe(`${BASE}/api/v1/convert/formats`);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        job_id: 'job-1',
        conversion_id: 'csv-to-xlsx',
        input_kind: 'csv',
        output_kind: 'xlsx',
        status: 'queued',
        needs_ai: false,
        editable: false,
        file: null,
        table: null,
        warnings: [],
        error: null,
        created_at: 1,
        expires_at: 2,
      }),
    );
    await startConversion({
      conversionId: 'csv-to-xlsx',
      file: { uri: 'file://a.csv', name: 'a.csv', mimeType: 'text/csv' },
      useAi: false,
    });
    const request = requestAt(1);
    expect(request.url).toBe(`${BASE}/api/v1/convert/jobs`);
    const form = request.init.body as unknown as FakeFormData;
    expect(form.fields).toEqual(
      expect.arrayContaining([
        ['conversion_id', 'csv-to-xlsx'],
        ['use_ai', 'false'],
      ]),
    );
    expect(form.fields.find(([key]) => key === 'file')?.[1]).toMatchObject({
      uri: 'file://a.csv',
      name: 'a.csv',
      type: 'text/csv',
    });

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        job_id: 'job-1',
        conversion_id: 'csv-to-xlsx',
        input_kind: 'csv',
        output_kind: 'xlsx',
        status: 'completed',
        needs_ai: false,
        editable: false,
        file: { name: 'a.xlsx', format: 'xlsx', size_bytes: 12 },
        table: null,
        warnings: [],
        error: null,
        created_at: 1,
        expires_at: 2,
      }),
    );
    await expect(getConversionJob('job-1')).resolves.toMatchObject({
      status: 'completed',
    });
    expect(requestAt(2).url).toBe(`${BASE}/api/v1/convert/jobs/job-1`);
  });

  it('exports an edited summary table and downloads the result', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        job_id: 'job-2',
        conversion_id: 'summary-pdf-to-xlsx',
        input_kind: 'pdf',
        output_kind: 'xlsx',
        status: 'completed',
        needs_ai: true,
        editable: true,
        file: { name: 'summary.xlsx', format: 'xlsx', size_bytes: 10 },
        table: { title: 'Summary', columns: ['A'], rows: [['B']] },
        warnings: [],
        error: null,
        created_at: 1,
        expires_at: 2,
      }),
    );
    await exportConversionTable(
      'job-2',
      'xlsx',
      ['Topic'],
      [['Photosynthesis']],
      'Summary',
    );
    expect(requestAt().url).toBe(`${BASE}/api/v1/convert/jobs/job-2/export`);
    expect(JSON.parse(requestAt().init.body as string)).toEqual({
      format: 'xlsx',
      columns: ['Topic'],
      rows: [['Photosynthesis']],
      title: 'Summary',
    });

    fileMocks.downloadFileAsync.mockResolvedValueOnce({
      uri: 'file://cache/summary.xlsx',
    });
    await expect(
      downloadConversionFile('job-2', 'summary.xlsx'),
    ).resolves.toBe('file://cache/summary.xlsx');
    expect(String(fileMocks.downloadFileAsync.mock.calls[0][0])).toBe(
      `${BASE}/api/v1/convert/jobs/job-2/file`,
    );
  });
});

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
  fileMocks.downloadFileAsync.mockReset();
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

  it('uploads outline images without requiring an attached document', async () => {
    class FakeFormData {
      fields: [string, unknown][] = [];
      append(name: string, value: unknown) { this.fields.push([name, value]); }
    }
    vi.stubGlobal('FormData', FakeFormData);
    fetchMock.mockResolvedValueOnce(jsonResponse({ source_id: 'outline-1' }));

    await createTopicSource(null, [{ uri: 'file://outline.jpg', name: 'outline.jpg', mimeType: 'image/jpeg' }]);

    const form = requestAt().init.body as unknown as FakeFormData;
    expect(form.fields.map(([name]) => name)).toEqual(['images']);
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

  it('resumes OCR batches until the document is ready', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          document_id: 'doc-1',
          format: 'pdf',
          page_count: 2,
          status: 'pending',
          total_pages: 2,
          needs_ocr_pages: 2,
          completed_pages: 0,
          pending_pages: [1, 2],
          failed_pages: [],
          warnings: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          document_id: 'doc-1',
          format: 'pdf',
          page_count: 2,
          status: 'pending',
          total_pages: 2,
          needs_ocr_pages: 2,
          completed_pages: 1,
          pending_pages: [2],
          failed_pages: [],
          warnings: [],
          attempted_pages: [1],
          completed_this_batch: [1],
          failed_this_batch: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          document_id: 'doc-1',
          format: 'pdf',
          page_count: 2,
          status: 'complete',
          total_pages: 2,
          needs_ocr_pages: 2,
          completed_pages: 2,
          pending_pages: [],
          failed_pages: [],
          warnings: [],
          attempted_pages: [2],
          completed_this_batch: [2],
          failed_this_batch: [],
        }),
      );

    const progress = await prepareDocumentOcr('doc-1');

    expect(progress.status).toBe('complete');
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(requestAt(2).init.body).toBe(JSON.stringify({ pages: [2] }));
  });

  it('preserves OCR error details for resumable preparation', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: 'ocr_incomplete',
            message: 'Pages still need OCR.',
            details: { pending_pages: [2, 3], failed_pages: [4] },
          },
        },
        409,
      ),
    );

    await expect(prepareOcrBatch('doc-1', [1])).rejects.toMatchObject({
      code: 'ocr_incomplete',
      status: 409,
      details: { pending_pages: [2, 3], failed_pages: [4] },
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

describe('export OCR recovery', () => {
  const completeProgress = {
    status: 'complete',
    pending_pages: [],
    failed_pages: [],
    completed_pages: [1],
    total_pages: 1,
  };

  it('prepares OCR and retries a study export after a 409', async () => {
    fileMocks.downloadFileAsync
      .mockRejectedValueOnce(new Error('HTTP 409'))
      .mockResolvedValueOnce({ uri: 'file://cache/study.pdf' });
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          job_id: 'job-1',
          status: 'completed',
          document_available: true,
          document_id: 'doc-1',
        }),
      )
      .mockResolvedValueOnce(jsonResponse(completeProgress));

    await expect(downloadStudyExport('job-1', 'pdf', 'study.pdf')).resolves.toBe(
      'file://cache/study.pdf',
    );

    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      `${BASE}/api/v1/study-jobs/job-1`,
      `${BASE}/api/v1/documents/doc-1/ocr`,
    ]);
    expect(fileMocks.downloadFileAsync).toHaveBeenCalledTimes(2);
  });

  it('prepares OCR and retries a topic result export after a 409', async () => {
    fileMocks.downloadFileAsync
      .mockRejectedValueOnce(new Error('status 409'))
      .mockResolvedValueOnce({ uri: 'file://cache/topic.docx' });
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          result_id: 'result-1',
          document_available: true,
          document: { document_id: 'doc-1' },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(completeProgress));

    await expect(
      downloadTopicResultExport('result-1', 'docx', 'topic.docx'),
    ).resolves.toBe('file://cache/topic.docx');

    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      `${BASE}/api/v1/topic-results/result-1`,
      `${BASE}/api/v1/documents/doc-1/ocr`,
    ]);
  });

  it('prepares OCR and retries a range extraction export after a 409', async () => {
    fileMocks.downloadFileAsync
      .mockRejectedValueOnce(new Error('HTTP 409'))
      .mockResolvedValueOnce({ uri: 'file://cache/range.pdf' });
    fetchMock.mockResolvedValueOnce(jsonResponse(completeProgress));

    await expect(
      downloadExtractionExport(
        {
          document_id: 'doc-1',
          selector: { type: 'pages', spec: '1' },
          mode: 'source_passages',
        },
        'pdf',
        'range.pdf',
      ),
    ).resolves.toBe('file://cache/range.pdf');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `${BASE}/api/v1/documents/doc-1/ocr`,
    );
    expect(fileMocks.downloadFileAsync).toHaveBeenCalledTimes(2);
  });
});

it('marks converter uploads with the table purpose', async () => {
  class FakeFormData {
    fields: [string, unknown][] = [];

    append(name: string, value: unknown) {
      this.fields.push([name, value]);
    }
  }
  vi.stubGlobal('FormData', FakeFormData);
  fetchMock.mockResolvedValueOnce(jsonResponse({ source_id: 'table-1' }));

  await createTopicSource(
    null,
    [{ uri: 'file://attendance.png', name: 'attendance.png', mimeType: 'image/png' }],
    '',
    true,
    'table',
  );

  const form = requestAt().init.body as unknown as FakeFormData;
  expect(form.fields).toContainEqual(['skip_ocr', 'true']);
  expect(form.fields).toContainEqual(['purpose', 'table']);
});
