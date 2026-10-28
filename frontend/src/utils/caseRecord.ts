import type { CaseRecord } from '../App';
import type { CaseRiskLevel } from '../services/uploadService';
import { caseStageLabel, caseStageTone } from './caseStages';

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
  };
}
