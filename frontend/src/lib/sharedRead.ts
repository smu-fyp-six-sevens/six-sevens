// One read asked for by several components (switching a case's sections, or React's dev StrictMode mounting twice)
// within a few seconds: one request, one result. A save stores what it returned (`remember`), so the next reader
// sees it rather than an older copy.
const FRESH_MS = 10_000;

const entries = new Map<string, { promise: Promise<unknown>; at: number }>();

export function sharedRead<T>(key: string, load: () => Promise<T>): Promise<T> {
  const entry = entries.get(key);
  if (entry && Date.now() - entry.at < FRESH_MS) return entry.promise as Promise<T>;
  const promise = load();
  entries.set(key, { promise, at: Date.now() });
  // A failed read isn't shared: the next caller tries again.
  promise.catch(() => {
    if (entries.get(key)?.promise === promise) entries.delete(key);
  });
  return promise;
}

export function remember<T>(key: string, value: T): void {
  entries.set(key, { promise: Promise.resolve(value), at: Date.now() });
}

export function forget(key: string): void {
  entries.delete(key);
}
