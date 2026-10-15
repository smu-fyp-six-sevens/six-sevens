import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import type { CaseRisk } from '../../src/services/uploadService/types';

/**
 * Each document type's required extraction fields, exported from the
 * backend's EXTRACTION_SCHEMAS (kept in sync by
 * backend/tests/test_regression_fixtures.py), so approval is blocked by
 * exactly the same rule as routers/documents.py.
 */
const REQUIRED_FIELDS: Record<
  string,
  { required: string[]; allow_empty: string[]; required_when_individual: string[] }
> = JSON.parse(
  readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../backend/tests/fixtures/regression/required_fields.json'),
    'utf8',
  ),
);

/** Same rule as backend case_service.missing_required_fields. */
export function missingRequiredFields(document: { document_type: string | null; extracted_fields: Record<string, unknown> | null }): string[] {
  const fields = document.extracted_fields ?? {};
  const schema = REQUIRED_FIELDS[document.document_type ?? ''] ?? {
    required: [],
    allow_empty: [],
    required_when_individual: [],
  };
  const unwrap = (raw: unknown): unknown =>
    raw && typeof raw === 'object' && 'value' in (raw as object) ? (raw as { value: unknown }).value : raw;
  const isEmpty = (value: unknown): boolean => {
    if (value === null || value === undefined) return true;
    if (typeof value === 'string') return !value.trim();
    if (Array.isArray(value)) return value.length === 0;
    if (typeof value === 'object') return Object.keys(value as object).length === 0;
    return false;
  };
  const hasValue = (name: string) => !isEmpty(unwrap(fields[name]));
  const individualAccount =
    schema.required_when_individual.length > 0 && hasValue('account_holder_name') && !hasValue('uen');
  const required = individualAccount ? schema.required_when_individual : schema.required;
  return [...required].sort().filter((name) => {
    if (!(name in fields)) return true;
    const value = unwrap(fields[name]);
    if (Array.isArray(value) && value.length === 0) return !schema.allow_empty.includes(name);
    return isEmpty(value);
  });
}

/**
 * A fake backend for one test: an in-memory "database" plus handlers for
 * every endpoint the frontend calls (see src/services/* and src/lib/api.ts).
 * The app is started with VITE_API_URL=http://api.test (playwright.config.ts),
 * so nothing here ever reaches the real FastAPI backend, Supabase or Dify.
 *
 * Anything a test didn't expect answers 599 and is recorded in
 * `unmockedRequests`; the shared fixture in test.ts fails the test if that
 * list isn't empty, so a missing mock can't silently pass.
 */

export const API_URL = 'http://api.test';

export interface CaseRow {
  id: string;
  greenlink_case_id: string;
  company_name: string | null;
  status: string;
  submission_finalized: boolean;
  finalized_at: string | null;
  recommended_additional_documents: string[];
  created_at: string;
  updated_at: string;
  dev: boolean;
  documents_status: string;
  fulfilled_requirements: string[];
  proof_of_address_coverage: { covered: number; total: number; unmatched_names: string[] };
  id_document_coverage: { covered: number; total: number; missing_side: string | null };
  // Computed by backend/app/case_risk.py on every case read.
  risk: CaseRisk | null;
  // Computed by backend/app/supporting_documents.py on every case read.
  supporting_document_recommendations: unknown[];
}

export interface DocumentRow {
  id: string;
  case_id: string | null;
  filename: string;
  document_type: string | null;
  classification_confidence: number | null;
  routing_status: 'accepted' | 'manual_review' | null;
  storage_path: string | null;
  status: string;
  retry_count: number;
  uploaded_at: string;
  queued_at: string | null;
  processing_started_at: string | null;
  failure_reason: string | null;
  evidence: string[];
  fulfilled_requirements: string[];
  extracted_fields: Record<string, unknown> | null;
  extraction_confidence: number | null;
  escalation_reason: string | null;
  metadata: Record<string, unknown> | null;
}

export interface UploadOutcome {
  success: boolean;
  error?: string;
}

export interface RecordedRequest {
  method: string;
  path: string;
  body: unknown;
}

const ISO = '2026-09-01T00:00:00Z';

