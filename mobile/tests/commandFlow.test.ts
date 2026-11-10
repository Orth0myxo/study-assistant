import { describe, expect, it } from 'vitest';

import { parseFastCommand } from '@/services/chatOrchestrator';
import { normalizeCommand, planCommand } from '@/services/commandDispatch';

describe('parseFastCommand', () => {
  it('reads pages with verbatim mode', () => {
    expect(parseFastCommand('pages 12-15 verbatim')).toEqual({
      action: 'extract',
      selector: { type: 'pages', value: '12-15' },
      mode: 'source_passages',
      exportFormat: null,
    });
  });
  it('reads mixed page lists with summary', () => {
    const c = parseFastCommand('Extract pages 3 to 5 and 9 with summary');
    expect(c?.selector).toEqual({ type: 'pages', value: '3-5, 9' });
    expect(c?.mode).toBe('both');
  });
  it('reads summary only and export format', () => {
    expect(parseFastCommand('summary only of pages 2-4')?.mode).toBe('ai_study_material');
    expect(parseFastCommand('questions 5-10 as docx')?.exportFormat).toBe('docx');
    expect(parseFastCommand('export as pdf')).toMatchObject({ action: 'export', exportFormat: 'pdf' });
  });
  it('defers anything with unknown words', () => {
    expect(parseFastCommand('find sickle cell anaemia')).toBeNull();
    expect(parseFastCommand('pages 12-15 on anaemia')).toBeNull();
  });
});

describe('normalizeCommand / planCommand', () => {
  it('maps the backend command', () => {
    const c = normalizeCommand({
      action: 'extract',
      selector: { type: 'pages', pages: '1-2' },
      mode: 'both',
      export_format: 'pdf',
    });
    expect(c?.selector).toEqual({ type: 'pages', value: '1-2' });
    expect(planCommand(c!, { hasDocument: true, hasLastResult: false })).toMatchObject({
      kind: 'range',
      selectorType: 'pages',
      thenExport: 'pdf',
    });
  });
  it('rejects junk', () => {
    expect(normalizeCommand({ action: 'nope' })).toBeNull();
    expect(normalizeCommand({ action: 'extract' })).toBeNull();
  });
  it('routes topics to the existing topic flow and needs a document', () => {
    const c = normalizeCommand({ action: 'extract', selector: { type: 'topic', topic: 'malaria' }, mode: 'ai_study_material' })!;
    expect(planCommand(c, { hasDocument: true, hasLastResult: false })).toEqual({
      kind: 'topic', topic: 'malaria', generateMode: 'ai_study_material',
    });
    expect(planCommand(c, { hasDocument: false, hasLastResult: false }).kind).toBe('say');
  });
  it('refuses export with no result', () => {
    expect(planCommand({ action: 'export', mode: 'source_passages', exportFormat: 'pdf' }, { hasDocument: true, hasLastResult: false }).kind).toBe('say');
  });
});
