import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  createTopicSource: vi.fn(),
  downloadAgentFile: vi.fn(),
  downloadDocumentPageImage: vi.fn(),
  getAgentExport: vi.fn(),
  getAgentResult: vi.fn(),
  isSharingAvailable: vi.fn(),
  prepareDocumentOcr: vi.fn(),
  startAgentExport: vi.fn(),
  shareFile: vi.fn(),
  uploadDocument: vi.fn(),
  waitForDocument: vi.fn(),
}));

vi.mock('expo-sharing', () => ({
  isAvailableAsync: mocks.isSharingAvailable,
  shareAsync: mocks.shareFile,
}));

vi.mock('@/services/apiClient', () => ({
  createTopicSource: mocks.createTopicSource,
  downloadAgentFile: mocks.downloadAgentFile,
  downloadDocumentPageImage: mocks.downloadDocumentPageImage,
  getAgentExport: mocks.getAgentExport,
  getAgentResult: mocks.getAgentResult,
  prepareDocumentOcr: mocks.prepareDocumentOcr,
  startAgentExport: mocks.startAgentExport,
  uploadDocument: mocks.uploadDocument,
  waitForDocument: mocks.waitForDocument,
}));

import type { AgentPassageResult } from '@/services/apiClient';
import {
  AGENT_CONTINUE_PROMPT,
  agentCanContinue,
  agentFailureMessage,
  agentResponseWarnings,
  conversationIdAfterAgentError,
  exportAgentResult,
  isExpiredAgentConversation,
  loadAgentPassages,
  prepareAgentResultOcr,
  shareAgentFile,
  uploadAgentDocument,
  uploadAgentImages,
  uniqueAgentWarnings,
} from '@/services/agentWorkflow';
import type { DocumentResponse } from '@/types/api';

describe('agent response warnings', () => {
  it('adds one time-limit warning instead of a network-timeout message', () => {
    expect(agentResponseWarnings('agent_time_limit', [])).toEqual([
      'The server reached its time limit before finishing. Try a smaller request or use any results shown above.',
    ]);
  });

  it('keeps server warnings and removes exact duplicate messages', () => {
    expect(agentResponseWarnings(null, ['Same warning.', 'Same warning.'])).toEqual([
      'Same warning.',
    ]);
  });
});