export function makeCase(overrides: Partial<CaseRow> = {}): CaseRow {
  const id = overrides.id ?? `case-${Math.random().toString(36).slice(2, 10)}`;
  return {
    id,
    greenlink_case_id: overrides.greenlink_case_id ?? `GLDB-${Math.floor(Math.random() * 9000) + 1000}`,
    company_name: 'Harbor Light Shipping Pte Ltd',
    status: 'open',
    submission_finalized: false,
    finalized_at: null,
    recommended_additional_documents: [],
    created_at: ISO,
    updated_at: ISO,
    dev: true,
    documents_status: 'no_documents',
    fulfilled_requirements: [],
    proof_of_address_coverage: { covered: 0, total: 0, unmatched_names: [] },
    id_document_coverage: { covered: 0, total: 0, missing_side: null },
    risk: makeRisk(),
    supporting_document_recommendations: [],
    ...overrides,
  };
}

/** Defaults to what the backend reports for a case with nothing to scan yet. */
export function makeRisk(overrides: Partial<CaseRisk> = {}): CaseRisk {
  return {
    state: 'no_documents',
    level: null,
    score: null,
    points: null,
    contributing_findings: [],
    unassessed_checks: [],
    reasons: [],
    documents_scanned: 0,
    documents_total: 0,
    ...overrides,
  };
}

export function makeDocument(overrides: Partial<DocumentRow> = {}): DocumentRow {
  return {
    id: `doc-${Math.random().toString(36).slice(2, 10)}`,
    case_id: null,
    filename: 'document.pdf',
    document_type: null,
    classification_confidence: null,
    routing_status: null,
    storage_path: 'placeholder/path.pdf',
    status: 'queued',
    retry_count: 0,
    uploaded_at: ISO,
    queued_at: ISO,
    processing_started_at: null,
    failure_reason: null,
    evidence: [],
    fulfilled_requirements: [],
    extracted_fields: null,
    extraction_confidence: null,
    escalation_reason: null,
    metadata: null,
    ...overrides,
  };
}

export class MockBackend {
  cases: CaseRow[] = [];
  documents: DocumentRow[] = [];
  /** Case-match result per case id, sent in the WebSocket snapshot. */
  caseMatch: Record<string, unknown> = {};
  /** Fraud-detection report per document id (GET/POST .../detection/...); none = 404. */
  detectionReports: Record<string, unknown> = {};
  /** LangGraph pipeline trace per document id (GET .../langgraph-trace); none = 404. */
  traces: Record<string, unknown> = {};
  /** Per-filename upload results; files not listed here upload successfully. */
  uploadOutcomes: Record<string, UploadOutcome> = {};
  /** When set, upload-batch waits for this before answering (to observe "Uploading"). */
  uploadGate: Promise<void> | null = null;
  /** Credentials /auth/login accepts. */
  credentials = { username: 'officer', password: 'correct-password' };

  requests: RecordedRequest[] = [];
  unmockedRequests: string[] = [];

  private sockets = new Map<string, WebSocketRoute>();

