export interface PendingRequestQueue<T> {
  current: T | null;
}

export function takePendingRequest<T>(
  queue: PendingRequestQueue<T>,
  ready: boolean,
): T | null {
  if (!ready || queue.current === null) {
    return null;
  }
  const pending = queue.current;
  queue.current = null;
  return pending;
}
