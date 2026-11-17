import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  buildWelcomeRequest,
  WELCOME_ACTIONS,
} from '../services/welcomeActions';

const sampleWords = [
  'sickle cell anaemia',
  'cell membrane transport',
  'vaginal cytology',
  'Summarize the whole material',
  'Show me pages',
];

describe('welcome actions', () => {
  it('names actions without adding sample study wording', () => {
    const labels = WELCOME_ACTIONS.map((action) => action.label).join(' ');
    for (const word of sampleWords) {
      expect(labels.toLowerCase()).not.toContain(word.toLowerCase());
    }
    for (const file of ['../app/workspace.tsx', '../app/photo-notes.tsx']) {
      const source = readFileSync(
        fileURLToPath(new URL(file, import.meta.url)),
        'utf8',
      );
      for (const word of sampleWords) {
        expect(source.toLowerCase()).not.toContain(word.toLowerCase());
      }
    }
  });

  it('builds an empty-form request with no sample content', () => {
    expect(buildWelcomeRequest('summary', '')).toBe(
      'summary of the whole document',
    );
    expect(buildWelcomeRequest('passages', '')).toBe(
      'verbatim text about the whole document',
    );
    expect(buildWelcomeRequest('pages', '')).toBeNull();
    expect(buildWelcomeRequest('pages', '2-4')).toBe('pages 2-4');
  });
});
