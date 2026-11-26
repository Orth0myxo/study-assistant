import { describe, expect, it } from 'vitest';

import {
  conversionDisabledReason,
  groupConversionOptions,
  inputLimitLine,
  outputLabel,
  preselectConversion,
} from '@/services/fileConverterLayout';
import type { ConversionOption } from '@/types/api';

function option(
  conversion_id: string,
  output_kind: ConversionOption['output_kind'],
  needs_ai = false,
  output_label?: string,
): ConversionOption {
  return {
    conversion_id,
    input_kind: 'pdf',
    output_kind,
    label: conversion_id,
    description: `${conversion_id} description`,
    output_label,
    needs_ai,
    input_mode: 'file',
  };
}

describe('file converter layout', () => {
  it('groups non-AI options before an AI section', () => {
    const groups = groupConversionOptions([
      option('summary-pdf-to-xlsx', 'xlsx', true),
      option('pdf-to-docx', 'docx'),
      option('summary-pdf-to-docx', 'docx', true),
    ]);

    expect(groups.map((group) => group.key)).toEqual(['standard', 'ai']);
    expect(groups[0].title).toBeNull();
    expect(groups[0].options.map((item) => item.conversion_id)).toEqual([
      'pdf-to-docx',
    ]);
    expect(groups[1].title).toBe('Uses AI');
    expect(groups[1].options.map((item) => item.conversion_id)).toEqual([
      'summary-pdf-to-xlsx',
      'summary-pdf-to-docx',
    ]);
  });

  it('preselects only when there is one option or the current one remains', () => {
    const only = option('text-to-pdf', 'pdf');
    expect(preselectConversion([only])).toBe(only);

    const first = option('pdf-to-docx', 'docx');
    const second = option('summary-pdf-to-xlsx', 'xlsx', true);
    expect(preselectConversion([first, second])).toBeNull();
    expect(preselectConversion([first, second], second.conversion_id)).toBe(
      second,
    );
  });

  it('reports the first missing input before a missing output', () => {
    const selected = option('pdf-to-docx', 'docx');
    expect(
      conversionDisabledReason({
        inputKind: 'pdf',
        selected,
        hasFile: false,
        text: '',
        aiConsent: false,
      }),
    ).toBe('Choose a file');
    expect(
      conversionDisabledReason({
        inputKind: 'text',
        selected: null,
        hasFile: false,
        text: '  ',
        aiConsent: false,
      }),
    ).toBe('Paste some text');
    expect(
      conversionDisabledReason({
        inputKind: 'pdf',
        selected: null,
        hasFile: true,
        text: '',
        aiConsent: false,
      }),
    ).toBe('Choose an output');
    expect(
      conversionDisabledReason({
        inputKind: 'pdf',
        selected: option('summary-pdf-to-xlsx', 'xlsx', true),
        hasFile: true,
        text: '',
        aiConsent: false,
      }),
    ).toBe('Confirm the AI notice');
    expect(
      conversionDisabledReason({
        inputKind: 'pdf',
        selected,
        hasFile: true,
        text: '',
        aiConsent: false,
      }),
    ).toBeNull();
  });

  it('uses the output label and input limits', () => {
    expect(outputLabel(option('pdf-to-docx', 'docx', false, 'Word'))).toBe(
      'Word',
    );
    expect(outputLabel(option('pdf-to-docx', 'docx'))).toBe('Word');
    expect(
      inputLimitLine('pdf', 'PDF', {
        max_file_mb: 25,
        max_rows: 5000,
        max_columns: 200,
        max_cell_chars: 5000,
        max_pages: 100,
      }),
    ).toBe('PDF · up to 25 MB, 100 pages');
    expect(
      inputLimitLine('xlsx', 'Excel workbook', {
        max_file_mb: 25,
        max_rows: 5000,
        max_columns: 200,
        max_cell_chars: 5000,
        max_pages: 100,
      }),
    ).toBe('Excel workbook · up to 25 MB, 5,000 rows, 200 columns');
  });
});
