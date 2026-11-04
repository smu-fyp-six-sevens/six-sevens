import type { CaseRiskLevel } from '../services/uploadService';
import { caseStageLabel, caseStageTone, type StatusTone } from './caseStages';

export interface CaseRecord {
  id: string;
  caseNumber?: string | null;
  applicant: string;
  // The stage's label and colour (see utils/caseStages.ts); caseStatus is the stage itself.
  status: string;
  statusTone: StatusTone;
  caseStatus: string;
  stuck?: boolean;
  created_at?: string | null;
  // Only set once the case has been scored (see backend/app/case_risk.py).
  riskLevel?: CaseRiskLevel | null;
  riskScore?: number | null;
  riskDescription?: string | null;
  // Automated processing time in ms (backend audit.processing_ms_many); null until analysed.
  processingMs?: number | null;
  // Titles of the documents its analysis recommends requesting; null until analysed.
  recommendedDocuments?: string[] | null;
  // The types of its documents (backend values such as "bank_statement"), each once.
  documentTypes?: string[];
  // Its flags grouped by detection check, most flags first.
  flags?: CaseFlagCount[];
  // The username of the officer who created it; null when the audit trail has no record.
  createdBy?: string | null;
}

export interface CaseFlagCount {
  check: string;
  severity: string;
  count: number;
  documentTypes: string[];
}

// What a case's saved risk is read for here: whether it is scored, and its level, score and first reason.
interface ApiRisk {
  state: string;
  level?: CaseRiskLevel | null;
  score?: number | null;
  reasons?: string[];
}

interface ApiCase {
  id: string;
  greenlink_case_id?: string | null;
  company_name?: string | null;
  status?: string | null;
  // Only on queue rows: a search result is a plain case.
  documents_status?: string | null;
  created_at?: string | null;
  risk?: ApiRisk | null;
  // Only on queue rows.
  processing_ms?: number | null;
  recommended_documents?: string[] | null;
  document_types?: string[];
  flags?: { check: string; severity: string; count: number; document_types?: string[] }[];
  created_by?: string | null;
}

export function toCaseRecord(item: ApiCase): CaseRecord {
  const scored = item.risk?.state === 'scored' ? item.risk : null;

  return {
    id: item.id,
    caseNumber: item.greenlink_case_id ?? null,
    applicant: item.company_name ?? 'Unnamed company',
    status: caseStageLabel(item.status),
    statusTone: caseStageTone(item.status),
    caseStatus: item.status ?? 'open',
    // A document held up past its time limit (backend monitoring.is_stuck): flagged next to the stage.
    stuck: item.documents_status === 'stuck',
    created_at: item.created_at ?? null,
    riskLevel: scored ? scored.level : null,
    riskScore: scored ? scored.score : null,
    riskDescription: scored ? scored.reasons?.[0] ?? null : null,
    processingMs: item.processing_ms ?? null,
    recommendedDocuments: item.recommended_documents ?? null,
    documentTypes: item.document_types ?? [],
    flags: (item.flags ?? []).map((flag) => ({
      check: flag.check, severity: flag.severity, count: flag.count, documentTypes: flag.document_types ?? [],
    })),
    createdBy: item.created_by ?? null,
  };
}
