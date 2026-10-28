// A failed request. `message` is what the officer sees: what failed and what they can do about it. The status and
// the parsed FastAPI detail stay on the error, and the raw response body goes to the console.
export class RequestError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = 'RequestError';
    this.status = status;
    this.detail = detail;
  }
}

// Throws a RequestError unless the response is ok. `message` names what failed ("Failed to save case"); `byStatus`
// replaces the message for one status where the call knows better (a 502 detection report is a crashed run).
export async function assertOk(
  response: Response,
  message: string,
  byStatus: Partial<Record<number, string>> = {},
): Promise<void> {
  if (response.ok) return;
  const raw = await response.text();
  console.error(`${message} (${response.status}):`, raw);
  const detail = parsedDetail(raw);
  throw new RequestError(byStatus[response.status] ?? officerMessage(response.status, detail, message), response.status, detail);
}

function officerMessage(status: number, detail: unknown, message: string): string {
  // The backend writes these for officers ("Cancel it first", "already approved").
  if ((status === 400 || status === 409) && typeof detail === 'string' && detail) return detail;
  if (status === 401) return 'Your session has expired. Sign in again.';
  if (status === 404) return 'This case or document no longer exists.';
  return `${couldnt(message)} Something went wrong on our side. Try again, and tell the team if it keeps happening.`;
}

// "Failed to fetch the case" as "Couldn't load the case.": what failed, as the officer would say it.
function couldnt(message: string): string {
  const text = message
    .replace(/\.$/, '')
    .replace(/^Failed to (fetch|get)\b/, "Couldn't load")
    .replace(/^Failed to /, "Couldn't ");
  return `${text}.`;
}

// FastAPI's {"detail": ...}: a sentence, or a validation error's list. Undefined for a body that isn't JSON.
function parsedDetail(raw: string): unknown {
  try {
    return (JSON.parse(raw) as { detail?: unknown }).detail;
  } catch {
    return undefined;
  }
}
