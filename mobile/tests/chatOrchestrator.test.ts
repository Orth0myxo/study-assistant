import { describe, expect, it } from 'vitest';

import {
  CHAT_TOPIC_MAX_CHARS,
  cleanTopicCandidate,
  describeIntent,
  isMaterialOnlyCandidate,
  parseChatIntent,
  parsePageSelection,
} from '@/services/chatOrchestrator';

describe('parseChatIntent', () => {
  it('extracts a named topic from an extract request', () => {
    expect(parseChatIntent('Extract the topic of sickle cell anaemia.')).toEqual({
      kind: 'topic',
      topic: 'sickle cell anaemia',
      generate: false,
    });
  });

  it('supports find, locate and search phrasing', () => {
    expect(parseChatIntent('find sickle cell anaemia')).toEqual({
      kind: 'topic',
      topic: 'sickle cell anaemia',
      generate: false,
    });
    expect(parseChatIntent('search for osmosis')).toEqual({
      kind: 'topic',
      topic: 'osmosis',
      generate: false,
    });
    expect(parseChatIntent('look up photosynthesis')).toEqual({
      kind: 'topic',
      topic: 'photosynthesis',
      generate: false,
    });
  });

  it('extracts topics from questions about the material', () => {
    expect(
      parseChatIntent('What does the material say about osmosis?'),
    ).toEqual({
      kind: 'topic',
      topic: 'osmosis',
      generate: false,
    });
    expect(parseChatIntent('What is photosynthesis?')).toEqual({
      kind: 'topic',
      topic: 'photosynthesis',
      generate: false,
    });
  });

  it('supports explicit topic prefixes', () => {
    expect(parseChatIntent('topic: osmosis')).toEqual({
      kind: 'topic',
      topic: 'osmosis',
      generate: false,
    });
  });

  it('supports conversational request framing', () => {
    expect(parseChatIntent('Can you find sickle cell anaemia?')).toEqual({
      kind: 'topic',
      topic: 'sickle cell anaemia',
      generate: false,
    });
    expect(parseChatIntent('Can you summarize osmosis?')).toEqual({
      kind: 'topic',
      topic: 'osmosis',
      generate: true,
    });
    expect(parseChatIntent('I want to study osmosis')).toEqual({
      kind: 'topic',
      topic: 'osmosis',
      generate: false,
    });
  });

  it('treats a plain short phrase as a topic', () => {
    expect(parseChatIntent('sickle cell anaemia')).toEqual({
      kind: 'topic',
      topic: 'sickle cell anaemia',
      generate: false,
    });
  });

  it('requests generation for summarize and explain phrasing', () => {
    expect(parseChatIntent('Give me a summary of sickle cell anaemia.')).toEqual({
      kind: 'topic',
      topic: 'sickle cell anaemia',
      generate: true,
    });
    expect(parseChatIntent('Explain photosynthesis')).toEqual({
      kind: 'topic',
      topic: 'photosynthesis',
      generate: true,
    });
  });

  it('detects whole-material summary requests', () => {
    expect(parseChatIntent('Summarize the whole document.')).toEqual({
      kind: 'whole_summary',
    });
    expect(parseChatIntent('Give me a summary of the whole material')).toEqual({
      kind: 'whole_summary',
    });
    expect(parseChatIntent('summary')).toEqual({ kind: 'whole_summary' });
    expect(parseChatIntent('Give me a summary of the whole document')).toEqual({
      kind: 'whole_summary',
    });
    expect(parseChatIntent('summary of the material')).toEqual({
      kind: 'whole_summary',
    });
    expect(parseChatIntent('Explain the whole material')).toEqual({
      kind: 'whole_summary',
    });
    expect(parseChatIntent('Teach me the whole document')).toEqual({
      kind: 'whole_summary',
    });
  });

  it('keeps a topic summary when a topic is named', () => {
    expect(parseChatIntent('summary of osmosis')).toEqual({
      kind: 'topic',
      topic: 'osmosis',
      generate: true,
    });
  });

  it('asks for a topic when no topic is named', () => {
    expect(parseChatIntent('extract topics')).toEqual({ kind: 'need_topic' });
    expect(parseChatIntent('another topic')).toEqual({ kind: 'need_topic' });
    expect(parseChatIntent('next topic')).toEqual({ kind: 'need_topic' });
  });

  it('returns help for greetings and usage questions', () => {
    expect(parseChatIntent('hello')).toEqual({ kind: 'help' });
    expect(parseChatIntent('How do I use this?')).toEqual({ kind: 'help' });
    expect(parseChatIntent('show me the material')).toEqual({ kind: 'help' });
  });

  it('does not treat empty input as a topic', () => {
    expect(parseChatIntent('   ')).toEqual({ kind: 'empty' });
  });

  it('rejects unsupported questions instead of inventing a topic', () => {
    expect(parseChatIntent('Why is the sky blue?')).toEqual({
      kind: 'unsupported',
    });
  });

  it('truncates an over-long topic to the backend limit', () => {
    const intent = parseChatIntent(`extract topic of ${'a'.repeat(400)}`);
    expect(intent.kind).toBe('topic');
    if (intent.kind === 'topic') {
      expect(intent.topic.length).toBe(CHAT_TOPIC_MAX_CHARS);
    }
  });
});

describe('parsePageSelection', () => {
  it('collects all page references used by verbatim requests', () => {
    expect(parsePageSelection('page 1 to page 2')).toEqual([
      [1, 1],
      [2, 2],
    ]);
    expect(
      parsePageSelection('show me the original text on page 1 and page 2'),
    ).toEqual([
      [1, 1],
      [2, 2],
    ]);
    expect(parsePageSelection('pages 1, 2 and 4')).toEqual([
      [1, 1],
      [2, 2],
      [4, 4],
    ]);
    expect(parsePageSelection('pages 1, 2, and 4')).toEqual([
      [1, 1],
      [2, 2],
      [4, 4],
    ]);
    expect(parsePageSelection('pages 12 through 15')).toEqual([[12, 15]]);
  });

  it('returns no selection for text without page references', () => {
    expect(parsePageSelection('extract verbatim text')).toEqual([]);
  });
});

describe('cleanTopicCandidate', () => {
  it('removes quotes, trailing punctuation and pleasantries', () => {
    expect(cleanTopicCandidate(' "The Great Gatsby" please ')).toBe(
      'The Great Gatsby',
    );
  });

  it('removes a leading topic-of phrase', () => {
    expect(cleanTopicCandidate('the topic of osmosis')).toBe('osmosis');
  });

  it('detects material-only candidates', () => {
    expect(isMaterialOnlyCandidate('the whole document')).toBe(true);
    expect(isMaterialOnlyCandidate('sickle cell anaemia')).toBe(false);
  });
});

describe('describeIntent', () => {
  it('describes topic requests', () => {
    expect(
      describeIntent({ kind: 'topic', topic: 'osmosis', generate: false }),
    ).toBe('Find "osmosis" in the material.');
    expect(
      describeIntent({ kind: 'topic', topic: 'osmosis', generate: true }),
    ).toBe('Extract and summarize "osmosis".');
  });
});
