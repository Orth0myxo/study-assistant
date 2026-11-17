export const SUMMARY_SINGLE_CALL_CHARS = 12000;
export const SUMMARY_CHUNK_CHARS = 4000;

/**
 * Approximate the number of AI requests a whole-material summary-only job uses.
 * The backend uses the same single-call threshold and 4,000-character units.
 */
export function estimateSummaryCalls(
  totalChars: number | null | undefined,
): number | null {
  if (typeof totalChars !== 'number' || !Number.isFinite(totalChars) || totalChars <= 0) {
    return null;
  }
  if (totalChars <= SUMMARY_SINGLE_CALL_CHARS) {
    return 1;
  }
  return Math.ceil(totalChars / SUMMARY_CHUNK_CHARS) + 1;
}
