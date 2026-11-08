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
