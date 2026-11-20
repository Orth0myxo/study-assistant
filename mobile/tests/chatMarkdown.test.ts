import { describe, expect, it } from 'vitest';

import { parseChatMarkdown } from '@/services/chatMarkdown';

describe('chat markdown', () => {
  it('renders bold, headings, lists, and blank-line breaks without raw markers', () => {
    const blocks = parseChatMarkdown(
      '# Summary\n**Epithelial cells** line the surface.\n- first point\n2. second point\n\nFinal line.',
    );

    expect(blocks).toEqual([
      { kind: 'heading', spans: [{ text: 'Summary', bold: false }] },
      {
        kind: 'paragraph',
        spans: [
          { text: 'Epithelial cells', bold: true },
          { text: ' line the surface.', bold: false },
        ],
      },
      { kind: 'bullet', marker: '•', spans: [{ text: 'first point', bold: false }] },
      { kind: 'number', marker: '2.', spans: [{ text: 'second point', bold: false }] },
      { kind: 'break' },
      { kind: 'paragraph', spans: [{ text: 'Final line.', bold: false }] },
    ]);
    expect(JSON.stringify(blocks)).not.toContain('**');
  });

  it('removes unmatched bold markers instead of showing them', () => {
    expect(parseChatMarkdown('**Unfinished note')).toEqual([
      { kind: 'paragraph', spans: [{ text: 'Unfinished note', bold: false }] },
    ]);
  });

  it('parses a pipe table into cells without raw delimiters', () => {
    const blocks = parseChatMarkdown(
      'Before\n| **Topic** | Detail |\n| --- | ---: |\n| Alpha | One \\| two |\n| Beta |\n\nAfter',
    );

    expect(blocks[0]).toEqual({
      kind: 'paragraph',
      spans: [{ text: 'Before', bold: false }],
    });
    expect(blocks[1]).toEqual({
      kind: 'table',
      header: [
        [{ text: 'Topic', bold: true }],
        [{ text: 'Detail', bold: false }],
      ],
      rows: [
        [
          [{ text: 'Alpha', bold: false }],
          [{ text: 'One | two', bold: false }],
        ],
        [
          [{ text: 'Beta', bold: false }],
          [],
        ],
      ],
    });
    expect(blocks[2]).toEqual({ kind: 'break' });
    expect(JSON.stringify(blocks)).not.toContain('| ---');
  });
});