  async install(page: Page): Promise<void> {
    await page.route(`${API_URL}/**`, (route) => this.handle(route));
    await page.routeWebSocket(/\/ws\/cases\//, (ws) => {
      const caseId = decodeURIComponent(new URL(ws.url()).pathname.split('/').pop() ?? '');
      this.sockets.set(caseId, ws);
      ws.send(JSON.stringify(this.snapshot(caseId)));
      // caseSocket.ts's refresh() sends a bare text frame asking for a snapshot.
      ws.onMessage(() => ws.send(JSON.stringify(this.snapshot(caseId))));
    });
  }

  /** Push a fresh snapshot to the open case page, e.g. after changing a document's status. */
  push(caseId: string): void {
    this.sockets.get(caseId)?.send(JSON.stringify(this.snapshot(caseId)));
  }

  requestsTo(method: string, path: string | RegExp): RecordedRequest[] {
    return this.requests.filter(
      (r) => r.method === method && (typeof path === 'string' ? r.path === path : path.test(r.path)),
    );
  }

  private snapshot(caseId: string) {
    const found = this.cases.find((c) => c.id === caseId);
    return {
      type: 'snapshot',
      case: found ?? null,
      documents: this.documents.filter((d) => d.case_id === caseId),
      case_match: this.caseMatch[caseId] ?? null,
    };
  }

  private async handle(route: Route): Promise<void> {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;

    if (method === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: corsHeaders() });
    }

    let body: unknown = null;
    try {
      body = request.postDataJSON();
    } catch {
      body = request.postData();
    }
    this.requests.push({ method, path: path + url.search, body });

    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, headers: corsHeaders(), contentType: 'application/json', body: JSON.stringify(data) });

    // --- auth -------------------------------------------------------------
    if (method === 'POST' && path === '/auth/login') {
      const { username, password } = (body ?? {}) as { username?: string; password?: string };
      if (username === this.credentials.username && password === this.credentials.password) {
        return json({ token: 'test-token' });
      }
      return json({ detail: 'Invalid username or password' }, 401);
    }
    if (method === 'POST' && path === '/ws/tickets') {
      return json({ ticket: 'test-ticket' });
    }

    // --- cases ------------------------------------------------------------
    if (path === '/cases' && method === 'GET') {
      return json(this.cases);
    }
    if (path === '/cases' && method === 'POST') {
      const companyName = (body as { company_name?: string } | null)?.company_name ?? null;
      const next = this.cases.length + 1;
      const created = makeCase({
        id: `case-new-${next}`,
        greenlink_case_id: `GLDB-${9000 + next}`,
        company_name: companyName,
      });
      this.cases.push(created);
      return json(created, 201);
    }
    const caseMatch = path.match(/^\/cases\/([^/]+)$/);
    if (caseMatch) {
      const found = this.cases.find((c) => c.id === caseMatch[1]);
      if (!found) return json({ detail: 'Case not found' }, 404);
      if (method === 'GET') return json(found);
      if (method === 'PATCH') {
        found.company_name = (body as { company_name: string }).company_name;
        return json(found);
      }
      if (method === 'DELETE') {
        this.cases = this.cases.filter((c) => c.id !== found.id);
        return route.fulfill({ status: 204, headers: corsHeaders() });
      }
    }

    // --- documents --------------------------------------------------------
    if (method === 'POST' && path === '/documents/upload-batch') {
      if (this.uploadGate) await this.uploadGate;
      const multipart = request.postDataBuffer()?.toString('latin1') ?? '';
      const caseId = multipart.match(/name="case_id"\r\n\r\n([^\r]*)/)?.[1] ?? null;
      const filenames = [...multipart.matchAll(/name="files"; filename="([^"]*)"/g)].map((m) => m[1]);
      const results = filenames.map((filename) => {
        const outcome = this.uploadOutcomes[filename] ?? { success: true };
        if (!outcome.success) {
          return { filename, success: false, error: outcome.error ?? 'Upload failed' };
        }
        const document = makeDocument({ case_id: caseId, filename });
        this.documents.push(document);
        return { filename, success: true, document_id: document.id, status: document.status };
      });
      return json({ results });
    }
    if (method === 'GET' && path === '/documents') {
      const caseId = url.searchParams.get('case_id');
      return json(this.documents.filter((d) => d.case_id === caseId));
    }
    const documentMatch = path.match(/^\/documents\/([^/]+)(\/.*)?$/);
    if (documentMatch) {
      const [, documentId, rest = ''] = documentMatch;
      const document = this.documents.find((d) => d.id === documentId);
      if (!document) return json({ detail: 'Document not found' }, 404);
      if (method === 'GET' && rest === '') return json({ ...document, missing_required_fields: missingRequiredFields(document) });
      if (method === 'DELETE' && rest === '') {
        this.documents = this.documents.filter((d) => d.id !== documentId);
        return route.fulfill({ status: 204, headers: corsHeaders() });
      }
      if (method === 'POST' && rest === '/approve') {
        if (document.status !== 'analyzed') {
          return json({ detail: 'Only completed documents can be approved.' }, 400);
        }
        const missing = missingRequiredFields(document);
        if (missing.length > 0) {
          return json({ detail: `Cannot approve document. Required fields are missing or empty: ${missing.join(', ')}` }, 400);
        }
        document.status = 'approved';
        return json(document);
      }
      if (method === 'POST' && rest === '/reject') {
        document.status = 'rejected';
        return json(document);
      }
      if (method === 'PATCH' && rest === '/extracted-fields') {
        // Same rule as routers/documents.py's update_extracted_fields: a
        // field the officer could edit (empty, below the 0.9 threshold, or
        // any field on an unrecognised document) becomes human-verified
        // (1.0); every other field keeps its original confidence.
        const submitted = (body as { extracted_fields: Record<string, unknown> }).extracted_fields;
        const previous = (document.extracted_fields ?? {}) as Record<
          string,
          { id?: string; value?: unknown; confidence?: number | null }
        >;
        const merged: Record<string, unknown> = {};
        for (const [name, value] of Object.entries(submitted)) {
          const old = previous[name];
          const oldValue = old?.value == null ? '' : String(old.value).trim();
          const wasEditable =
            !old ||
            document.document_type === 'unrecognised' ||
            oldValue === '' ||
            (old.confidence != null && old.confidence < 0.9);
          merged[name] = { id: old?.id, value, confidence: wasEditable ? 1.0 : old.confidence ?? null };
        }
        document.extracted_fields = merged;
        document.extraction_confidence = 1.0;
        document.routing_status = 'accepted';
        return json(document);
      }
      if (method === 'POST' && rest === '/extracted-fields') {
        const { field_name, field_value } = body as { field_name: string; field_value: unknown };
        document.extracted_fields = {
          ...(document.extracted_fields ?? {}),
          [field_name]: { id: `field-${field_name}`, value: field_value, confidence: 1.0 },
        };
        return json(document);
      }
      if (method === 'DELETE' && rest.startsWith('/extracted-fields/')) {
        const fieldId = rest.slice('/extracted-fields/'.length);
        const remaining: Record<string, unknown> = {};
        for (const [name, field] of Object.entries(document.extracted_fields ?? {})) {
          if ((field as { id?: string }).id !== fieldId) remaining[name] = field;
        }
        document.extracted_fields = remaining;
        return json(document);
      }
      if (method === 'PATCH' && rest === '/document-type') {
        document.document_type = (body as { document_type: string }).document_type;
        document.routing_status = 'accepted';
        document.classification_confidence = 1.0;
        document.status = 'extracting';
        return json(document);
      }
      // Fraud-detection report and LangGraph trace: the real backend answers
      // 404 until the document has one, which the page shows as "none yet".
      if (method === 'GET' && rest === '/detection/report') {
        const report = this.detectionReports[documentId];
        return report ? json(report) : json({ detail: 'No detection report' }, 404);
      }
      if (method === 'GET' && rest === '/langgraph-trace') {
        const trace = this.traces[documentId];
        return trace ? json(trace) : json({ detail: 'No trace' }, 404);
      }
      // Same rules as routers/documents.py: retry a failed document back
      // into the queue; cancel one that's still in flight.
      if (method === 'POST' && rest === '/retry') {
        if (document.status !== 'failed') {
          return json({ detail: 'Only failed or stuck documents can be retried.' }, 409);
        }
        document.status = 'queued';
        document.failure_reason = null;
        return json(document);
      }
      if (method === 'POST' && rest === '/cancel') {
        if (!['queued', 'processing', 'extracting'].includes(document.status)) {
          return json({ detail: 'Document can only be cancelled while queued, processing, or extracting.' }, 409);
        }
        document.status = 'cancelled';
        return json(document);
      }
      if (method === 'GET' && rest === '/detection/case-match') {
        const result = this.caseMatch[document.case_id ?? ''] as
          | { documents?: { document_id: string }[] }
          | undefined;
        const entry = result?.documents?.find((d) => d.document_id === documentId);
        return entry ? json(entry) : json({ detail: 'No case match' }, 404);
      }
    }

    this.unmockedRequests.push(`${method} ${path}${url.search}`);
    return route.fulfill({ status: 599, headers: corsHeaders(), body: `Unmocked request: ${method} ${path}` });
  }
}

function corsHeaders(): Record<string, string> {
  return {
    'access-control-allow-origin': '*',
    'access-control-allow-headers': '*',
    'access-control-allow-methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  };
}
