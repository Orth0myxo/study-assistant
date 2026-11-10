import type {
  ChatCommand,
  CommandExportFormat,
  CommandMode,
  RangeSelectorType,
} from '@/services/chatOrchestrator';

const ACTIONS = ['extract', 'summarize_whole', 'export', 'clarify', 'help'];
const MODES: CommandMode[] = ['source_passages', 'both', 'ai_study_material'];
const SELECTOR_KEYS = [
  'topic',
  'pages',
  'items',
  'chapter',
  'heading',
  'paragraphs',
] as const;

/** Convert the backend's snake_case command into the app's ChatCommand. */
export function normalizeCommand(raw: unknown): ChatCommand | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const data = raw as Record<string, unknown>;
  const action = data.action;
  if (typeof action !== 'string' || !ACTIONS.includes(action)) {
    return null;
  }
  const mode = MODES.includes(data.mode as CommandMode)
    ? (data.mode as CommandMode)
    : 'source_passages';
  const format =
    data.export_format === 'pdf' || data.export_format === 'docx'
      ? (data.export_format as CommandExportFormat)
      : null;
  const command: ChatCommand = {
    action: action as ChatCommand['action'],
    mode,
    exportFormat: format,
    question: typeof data.question === 'string' ? data.question : null,
  };
  const selector = data.selector as Record<string, unknown> | null | undefined;
  if (selector && typeof selector === 'object') {
    const type = selector.type;
    if (type === 'whole') {
      command.selector = { type: 'whole', value: '' };
    } else if (typeof type === 'string' && (SELECTOR_KEYS as readonly string[]).includes(type)) {
      const value = selector[type];
      if (typeof value === 'string' && value.trim()) {
        command.selector = { type, value: value.trim() } as ChatCommand['selector'];
      }
    }
  }
  if (command.action === 'extract' && !command.selector) {
    return null;
  }
  return command;
}

export type CommandPlan =
  | { kind: 'say'; text: string; tone?: 'info' | 'warn' }
  | { kind: 'topic'; topic: string; generateMode: CommandMode | null }
  | { kind: 'whole_summary' }
  | {
      kind: 'range';
      selectorType: RangeSelectorType;
      spec: string | null;
      mode: CommandMode;
      thenExport: CommandExportFormat | null;
    }
  | { kind: 'export'; format: CommandExportFormat | null };

export const HELP_TEXT =
  'You can ask for exact text by page (“pages 12-15 verbatim”), by numbered question (“questions 5-10”), by chapter, or by topic. Add “with summary” or “summary only” if you want an explanation, then say “export as PDF” or “export as Word”.';

/** Decide what to do with a command. Pure, so it is easy to test. */
export function planCommand(
  command: ChatCommand,
  state: { hasDocument: boolean; hasLastResult: boolean },
): CommandPlan {
  switch (command.action) {
    case 'help':
      return { kind: 'say', text: HELP_TEXT };
    case 'clarify':
      return {
        kind: 'say',
        text: command.question?.trim() || 'Could you tell me which pages or topic you want?',
      };
    case 'export':
      if (!state.hasLastResult) {
        return {
          kind: 'say',
          text: 'There is nothing to export yet. Ask for some text or a summary first.',
          tone: 'warn',
        };
      }
      return { kind: 'export', format: command.exportFormat };
    case 'summarize_whole':
      return state.hasDocument
        ? { kind: 'whole_summary' }
        : { kind: 'say', text: 'Attach a PDF or DOCX first.', tone: 'warn' };
    case 'extract': {
      if (!state.hasDocument) {
        return { kind: 'say', text: 'Attach a PDF or DOCX first.', tone: 'warn' };
      }
      const selector = command.selector;
      if (!selector) {
        return { kind: 'say', text: 'Tell me which pages or topic you want.' };
      }
      if (selector.type === 'topic') {
        return {
          kind: 'topic',
          topic: selector.value,
          generateMode: command.mode === 'source_passages' ? null : command.mode,
        };
      }
      if (selector.type === 'whole' && command.mode === 'ai_study_material') {
        return { kind: 'whole_summary' };
      }
      return {
        kind: 'range',
        selectorType: selector.type,
        spec: selector.value || null,
        mode: command.mode,
        thenExport: command.exportFormat,
      };
    }
  }
}
