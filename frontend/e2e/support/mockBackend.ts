import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import type { AuditEvent, CaseAnalysis, CaseRisk } from '../../src/services/uploadService/types';

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
function missingRequiredFields(document: { document_type: string | null; extracted_fields: Record<string, unknown> | null }): string[] {
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
 *
 * Keeping it from drifting from the backend: where a rule is too involved to
 * copy, the mock serves what the real backend computed, from a fixture a
 * backend test keeps in step (it fails when the backend changes; regenerate
 * with REGENERATE_REGRESSION_FIXTURES=1):
 *   required_fields.json            test_regression_fixtures.py (approval's required fields)
 *   checklist-variants.json         test_regression_checklist.py (the checklist for a set of documents)
 *   case-match-foreign-company.json test_regression_case_match.py
 *   detection-report-{c028,clean,inconclusive}.json  test_regression_detection.py
 *   case-risk-sprint4.json          test_regression_case_risk.py
 *   case-views.json                 test_regression_case_views.py (a case's risk and additional documents)
 *   detection-metrics-sprint5.json  test_regression_detection_metrics.py (the Accuracy tab's figures)
 * The simpler rules are mirrored here, each marked with the backend function
 * it copies (analysis readiness, the documents status, which documents can be
 * approved, rejected, edited, re-typed, deleted, moved, retried or cancelled,
 * the case cost and timings): change both together.
 */

const API_URL = 'http://api.test';

export interface CaseRow {
  id: string;
  greenlink_case_id: string;
  company_name: string | null;
  // A CaseStage (backend/app/models.py): what the queue filters and counts on.
  status: string;
  // What a failed case needs fixed (backend/app/case_stage.py).
  failure?: string | null;
  // While analysed: findings without the officer's verdict; finalising waits for 0.
  findings_to_review?: number | null;
  // Whether the case needs every required document approved before analysis (set when it is created).
  checklist_enforced: boolean;
  // Whether officers review this case's findings before it can be finalised (chosen when it is created).
  reviews_required: boolean;
  missing_requirements: string[];
  // What the analysis waits for: required items no approved document covers; empty unless checklist_enforced.
  unapproved_requirements: string[];
  created_at: string;
  updated_at: string;
  dev: boolean;
  fulfilled_requirements: string[];
  proof_of_address_coverage: { covered: number; total: number; unmatched_names: string[] };
  id_document_coverage: { covered: number; total: number; missing_side: string | null };
  // Computed by backend/app/case_risk.py on every case read.
  risk: CaseRisk | null;
  // Computed by backend/app/case_pipeline/risk/additional_documents.py when a case analysis runs.
  additional_documents?: unknown;
  // The latest case analysis run in brief (case_service.build_summary). analysis_ready isn't stored: like the
  // backend, the mock computes it from the documents on every read (see isReadyForAnalysis).
  analysis?: { run_id: string; status: CaseAnalysis['status']; stale: boolean; finished_at: string | null; finding_count: number } | null;
}

export interface DocumentRow {
  /** Set by the snapshot when the last detection run left an error and no report. */
  has_detection_error?: boolean;
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

/** A review sign-off as GET /cases/{id}/finding-reviews returns it, with the case it belongs to. */
export interface SignOffRow {
  case_id: string;
  scope: 'document' | 'case';
  document_id: string | null;
  reviewer: string;
  updated_at: string;
}

export interface NoteRow {
  id: string;
  case_id: string;
  document_id: string | null;
  parent_id: string | null;
  author: string;
  body: string;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
}

/**
 * The checklist the real backend computes for one exact set of a case's documents (build_summary), written by
 * backend/tests/test_regression_checklist.py into checklist-variants.json. The mock can't run the backend's
 * checklist rules, so it serves this whenever the case holds exactly these documents.
 */
export interface ChecklistVariant {
  name: string;
  case_id: string;
  document_ids: string[];
  fulfilled_requirements: string[];
  missing_requirements: string[];
  proof_of_address_coverage: CaseRow['proof_of_address_coverage'];
  id_document_coverage: CaseRow['id_document_coverage'];
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

// Ids come from a counter, so two cases or documents made in one test can never collide. Case numbers made here stay
// below 9000, where the ones the mock creates itself begin.
let serial = 0;
const nextSerial = () => (serial += 1);

export function makeCase(overrides: Partial<CaseRow> = {}): CaseRow {
  const number = nextSerial();
  const id = overrides.id ?? `case-${number}`;
  return {
    id,
    greenlink_case_id: overrides.greenlink_case_id ?? `GLDB-${1000 + (number % 8000)}`,
    company_name: 'Harbor Light Shipping Pte Ltd',
    status: 'open',
    checklist_enforced: false,
    reviews_required: true,
    missing_requirements: [],
    unapproved_requirements: [],
    created_at: ISO,
    updated_at: ISO,
    dev: true,
    fulfilled_requirements: [],
    proof_of_address_coverage: { covered: 0, total: 0, unmatched_names: [] },
    id_document_coverage: { covered: 0, total: 0, missing_side: null },
    // Only a completed case analysis sets a case's risk; until then the backend reads null.
    risk: null,
    ...overrides,
  };
}

/** A case analysis run as GET /cases/:id/analysis returns it (backend case_pipeline/models.py CaseAnalysis). */
export function makeAnalysis(overrides: Partial<CaseAnalysis> & Pick<CaseAnalysis, 'case_id'>): CaseAnalysis {
  return {
    run_id: `run-${nextSerial()}`,
    status: 'complete',
    fingerprint: 'fingerprint',
    started_at: ISO,
    finished_at: ISO,
    error: null,
    confidence: 0,
    findings: [],
    outlook: null,
    structured_summary: null,
    risk: null,
    supporting_documents: [],
    additional_documents: null,
    consistent_checks: [],
    clean_checks: [],
    audit: { models: {}, prompt_version: '', hypotheses_tried: 0, findings_discarded: 0, tool_calls: [], agent_decisions: [] },
    trace: [],
    stale: false,
    ...overrides,
  };
}

const IN_FLIGHT = ['uploaded', 'queued', 'processing', 'extracting'];
const APPROVED = ['approved', 'detecting', 'detected'];
// The officer's decision is final: approved (and later) and rejected documents are locked (routers/documents.py).
const LOCKED = [...APPROVED, 'rejected'];

/** Same rule as backend models.is_classification_confirmed: an Other or untyped document needs the officer. */
function isClassificationConfirmed(document: DocumentRow): boolean {
  const confidence = document.classification_confidence;
  if (confidence === null) return false;
  if (!document.document_type || document.document_type === 'unrecognised') return confidence >= 1.0;
  return confidence >= 0.85;
}

/** Same rule as backend case_service.has_extracted_information: at least one non-empty value. */
function hasExtractedInformation(document: DocumentRow): boolean {
  const value = (field: unknown) => (field && typeof field === 'object' && 'value' in field ? field.value : field);
  return Object.values(document.extracted_fields ?? {}).some((field) => {
    const v = value(field);
    if (v === null || v === undefined) return false;
    if (typeof v === 'string') return v.trim() !== '';
    if (Array.isArray(v)) return v.length > 0;
    if (typeof v === 'object') return Object.keys(v).length > 0;
    return true;
  });
}

/** Same rules as backend document_service.approve: why this document can't be approved, or null. */
function approvalRefusal(document: DocumentRow): string | null {
  if (document.status !== 'analyzed') return 'Only completed documents can be approved.';
  if (!isClassificationConfirmed(document)) return 'Cannot approve document. Confirm the document type first.';
  if (!hasExtractedInformation(document)) return 'Cannot approve document. No information was extracted from it.';
  const missing = missingRequiredFields(document);
  if (missing.length > 0) return `Cannot approve document. Required fields are missing or empty: ${missing.join(', ')}`;
  return null;
}

// The finished runs backend audit.case_cost reads (its _COSTED).
const COSTED_EVENTS = new Set([
  'document.analyzed', 'document.failed', 'document.detected', 'detection.failed', 'case.analysis.completed',
]);

// STUCK_THRESHOLD_SECONDS and STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS in backend config.py (both 600).
const STUCK_THRESHOLD_MS = 600_000;
const sinceLastProgress = (document: DocumentRow) =>
  Date.now() - new Date(document.processing_started_at ?? document.queued_at ?? document.uploaded_at).getTime();

/** Same rule as backend monitoring.is_stuck: still in flight, with no progress for the threshold. */
function isStuck(document: DocumentRow): boolean {
  return IN_FLIGHT.includes(document.status) && sinceLastProgress(document) > STUCK_THRESHOLD_MS;
}

/** Same rule as backend monitoring.is_stuck_processing_for_retry: processing, with no progress for the threshold. */
function isStuckProcessing(document: DocumentRow): boolean {
  return document.status === 'processing' && sinceLastProgress(document) > STUCK_THRESHOLD_MS;
}

/** Same rule as backend case_service.compute_documents_status: the worst state among the case's documents. */
function documentsStatus(documents: DocumentRow[]): string {
  if (documents.length === 0) return 'no_documents';
  if (documents.some(isStuck)) return 'stuck';
  if (documents.some((d) => d.status === 'failed')) return 'failed';
  if (documents.every((d) => d.status === 'analyzed')) return 'analyzed';
  return 'in_progress';
}

/** Same rule as backend case_pipeline/readiness.py: at least one approved document, and every approved one scanned. */
function isReadyForAnalysis(documents: Pick<DocumentRow, 'status'>[]): boolean {
  const approved = documents.filter((d) => APPROVED.includes(d.status));
  return approved.length > 0 && approved.every((d) => d.status === 'detected');
}

export function makeDocument(overrides: Partial<DocumentRow> = {}): DocumentRow {
  return {
    id: `doc-${nextSerial()}`,
    case_id: null,
    filename: 'document.pdf',
    document_type: null,
    classification_confidence: null,
    routing_status: null,
    storage_path: 'placeholder/path.pdf',
    status: 'queued',
    retry_count: 0,
    // Just uploaded: an older in-flight document would read as stuck (monitoring.is_stuck).
    uploaded_at: new Date().toISOString(),
    queued_at: new Date().toISOString(),
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
  /** Why a document's last detection run left no report (cancelled or crashed), per document id: the report GET answers 502. */
  detectionErrors: Record<string, string> = {};
  /** LangGraph pipeline trace per document id (GET .../langgraph-trace); none = 404. */
  traces: Record<string, unknown> = {};
  /** What the content guard found at ingest, per document id (GET .../content-guard); none = checked, nothing found. */
  contentGuards: Record<string, unknown> = {};
  /** Notes on cases and documents (GET/POST /cases/:id/notes), signed by the logged-in officer. */
  notes: NoteRow[] = [];
  /** Officers' reviews of groups with no findings to mark, one per case and group, like findings_repository.sign_off. */
  signOffs: SignOffRow[] = [];
  /** Audit trail entries (GET /audit/events?case_id=), in any order: served newest first, like the backend. */
  auditEvents: AuditEvent[] = [];
  /** Each case's latest analysis run (GET /cases/:id/analysis); none = 404. POST .../analysis/run starts one. */
  analyses: Record<string, CaseAnalysis> = {};
  /** Per-filename upload results; files not listed here upload successfully. */
  uploadOutcomes: Record<string, UploadOutcome> = {};
  /** When set, upload-batch waits for this before answering (to observe "Uploading"). */
  uploadGate: Promise<void> | null = null;
  /** When set, a detection cancel is recorded at once but answered only after this (to have the worker's snapshot arrive first). */
  cancelGate: Promise<void> | null = null;
  /** Backend-computed checklists for exact document sets (see ChecklistVariant); loadFixture registers them. */
  checklists: ChecklistVariant[] = [];
  /** GET /detection-metrics as the backend computed it (detection-metrics-sprint5.json); null: no reviews yet. */
  detectionMetrics: unknown = null;
  /** Credentials /auth/login accepts. */
  credentials = { username: 'officer', password: 'correct-password' };
  /** When true the session has expired: like require_auth, every call but /auth/login answers 401. */
  sessionExpired = false;

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

  /** Drop the case page's live connection from the server side, as a backend restart or network blip would. */
  async dropConnection(caseId: string): Promise<void> {
    const socket = this.sockets.get(caseId);
    this.sockets.delete(caseId);
    await socket?.close();
  }

  requestsTo(method: string, path: string | RegExp): RecordedRequest[] {
    return this.requests.filter(
      (r) => r.method === method && (typeof path === 'string' ? r.path === path : path.test(r.path)),
    );
  }

  // The case endpoints below mirror backend/app/case_service.py (list_cases_page, case_counts, search_cases).
  private matching(q: string | null): CaseRow[] {
    const needle = (q ?? '').trim().toLowerCase();
    return this.cases.filter(
      (c) => !needle || c.greenlink_case_id.toLowerCase().includes(needle) || (c.company_name ?? '').toLowerCase().includes(needle),
    );
  }

  private newestFirst(cases: CaseRow[]): CaseRow[] {
    return [...cases].sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
  }

  private casePage(params: URLSearchParams) {
    const limit = Number(params.get('limit') ?? 25);
    const offset = Number(params.get('offset') ?? 0);
    const sort = params.get('sort') ?? 'newest';
    const statuses = params.getAll('status');
    const risks = params.getAll('risk');
    const rank = (c: CaseRow) => (c.risk?.state === 'scored' ? ({ Critical: 4, High: 3, Medium: 2, Low: 1 } as Record<string, number>)[c.risk.level ?? ''] ?? 0 : 0);
    const riskValue = (c: CaseRow) => (rank(c) ? c.risk!.level! : 'unscored');

    let cases = this.newestFirst(this.matching(params.get('q')));
    if (statuses.length) cases = cases.filter((c) => statuses.includes(c.status));
    if (risks.length) cases = cases.filter((c) => risks.includes(riskValue(c)));
    if (sort === 'oldest') cases.reverse();
    // Stable sorts: newest first among equals.
    if (sort === 'risk') cases = [...cases].sort((a, b) => rank(b) - rank(a));
    if (sort === 'lowest_risk') cases = [...cases].sort((a, b) => rank(a) - rank(b));
    return { items: cases.slice(offset, offset + limit).map((c) => this.listItem(c)), total: cases.length, limit, offset };
  }

  private caseCounts() {
    const by_status: Record<string, number> = {};
    for (const c of this.cases) by_status[c.status] = (by_status[c.status] ?? 0) + 1;
    return { total: this.cases.length, by_status };
  }

  private caseSearch(params: URLSearchParams) {
    const limit = Number(params.get('limit') ?? 8);
    const q = (params.get('q') ?? '').trim().toLowerCase();
    const rank = (c: CaseRow) => {
      const number = c.greenlink_case_id.toLowerCase();
      if (q === number || q === c.id.toLowerCase()) return 0;
      return number.startsWith(q) || (c.company_name ?? '').toLowerCase().startsWith(q) ? 1 : 2;
    };
    const matches = this.newestFirst(this.matching(q));
    const ranked = q ? [...matches].sort((a, b) => rank(a) - rank(b)) : matches;
    return { items: ranked.slice(0, limit).map((c) => this.listItem(c)), total: matches.length };
  }

  /**
   * Same as backend audit.case_cost: each run in the case's audit trail that recorded its Claude spend, a total
   * per document, and the case total; and how long each finished run took (the summary's Processing Time).
   */
  private caseCost(caseId: string) {
    const recorded = this.auditEvents
      .filter((e) => e.case_id === caseId && COSTED_EVENTS.has(e.type))
      .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at));
    const timings = recorded
      .filter((e) => e.outcome === 'success' && e.duration_ms !== null)
      .map((e) => ({ occurred_at: e.occurred_at, type: e.type, document_id: e.document_id, duration_ms: e.duration_ms }));
    const runs = recorded
      .filter((e) => typeof e.detail.llm_cost_usd === 'number')
      .map((e) => ({
        occurred_at: e.occurred_at,
        type: e.type,
        run: (e.detail.run as string | undefined) ?? null,
        document_id: e.document_id,
        llm_cost_usd: e.detail.llm_cost_usd as number,
        llm_calls: (e.detail.llm_calls as number | undefined) ?? null,
        llm_cache_read_tokens: (e.detail.llm_cache_read_tokens as number | undefined) ?? null,
      }));
    const round = (usd: number) => Math.round(usd * 1e6) / 1e6;
    const documents_usd: Record<string, number> = {};
    for (const run of runs) {
      if (run.document_id) documents_usd[run.document_id] = round((documents_usd[run.document_id] ?? 0) + run.llm_cost_usd);
    }
    return {
      case_id: caseId,
      total_usd: round(runs.reduce((sum, r) => sum + r.llm_cost_usd, 0)),
      documents_usd,
      case_analysis_usd: round(runs.filter((r) => r.run === 'case_analysis').reduce((sum, r) => sum + r.llm_cost_usd, 0)),
      runs,
      timings,
    };
  }

  private caseDocuments(caseId: string): DocumentRow[] {
    return this.documents.filter((d) => d.case_id === caseId);
  }

  private documentsStatusOf(theCase: CaseRow): string {
    return documentsStatus(this.caseDocuments(theCase.id));
  }

  /** A case as the case list returns it: its documents' rollup computed on every read, like the backend. */
  private listItem(theCase: CaseRow) {
    return { ...theCase, documents_status: this.documentsStatusOf(theCase) };
  }

  /**
   * The case as the backend's case summary reads: analysis_ready computed from its documents, never stored, and
   * the checklist the backend computed for exactly these documents when a test has left the case with one of them.
   */
  private summary(theCase: CaseRow) {
    const documents = this.caseDocuments(theCase.id);
    const ids = documents.map((d) => d.id).sort().join(',');
    const checklist = this.checklists.find((c) => c.case_id === theCase.id && c.document_ids.join(',') === ids);
    return {
      ...this.listItem(theCase),
      ...(checklist && {
        fulfilled_requirements: checklist.fulfilled_requirements,
        missing_requirements: checklist.missing_requirements,
        proof_of_address_coverage: checklist.proof_of_address_coverage,
        id_document_coverage: checklist.id_document_coverage,
      }),
      analysis_ready: isReadyForAnalysis(documents),
    };
  }

  private snapshot(caseId: string) {
    const found = this.cases.find((c) => c.id === caseId);
    return {
      type: 'snapshot',
      case: found ? this.summary(found) : null,
      documents: this.caseDocuments(caseId),
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

    if (this.sessionExpired && path !== '/auth/login') {
      return json({ detail: 'Invalid or expired token' }, 401);
    }

    // --- auth -------------------------------------------------------------
    if (method === 'POST' && path === '/auth/login') {
      const { username, password } = (body ?? {}) as { username?: string; password?: string };
      if (username === this.credentials.username && password === this.credentials.password) {
        // Shaped like the real token: base64url({"sub": username}) + "." + signature.
        const payload = Buffer.from(JSON.stringify({ sub: username })).toString('base64url');
        return json({ token: `${payload}.test-signature`, username });
      }
      return json({ detail: 'Invalid username or password' }, 401);
    }
    if (method === 'POST' && path === '/ws/tickets') {
      return json({ ticket: 'test-ticket' });
    }

    // Read-only agent registry used by the Know Your Agents page. Keep this
    // fixture aligned with the roles returned by backend/app/agent_registry.py.
    if (method === 'GET' && path === '/agents') {
      const agents = [
        {
          id: 'document_classifier', name: 'Document classifier', group: 'Document processing',
          purpose: 'Identifies the uploaded document type.', limitations: 'OCR or unusual layouts can lead to a wrong type.',
          model: 'claude-sonnet', prompt_version: null, tools: ['llm_classify_tool'],
          access_summary: ['Read document text'], call_budget: 'Shared document run cap: 20 calls', owner: null,
          prompt_options: [],
        },
        {
          id: 'field_extractor', name: 'Field extractor', group: 'Document processing',
          purpose: 'Extracts document details into the fields for its document type.', limitations: 'Extracted fields need review.',
          model: 'claude-sonnet', prompt_version: null, tools: [],
          access_summary: ['Read document text and page images'], call_budget: 'Shared document run cap: 20 calls', owner: null,
          prompt_options: ['nric', 'fatca_crs'],
        },
        {
          id: 'detection_lead', name: 'Detection lead', group: 'Fraud detection',
          purpose: 'Plans and reviews specialist checks for one document.', limitations: 'Unavailable checks remain unassessed.',
          model: 'claude-sonnet', prompt_version: null, tools: ['Specialist agents'],
          access_summary: ['Read one document'], call_budget: 'Shared detection run cap: 30 calls', owner: null,
          prompt_options: [],
        },
        {
          id: 'layout_rendering', name: 'Layout & Rendering Analyst', group: 'Fraud detection specialists',
          purpose: 'Checks visual layout, typography, hidden text and image integrity.', limitations: 'Visual heuristics can raise false alarms.',
          model: 'claude-haiku', prompt_version: null, tools: ['font_consistency_tool'],
          access_summary: ['Read one document'], call_budget: 'Shared detection run cap: 30 calls', owner: null,
          prompt_options: [],
        },
        {
          id: 'content_validation', name: 'Content Validation Analyst', group: 'Fraud detection specialists',
          purpose: 'Checks dates, figures, spelling, page consistency and relevant public records.', limitations: 'Some checks depend on available reference data.',
          model: 'claude-haiku', prompt_version: null, tools: ['numerical_checks_tool'],
          access_summary: ['Read one document'], call_budget: 'Shared detection run cap: 30 calls', owner: null,
          prompt_options: [],
        },
        {
          id: 'digital_authenticity', name: 'Digital Authenticity Analyst', group: 'Fraud detection specialists',
          purpose: 'Checks file metadata and signs of AI-generated or altered imagery.', limitations: 'Some file checks may be unavailable.',
          model: 'claude-haiku', prompt_version: null, tools: ['metadata_tool'],
          access_summary: ['Read one document and file metadata'], call_budget: 'Shared detection run cap: 30 calls', owner: null,
          prompt_options: [],
        },
        {
          id: 'hypothesis_planner', name: 'Hypothesis planner', group: 'Case analysis',
          purpose: 'Proposes cross-document inconsistencies that can be tested.', limitations: 'A proposed hypothesis is only a lead.',
          model: 'claude-sonnet', prompt_version: 'case-analysis-v1', tools: ['Cross-document evidence tools'],
          access_summary: ['Read approved case facts'], call_budget: '20 calls across case-analysis agents per run', owner: null,
          prompt_options: [],
        },
        {
          id: 'hypothesis_judge', name: 'Hypothesis judge', group: 'Case analysis',
          purpose: 'Assesses evidence and counter-evidence for proposed hypotheses.', limitations: 'May return inconclusive.',
          model: 'claude-sonnet', prompt_version: 'case-analysis-v1', tools: ['Cross-document evidence tools'],
          access_summary: ['Read approved case facts and test results'], call_budget: '20 calls across case-analysis agents per run', owner: null,
          prompt_options: [],
        },
      ];
      return json(agents);
    }

    // --- cases ------------------------------------------------------------
    if (path === '/cases' && method === 'GET') {
      return json(this.casePage(url.searchParams));
    }
    if (path === '/cases/counts' && method === 'GET') {
      return json(this.caseCounts());
    }
    if (path === '/cases/search' && method === 'GET') {
      return json(this.caseSearch(url.searchParams));
    }
    const byNumber = path.match(/^\/cases\/by-number\/([^/]+)$/);
    if (byNumber && method === 'GET') {
      const number = decodeURIComponent(byNumber[1]);
      const found = this.cases.find((c) => c.greenlink_case_id === number || c.id === number);
      return found ? json(this.listItem(found)) : json({ detail: 'Case not found' }, 404);
    }
    if (path === '/cases' && method === 'POST') {
      const request = body as { company_name?: string; reviews_required?: boolean } | null;
      const next = this.cases.length + 1;
      const created = makeCase({
        id: `case-new-${next}`,
        greenlink_case_id: `GLDB-${9000 + next}`,
        company_name: request?.company_name ?? null,
        reviews_required: request?.reviews_required ?? true,
      });
      this.cases.push(created);
      return json(created, 201);
    }
    // Same as audit.cost_usd_many: each case's total, absent when nothing was costed.
    if (method === 'GET' && path === '/audit/costs') {
      const totals = url.searchParams.getAll('case_id').map((id) => [id, this.caseCost(id).total_usd] as const);
      return json(Object.fromEntries(totals.filter(([, usd]) => usd > 0)));
    }
    const costMatch = path.match(/^\/audit\/cases\/([^/]+)\/cost$/);
    if (costMatch && method === 'GET') {
      return json(this.caseCost(costMatch[1]));
    }
    // Like routers/finding_reviews.py: a case created without reviews refuses every review route.
    const reviewedCase = path.match(/^\/cases\/([^/]+)\/finding-reviews/);
    if (reviewedCase && this.cases.find((c) => c.id === reviewedCase[1])?.reviews_required === false) {
      return json({ detail: 'This case was created without finding reviews.' }, 409);
    }
    // Officers' reviews of findings (precision/recall): no verdicts recorded, so every rate is "nothing to measure";
    // the case's sign-offs as saved.
    const reviews = (caseId: string) =>
      json({
        reviews: [],
        sign_offs: this.signOffs
          .filter((signOff) => signOff.case_id === caseId)
          .map(({ scope, document_id, reviewer, updated_at }) => ({ scope, document_id, reviewer, updated_at })),
        case: emptyMetrics(),
        documents: {},
        cross_document: emptyMetrics(),
        checks: { document: [], case: [] },
      });
    if (method === 'GET' && reviewedCase && path.endsWith('/finding-reviews')) {
      return reviews(reviewedCase[1]);
    }
    if (method === 'PUT' && reviewedCase && path.endsWith('/finding-reviews/sign-offs')) {
      // Like findings_repository.sign_off: one per group, a later one replacing it, signed by the logged-in officer.
      const { scope, document_id } = body as Pick<SignOffRow, 'scope' | 'document_id'>;
      this.signOffs = this.signOffs.filter((s) => !(s.case_id === reviewedCase[1] && s.document_id === document_id));
      this.signOffs.push({
        case_id: reviewedCase[1], scope, document_id, reviewer: this.credentials.username, updated_at: '2026-10-31T00:00:00Z',
      });
      return reviews(reviewedCase[1]);
    }
    if (method === 'GET' && path === '/detection-metrics') {
      return json(this.detectionMetrics ?? {
        overall: emptyMetrics(), cases_reviewed: 0, documents_reviewed: 0,
        target_precision: 0.8, target_recall: 0.8, min_reviews: 5,
      });
    }
    // Like routers/cases.py download_case_report: the PDF, named by case_report.filename_for. The report's content
    // is covered by the backend's test_regression_case_report.py.
    const reportMatch = path.match(/^\/cases\/([^/]+)\/report\.pdf$/);
    if (reportMatch && method === 'GET') {
      const found = this.cases.find((c) => c.id === reportMatch[1]);
      if (!found) return json({ detail: 'Case not found' }, 404);
      return route.fulfill({
        status: 200,
        headers: {
          ...corsHeaders(),
          'access-control-expose-headers': 'Content-Disposition',
          'content-disposition': `attachment; filename="${found.greenlink_case_id} Case Report.pdf"`,
        },
        contentType: 'application/pdf',
        body: MINIMAL_PDF,
      });
    }

    const caseMatch = path.match(/^\/cases\/([^/]+)$/);
    if (caseMatch) {
      const found = this.cases.find((c) => c.id === caseMatch[1]);
      if (!found) return json({ detail: 'Case not found' }, 404);
      if (method === 'GET') return json(this.summary(found));
      if (method === 'PATCH') {
        found.company_name = (body as { company_name: string }).company_name;
        return json(this.summary(found));
      }
      if (method === 'DELETE') {
        this.cases = this.cases.filter((c) => c.id !== found.id);
        return route.fulfill({ status: 204, headers: corsHeaders() });
      }
    }
    // Same rules as routers/cases.py run_case_analysis: only once every approved document is scanned, and never
    // while a run is going. The run then stays "running" until a test finishes it (the case-worker's part) and
    // pushes a snapshot.
    // GET .../analysis/runs (newest first) and .../analysis/runs/:runId: the one run the mock keeps per case.
    const runsMatch = path.match(/^\/cases\/([^/]+)\/analysis\/runs(?:\/([^/]+))?$/);
    if (runsMatch && method === 'GET') {
      const latest = this.analyses[runsMatch[1]];
      if (runsMatch[2]) {
        return latest?.run_id === runsMatch[2] ? json(latest) : json({ detail: 'No analysis with that id for this case.' }, 404);
      }
      return json(
        latest
          ? [{
              run_id: latest.run_id, status: latest.status, started_at: latest.started_at, finished_at: latest.finished_at,
              started_by: null, risk_level: latest.risk?.level ?? null, risk_score: latest.risk?.score ?? null,
              score_change: null, finding_count: latest.findings.length, stale: latest.stale,
            }]
          : [],
      );
    }
    const analysisMatch = path.match(/^\/cases\/([^/]+)\/analysis(\/run)?$/);
    if (analysisMatch) {
      const theCase = this.cases.find((c) => c.id === analysisMatch[1]);
      if (!theCase) return json({ detail: 'Case not found' }, 404);
      const latest = this.analyses[theCase.id];
      if (method === 'GET' && !analysisMatch[2]) {
        return latest ? json(latest) : json({ detail: 'This case has not been analysed.' }, 404);
      }
      if (method === 'POST' && analysisMatch[2]) {
        if (!isReadyForAnalysis(this.caseDocuments(theCase.id))) {
          return json({ detail: 'Every approved document must finish scanning before the case can be analysed.' }, 409);
        }
        if (latest?.status === 'running') {
          return json({ detail: 'An analysis is already running for this case.' }, 409);
        }
        const started = makeAnalysis({ case_id: theCase.id, status: 'running', finished_at: null });
        this.analyses[theCase.id] = started;
        theCase.analysis = { run_id: started.run_id, status: 'running', stale: false, finished_at: null, finding_count: 0 };
        return json(started, 202);
      }
    }
    if (path === '/audit/events' && method === 'GET') {
      const caseId = url.searchParams.get('case_id');
      return json(
        this.auditEvents
          .filter((e) => caseId === null || e.case_id === caseId)
          .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)),
      );
    }
    // The officer finalising a case (permanent).
    const stageMatch = path.match(/^\/cases\/([^/]+)\/(finalise)$/);
    if (stageMatch && method === 'POST') {
      const found = this.cases.find((c) => c.id === stageMatch[1]);
      if (!found) return json({ detail: 'Case not found' }, 404);
      if (found.status !== 'case_analysed') {
        return json({ detail: 'Only an analysed case, with its findings evaluated, can be finalised.' }, 409);
      }
      // Like routers/cases.py finalise_case: the change is recorded in the audit trail as the officer's.
      this.auditEvents.push({
        event_id: `case.finalised-${found.id}`, type: 'case.finalised', occurred_at: new Date().toISOString(),
        actor_type: 'user', actor: this.credentials.username, case_id: found.id, document_id: null,
        outcome: 'success', started_at: null, duration_ms: null, detail: { from: found.status },
      });
      found.status = 'finalised';
      return json(found);
    }
    const notesMatch = path.match(/^\/cases\/([^/]+)\/notes$/);
    if (notesMatch) {
      const caseId = notesMatch[1];
      if (method === 'GET') return json(this.notes.filter((n) => n.case_id === caseId));
      if (method === 'POST') {
        const { body: text, document_id, parent_id } = body as {
          body: string;
          document_id: string | null;
          parent_id: string | null;
        };
        const parent = parent_id ? this.notes.find((n) => n.id === parent_id) : undefined;
        if (parent?.deleted_at) return json({ detail: 'This note was deleted.' }, 409);
        const note: NoteRow = {
          id: `note-${this.notes.length + 1}`,
          case_id: caseId,
          // Like the backend: a reply belongs to its note's document.
          document_id: parent ? parent.document_id : document_id,
          parent_id: parent_id ?? null,
          author: this.credentials.username,
          body: text.trim(),
          created_at: new Date().toISOString(),
          edited_at: null,
          deleted_at: null,
        };
        this.notes.push(note);
        return json(note, 201);
      }
    }
    const noteMatch = path.match(/^\/cases\/([^/]+)\/notes\/([^/]+)$/);
    if (noteMatch && (method === 'PATCH' || method === 'DELETE')) {
      const note = this.notes.find((n) => n.id === noteMatch[2] && n.case_id === noteMatch[1]);
      if (!note) return json({ detail: 'Note not found in this case' }, 404);
      // Like the backend: only the officer who wrote a note can change it.
      if (note.author !== this.credentials.username) {
        return json({ detail: `Only the officer who wrote a note can ${method === 'PATCH' ? 'edit' : 'delete'} it.` }, 403);
      }
      if (note.deleted_at) return json({ detail: 'This note was already deleted.' }, 409);
      if (method === 'PATCH') {
        note.body = (body as { body: string }).body.trim();
        note.edited_at = new Date().toISOString();
        return json(note);
      }
      // Like the backend: replies stay. A note with replies becomes a placeholder, which goes with its last reply.
      const replies = (id: string) => this.notes.filter((n) => n.parent_id === id);
      if (note.parent_id === null && replies(note.id).length > 0) {
        note.body = '(deleted)';
        note.deleted_at = new Date().toISOString();
      } else {
        this.notes = this.notes.filter((n) => n.id !== note.id);
        const parent = this.notes.find((n) => n.id === note.parent_id);
        if (parent?.deleted_at && replies(parent.id).length === 0) {
          this.notes = this.notes.filter((n) => n.id !== parent.id);
        }
      }
      return route.fulfill({ status: 204, headers: corsHeaders() });
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
    // Same rules as routers/documents.py bulk_move_documents: approved, rejected or in-flight documents, and
    // ones already in the target case, are refused one by one; the rest move.
    if (method === 'POST' && path === '/documents/bulk-move') {
      const { document_ids, case_id } = body as { document_ids: string[]; case_id: string };
      if (document_ids.length === 0) return json({ detail: 'No documents specified.' }, 400);
      if (!this.cases.some((c) => c.id === case_id)) return json({ detail: 'Target case not found' }, 404);
      const moved: string[] = [];
      const failed: { document_id: string; reason: string }[] = [];
      for (const id of document_ids) {
        const document = this.documents.find((d) => d.id === id);
        const refusal = !document
          ? 'Document not found'
          : LOCKED.includes(document.status)
            ? `An ${document.status} document cannot be moved.`
            : IN_FLIGHT.includes(document.status)
              ? 'Document is still being processed.'
              : document.case_id === case_id
                ? 'Document is already in that case.'
                : null;
        if (refusal) {
          failed.push({ document_id: id, reason: refusal });
        } else {
          document!.case_id = case_id;
          moved.push(id);
        }
      }
      return json({ moved, failed });
    }
    // Same rules as routers/documents.py bulk_approve_documents: each document is approved as a single one would be,
    // and refusals come back one by one.
    if (method === 'POST' && path === '/documents/bulk-approve') {
      const { document_ids } = body as { document_ids: string[] };
      if (document_ids.length === 0) return json({ detail: 'No documents specified.' }, 400);
      const approved: DocumentRow[] = [];
      const failed: { document_id: string; reason: string }[] = [];
      for (const id of document_ids) {
        const document = this.documents.find((d) => d.id === id);
        const refusal = document ? approvalRefusal(document) : 'Document not found';
        if (refusal) {
          failed.push({ document_id: id, reason: refusal });
        } else {
          document!.status = 'approved';
          approved.push(document!);
        }
      }
      return json({ approved, failed });
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
      // Same rules as routers/documents.py: an approved document can't be deleted, an in-flight one is cancelled
      // first; a locked (approved or rejected) document can't be edited or re-typed.
      const locked = (action: string) =>
        json({ detail: `An ${document.status} document is locked and cannot be ${action}.` }, 409);
      if (method === 'DELETE' && rest === '') {
        if (APPROVED.includes(document.status)) {
          return json({ detail: `An ${document.status} document cannot be deleted.` }, 409);
        }
        if (IN_FLIGHT.includes(document.status)) {
          return json({
            detail: 'Document is still being processed; cancel it first, or wait for it to finish, before deleting.',
          }, 409);
        }
        this.documents = this.documents.filter((d) => d.id !== documentId);
        return route.fulfill({ status: 204, headers: corsHeaders() });
      }
      if (method === 'POST' && rest === '/approve') {
        const refusal = approvalRefusal(document);
        if (refusal) return json({ detail: refusal }, 400);
        document.status = 'approved';
        return json(document);
      }
      // Same rule as routers/documents.py's reject_document: anything settled (failed and blocked included) that isn't
      // decided yet; an in-flight one is cancelled first.
      if (method === 'POST' && rest === '/reject') {
        if (LOCKED.includes(document.status)) return locked('rejected');
        if (IN_FLIGHT.includes(document.status)) {
          return json({ detail: 'This document is still being processed. Cancel it first, then reject it.' }, 409);
        }
        document.status = 'rejected';
        return json(document);
      }
      if (rest.startsWith('/extracted-fields') && LOCKED.includes(document.status)) return locked('edited');
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
      // Same rules as routers/documents.py update_document_type: only an unconfirmed type can be set, and only a
      // changed type (or a first pass extracted generically) re-runs extraction.
      if (method === 'PATCH' && rest === '/document-type') {
        if (LOCKED.includes(document.status)) return locked('re-typed');
        if (IN_FLIGHT.includes(document.status)) {
          return json({ detail: 'Document is still being processed; try again once it finishes.' }, 409);
        }
        if (isClassificationConfirmed(document)) {
          return json({ detail: "Classification confidence is above the threshold; the type can't be overridden." }, 400);
        }
        const documentType = (body as { document_type: string }).document_type;
        const firstPassGeneric =
          document.routing_status === 'manual_review' && !['', 'unrecognised'].includes(document.document_type ?? '');
        const needsExtraction =
          documentType !== document.document_type || (firstPassGeneric && documentType !== 'unrecognised');
        document.document_type = documentType;
        document.routing_status = 'accepted';
        document.classification_confidence = 1.0;
        if (needsExtraction) document.status = 'extracting';
        return json(document);
      }
      // Fraud-detection report and LangGraph trace: the real backend answers
      // 404 until the document has one, which the page shows as "none yet".
      if (method === 'GET' && rest === '/detection/report') {
        const report = this.detectionReports[documentId];
        const error = this.detectionErrors[documentId];
        // A rerun that didn't finish leaves the earlier report standing, with why beside it.
        if (report) return json(error ? { ...report, last_run_error: error } : report);
        return error
          ? json({ detail: `Detection failed: ${error}. Run detection again.` }, 502)
          : json({ detail: 'No detection report for this document yet.' }, 404);
      }
      // Like routers/detection.py cancel_document_detection and the worker. A run in flight stops and leaves its
      // error; the document returns to detected when an earlier report still stands, else to approved. The page
      // only hears of it with the next snapshot, so a test pushes one. A run still waiting for a worker (approved,
      // nothing stored) is cancelled at once ("cancelled").
      if (method === 'POST' && rest === '/detection/cancel') {
        const queued =
          document.status === 'approved' && !this.detectionReports[documentId] && !this.detectionErrors[documentId];
        if (document.status !== 'detecting' && !queued) {
          return json({ detail: 'No detection run is currently in progress for this document.' }, 409);
        }
        this.detectionErrors[documentId] = 'Cancelled by loan officer';
        document.status = this.detectionReports[documentId] ? 'detected' : 'approved';
        document.has_detection_error = true;
        if (this.cancelGate) await this.cancelGate;
        return json({ status: queued ? 'cancelled' : 'cancelling' });
      }
      // Same rules as routers/detection.py retry_document_detection: on an approved or detected document, after a
      // cancelled or failed run or over a finished report, never while one runs or waits for a worker. The run
      // then starts, as the detection-worker would.
      if (method === 'POST' && rest === '/detection/retry') {
        if (document.status === 'detecting') {
          return json({ detail: 'Detection is already running for this document. Cancel it first.' }, 409);
        }
        if (document.status !== 'approved' && document.status !== 'detected') {
          return json({ detail: 'Detection runs only on an approved document.' }, 409);
        }
        if (!this.detectionReports[documentId] && !this.detectionErrors[documentId]) {
          return json({ detail: 'Detection is already queued for this document. Cancel it first.' }, 409);
        }
        delete this.detectionErrors[documentId];
        document.status = 'detecting';
        document.has_detection_error = false;
        return json({ status: 'retrying' });
      }
      if (method === 'GET' && rest === '/langgraph-trace') {
        const trace = this.traces[documentId];
        return trace ? json(trace) : json({ detail: 'No langgraph trace found for this document.' }, 404);
      }
      if (method === 'GET' && rest === '/content-guard') {
        return json(
          this.contentGuards[documentId] ?? { checked: true, risk: 'none', blocked: false, error: null, findings: [] },
        );
      }
      // Same rules as routers/documents.py: retry a failed document back
      // into the queue; cancel one that's still in flight.
      if (method === 'POST' && rest === '/retry') {
        if (document.status !== 'failed' && !isStuckProcessing(document)) {
          return json({
            detail: 'Document can only be retried when it has failed, or has been processing for an unusually long time (possibly orphaned).',
          }, 409);
        }
        document.status = 'queued';
        document.failure_reason = null;
        document.retry_count += 1;
        return json(document);
      }
      if (method === 'POST' && rest === '/cancel') {
        if (!['queued', 'processing', 'extracting'].includes(document.status)) {
          return json({ detail: 'Document can only be cancelled while queued, processing, or extracting.' }, 409);
        }
        document.status = 'cancelled';
        return json(document);
      }
      // A signed Storage URL for the file. The document page's side-by-side preview asks for one
      // (?inline=true) as soon as it loads; about:blank renders an empty preview without another request.
      if (method === 'GET' && rest === '/download') {
        return json({ document_id: documentId, signed_url: 'about:blank', expires_in: 3600 });
      }
      // Same answers as routers/detection.py get_case_match.
      if (method === 'GET' && rest === '/detection/case-match') {
        if (document.case_id === null) return json({ detail: 'Document is not attached to a case' }, 404);
        const result = this.caseMatch[document.case_id] as { documents?: { document_id: string }[] } | undefined;
        const entry = result?.documents?.find((d) => d.document_id === documentId);
        return entry ? json(entry) : json({ detail: 'Document not found' }, 404);
      }
    }

    this.unmockedRequests.push(`${method} ${path}${url.search}`);
    return route.fulfill({ status: 599, headers: corsHeaders(), body: `Unmocked request: ${method} ${path}` });
  }
}

function emptyMetrics() {
  return { correct: 0, wrong: 0, missed: 0, precision: null, recall: null, by_check: [] };
}

/** A one-page blank PDF: what the browser saves when the officer downloads a case report. */
const MINIMAL_PDF =
  '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' +
  '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n';

function corsHeaders(): Record<string, string> {
  return {
    'access-control-allow-origin': '*',
    'access-control-allow-headers': '*',
    'access-control-allow-methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  };
}
