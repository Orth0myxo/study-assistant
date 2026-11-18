import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  createTopicSource: vi.fn(),
  downloadAgentFile: vi.fn(),
  downloadDocumentPageImage: vi.fn(),
  getAgentResult: vi.fn(),
  isSharingAvailable: vi.fn(),
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
  getAgentResult: mocks.getAgentResult,
  uploadDocument: mocks.uploadDocument,
  waitForDocument: mocks.waitForDocument,
}));

import type { AgentPassageResult } from '@/services/apiClient';
import {
  agentFailureMessage,
  conversationIdAfterAgentError,
  isExpiredAgentConversation,
  loadAgentPassages,
  shareAgentFile,
  uploadAgentDocument,
  uploadAgentImages,
  uniqueAgentWarnings,
} from '@/services/agentWorkflow';
import type { DocumentResponse } from '@/types/api';

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

  it('creates an independent image attachment and returns its source id', async () => {
    mocks.createTopicSource.mockResolvedValueOnce({ source_id: 'outline-1', image_count: 2 });
    const files = [
      { uri: 'file://outline-a.jpg', name: 'a.jpg', mimeType: 'image/jpeg' },
      { uri: 'file://outline-b.jpg', name: 'b.jpg', mimeType: 'image/jpeg' },
    ];

    await expect(uploadAgentImages(files)).resolves.toEqual({
      id: 'outline-1', kind: 'image', name: '2 image(s)',
    });
    expect(mocks.createTopicSource).toHaveBeenCalledWith(null, files);
  });

  it('loads stored passages and only downloads page images marked present', async () => {
    mocks.getAgentResult.mockResolvedValueOnce({
      result_id: 'result-1',
      document_id: 'doc-1',
      topics: [{
        topic: 'Topic A',
        passages: [
          { page: 2, text: 'Text', has_page_image: true },
          { page: 3, text: 'Text', has_page_image: false },
          { page: 2, text: 'More text', has_page_image: true },
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
    })).toBe('The AI provider is rate limited. Try again in 13 seconds.');
    expect(agentFailureMessage({ code: 'agent_model_not_found' })).toBe(
      'The configured agent model was not found.',
    );
    expect(agentFailureMessage({
      code: 'agent_provider_error',
      message: 'Provider failed.',
    })).toBe('The AI provider rejected the request.');
    expect(uniqueAgentWarnings(['Provider failed.', 'Provider failed.'])).toEqual([
      'Provider failed.',
    ]);
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
