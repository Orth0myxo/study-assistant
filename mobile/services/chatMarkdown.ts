export type ChatMarkdownSpan = {
  text: string;
  bold: boolean;
};

export type ChatMarkdownBlock =
  | { kind: 'paragraph' | 'heading'; spans: ChatMarkdownSpan[] }
  | { kind: 'bullet' | 'number'; marker: string; spans: ChatMarkdownSpan[] }
  | {
      kind: 'table';
      header: ChatMarkdownSpan[][];
      rows: ChatMarkdownSpan[][][];
    }
  | { kind: 'break' };

const BOLD = /\*\*(.+?)\*\*/g;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;

function spans(value: string): ChatMarkdownSpan[] {
  const result: ChatMarkdownSpan[] = [];
  let cursor = 0;
  for (const match of value.matchAll(BOLD)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      result.push({
        text: value.slice(cursor, start).replaceAll('**', ''),
        bold: false,
      });
    }
    result.push({ text: match[1].replaceAll('**', ''), bold: true });
    cursor = start + match[0].length;
  }
  if (cursor < value.length) {
    result.push({
      text: value.slice(cursor).replaceAll('**', ''),
      bold: false,
    });
  }
  return result.filter((span) => span.text.length > 0);
}

function tableCells(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) {
    return null;
  }
  let value = trimmed;
  if (value.startsWith('|')) {
    value = value.slice(1);
  }
  if (value.endsWith('|') && !value.endsWith('\\|')) {
    value = value.slice(0, -1);
  }
  const cells = value
    .split(/(?<!\\)\|/)
    .map((cell) => cell.replaceAll('\\|', '|').trim());
  return cells.length >= 1 ? cells : null;
}

function isSeparator(line: string): boolean {
  if (!TABLE_SEPARATOR.test(line)) {
    return false;
  }
  const cells = tableCells(line);
  return (
    !!cells &&
    cells.length >= 2 &&
    cells.every((cell) => /^:?-{3,}:?$/.test(cell))
  );
}

function parseTable(
  lines: string[],
  index: number,
): { block: Extract<ChatMarkdownBlock, { kind: 'table' }>; end: number } | null {
  const header = tableCells(lines[index]);
  if (
    !header ||
    header.length < 2 ||
    index + 1 >= lines.length ||
    !isSeparator(lines[index + 1])
  ) {
    return null;
  }
  const width = header.length;
  const rows: string[][] = [];
  let cursor = index + 2;
  while (cursor < lines.length) {
    if (!lines[cursor].trim()) {
      break;
    }
    const cells = tableCells(lines[cursor]);
    if (!cells) {
      break;
    }
    rows.push([...cells, ...Array(width).fill('')].slice(0, width));
    cursor += 1;
  }
  return {
    block: {
      kind: 'table',
      header: header.map(spans),
      rows: rows.map((row) => row.map(spans)),
    },
    end: cursor,
  };
}

export function parseChatMarkdown(value: string): ChatMarkdownBlock[] {
  const lines = value.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ChatMarkdownBlock[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const table = parseTable(lines, index);
    if (table) {
      blocks.push(table.block);
      index = table.end - 1;
      continue;
    }
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
