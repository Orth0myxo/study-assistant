import type { AgentBlock } from '@/services/apiClient';

export type AgentBlockPresentation = {
  title: string;
  body: string;
  basis?: string;
  action?: { kind: 'result' | 'file'; id: string; name?: string };
  options: string[];
};

export function presentAgentBlock(block: AgentBlock): AgentBlockPresentation {
  switch (block.type) {
    case 'passages':
      return {
        title: 'Original passages',
        body: '',
        action: block.result_id
          ? { kind: 'result', id: block.result_id }
          : undefined,
        options: [],
      };
    case 'summary':
      return {
        title: block.label || 'AI summary',
        body: block.text || '',
        basis: block.basis,
        options: [],
      };
    case 'file':
      return {
        title: block.name || 'Study file',
        body: block.format?.toUpperCase() || 'FILE',
        action: block.file_id
          ? { kind: 'file', id: block.file_id, name: block.name }
          : undefined,
        options: [],
      };
    case 'question':
      return {
        title: 'One detail needed',
        body: block.text || '',
        options: Array.isArray(block.options) ? block.options : [],
      };
    case 'approval':
      return {
        title: block.label || 'Allow AI reading?',
        body: block.text || '',
        options: [],
      };
    case 'ai_read':
      return {
        title: block.label || `AI-read transcription${block.page ? ` · Page ${block.page}` : ''}`,
        body: block.text || '',
        options: [],
      };
  }
}
