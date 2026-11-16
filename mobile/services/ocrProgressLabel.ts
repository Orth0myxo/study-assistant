import type { OcrProgressResponse } from '@/types/api';

export function ocrProgressLabel(
  progress: OcrProgressResponse,
  filename: string,
): string {
  const currentPage = progress.pending_pages[0];
  if (progress.status === 'pending' && currentPage && progress.total_pages > 0) {
    return `Reading page ${currentPage} of ${progress.total_pages} · ${filename}`;
  }
  return `${progress.completed_pages} of ${progress.needs_ocr_pages} scanned pages read · ${filename}`;
}
