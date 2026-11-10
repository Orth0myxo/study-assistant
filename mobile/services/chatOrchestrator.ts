export const CHAT_TOPIC_MAX_CHARS = 200;

export const CHAT_TOPIC_SOURCE_SEED = 'Study chat topics';

export type ChatIntent =
  | { kind: 'empty' }
  | { kind: 'help' }
  | { kind: 'need_topic' }
  | { kind: 'unsupported' }
  | { kind: 'whole_summary' }
  | { kind: 'topic'; topic: string; generate: boolean };

const HELP_PATTERN =
  /^(?:hi|hello|hey|thanks|thank you|help)(?:\s+there)?[\s!.?]*$|^(?:what can you do|how (?:do|does) (?:this|it) work|how do i use this)[\s!.?]*$/i;

const ANOTHER_TOPIC_PATTERN =
  /^(?:another|next|different|new) topic[\s!.?]*$/i;

const TOPIC_LIST_PATTERN =
  /^(?:(?:please\s+)?(?:extract|find|locate|identify|list|show|give me)\s+)?(?:me\s+)?(?:the\s+|all\s+)?(?:topics?|topic list|topics list)(?:\s+in\s+.+)?[\s!.?]*$/i;

const MATERIAL_REQUEST_PATTERN =
  /^(?:(?:please\s+)?(?:show|give|open|list)\s+(?:me\s+)?)?(?:the\s+|this\s+|my\s+)?(?:material|document|file|sources?)[\s!.?]*$/i;

const GENERATE_PATTERN =
  /\b(summar(?:y|ize|ise)|study material|study notes|explain|teach me|give me notes|revise)\b/i;

const SUMMARY_LEAD_PATTERN =
  /^(?:please\s+)?(?:give me\s+)?(?:a\s+)?(?:brief|short|quick)?\s*(?:summary|summarize|summarise|overview)\b/i;

const SUMMARY_OF_PATTERN = /\b(?:of|on|about)\s+(.+)$/i;

const WHOLE_MATERIAL_PATTERN =
  /\b(?:whole|entire|full|everything|all of it)\b|\b(?:the|this|my|uploaded)\s+(?:material|document|file|lecture|notes)\b/i;

const MATERIAL_ONLY_PATTERN =
  /^(?:(?:the|this|my|uploaded)\s+)?(?:material|document|file|lecture|notes|everything|content|text|whole material|entire document|whole document|full document|source|sources)$/i;

const TOPIC_QUESTION_PATTERN = /^(?!.*\babout\b).*[?]$/i;

const EXTRACT_PATTERNS: RegExp[] = [
  /^(?:please\s+)?(?:extract|find|locate|identify|search(?:\s+for)?|look\s+(?:up|for)|match|show\s+me)\s+(?:(?:the|any|a|all)\s+)?(?:topics?|content|passages?|material|text)?\s*(?:of|on|about|for|:|-)?\s*(.+)$/i,
  /^(?:what|where|how)\b.*?\b(?:about|on|regarding|mention(?:s|ed)?|say(?:s)?\s+about|discuss(?:es|ed)?|cover(?:s|ed)?)\s+(.+)$/i,
  /^topics?\s*[:\-]\s*(.+)$/i,
  /^(?:explain|tell\s+me\s+about|notes?\s+on|revise)\s+(.+)$/i,
  /^(?:i\s+(?:want|need)\s+to\s+)?(?:study|learn|revise)\s+(.+)$/i,
  /^(?:can|could|would)\s+you\s+(?:find|locate|extract|look\s+up|summari[sz]e)\s+(.+)$/i,
  /^(?:what|who)\s+(?:is|are)\s+(.+)$/i,
];

