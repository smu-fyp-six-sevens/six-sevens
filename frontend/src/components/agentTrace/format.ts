export function formatSeconds(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export const formatCost = (usd: number) => `$${usd.toFixed(usd < 0.01 ? 4 : 3)}`;

export const formatTokens = (tokens: number) =>
  tokens >= 1000 ? `${(tokens / 1000).toFixed(tokens >= 10000 ? 0 : 1)}k` : String(tokens);

// Slow spans stand out, as in most trace viewers.
export const durationTone = (seconds: number) => (seconds >= 5 ? 'slow' : seconds >= 2 ? 'medium' : 'fast');

export function prettyJson(value: unknown): string {
  if (typeof value !== 'string') return JSON.stringify(value, null, 2);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}
