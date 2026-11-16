export function decodeFilename(filename: string): string {
  try {
    return decodeURIComponent(filename);
  } catch {
    return filename;
  }
}