export function cleanTopicCandidate(raw: string): string {
  let value = raw.trim();
  value = value.replace(/\s+(?:please|for me|thanks|thank you)$/i, '');
  value = value.replace(/[.?!,;:]+$/g, '');
  value = value.replace(/^["'\u201c\u201d\u2018\u2019]+|["'\u201c\u201d\u2018\u2019]+$/g, '');
  value = value.replace(
    /^(?:the\s+)?(?:topic|topics|subject)\s*(?:of|on|about|:|-)?\s*/i,
    '',
  );
  value = value.replace(/[.?!,;:]+$/g, '');
  value = value.replace(/\s+/g, ' ').trim();
  if (value.length > CHAT_TOPIC_MAX_CHARS) {
    value = value.slice(0, CHAT_TOPIC_MAX_CHARS).trim();
  }
  return value;
}

export function isMaterialOnlyCandidate(value: string): boolean {
  return MATERIAL_ONLY_PATTERN.test(value.trim());
}

function topicIntent(topic: string, generate: boolean): ChatIntent | null {
  const candidate = cleanTopicCandidate(topic);
  if (!candidate || isMaterialOnlyCandidate(candidate)) {
    return null;
  }
  return { kind: 'topic', topic: candidate, generate };
}

function extractTopicVerb(input: string): string | null {
  for (const pattern of EXTRACT_PATTERNS) {
    const match = input.match(pattern);
    if (match?.[1]) {
      const candidate = cleanTopicCandidate(match[1]);
      if (candidate && !isMaterialOnlyCandidate(candidate)) {
        return candidate;
      }
    }
  }
  return null;
}

function extractSummaryTopic(input: string): string | null {
  const ofMatch = input.match(SUMMARY_OF_PATTERN);
  if (ofMatch?.[1]) {
    const candidate = cleanTopicCandidate(ofMatch[1]);
    return candidate && !isMaterialOnlyCandidate(candidate)
      ? candidate
      : null;
  }
  const remainder = cleanTopicCandidate(
    input
      .replace(/^(?:can|could|would)\s+you\s+/i, '')
      .replace(SUMMARY_LEAD_PATTERN, '')
      .replace(/^[:\-\u2013\u2014]\s*/, ''),
  );
  if (
    remainder &&
    !isMaterialOnlyCandidate(remainder) &&
    !WHOLE_MATERIAL_PATTERN.test(remainder)
  ) {
    return remainder;
  }
  return null;
}

export function parseChatIntent(raw: string): ChatIntent {
  const input = raw.trim().replace(/\s+/g, ' ');
  if (!input) {
    return { kind: 'empty' };
  }
  if (HELP_PATTERN.test(input)) {
    return { kind: 'help' };
  }
  if (ANOTHER_TOPIC_PATTERN.test(input) || TOPIC_LIST_PATTERN.test(input)) {
    return { kind: 'need_topic' };
  }
  if (MATERIAL_REQUEST_PATTERN.test(input)) {
    return { kind: 'help' };
  }

  const hasSummaryVerb = /\b(summar(?:y|ize|ise)|overview)\b/i.test(input);
  if (hasSummaryVerb) {
    const topicFromSummary = extractSummaryTopic(input);
    if (topicFromSummary) {
      return (
        topicIntent(topicFromSummary, true) ?? { kind: 'whole_summary' }
      );
    }
    return { kind: 'whole_summary' };
  }

  const topic = extractTopicVerb(input);
  if (topic) {
    return (
      topicIntent(topic, GENERATE_PATTERN.test(input)) ?? { kind: 'need_topic' }
    );
  }

  if (
    WHOLE_MATERIAL_PATTERN.test(input) &&
    /\b(?:explain|teach|study|materials?|notes?)\b/i.test(input)
  ) {
    return { kind: 'whole_summary' };
  }

  if (TOPIC_QUESTION_PATTERN.test(input)) {
    return { kind: 'unsupported' };
  }

  if (input.split(' ').length <= 14) {
    const plain = topicIntent(input, GENERATE_PATTERN.test(input));
    if (plain) {
      return plain;
    }
  }

  return { kind: 'unsupported' };
}

export function describeIntent(intent: ChatIntent): string {
  switch (intent.kind) {
    case 'empty':
      return 'Type a topic or a request.';
    case 'help':
      return 'Ask for a whole-material summary or name a topic to find.';
    case 'need_topic':
      return 'Name the next topic to look for.';
    case 'unsupported':
      return 'Try naming a topic or asking for a whole-material summary.';
    case 'whole_summary':
      return 'Summarize the whole material.';
    case 'topic':
      return intent.generate
        ? `Extract and summarize "${intent.topic}".`
        : `Find "${intent.topic}" in the material.`;
  }
}

// ---------------------------------------------------------------------------
// Command fast path
//
// Obvious requests ("pages 12-15 verbatim", "export as pdf") are understood
// here without any AI call. Anything with words we do not recognise returns
// null so the message goes to the backend command step (Groq) or, failing
// that, to parseChatIntent above. Nothing in this section ever produces study
// content; it only describes what the backend should do.
// ---------------------------------------------------------------------------

export type CommandMode = 'source_passages' | 'both' | 'ai_study_material';
export type CommandExportFormat = 'pdf' | 'docx';
export type RangeSelectorType =
  | 'pages'
  | 'items'
  | 'paragraphs'
  | 'heading'
  | 'chapter'
  | 'whole';

export type ChatCommand = {
  action: 'extract' | 'summarize_whole' | 'export' | 'clarify' | 'help';
  selector?:
    | { type: 'topic'; value: string }
    | { type: RangeSelectorType; value: string };
  mode: CommandMode;
  exportFormat: CommandExportFormat | null;
  question?: string | null;
};

const NUMBER_LIST =
  '\\d{1,5}(?:\\s*(?:-|–|to|through)\\s*\\d{1,5})?(?:\\s*(?:,|and|&)\\s*\\d{1,5}(?:\\s*(?:-|–|to|through)\\s*\\d{1,5})?)*';

const PAGES_RE = new RegExp(`\\b(?:pages?|pgs?|pp\\.?)\\s*(${NUMBER_LIST})`, 'i');
const ITEMS_RE = new RegExp(
  `\\b(?:questions?|qs?|items?|numbers?|nos?\\.?)\\s*(${NUMBER_LIST})`,
  'i',
);
const PARAGRAPHS_RE = new RegExp(`\\bparagraphs?\\s*(${NUMBER_LIST})`, 'i');
const CHAPTER_RE = /\bchapter\s+(\d{1,3}|[ivxlc]{1,6})\b/i;
const WHOLE_RE =
  /\b(?:the\s+)?(?:whole|entire|full|all of the|everything in the)\s+(?:document|material|file|text|notes|pdf)\b|\ball of it\b|\beverything\b/i;
const FORMAT_RE = /\b(pdf|docx|word(?:\s+doc(?:ument)?)?)\b/i;
const EXPORT_RE =
  /^(?:please\s+)?(?:export|download|save|send|share|give)\b/i;

const SUMMARY_WORDS = /\b(?:summar(?:y|ies|ize|ise|ized|ised)|explain|explanation|overview|simplif(?:y|ied))\b/i;
const VERBATIM_WORDS =
  /\b(?:verbatim|word for word|exact(?:ly)?|exactly as (?:written|is)|as written|original(?: text| wording)?|copy|extract|text only|just the text)\b/i;
const SUMMARY_ONLY_WORDS =
  /\b(?:summary only|summaries only|only (?:a |the )?summar(?:y|ies)|just (?:a |the )?summar(?:y|ies)|without (?:the )?(?:original|text))\b/i;
const WITH_SUMMARY_WORDS =
  /\b(?:with (?:a |the )?(?:summary|summaries|explanation)|and (?:a |the )?(?:summary|summaries|explain|explanation)|plus (?:a |the )?(?:summary|explanation)|both)\b/i;

// Words that may remain once the recognised parts are removed.
const FILLER_RE =
  /\b(?:please|pls|can|could|would|you|me|i|want|need|like|to|get|give|show|find|pull|take|out|from|of|in|on|the|a|an|my|this|that|it|its|and|or|also|then|with|without|as|for|all|document|material|file|pdf|docx|word|doc|text|verbatim|exact|exactly|written|original|copy|extract|summary|summaries|summarize|summarise|summarized|explain|explanation|overview|only|just|both|plus|into|export|download|save|send|share|result|results|output|last|previous|format|whole|entire|full|here|up|us|ok|okay|thanks|thank|hi)\b/gi;

function normalizeNumberSpec(raw: string): string {
  return raw
    .replace(/–/g, '-')
    .replace(/\s*(?:to|through)\s*/gi, '-')
    .replace(/\s*(?:and|&)\s*/gi, ', ')
    .replace(/\s*,\s*/g, ', ')
    .replace(/\s*-\s*/g, '-')
    .trim();
}

function detectMode(input: string): CommandMode {
  if (SUMMARY_ONLY_WORDS.test(input)) {
    return 'ai_study_material';
  }
  const wantsSummary = SUMMARY_WORDS.test(input);
  const wantsVerbatim = VERBATIM_WORDS.test(input);
  if (WITH_SUMMARY_WORDS.test(input) || (wantsSummary && wantsVerbatim)) {
    return 'both';
  }
  if (wantsSummary) {
    return 'ai_study_material';
  }
  return 'source_passages';
}

function detectFormat(input: string): CommandExportFormat | null {
  const match = input.match(FORMAT_RE);
  if (!match) {
    return null;
  }
  return match[1].toLowerCase() === 'pdf' ? 'pdf' : 'docx';
}

function hasLeftoverWords(input: string, removed: RegExp[]): boolean {
  let rest = input;
  for (const pattern of removed) {
    rest = rest.replace(pattern, ' ');
  }
  rest = rest.replace(FILLER_RE, ' ').replace(/[^a-z]+/gi, ' ').trim();
  return rest.length > 0;
}

export function parseFastCommand(raw: string): ChatCommand | null {
  const input = raw.trim().replace(/\s+/g, ' ');
  if (!input || input.length > 300) {
    return null;
  }
  const format = detectFormat(input);

  // "export as pdf" / "download it as word" with nothing else to look up.
  if (EXPORT_RE.test(input) && !PAGES_RE.test(input) && !ITEMS_RE.test(input)) {
    if (
      format &&
      !hasLeftoverWords(input, [new RegExp(FORMAT_RE.source, 'gi')])
    ) {
      return {
        action: 'export',
        mode: 'source_passages',
        exportFormat: format,
      };
    }
    if (/^(?:please\s+)?(?:export|download)(?:\s+(?:it|this|that|the result|the last result))?[\s!.]*$/i.test(input)) {
      return { action: 'export', mode: 'source_passages', exportFormat: null };
    }
    return null;
  }

  const mode = detectMode(input);
  const patterns: Array<[RangeSelectorType, RegExp, boolean]> = [
    ['pages', PAGES_RE, true],
    ['items', ITEMS_RE, true],
    ['paragraphs', PARAGRAPHS_RE, true],
    ['chapter', CHAPTER_RE, false],
  ];
  const matched = patterns.filter(([, re]) => re.test(input));
  if (matched.length === 1) {
    const [type, re, numeric] = matched[0];
    const value = input.match(re)![1];
    const removed = [
      new RegExp(re.source, 'gi'),
      new RegExp(FORMAT_RE.source, 'gi'),
    ];
    if (hasLeftoverWords(input, removed)) {
      return null;
    }
    return {
      action: 'extract',
      selector: { type, value: numeric ? normalizeNumberSpec(value) : value },
      mode,
      exportFormat: format,
    };
  }
  if (matched.length > 1) {
    return null;
  }

  if (WHOLE_RE.test(input)) {
    const removed = [
      new RegExp(WHOLE_RE.source, 'gi'),
      new RegExp(FORMAT_RE.source, 'gi'),
    ];
    if (hasLeftoverWords(input, removed)) {
      return null;
    }
    if (mode === 'ai_study_material') {
      return { action: 'summarize_whole', mode, exportFormat: format };
    }
    return {
      action: 'extract',
      selector: { type: 'whole', value: '' },
      mode,
      exportFormat: format,
    };
  }
  return null;
}