describe('agent screen workflows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uploads and waits for a document before attaching its owned id', async () => {
    mocks.uploadDocument.mockResolvedValueOnce({
      document_id: 'doc-1', format: 'pdf', filename: 'lecture.pdf',
    } as DocumentResponse);
    mocks.waitForDocument.mockImplementationOnce(async (_id, onProgress) => {
      onProgress?.({ progress: { processed_units: 1, total_units: 1 } } as DocumentResponse);
    });
    const progress = vi.fn();

    await expect(uploadAgentDocument({
      uri: 'file://lecture.pdf', name: 'lecture.pdf', mimeType: 'application/pdf',
    }, progress)).resolves.toEqual({ id: 'doc-1', kind: 'pdf', name: 'lecture.pdf' });
    expect(mocks.waitForDocument).toHaveBeenCalledWith('doc-1', progress);
    expect(progress).toHaveBeenCalledTimes(1);
  });

  it('groups several selected images into one attachment', async () => {
    mocks.createTopicSource.mockResolvedValueOnce({ source_id: 'outline-1', image_count: 7 });
    const files = Array.from({ length: 7 }, (_, index) => ({
      uri: `file://outline-${index + 1}.jpg`,
      name: `outline-${index + 1}.jpg`,
      mimeType: 'image/jpeg',
    }));

    await expect(uploadAgentImages(files)).resolves.toEqual({
      id: 'outline-1', kind: 'image', name: '7 image(s)',
    });
    expect(mocks.createTopicSource).toHaveBeenCalledTimes(1);
    expect(mocks.createTopicSource).toHaveBeenCalledWith(null, files);
  });

  it('loads stored passages and only downloads page images marked present', async () => {
    mocks.getAgentResult.mockResolvedValueOnce({
      result_id: 'result-1',
      document_id: 'doc-1',
      topics: [{
        topic: 'Topic A',
        passages: [
          { passage_id: 'doc-1:0', page: 2, text: 'Text', has_page_image: true },
          { passage_id: 'doc-1:1', page: 3, text: 'Text', has_page_image: false },
          { passage_id: 'doc-1:2', page: 2, text: 'More text', has_page_image: true },
        ],
      }],
    } satisfies AgentPassageResult);
    mocks.downloadDocumentPageImage.mockResolvedValueOnce('file://page-2.png');

    const loaded = await loadAgentPassages('result-1');

    expect(loaded.pageImages).toEqual({ 2: 'file://page-2.png' });
    expect(mocks.downloadDocumentPageImage).toHaveBeenCalledTimes(1);
    expect(mocks.downloadDocumentPageImage).toHaveBeenCalledWith('doc-1', 2);
  });

  it('identifies an expired conversation so the screen can reset its id', () => {
    const expired = { code: 'conversation_not_found' };
    const unavailable = { code: 'agent_unavailable' };
    expect(isExpiredAgentConversation(expired)).toBe(true);
    expect(isExpiredAgentConversation(unavailable)).toBe(false);
    expect(conversationIdAfterAgentError('expired-id', expired)).toBeNull();
    expect(conversationIdAfterAgentError('current-id', unavailable)).toBe('current-id');
  });

  it('maps provider failures to app text and shows each warning once', () => {
    expect(agentFailureMessage({
      code: 'agent_rate_limited',
      message: 'provider detail',
      details: { retry_after_s: 12.2 },
    })).toBe(
      'The AI provider is rate limited. Wait about 13 seconds before sending again; the app will not resend it automatically.',
    );
    expect(agentFailureMessage({ code: 'agent_model_not_found' })).toBe(
      'The configured agent model was not found.',
    );
    expect(agentFailureMessage({
      code: 'agent_provider_error',
      message: 'Provider failed.',
    })).toBe('The AI provider rejected the request.');
    expect(agentFailureMessage({ code: 'attachment_not_found' })).toBe(
      'A source in this chat is no longer available. Reattach the file and try again.',
    );
    expect(agentFailureMessage({ code: 'active_source_not_found' })).toBe(
      'The selected source is no longer available. Reattach it and try again.',
    );
    expect(uniqueAgentWarnings(['Provider failed.', 'Provider failed.'])).toEqual([
      'Provider failed.',
    ]);
    expect(agentResponseWarnings('agent_output_truncated', [])).toEqual([
      'The reply was cut off before it finished. The partial answer is shown above.',
    ]);
    expect(agentResponseWarnings('ok', ['Provider failed.'])).toEqual([
      'Provider failed.',
    ]);
    expect(agentCanContinue('agent_output_truncated')).toBe(true);
    expect(agentCanContinue('ok')).toBe(false);
    expect(AGENT_CONTINUE_PROMPT).toContain('previous answer');
  });

  it('starts, polls, then downloads a completed result export', async () => {
    mocks.startAgentExport.mockResolvedValueOnce({
      job_id: 'job-1', result_id: 'result-1', format: 'docx',
      status: 'queued', file_id: null, name: null, error: null,
    });
    mocks.getAgentExport
      .mockResolvedValueOnce({
        job_id: 'job-1', result_id: 'result-1', format: 'docx',
        status: 'running', file_id: null, name: null, error: null,
      })
      .mockResolvedValueOnce({
        job_id: 'job-1', result_id: 'result-1', format: 'docx',
        status: 'completed', file_id: 'file-1', name: 'study.docx', error: null,
      });
    mocks.downloadAgentFile.mockResolvedValueOnce('file://study.docx');
    mocks.isSharingAvailable.mockResolvedValueOnce(true);
    const stages: string[] = [];

    await expect(exportAgentResult('result-1', 'docx', {
      onStage: (stage) => stages.push(stage),
      wait: async () => undefined,
      now: () => 0,
    })).resolves.toEqual({ file_id: 'file-1', name: 'study.docx' });

    expect(stages).toEqual(['queued', 'running', 'completed', 'opening']);
    expect(mocks.startAgentExport).toHaveBeenCalledWith('result-1', 'docx', false);
    expect(mocks.getAgentExport).toHaveBeenCalledTimes(2);
    expect(mocks.downloadAgentFile).toHaveBeenCalledWith('file-1', 'study.docx');
  });

  it('forwards local edits when starting an export', async () => {
    mocks.startAgentExport.mockResolvedValueOnce({
      job_id: 'job-4', result_id: 'result-1', format: 'docx',
      status: 'completed', file_id: 'file-4', name: 'edited.docx', error: null,
    });
    mocks.downloadAgentFile.mockResolvedValueOnce('file://edited.docx');
    mocks.isSharingAvailable.mockResolvedValueOnce(true);
    const edits = {
      summary: 'Edited summary.',
      removed_passage_ids: ['doc-1:1'],
      included_pages: [1],
    };

    await exportAgentResult('result-1', 'docx', {
      edits,
      wait: async () => undefined,
      now: () => 0,
    });

    expect(mocks.startAgentExport).toHaveBeenCalledWith(
      'result-1', 'docx', false, edits,
    );
  });

  it('reports a failed export without downloading a file', async () => {
    mocks.startAgentExport.mockResolvedValueOnce({
      job_id: 'job-2', result_id: 'result-1', format: 'pdf',
      status: 'failed', file_id: null, name: null,
      error: {
        code: 'export_empty',
        message: 'The result has no readable content to export.',
        details: {},
      },
    });

    await expect(exportAgentResult('result-1', 'pdf', {
      wait: async () => undefined,
      now: () => 0,
    })).rejects.toThrow('The result has no readable content to export.');
    expect(mocks.downloadAgentFile).not.toHaveBeenCalled();
  });

  it('prepares OCR for the document behind a stored result', async () => {
    mocks.getAgentResult.mockResolvedValueOnce({
      result_id: 'result-1', document_id: 'doc-1', topics: [],
    } satisfies AgentPassageResult);
    mocks.prepareDocumentOcr.mockResolvedValueOnce({
      status: 'complete', pending_pages: [], failed_pages: [],
    });
    const progress = vi.fn();

    await expect(prepareAgentResultOcr('result-1', progress)).resolves.toMatchObject({
      status: 'complete',
    });
    expect(mocks.prepareDocumentOcr).toHaveBeenCalledWith('doc-1', progress);
  });

  it('downloads then shares an agent file', async () => {
    mocks.downloadAgentFile.mockResolvedValueOnce('file://study.docx');
    mocks.isSharingAvailable.mockResolvedValueOnce(true);

    await expect(shareAgentFile('file-1', 'study.docx')).resolves.toBe('file://study.docx');
    expect(mocks.downloadAgentFile).toHaveBeenCalledWith('file-1', 'study.docx');
    expect(mocks.shareFile).toHaveBeenCalledWith('file://study.docx');
  });

  it('keeps the user text exact and exports no preset request wording', async () => {
    const { agentMessageText } = await import('@/services/agentWorkflow');
    const presentation = await import('@/services/agentPresentation');
    const text = '  Compare pages 2 and 3, then explain the last line.\n';

    expect(agentMessageText(text)).toBe(text);
    expect(agentMessageText('  \n')).toBeNull();

    const exported = [
      ...Object.keys(await import('@/services/agentWorkflow')),
      ...Object.keys(presentation),
    ].join(' ').toLowerCase();
    expect(exported).not.toMatch(/welcome|suggest|preset/);
  });
});
