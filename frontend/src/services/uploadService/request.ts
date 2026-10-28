// A failed request. Keeps the status and FastAPI's parsed detail, logs the raw response body to the console, and picks
// the message the officer sees: what failed, and what they can do about it.
export class RequestError extends Error {
  readonly status: number;
  readonly detail: unknown;

  private constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = 'RequestError';
    this.status = status;
    this.detail = detail;
  }

  // `message` names what failed ("Failed to save case"); `byStatus` replaces the message for one status where the
  // call knows better (a 502 detection report is a crashed run). Status 0 is no response at all (authFetch).
  static async fromResponse(
    response: Response,
    message: string,
    byStatus: Partial<Record<number, string>> = {},
  ): Promise<RequestError> {
    const raw = await response.text();
    console.error(`${message} (${response.status}):`, raw);
    const detail = RequestError.parseDetail(raw);
    const officerMessage = byStatus[response.status] ?? RequestError.pickMessage(response.status, detail, message);
    return new RequestError(officerMessage, response.status, detail);
  }

  private static pickMessage(status: number, detail: unknown, message: string): string {
    // The backend writes these for officers ("cancel it first").
    if ((status === 400 || status === 409) && typeof detail === 'string' && detail) return detail;
    if (status === 401) return 'Your session has expired. Sign in again.';
    if (status === 404) return 'This case or document no longer exists.';
    // 5xx or no response.
    return `${message.replace(/\.$/, '')}. Something went wrong on our side; try again, and tell the team if it keeps happening.`;
  }

  // FastAPI's {"detail": ...}: a sentence, or a validation error's list. Undefined for a body that isn't JSON.
  private static parseDetail(raw: string): unknown {
    try {
      return (JSON.parse(raw) as { detail?: unknown }).detail;
    } catch {
      return undefined;
    }
  }
}

// What every service call runs on its response.
export async function assertOk(
  response: Response,
  message: string,
  byStatus: Partial<Record<number, string>> = {},
): Promise<void> {
  if (!response.ok) throw await RequestError.fromResponse(response, message, byStatus);
}
