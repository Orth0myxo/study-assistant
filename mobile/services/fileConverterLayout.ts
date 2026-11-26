import type {
  ConversionInputKind,
  ConversionLimits,
  ConversionOption,
} from '@/types/api';

export type ConversionGroup = {
  key: 'standard' | 'ai';
  title: string | null;
  options: ConversionOption[];
};

const OUTPUT_LABELS: Record<ConversionOption['output_kind'], string> = {
  xlsx: 'Excel',
  docx: 'Word',
  pdf: 'PDF',
  csv: 'CSV',
};

export function outputLabel(option: ConversionOption): string {
  return option.output_label?.trim() || OUTPUT_LABELS[option.output_kind];
}

export function groupConversionOptions(
  options: ConversionOption[],
): ConversionGroup[] {
  const standard = options.filter((option) => !option.needs_ai);
  const ai = options.filter((option) => option.needs_ai);
  const groups: ConversionGroup[] = [];
  if (standard.length > 0) {
    groups.push({ key: 'standard', title: null, options: standard });
  }
  if (ai.length > 0) {
    groups.push({ key: 'ai', title: 'Uses AI', options: ai });
  }
  return groups;
}

export function preselectConversion(
  options: ConversionOption[],
  currentId?: string | null,
): ConversionOption | null {
  const current = currentId
    ? options.find((option) => option.conversion_id === currentId)
    : null;
  if (current) return current;
  return options.length === 1 ? options[0] : null;
}

export function conversionDisabledReason(input: {
  inputKind: ConversionInputKind;
  selected: ConversionOption | null;
  hasFile: boolean;
  text: string;
  aiConsent: boolean;
}): string | null {
  const mode =
    input.selected?.input_mode ?? (input.inputKind === 'text' ? 'text' : 'file');
  if (mode === 'text' && !input.text.trim()) return 'Paste some text';
  if (mode === 'file' && !input.hasFile) return 'Choose a file';
  if (!input.selected) return 'Choose an output';
  if (input.selected.needs_ai && !input.aiConsent) {
    return 'Confirm the AI notice';
  }
  return null;
}

function formatCount(value: number): string {
  return value.toLocaleString('en-US');
}

export function inputLimitLine(
  kind: ConversionInputKind,
  label: string,
  limits: ConversionLimits,
): string {
  if (kind === 'text') {
    return `${label} · up to 200,000 characters`;
  }
  if (kind === 'csv' || kind === 'xlsx') {
    return `${label} · up to ${limits.max_file_mb} MB, ${formatCount(limits.max_rows)} rows, ${limits.max_columns} columns`;
  }
  return `${label} · up to ${limits.max_file_mb} MB, ${limits.max_pages} pages`;
}
