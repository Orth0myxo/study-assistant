import { describe, expect, it } from 'vitest';

import { presentAgentBlock } from '@/services/agentPresentation';

describe('agent block renderer presentation', () => {
  it('keeps summary content labelled as AI generated', () => {
    expect(presentAgentBlock({
      type: 'summary', text: 'Summary text', label: 'AI summary, not source text',
    })).toMatchObject({ title: 'AI summary, not source text', body: 'Summary text' });
  });

  it('resolves passage and file blocks by their stored ids', () => {
    expect(presentAgentBlock({ type: 'passages', result_id: 'result-1' }).action)
      .toEqual({ kind: 'result', id: 'result-1' });
    expect(presentAgentBlock({ type: 'file', file_id: 'file-1', name: 'notes.docx', format: 'docx' }))
      .toMatchObject({ title: 'notes.docx', action: { kind: 'file', id: 'file-1' } });
  });

  it('turns clarification options into choices the user can send', () => {
    expect(presentAgentBlock({
      type: 'question', text: 'Choose a passage', options: ['Page 2', 'Page 4'],
    })).toMatchObject({ title: 'One detail needed', body: 'Choose a passage', options: ['Page 2', 'Page 4'] });
  });
});
