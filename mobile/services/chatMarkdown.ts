export type ChatMarkdownSpan = {
  text: string;
  bold: boolean;
};

export type ChatMarkdownBlock =
  | { kind: 'paragraph' | 'heading'; spans: ChatMarkdownSpan[] }
  | { kind: 'bullet' | 'number'; marker: string; spans: ChatMarkdownSpan[] }
  | { kind: 'break' };

const BOLD = /\*\*(.+?)\*\*/g;

function spans(value: string): ChatMarkdownSpan[] {
  const result: ChatMarkdownSpan[] = [];
  let cursor = 0;
  for (const match of value.matchAll(BOLD)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      result.push({ text: value.slice(cursor, start).replaceAll('**', ''), bold: false });
    }
    result.push({ text: match[1].replaceAll('**', ''), bold: true });
    cursor = start + match[0].length;
  }
  if (cursor < value.length) {
    result.push({ text: value.slice(cursor).replaceAll('**', ''), bold: false });
  }
  return result.filter((span) => span.text.length > 0);
}

export function parseChatMarkdown(value: string): ChatMarkdownBlock[] {
  const blocks: ChatMarkdownBlock[] = [];
  for (const line of value.replace(/\r\n?/g, '\n').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) {
      blocks.push({ kind: 'break' });
      continue;
    }
    const heading = /^#{1,6}\s+(.*)$/.exec(trimmed);
    if (heading) {
      blocks.push({ kind: 'heading', spans: spans(heading[1]) });
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (bullet) {
      blocks.push({ kind: 'bullet', marker: '•', spans: spans(bullet[1]) });
      continue;
    }
    const numbered = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      blocks.push({
        kind: 'number',
        marker: `${numbered[1]}.`,
        spans: spans(numbered[2]),
      });
      continue;
    }
    blocks.push({ kind: 'paragraph', spans: spans(trimmed) });
  }
  return blocks;
}
