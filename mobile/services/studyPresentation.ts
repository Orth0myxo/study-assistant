import { StudyItem } from '@/types/api';

export const VERBATIM_SOURCE_LABEL = 'Verbatim source text';

export function verbatimSourceText(item: StudyItem): string | null {
  if (typeof item.text !== 'string' || item.text.trim().length === 0) {
    return null;
  }
  return item.text;
}

export function studySectionsForDisplay(items: StudyItem[]): StudyItem[] {
  return items.filter(
    (item) => Boolean(item.summary) || verbatimSourceText(item) !== null,
  );
}
