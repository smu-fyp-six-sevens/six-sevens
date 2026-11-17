import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { MockBackend } from '../../../e2e/support/mockBackend';

// Every route the backend publishes (openapi.json) is either answered by the Playwright mock or listed here. A new
// backend route fails this test until it is mocked or listed, so the specs can't keep passing against a mock that
// has quietly fallen behind. Delete a line when its route gets a mock (the test fails if it stays after that).
const NOT_MOCKED = new Set<string>([
  // The page calls these, but no spec exercises them yet (missed issues, editing and deleting a review, the prompt view).
  'POST /cases/{case_id}/finding-reviews/missed',
  'PATCH /cases/{case_id}/finding-reviews/{review_id}',
  'DELETE /cases/{case_id}/finding-reviews/{review_id}',
  'GET /agents/{agent_id}/prompt',
  // Not called by the page: probes, the dev tools, and the case match, which arrives in the case snapshot.
  'GET /health',
  'GET /health/db',
  'GET /healthz',
  'GET /api/dev/documents',
  'GET /api/dev/documents/{document_id}/fields',
  'POST /api/dev/inject',
  'GET /cases/{case_id}/case-match',
]);

const operations: string[] = Object.entries(
  (JSON.parse(
    readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../openapi.json'), 'utf8'),
  ) as { paths: Record<string, Record<string, unknown>> }).paths,
).flatMap(([route, methods]) =>
  Object.keys(methods)
    .map((method) => method.toUpperCase())
    .filter((method) => ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method))
    .map((method) => `${method} ${route}`),
);

/** Sends one request through the mock's handler without a browser; true when no mock answered it. */
async function isUnmocked(operation: string): Promise<boolean> {
  const [method, route] = operation.split(' ');
  const url = `http://api.test${route.replace(/\{[^}]+\}/g, 'x')}`;
  const backend = new MockBackend();
  const fakeRoute = {
    // The CSRF header the page sends on every call (lib/api.ts), so the mock's own check lets the request through.
    request: () => ({
      url: () => url, method: () => method, postDataJSON: () => ({}), postData: () => '{}',
      headers: () => ({ 'x-requested-with': 'fetch' }),
    }),
    fulfill: async () => undefined,
  };
  try {
    await (backend as unknown as { handle(route: unknown): Promise<void> }).handle(fakeRoute);
  } catch {
    // A handler that matched and then choked on an empty body is still a handler.
  }
  return backend.unmockedRequests.length > 0;
}

describe('the Playwright mock serves every backend route', () => {
  it('has a mock for each route, or lists it as not mocked', async () => {
    const unmocked: string[] = [];
    for (const operation of operations) {
      if (await isUnmocked(operation)) unmocked.push(operation);
    }
    expect(unmocked.sort()).toEqual([...NOT_MOCKED].sort());
  });
});
